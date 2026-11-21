"""Catalogue local explicite : aucun crawl ni découverte de tokens supposés."""

from dataclasses import dataclass, replace
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit

from ai_job_hunter.config import BOARD_SOURCES, GreenhouseBoard, GreenhouseConfig

HOSTS = {
    "greenhouse": {"job-boards.greenhouse.io", "boards.greenhouse.io"},
    "lever": {"jobs.lever.co"},
    "lever_eu": {"jobs.eu.lever.co"},
    "ashby": {"jobs.ashbyhq.com"},
    "smartrecruiters": {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"},
}


def public_url(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("URL HTTPS publique attendue.")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)):
        raise ValueError("URL HTTPS publique sans identifiants attendue.")
    return value


@dataclass(frozen=True)
class Target:
    company: str
    career_url: str
    source: str
    board_token: str | None = None
    siren: str | None = None
    application_form_url: str | None = None
    allow_contact_scan: bool = False
    permission_note: str | None = None


def load_catalog(path: Path) -> tuple[Target, ...]:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    entries = raw.get("targets")
    if raw.keys() != {"targets"} or not isinstance(entries, list) or not 1 <= len(entries) <= 500:
        raise ValueError("Catalogue : 1 à 500 entrées [[targets]] attendues.")
    targets = []
    identities = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.keys() - {"company", "career_url", "source", "board_token", "siren", "application_form_url", "allow_contact_scan", "permission_note"}:
            raise ValueError("Entrée de catalogue invalide.")
        company, source = entry.get("company"), entry.get("source")
        if not isinstance(company, str) or not company.strip():
            raise ValueError("Nom d'entreprise obligatoire.")
        if source not in (*BOARD_SOURCES, "manual"):
            raise ValueError("Source du catalogue inconnue.")
        url = public_url(entry.get("career_url"))
        token = entry.get("board_token")
        siren = entry.get("siren")
        if siren is not None and (not isinstance(siren, str) or not re.fullmatch(r"\d{9}", siren)):
            raise ValueError("SIREN de catalogue invalide.")
        allow_scan = entry.get("allow_contact_scan", False)
        permission_note = entry.get("permission_note")
        if type(allow_scan) is not bool:
            raise ValueError("allow_contact_scan doit être un booléen TOML.")
        if permission_note is not None and (
            not isinstance(permission_note, str) or not 12 <= len(permission_note.strip()) <= 400
            or any(ord(ch) < 32 for ch in permission_note)
        ):
            raise ValueError("permission_note doit décrire l'autorisation sans données sensibles.")
        if allow_scan and (source != "manual" or not siren or not permission_note):
            raise ValueError("Analyse réseau réservée aux pages manuelles avec SIREN et justification d'autorisation.")
        form_url = entry.get("application_form_url")
        if form_url is not None:
            # A declaration is not a verification. The form stays pending in M5.
            form_url = public_url(form_url)
            career_parsed = urlsplit(url)
            form_parsed = urlsplit(form_url)
            if career_parsed.query or career_parsed.fragment:
                raise ValueError("La provenance du formulaire ne doit pas contenir de paramètre.")
            if form_parsed.query or form_parsed.fragment:
                raise ValueError("URL de formulaire avec requête ou fragment interdite.")
            if form_parsed.hostname != career_parsed.hostname:
                raise ValueError("Formulaire sur un autre hôte : importer manuellement avec preuve dédiée.")
        if source == "manual":
            if token is not None:
                raise ValueError("Une cible manuelle ne peut pas avoir de board_token.")
            identity = (source, url)
        else:
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", token):
                raise ValueError("Token ATS invalide.")
            parsed = urlsplit(url)
            first_segment = parsed.path.strip("/").split("/")[0]
            if parsed.hostname not in HOSTS[source] or first_segment != token:
                raise ValueError("Le token doit correspondre au lien du board officiel.")
            identity = (source, token.casefold())
        if identity in identities:
            raise ValueError("Cible dupliquée dans le catalogue.")
        identities.add(identity)
        targets.append(Target(company.strip(), url, source, token, siren, form_url, allow_scan, permission_note.strip() if permission_note else None))
    return tuple(targets)


def catalog_settings(targets: tuple[Target, ...], source: str, settings: GreenhouseConfig) -> GreenhouseConfig:
    """Sélection explicite du catalogue, sans modifier ajh.toml."""
    if source not in BOARD_SOURCES:
        raise ValueError("Le catalogue ne déclenche que les collecteurs ATS.")
    boards = tuple(GreenhouseBoard(t.company, t.board_token) for t in targets if t.source == source)
    if not 1 <= len(boards) <= 10:
        raise ValueError("Choisir entre 1 et 10 boards de cette source par collecte.")
    return replace(settings, boards=boards)
