"""Chaîne M1 : acquisition, normalisation, dédoublonnage et persistance."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
import sqlite3
import time

import httpx

from ai_job_hunter.collectors.greenhouse import CollectorError, fetch_board
from ai_job_hunter.config import GreenhouseConfig
from ai_job_hunter.normalizer import normalize_greenhouse
from ai_job_hunter.storage import JobStore


@dataclass
class CollectionSummary:
    received: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def collect_greenhouse(
    settings: GreenhouseConfig, store: JobStore, client: httpx.Client,
) -> CollectionSummary:
    summary = CollectionSummary()
    for index, board in enumerate(settings.boards):
        if index:
            time.sleep(settings.request_interval_seconds)
        try:
            response = fetch_board(client, board)
        except CollectorError as exc:
            summary.errors.append(f"{board.board_token} : {exc}")
            if exc.rate_limited:
                break  # Aucun autre appel ni retry après une limitation serveur.
            continue
        observed_at = datetime.now(UTC)
        summary.received += len(response.jobs)
        summary.errors.extend(f"{board.board_token} : {error}" for error in response.errors)
        unique = {}
        for index_in_response, raw in enumerate(response.jobs):
            try:
                job = normalize_greenhouse(raw, board, response.endpoint, observed_at)
                if job.id in unique:
                    summary.duplicates += 1
                    if unique[job.id].content_hash != job.content_hash:
                        summary.errors.append(f"{board.board_token}/{job.external_id} : doublon contradictoire, première occurrence conservée.")
                else:
                    unique[job.id] = job
            except ValueError as exc:
                summary.errors.append(f"{board.board_token}[{index_in_response}] : {exc}")
        try:
            counts = store.upsert_jobs(unique.values())
        except (sqlite3.Error, ValueError) as exc:
            summary.errors.append(f"{board.board_token} : transaction annulée : {exc}")
            continue
        summary.new += counts.new
        summary.updated += counts.updated
        summary.unchanged += counts.unchanged
    return summary
