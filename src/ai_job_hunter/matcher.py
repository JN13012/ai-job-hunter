"""Classement local et explicable ; aucune inférence LLM, aucun envoi."""

from dataclasses import dataclass, asdict
import json
from pathlib import Path
import re
import tomllib
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from ai_job_hunter.storage import JobStore

VERSION = 1
WEIGHTS = {"role": 30, "contract": 25, "skills": 25, "location": 15, "company": 5}
STATES = ("match", "review", "excluded")


def normalize(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", (text or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w+#.]+", " ", text).split())


def terms_in(text: str | None, terms: tuple[str, ...]) -> tuple[str, ...]:
    haystack = normalize(text)
    return tuple(term for term in terms if re.search(
        r"(?<![\w])" + re.escape(normalize(term)) + r"(?![\w])", haystack
    ))


@dataclass(frozen=True)
class Profile:
    roles: dict[str, tuple[str, ...]]
    skills: tuple[str, ...]
    preferred_locations: tuple[str, ...]
    other_locations: tuple[str, ...]
    excluded_locations: tuple[str, ...]
    preferred_companies: tuple[str, ...]
    min_score: int = 60


def load_profile(path: Path) -> Profile:
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    allowed = {"roles", "skills", "preferred_locations", "other_locations",
               "excluded_locations", "preferred_companies", "min_score"}
    if data.keys() - allowed:
        raise ValueError("Option inconnue dans le profil.")
    def parse_terms(values: object, label: str) -> tuple[str, ...]:
        if (not isinstance(values, list) or len(values) > 200
                or any(not isinstance(x, str) or len(x) > 200 or not normalize(x) for x in values)):
            raise ValueError(f"{label} : liste de termes non vides attendue.")
        return tuple(dict.fromkeys(normalize(x) for x in values))
    roles = data.get("roles")
    if not isinstance(roles, dict) or not 1 <= len(roles) <= 30:
        raise ValueError("Définir de 1 à 30 familles de métiers dans [roles].")
    parsed = {name: parse_terms(terms, name) for name, terms in roles.items()}
    if any(not name.strip() or not terms for name, terms in parsed.items()):
        raise ValueError("Famille de métiers vide.")
    score = data.get("min_score", 60)
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError("min_score doit être un entier de 0 à 100.")
    values = {key: parse_terms(data.get(key, []), key) for key in allowed - {"roles", "min_score"}}
    return Profile(roles=parsed, min_score=score, **values)


def obj(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def contract_info(row: dict, provenance: dict) -> tuple[str, str]:
    """Ne pas assimiler un stage, un CDD ou une mention dans la description à une alternance."""
    raw = obj(provenance.get("raw"))
    source = row["source"]
    fields = []
    if source == "lba":
        values = obj(provenance.get("contract")).get("type")
        if isinstance(values, list):
            fields.extend(x for x in values if isinstance(x, str))
    elif source in ("lever", "lever_eu"):
        fields.append(obj(raw.get("categories")).get("commitment"))
    elif source == "ashby":
        fields.append(raw.get("employmentType"))
    elif source == "smartrecruiters":
        fields.append(obj(raw.get("typeOfEmployment")).get("label"))
    # Les titres constituent une preuve textuelle ; la description n'en est pas une.
    title = row.get("title") or ""
    structured = " ".join(x for x in fields if isinstance(x, str))
    alt = ("alternance", "alternant", "alternante", "apprentissage", "apprenti",
           "apprentie", "apprentice", "apprenticeship", "work study",
           "contrat de professionnalisation", "professionnalisation")
    other = ("stage", "stagiaire", "intern", "internship", "cdi", "cdd",
             "permanent", "freelance", "full time", "part time", "temporary", "contractor")
    title_alt, title_other = terms_in(title, alt), terms_in(title, other)
    struct_alt, struct_other = terms_in(structured, alt), terms_in(structured, other)
    # Full time / part time sont parfois des horaires, pas une preuve d'incompatibilité
    # lorsqu'une alternance figure explicitement dans le titre.
    hard_other = tuple(x for x in (*title_other, *struct_other) if x not in ("full time", "part time"))
    if title_alt or struct_alt:
        if hard_other:
            return "ambiguous", "Indices alternance et autre contrat contradictoires ou mixtes."
        return "alternance", "Alternance explicite dans " + ("les métadonnées." if struct_alt else "le titre.")
    if title_other or struct_other:
        return "other", "Autre contrat indiqué dans le titre ou les métadonnées."
    return "unknown", "Contrat non confirmé ; description seule insuffisante."


@dataclass(frozen=True)
class Match:
    id: str
    source: str
    company: str
    title: str | None
    location: str | None
    application_url: str | None
    source_url: str | None
    state: str
    score: int
    contract: str
    roles: tuple[str, ...]
    skills: tuple[str, ...]
    components: dict[str, int]
    reasons: tuple[str, ...]
    duplicate_ids: tuple[str, ...] = ()
    version: int = VERSION


def score_job(row: dict, profile: Profile) -> Match:
    provenance = obj(json.loads(row["provenance_json"]))
    title = row.get("title") or ""
    desc = row.get("description") or ""
    location = row.get("location_raw")
    role_title = tuple(name for name, terms in profile.roles.items() if terms_in(title, terms))
    role_desc = tuple(name for name, terms in profile.roles.items() if terms_in(desc, terms))
    roles = tuple(dict.fromkeys((*role_title, *role_desc)))
    skills = terms_in(title + "\n" + desc, profile.skills)
    contract, evidence = contract_info(row, provenance)
    pref_loc = terms_in(location, profile.preferred_locations)
    other_loc = terms_in(location, profile.other_locations)
    excluded_loc = terms_in(location, profile.excluded_locations)
    preferred = normalize(row["company"]) in profile.preferred_companies
    reasons = [evidence]
    components = {
        "role": 30 if role_title else 12 if role_desc else 0,
        "contract": 25 if contract == "alternance" else 0,
        "skills": min(25, 5 * len(skills)),
        "location": 15 if pref_loc else 8 if other_loc else 0,
        "company": 5 if preferred else 0,
    }
    senior = terms_in(title, ("senior", "principal", "staff engineer", "director",
                              "directeur", "directrice", "head of", "vp", "vice president"))
    if role_title:
        reasons.append("Métier repéré dans le titre : " + ", ".join(role_title))
    elif role_desc:
        reasons.append("Métier seulement dans la description ; à confirmer.")
    else:
        reasons.append("Aucun métier cible reconnu.")
    reasons.append("Compétences repérées : " + (", ".join(skills) if skills else "aucune"))
    if pref_loc or other_loc:
        reasons.append("Zone reconnue : " + ", ".join((*pref_loc, *other_loc)))
    else:
        reasons.append("Localisation hors liste ou inconnue ; à vérifier.")
    if preferred:
        reasons.append("Entreprise explicitement préférée.")
    if not desc:
        reasons.append("Description absente ; enrichissement ou vérification manuelle nécessaire.")
    score = sum(components.values())
    state = "match"
    if excluded_loc and not (pref_loc or other_loc):
        state = "excluded"
        reasons.append("Zone explicitement exclue.")
    elif contract == "other" or (not roles and bool(desc)):
        state = "excluded"
    elif senior:
        state = "review" if contract == "alternance" else "excluded"
        reasons.append("Intitulé senior ou de direction : vérifier le niveau.")
    elif (contract != "alternance" or not role_title or not (pref_loc or other_loc)
          or excluded_loc or score < profile.min_score or not desc):
        state = "review"
    if excluded_loc and (pref_loc or other_loc):
        reasons.append("Localisation multiple ou contradictoire ; à confirmer.")
    return Match(row["id"], row["source"], row["company"], row.get("title"), location,
                 row.get("application_url"), provenance.get("source_url"), state, score,
                 contract, roles, skills, components, tuple(reasons))


def _url_key(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, parsed.query, ""))


def rank_jobs(store: JobStore, profile: Profile, *, deduplicate: bool = True) -> list[Match]:
    rows = store.connection.execute("SELECT * FROM jobs ORDER BY id")
    matches = [score_job(dict(row), profile) for row in rows]
    # Un lien réellement identique avec entreprise, titre et localisation identiques
    # peut être regroupé. Aucun effacement en base ni rapprochement flou.
    if deduplicate:
        from dataclasses import replace
        groups = {}
        for match in matches:
            url = _url_key(match.application_url or match.source_url)
            if url and match.title and match.location:
                key = (url, normalize(match.company), normalize(match.title), normalize(match.location))
            else:
                key = (match.id,)
            groups.setdefault(key, []).append(match)
        matches = []
        for group in groups.values():
            group.sort(key=lambda m: (STATES.index(m.state), -m.score, m.id))
            best = group[0]
            matches.append(replace(best, duplicate_ids=tuple(m.id for m in group[1:])))
    return sorted(matches, key=lambda m: (STATES.index(m.state), -m.score, m.id))


def match_dict(match: Match) -> dict:
    return asdict(match)
