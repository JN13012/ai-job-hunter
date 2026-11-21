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


def load_catalog(path: Path) -> tuple[Target, ...]:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    entries = raw.get("targets")
    if raw.keys() != {"targets"} or not isinstance(entries, list) or not 1 <= len(entries) <= 500:
        raise ValueError("Catalogue : 1 à 500 entrées [[targets]] attendues.")
    targets = []
    identities = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.keys() - {"company", "career_url", "source", "board_token", "siren"}:
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
        targets.append(Target(company.strip(), url, source, token, siren))
    return tuple(targets)


def catalog_settings(targets: tuple[Target, ...], source: str, settings: GreenhouseConfig) -> GreenhouseConfig:
    """Sélection explicite du catalogue, sans modifier ajh.toml."""
    if source not in BOARD_SOURCES:
        raise ValueError("Le catalogue ne déclenche que les collecteurs ATS.")
    boards = tuple(GreenhouseBoard(t.company, t.board_token) for t in targets if t.source == source)
    if not 1 <= len(boards) <= 10:
        raise ValueError("Choisir entre 1 et 10 boards de cette source par collecte.")
    return replace(settings, boards=boards)
