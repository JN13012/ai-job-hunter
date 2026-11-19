"""Import local d'annonces sélectionnées ou exportées avec autorisation."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ai_job_hunter.catalog import public_url
from ai_job_hunter.models import Job
from ai_job_hunter.storage import JobStore, WriteCounts

SOURCES = ("linkedin", "indeed", "wttj", "freework", "career", "other")
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 1000


def _text(value: object, field: str, *, required: bool = False, limit: int = 200000) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Champ {field} absent ou invalide.")
    return value.strip()


def normalize_import(raw: object, seen: datetime) -> Job:
    if not isinstance(raw, dict) or raw.keys() - {
        "source", "url", "company", "title", "location", "description", "application_url"
    }:
        raise ValueError("Objet d'import invalide ou champ inconnu.")
    origin = raw.get("source")
    if origin not in SOURCES:
        raise ValueError("Source d'import inconnue.")
    url = public_url(raw.get("url"))
    parsed = urlsplit(url)
    # Retirer uniquement le fragment ; conserver la requête qui peut identifier l'offre.
    stable_url = urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, parsed.query, ""))
    company = _text(raw.get("company"), "company", required=True, limit=300)
    title = _text(raw.get("title"), "title", required=True, limit=500)
    application = raw.get("application_url")
    if application is not None:
        application = public_url(application)
    external = hashlib.sha256(stable_url.encode()).hexdigest()
    source = f"import_{origin}"
    identity = json.dumps([source, "manual", external])
    return Job(
        id=hashlib.sha256(identity.encode()).hexdigest(), external_id=external,
        source=source, board_token="manual", company=company, title=title,
        location_raw=_text(raw.get("location"), "location", limit=1000),
        description=_text(raw.get("description"), "description"),
        application_url=application, updated_at=None,
        first_seen=seen, last_seen=seen,
        provenance={"source_url": stable_url, "method": "manual_authorized_import",
                    "normalizer_version": 1, "availability": "unverified"},
    )


@dataclass
class ImportResult:
    received: int = 0
    duplicates: int = 0
    counts: WriteCounts = field(default_factory=WriteCounts)
    errors: list[str] = field(default_factory=list)


def import_jobs(path: Path, store: JobStore) -> ImportResult:
    def reject_constant(value: str) -> None:
        raise ValueError("Constante JSON non valide.")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Fichier d'import supérieur à 5 Mio.")
    payload = json.loads(data, parse_constant=reject_constant)
    if not isinstance(payload, list) or len(payload) > MAX_ROWS:
        raise ValueError("Tableau JSON de 1000 annonces maximum attendu.")
    result = ImportResult(received=len(payload))
    jobs = {}
    seen = datetime.now(UTC)
    for index, raw in enumerate(payload):
        try:
            job = normalize_import(raw, seen)
            if job.id in jobs:
                result.duplicates += 1
                if jobs[job.id].content_hash != job.content_hash:
                    result.errors.append(f"Entrée {index + 1} : doublon contradictoire.")
            else:
                jobs[job.id] = job
        except ValueError as exc:
            result.errors.append(f"Entrée {index + 1} : {exc}")
    result.counts = store.upsert_jobs(jobs.values())
    return result
