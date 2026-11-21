"""M5.4 : classement déterministe des canaux de candidature, en lecture seule.

Les canaux enregistrés ne sont jamais une autorisation de soumission.
Aucun email, formulaire ou requête réseau n'est envoyé par ce module.
"""

import ipaddress
import json
import re
from collections import defaultdict
from urllib.parse import urlsplit, urlunsplit

from ai_job_hunter.catalog import Target
from ai_job_hunter.matcher import Match, Profile, rank_jobs
from ai_job_hunter.storage import JobStore

SIREN_PATTERN = re.compile(r"\d{9}\Z")
SIRET_PATTERN = re.compile(r"\d{14}\Z")
HOST_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
BAD_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan",
                     ".home", ".invalid", ".test", ".onion", ".arpa")
CHANNEL_PRIORITY = {"offer_link": 100, "company_form": 80, "company_email": 60}


def _safe_application_url(raw: object) -> str | None:
    """Conserver les query strings légitimes des ATS, refuser les destinations locales.

    Une URL validée syntaxiquement demeure NON vérifiée par la plateforme.
    """
    if (not isinstance(raw, str) or not raw or len(raw) > 4096
            or any(ch.isspace() or ord(ch) < 32 for ch in raw)):
        return None
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        port = parsed.port
        if (parsed.scheme != "https" or parsed.username is not None
                or parsed.password is not None or port not in (None, 443)
                or not host or not HOST_PATTERN.fullmatch(host)
                or "." not in host or host.endswith(BAD_HOST_SUFFIXES)):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None
        return urlunsplit(("https", parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
    except ValueError:
        return None


def _company_index(store: JobStore) -> dict[str, str]:
    exists = store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_targets'"
    ).fetchone()
    if not exists:
        return {}
    return {row["siren"]: row["company"] for row in store.connection.execute(
        "SELECT siren,company FROM company_targets"
    )}


def _contacts(store: JobStore) -> tuple[dict[str, list[dict]], dict[str, int]]:
    exists = store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recruitment_contacts'"
    ).fetchone()
    approved: dict[str, list[dict]] = defaultdict(list)
    pending: dict[str, int] = defaultdict(int)
    if not exists:
        return approved, pending
    for record in store.connection.execute(
        "SELECT id,siren,channel,target,role,source_url,source_type,status "
        "FROM recruitment_contacts WHERE status IN ('approved','pending')"
    ):
        if record["status"] == "pending":
            pending[record["siren"]] += 1
            continue
        if record["channel"] not in ("email", "form") or not record["target"]:
            continue
        if record["channel"] == "form":
            target = _safe_application_url(record["target"])
            if target is None:
                continue
            kind = "company_form"
        else:
            target = record["target"]
            kind = "company_email"
        approved[record["siren"]].append({
            "kind": kind, "target": target, "contact_id": record["id"],
            "source_url": record["source_url"], "role": record["role"],
            "priority": CHANNEL_PRIORITY[kind], "verification": "contact_approved",
            "reason": "Canal professionnel approuvé dans M5, sans autorisation présumée d'automatisation.",
        })
    for siren in approved:
        approved[siren].sort(key=lambda item: (-item["priority"], item["target"], item["contact_id"]))
    return approved, pending


def _catalog_links(targets: tuple[Target, ...], known: dict[str, str]) -> dict[tuple[str, str], str | None]:
    """Un board ne relie une offre à une entreprise que si le SIREN est univoque."""
    declared: dict[tuple[str, str], set[str]] = defaultdict(set)
    for target in targets:
        if target.siren in known and target.board_token and target.source != "manual":
            declared[(target.source, target.board_token.casefold())].add(target.siren)
    return {key: next(iter(sirens)) if len(sirens) == 1 else None
            for key, sirens in declared.items()}


def _explicit_siren(
    match: Match, record: dict, catalog_map: dict[tuple[str, str], str | None],
    known: dict[str, str],
) -> tuple[str | None, str]:
    if match.source == "lba":
        try:
            provenance = json.loads(record["provenance_json"])
        except (TypeError, ValueError):
            return None, "missing_verified_siren"
        workplace = provenance.get("workplace") if isinstance(provenance, dict) else None
        siret = workplace.get("siret") if isinstance(workplace, dict) else None
        if isinstance(siret, str) and SIRET_PATTERN.fullmatch(siret) and siret[:9] in known:
            return siret[:9], "lba_explicit_siret"
        return None, "missing_verified_siren"
    key = (record["source"], record["board_token"].casefold())
    if key in catalog_map:
        value = catalog_map[key]
        return (value, "catalog_explicit_board_siren") if value else (None, "conflicting_catalog_siren")
    return None, "missing_verified_siren"


def _selected(channels: list[dict]) -> dict | None:
    if not channels:
        return None
    return sorted(channels, key=lambda c: (-c["priority"], c["target"],
                                          c.get("contact_id") or ""))[0]


def _state(selected: dict | None, pending_count: int) -> str:
    if selected is None:
        return "needs_contact_review" if pending_count else "no_channel"
    if selected["kind"] == "offer_link":
        return "source_link_unverified"
    return "approved_contact"


def route_jobs(
    store: JobStore, profile: Profile, *,
    catalog: tuple[Target, ...] = (), include_review: bool = False,
) -> list[dict]:
    """Retour machine-readable pour M6/M7, sans créer de dossier ni envoyer."""
    known = _company_index(store)
    catalog_map = _catalog_links(catalog, known)
    approved, pending = _contacts(store)
    matches = rank_jobs(store, profile)
    routes = []
    for match in matches:
        if match.state == "excluded" or (match.state != "match" and not include_review):
            continue
        record = store.connection.execute(
            "SELECT source,board_token,provenance_json FROM jobs WHERE id=?", (match.id,),
        ).fetchone()
        if record is None:
            continue
        siren, evidence = _explicit_siren(match, dict(record), catalog_map, known)
        choices = [dict(item) for item in approved.get(siren, ())] if siren else []
        direct = _safe_application_url(match.application_url)
        if direct:
            choices.append({
                "kind": "offer_link", "target": direct, "contact_id": None,
                "source_url": None, "role": "job_application",
                "priority": CHANNEL_PRIORITY["offer_link"],
                "verification": "link_from_job_source",
                "reason": "Lien de candidature de cette offre, disponibilité et politique du site non vérifiées.",
            })
        choices.sort(key=lambda c: (-c["priority"], c["target"], c.get("contact_id") or ""))
        selected = _selected(choices)
        reviews = pending.get(siren, 0) if siren else 0
        routes.append({
            "job_id": match.id, "related_job_ids": list(match.duplicate_ids),
            "company": match.company, "title": match.title, "source": match.source,
            "match_score": match.score, "match_state": match.state,
            "siren": siren, "company_link_evidence": evidence,
            "routing_status": _state(selected, reviews), "selected": selected,
            "alternatives": [item for item in choices if item is not selected],
            "pending_contacts": reviews,
            "ready_for_preparation": bool(selected) and match.state == "match",
            "automation_authorized": False, "human_final_submit_required": True,
            "notes": [
                "Aucun contrôle du formulaire ni des conditions d'utilisation du site effectué.",
                "Le dernier clic Envoyer reste humain ; aucune soumission automatique.",
            ],
        })
    return routes


def route_companies(store: JobStore) -> list[dict]:
    """Candidatures spontanées : signal de canal, pas preuve de recrutement."""
    known = _company_index(store)
    approved, pending = _contacts(store)
    routes = []
    for siren, company in sorted(known.items(), key=lambda pair: (pair[1].casefold(), pair[0])):
        choices = [dict(item) for item in approved.get(siren, ())]
        selected = _selected(choices)
        routes.append({
            "siren": siren, "company": company, "application_type": "spontaneous",
            "routing_status": _state(selected, pending.get(siren, 0)),
            "selected": selected,
            "alternatives": [item for item in choices if item is not selected],
            "pending_contacts": pending.get(siren, 0),
            "channel_available": selected is not None,
            "recruitment_confirmed": False, "automation_authorized": False,
            "human_final_submit_required": True,
            "notes": ["Existence d'un canal ≠ recrutement en cours.",
                      "La préparation ne constitue pas une soumission."],
        })
    return sorted(routes, key=lambda r: (not r["channel_available"],
                                        r["company"].casefold(), r["siren"]))
