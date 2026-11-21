"""M6 : profil candidat factuel déclaré explicitement, conservé en local."""

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
ANSWER_KEY = re.compile(r"[a-z][a-z0-9_]{1,59}\Z")


def _text(value: object, field: str, limit: int = 1000, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(ch) < 32 for ch in value)):
        raise ValueError(f"{field} : chaîne non vide et sans caractères de contrôle attendue.")
    return value.strip()


def _section(raw: dict, key: str, allowed: set[str] | None) -> dict:
    item = raw.get(key, {})
    if not isinstance(item, dict) or (allowed is not None and item.keys() - allowed):
        raise ValueError(f"[{key}] : section invalide ou option inconnue.")
    return item


def _list(raw: dict, key: str) -> tuple[str, ...]:
    items = raw.get(key, [])
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError(f"facts.{key} : 100 entrées au maximum.")
    values = [_text(v, f"facts.{key}", 500, required=True) for v in items]
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class Candidate:
    full_name: str
    email: str
    city: str | None
    phone: str | None
    headline: str | None
    portfolio_url: str | None
    skills: tuple[str, ...]
    learning: tuple[str, ...]
    experiences: tuple[str, ...]
    education: tuple[str, ...]
    certifications: tuple[str, ...]
    projects: tuple[str, ...]
    languages: tuple[str, ...]
    availability: str | None
    alternance_rhythm: str | None
    motivation: str | None
    answers: dict[str, str]


def load_candidate(path: Path) -> Candidate:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    if not isinstance(raw, dict) or raw.keys() - {"identity", "facts", "application", "answers"}:
        raise ValueError("Sections autorisées : identity, facts, application, answers.")
    identity = _section(raw, "identity", {
        "full_name", "email", "city", "phone", "headline", "portfolio_url",
    })
    facts = _section(raw, "facts", {
        "skills", "learning", "experiences", "education",
        "certifications", "projects", "languages",
    })
    application = _section(raw, "application", {
        "availability", "alternance_rhythm", "motivation",
    })
    answers = _section(raw, "answers", None)
    fullname = _text(identity.get("full_name"), "identity.full_name", 150, required=True)
    email = _text(identity.get("email"), "identity.email", 254, required=True)
    if not EMAIL.fullmatch(email):
        raise ValueError("identity.email invalide.")
    portfolio = _text(identity.get("portfolio_url"), "identity.portfolio_url", 2048)
    if portfolio is not None:
        parts = urlsplit(portfolio)
        if (parts.scheme != "https" or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or parts.query or parts.fragment):
            raise ValueError("Lien portfolio HTTPS public sans identifiants requis.")
    if len(answers) > 100:
        raise ValueError("Au maximum 100 réponses personnelles.")
    parsed_answers = {}
    for key, value in answers.items():
        if not ANSWER_KEY.fullmatch(key):
            raise ValueError(f"Clé de réponse non autorisée : {key}")
        parsed_answers[key] = _text(value, f"answers.{key}", 1500, required=True)
    return Candidate(
        fullname, email, _text(identity.get("city"), "identity.city", 120),
        _text(identity.get("phone"), "identity.phone", 40),
        _text(identity.get("headline"), "identity.headline", 200),
        portfolio,
        _list(facts, "skills"), _list(facts, "learning"),
        _list(facts, "experiences"), _list(facts, "education"),
        _list(facts, "certifications"), _list(facts, "projects"),
        _list(facts, "languages"),
        _text(application.get("availability"), "application.availability", 200),
        _text(application.get("alternance_rhythm"), "application.alternance_rhythm", 200),
        _text(application.get("motivation"), "application.motivation", 600),
        parsed_answers,
    )
