"""Persistance distincte des offres publiées et des entreprises à potentiel."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from ai_job_hunter.collectors.common import CollectorError
from ai_job_hunter.collectors.lba import ENDPOINT, fetch_opportunities
from ai_job_hunter.config import LBAConfig
from ai_job_hunter.models import Job
from ai_job_hunter.normalizer import strip_description_html
from ai_job_hunter.storage import JobStore, WriteCounts

import httpx


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _obj(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _url(value: Any) -> str | None:
    value = _text(value)
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL non valide.")
    return value


def _workplace(raw: dict) -> dict:
    work = _obj(raw.get("workplace"))
    name = _text(work.get("name")) or _text(work.get("legal_name")) or _text(work.get("brand"))
    location = _obj(work.get("location"))
    return {"company": name, "siret": _text(work.get("siret")), "website": _url(work.get("website")),
            "location": _text(location.get("address")), "size": _text(work.get("size")),
            "domain": _obj(work.get("domain"))}


def normalize_job(raw: Any, seen: datetime) -> Job:
    if not isinstance(raw, dict):
        raise ValueError("Offre non valide.")
    identifier = _obj(raw.get("identifier"))
    partner = _text(identifier.get("partner_label"))
    external = _text(identifier.get("partner_job_id")) or _text(identifier.get("id"))
    if not partner or not external:
        raise ValueError("Identité de l'offre absente.")
    offer = _obj(raw.get("offer"))
    if offer.get("status") not in (None, "Active"):
        raise ValueError("Statut non actif.")
    title = _text(offer.get("title"))
    if not title:
        raise ValueError("Titre absent.")
    work = _workplace(raw)
    desc = _text(offer.get("description"))
    url = _url(_obj(raw.get("apply")).get("url"))
    identity = json.dumps(["lba", partner, external])
    # Ne pas conserver les numéros de téléphone ou identifiants de destinataire.
    provenance = {"endpoint": ENDPOINT, "normalizer_version": 1,
                  "identifier": identifier,
                  "workplace": work, "contract": _obj(raw.get("contract")),
                  "offer": {k: offer[k] for k in ("rome_codes", "publication", "target_diploma",
                            "desired_skills", "to_be_acquired_skills", "access_conditions",
                            "status", "opening_count") if k in offer},
                  "is_delegated": raw.get("is_delegated")}
    return Job(id=hashlib.sha256(identity.encode()).hexdigest(), source="lba",
               board_token=partner, external_id=external,
               company=work["company"] or "Non renseignée", title=title,
               location_raw=work["location"], description=strip_description_html(desc) if desc else None,
               application_url=url, updated_at=None, first_seen=seen, last_seen=seen,
               provenance=provenance)


def normalize_recruiter(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Entreprise non valide.")
    external = _text(_obj(raw.get("identifier")).get("id"))
    if not external:
        raise ValueError("Identité entreprise absente.")
    work = _workplace(raw)
    return {"external_id": external, **work,
            "application_url": _url(_obj(raw.get("apply")).get("url")),
            "kind": "potential_recruiter", "source": "lba", "endpoint": ENDPOINT}


def _ensure_recruiters(store: JobStore) -> None:
    # Extension additive du schéma existant : aucune modification de jobs.
    with store.connection:
        store.connection.execute("""
            CREATE TABLE IF NOT EXISTS lba_recruiters (
                external_id TEXT PRIMARY KEY NOT NULL,
                company TEXT, siret TEXT, location TEXT, website TEXT,
                application_url TEXT, payload_json TEXT NOT NULL,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
            )
        """)


def save_recruiters(store: JobStore, records: dict, seen: datetime) -> WriteCounts:
    _ensure_recruiters(store)
    counts = WriteCounts()
    timestamp = seen.astimezone(UTC).isoformat()
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        for external, record in records.items():
            payload = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False)
            previous = store.connection.execute(
                "SELECT payload_json FROM lba_recruiters WHERE external_id=?", (external,)
            ).fetchone()
            if previous is None:
                counts.new += 1
            elif previous["payload_json"] == payload:
                counts.unchanged += 1
            else:
                counts.updated += 1
            store.connection.execute("""
                INSERT INTO lba_recruiters
                (external_id,company,siret,location,website,application_url,payload_json,first_seen,last_seen)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(external_id) DO UPDATE SET company=excluded.company,
                siret=excluded.siret, location=excluded.location, website=excluded.website,
                application_url=excluded.application_url, payload_json=excluded.payload_json,
                first_seen=MIN(lba_recruiters.first_seen,excluded.first_seen),
                last_seen=MAX(lba_recruiters.last_seen,excluded.last_seen)
            """, (external, record["company"], record["siret"], record["location"],
                  record["website"], record["application_url"], payload, timestamp, timestamp))
    return counts


def list_recruiters(store: JobStore, limit: int) -> list:
    _ensure_recruiters(store)
    return store.connection.execute(
        "SELECT * FROM lba_recruiters ORDER BY last_seen DESC, external_id LIMIT ?", (limit,)
    ).fetchall()


def count_recruiters(store: JobStore) -> int:
    _ensure_recruiters(store)
    return store.connection.execute("SELECT COUNT(*) FROM lba_recruiters").fetchone()[0]


@dataclass
class LBAResult:
    jobs: WriteCounts = field(default_factory=WriteCounts)
    recruiters: WriteCounts = field(default_factory=WriteCounts)
    received_jobs: int = 0
    received_recruiters: int = 0
    duplicates: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def collect_lba(settings: LBAConfig, store: JobStore, client: httpx.Client, key: str) -> LBAResult:
    result = LBAResult()
    try:
        payload = fetch_opportunities(client, settings, key)
    except CollectorError as exc:
        result.errors.append(str(exc))
        return result
    seen = datetime.now(UTC)
    result.received_jobs = len(payload["jobs"])
    result.received_recruiters = len(payload["recruiters"])
    # Les résultats sont plafonnés côté serveur même sans avertissement explicite.
    result.warnings.append("Recherche non exhaustive : résultats plafonnés par l'API.")
    if payload.get("warnings"):
        result.warnings.append("La source a retourné des avertissements de recherche.")
    jobs, recruiters = {}, {}
    for index, raw in enumerate(payload["jobs"]):
        try:
            job = normalize_job(raw, seen)
            if job.id in jobs:
                result.duplicates += 1
                if jobs[job.id].content_hash != job.content_hash:
                    result.errors.append(f"jobs[{index}] : doublon contradictoire.")
            else:
                jobs[job.id] = job
        except ValueError as exc:
            result.errors.append(f"jobs[{index}] : {exc}")
    for index, raw in enumerate(payload["recruiters"]):
        try:
            record = normalize_recruiter(raw)
            external = record["external_id"]
            if external in recruiters:
                result.duplicates += 1
                if recruiters[external] != record:
                    result.errors.append(f"recruiters[{index}] : doublon contradictoire.")
            else:
                recruiters[external] = record
        except ValueError as exc:
            result.errors.append(f"recruiters[{index}] : {exc}")
    try:
        result.jobs = store.upsert_jobs(jobs.values())
    except (sqlite3.Error, ValueError) as exc:
        result.errors.append(f"Transaction offres annulée : {exc}")
    try:
        result.recruiters = save_recruiters(store, recruiters, seen)
    except (sqlite3.Error, ValueError) as exc:
        result.errors.append(f"Transaction entreprises annulée : {exc}")
    return result
