"""M4 : découverte légale de sociétés françaises, sans coordonnées personnelles.

API officielle gratuite Recherche d'Entreprises, recherches explicites bornées.
Chaque SIREN est une cible légale, pas une offre ni une preuve de recrutement.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import tomllib
from typing import Any
from urllib.parse import urlencode

import httpx

from ai_job_hunter.catalog import Target
from ai_job_hunter.collectors.common import CollectorError, fetch_json
from ai_job_hunter.storage import JobStore, WriteCounts

API_URL = "https://recherche-entreprises.api.gouv.fr/search"
SIREN = re.compile(r"\d{9}\Z")
SIRET = re.compile(r"\d{14}\Z")
NAF = re.compile(r"\d{2}\.\d{2}[A-Z]\Z")
DEPARTMENT = re.compile(r"(?:0[1-9]|[1-8]\d|9[0-5]|2A|2B|97[1-6])\Z")


@dataclass(frozen=True)
class Search:
    q: str
    departement: str | None = None
    code_naf: str | None = None
    max_pages: int = 2


@dataclass(frozen=True)
class DiscoveryConfig:
    searches: tuple[Search, ...]
    per_page: int = 20
    interval_seconds: float = 0.5
    timeout_seconds: float = 20.0


def load_searches(path: Path) -> DiscoveryConfig:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if not isinstance(raw, dict) or raw.keys() - {"discovery", "searches"}:
        raise ValueError("Sections autorisées : [discovery] et [[searches]].")
    base = raw.get("discovery", {})
    if not isinstance(base, dict) or base.keys() - {"per_page", "interval_seconds", "timeout_seconds"}:
        raise ValueError("Options de [discovery] invalides.")
    per_page = base.get("per_page", 20)
    if type(per_page) is not int or not 1 <= per_page <= 25:
        raise ValueError("discovery.per_page doit être entier de 1 à 25.")
    def bounded_float(key: str, default: float, low: float, high: float) -> float:
        value = base.get(key, default)
        if (type(value) not in (float, int) or not math.isfinite(value)
                or not low <= value <= high):
            raise ValueError(f"discovery.{key} doit être entre {low} et {high}.")
        return float(value)
    interval = bounded_float("interval_seconds", 0.5, 0.3, 60)
    timeout = bounded_float("timeout_seconds", 20, 1, 60)
    searches = raw.get("searches")
    if not isinstance(searches, list) or not 1 <= len(searches) <= 20:
        raise ValueError("Définir entre 1 et 20 recherches explicites.")
    parsed = []
    seen = set()
    for entry in searches:
        if not isinstance(entry, dict) or entry.keys() - {"q", "departement", "code_naf", "max_pages"}:
            raise ValueError("Entrée [[searches]] invalide.")
        q = entry.get("q")
        if (not isinstance(q, str) or not 2 <= len(q.strip()) <= 80
                or any(ord(ch) < 32 for ch in q)
                or re.fullmatch(r"\d{9}|\d{14}", q.strip())):
            raise ValueError("q doit être un terme textuel, pas un SIREN/SIRET.")
        department = entry.get("departement")
        if department is not None and (not isinstance(department, str) or not DEPARTMENT.fullmatch(department)):
            raise ValueError("departement invalide.")
        naf = entry.get("code_naf")
        if naf is not None and (not isinstance(naf, str) or not NAF.fullmatch(naf)):
            raise ValueError("code_naf invalide.")
        max_pages = entry.get("max_pages", 2)
        if type(max_pages) is not int or not 1 <= max_pages <= 5:
            raise ValueError("max_pages doit être entier de 1 à 5.")
        search = Search(q.strip(), department, naf, max_pages)
        if search in seen:
            raise ValueError("Recherche dupliquée.")
        seen.add(search)
        parsed.append(search)
    return DiscoveryConfig(tuple(parsed), per_page, interval, timeout)


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class Company:
    siren: str
    company: str
    naf: str | None
    category: str | None
    city: str | None
    postal_code: str | None
    discovered_from: str

    @property
    def content_hash(self) -> str:
        payload = (self.company, self.naf, self.category, self.city, self.postal_code)
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def normalize_company(raw: Any, search: Search) -> Company:
    if not isinstance(raw, dict):
        raise ValueError("Résultat entreprise non objet.")
    siren = raw.get("siren")
    if not isinstance(siren, str) or not SIREN.fullmatch(siren):
        raise ValueError("SIREN absent ou invalide.")
    name = _optional_string(raw.get("nom_complet")) or _optional_string(raw.get("nom_raison_sociale"))
    if name is None or len(name) > 500:
        raise ValueError("Dénomination légale absente ou invalide.")
    siege = raw.get("siege")
    if not isinstance(siege, dict):
        siege = {}
    def opt(field: Any, cap: int = 200) -> str | None:
        value = _optional_string(field)
        return value if value is None or len(value) <= cap else None
    return Company(siren, name, opt(raw.get("activite_principale"), 20),
                   opt(raw.get("categorie_entreprise"), 20),
                   opt(siege.get("libelle_commune")), opt(siege.get("code_postal"), 5),
                   search.q)


def ensure_schema(store: JobStore) -> None:
    # Table additionnelle : schéma jobs et historique existants préservés.
    with store.connection:
        store.connection.execute("""
            CREATE TABLE IF NOT EXISTS company_targets (
                siren TEXT PRIMARY KEY NOT NULL,
                company TEXT NOT NULL,
                naf TEXT,
                category TEXT,
                city TEXT,
                postal_code TEXT,
                source TEXT NOT NULL,
                discovered_from TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                content_hash TEXT NOT NULL
            )
        """)


def upsert_companies(store: JobStore, companies: list[Company], observed: datetime) -> WriteCounts:
    ensure_schema(store)
    stamp = observed.astimezone(UTC).isoformat()
    counts = WriteCounts()
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        for company in companies:
            previous = store.connection.execute(
                "SELECT content_hash FROM company_targets WHERE siren=?", (company.siren,)
            ).fetchone()
            if previous is None:
                counts.new += 1
            elif previous["content_hash"] == company.content_hash:
                counts.unchanged += 1
            else:
                counts.updated += 1
            store.connection.execute("""
                INSERT INTO company_targets
                (siren,company,naf,category,city,postal_code,source,discovered_from,
                 first_seen,last_seen,content_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(siren) DO UPDATE SET
                company=excluded.company, naf=excluded.naf, category=excluded.category,
                city=excluded.city, postal_code=excluded.postal_code,
                last_seen=MAX(company_targets.last_seen, excluded.last_seen),
                content_hash=excluded.content_hash
            """, (company.siren, company.company, company.naf, company.category,
                  company.city, company.postal_code, "annuaire",
                  company.discovered_from, stamp, stamp, company.content_hash))
    return counts


@dataclass
class DiscoveryResult:
    requests: int = 0
    received: int = 0
    duplicates: int = 0
    counts: WriteCounts = field(default_factory=WriteCounts)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def discover(config: DiscoveryConfig, store: JobStore, client: httpx.Client) -> DiscoveryResult:
    result = DiscoveryResult()
    seen_global: set[str] = set()
    for search in config.searches:
        unique: dict[str, Company] = {}
        complete = True
        total_pages = 0
        for page in range(1, search.max_pages + 1):
            if result.requests:
                time.sleep(config.interval_seconds)
            params = {
                "q": search.q, "page": page, "per_page": config.per_page,
                "etat_administratif": "A", "est_entrepreneur_individuel": "false",
                "minimal": "true", "include": "siege",
            }
            if search.departement:
                params["departement"] = search.departement
            if search.code_naf:
                params["activite_principale"] = search.code_naf
            result.requests += 1
            try:
                payload = fetch_json(client, API_URL + "?" + urlencode(params))
                if not isinstance(payload, dict):
                    raise CollectorError("Réponse non objet.")
                hits = payload.get("results")
                pages = payload.get("total_pages")
                if (not isinstance(hits, list) or len(hits) > config.per_page
                        or type(pages) is not int or pages < 0):
                    raise CollectorError("Réponse invalide : results/total_pages.")
                total_pages = pages
                result.received += len(hits)
                for index, raw in enumerate(hits):
                    try:
                        item = normalize_company(raw, search)
                    except ValueError as exc:
                        result.errors.append(f"{search.q}, page {page}, ligne {index}: {exc}")
                        continue
                    if item.siren in seen_global or item.siren in unique:
                        result.duplicates += 1
                        if item.siren in unique and unique[item.siren].content_hash != item.content_hash:
                            result.errors.append(f"{search.q}: SIREN contradictoire dans le même lot.")
                        continue
                    unique[item.siren] = item
                if page >= pages or not hits:
                    if not hits and page < pages:
                        result.warnings.append(f"{search.q}: page vide avant total_pages.")
                    break
            except CollectorError as exc:
                result.errors.append(f"{search.q}, page {page}: {exc}")
                complete = False
                if exc.rate_limited:
                    # Les données des pages déjà reçues restent utilisables, mais
                    # aucune autre requête n'est envoyée.
                    result.warnings.append("Limite API atteinte : collecte interrompue, sans retry.")
                    if unique:
                        try:
                            count = upsert_companies(store, list(unique.values()), datetime.now(UTC))
                            for key in ("new", "updated", "unchanged"):
                                setattr(result.counts, key, getattr(result.counts, key) + getattr(count, key))
                        except sqlite3.Error as db_exc:
                            result.errors.append(f"{search.q}: persistance échouée: {db_exc}")
                    return result
                break
        if total_pages > search.max_pages:
            result.warnings.append(f"{search.q}: recherche partielle (max_pages={search.max_pages}, total_pages={total_pages}).")
        if not complete:
            result.warnings.append(f"{search.q}: erreur sur une page, résultats incomplets.")
        if unique:
            try:
                count = upsert_companies(store, list(unique.values()), datetime.now(UTC))
            except sqlite3.Error as exc:
                result.errors.append(f"{search.q}: persistance échouée: {exc}")
                continue
            seen_global.update(unique)
            for key in ("new", "updated", "unchanged"):
                setattr(result.counts, key, getattr(result.counts, key) + getattr(count, key))
    return result


def _lba_counts(store: JobStore) -> dict[str, int]:
    exists = store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lba_recruiters'"
    ).fetchone()
    if not exists:
        return {}
    counts: dict[str, int] = {}
    for (siret,) in store.connection.execute("SELECT siret FROM lba_recruiters"):
        if isinstance(siret, str) and SIRET.fullmatch(siret):
            counts[siret[:9]] = counts.get(siret[:9], 0) + 1
    return counts


def company_rows(store: JobStore, *, catalog: tuple[Target, ...] = ()) -> list[dict]:
    ensure_schema(store)
    tracked: dict[str, set[str]] = {}
    for target in catalog:
        if target.siren is not None:
            tracked.setdefault(target.siren, set()).add(target.source)
    lba = _lba_counts(store)
    output = []
    for row in store.connection.execute("SELECT * FROM company_targets ORDER BY company, siren"):
        item = dict(row)
        item["lba_potential"] = bool(lba.get(item["siren"]))
        item["lba_locations"] = lba.get(item["siren"], 0)
        item["catalog_sources"] = sorted(tracked.get(item["siren"], ()))
        item["signals"] = (["lba_potential_recruiter"] if item["lba_potential"] else []) + (
            ["catalog_siren_declared"] if item["catalog_sources"] else [])
        output.append(item)
    # Classement des signaux observés seulement, sans prédire les recrutements.
    return sorted(output, key=lambda x: (-len(x["signals"]), x["company"], x["siren"]))


def company_stats(store: JobStore, *, catalog: tuple[Target, ...] = ()) -> dict:
    rows = company_rows(store, catalog=catalog)
    return {"total": len(rows),
            "lba_potential": sum(row["lba_potential"] for row in rows),
            "catalog_linked": sum(bool(row["catalog_sources"]) for row in rows)}
