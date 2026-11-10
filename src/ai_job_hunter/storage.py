"""Persistance locale SQLite ; un lot de board est une transaction atomique."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC
import json
from pathlib import Path
import sqlite3

from ai_job_hunter.models import Job


@dataclass
class WriteCounts:
    new: int = 0
    updated: int = 0
    unchanged: int = 0


class JobStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        try:
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                version = self.connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise sqlite3.DatabaseError(f"Version de schéma non supportée : {version}")
                self.connection.execute("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY NOT NULL,
                        source TEXT NOT NULL,
                        board_token TEXT NOT NULL,
                        external_id TEXT NOT NULL,
                        company TEXT NOT NULL,
                        title TEXT,
                        location_raw TEXT,
                        description TEXT,
                        application_url TEXT,
                        updated_at TEXT,
                        first_seen TEXT NOT NULL,
                        last_seen TEXT NOT NULL,
                        provenance_json TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        UNIQUE(source, board_token, external_id)
                    )
                """)
                self.connection.execute("PRAGMA user_version = 1")
        except Exception:
            self.connection.close()
            raise

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.connection.close()

    def upsert_jobs(self, jobs: Iterable[Job]) -> WriteCounts:
        counts = WriteCounts()
        with self.connection:
            # Le verrou précède les lectures pour éviter un bilan concurrent erroné.
            self.connection.execute("BEGIN IMMEDIATE")
            for job in jobs:
                previous = self.connection.execute(
                    "SELECT id, content_hash FROM jobs WHERE source=? AND board_token=? AND external_id=?",
                    (job.source, job.board_token, job.external_id),
                ).fetchone()
                digest = job.content_hash
                first = job.first_seen.astimezone(UTC).isoformat()
                last = job.last_seen.astimezone(UTC).isoformat()
                provenance = json.dumps(job.provenance, ensure_ascii=False, sort_keys=True, allow_nan=False)
                values = (
                    job.company, job.title, job.location_raw, job.description,
                    job.application_url, job.updated_at, first, last, provenance, digest,
                )
                if previous is None:
                    self.connection.execute(
                        """INSERT INTO jobs (
                            id, source, board_token, external_id, company, title,
                            location_raw, description, application_url, updated_at,
                            first_seen, last_seen, provenance_json, content_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (job.id, job.source, job.board_token, job.external_id, *values),
                    )
                    counts.new += 1
                else:
                    changed = previous["content_hash"] != digest
                    # Un passage identique avance last_seen mais ne compte pas comme mise à jour.
                    self.connection.execute(
                        """UPDATE jobs SET company=?, title=?, location_raw=?, description=?,
                            application_url=?, updated_at=?, first_seen=MIN(first_seen, ?),
                            last_seen=MAX(last_seen, ?), provenance_json=?, content_hash=? WHERE id=?""",
                        (*values, previous["id"]),
                    )
                    if changed:
                        counts.updated += 1
                    else:
                        counts.unchanged += 1
        return counts

    def list_jobs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM jobs ORDER BY last_seen DESC, id LIMIT ?", (limit,),
        ).fetchall()

    def stats(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT source, board_token, company, COUNT(*) AS count FROM jobs
               GROUP BY source, board_token, company ORDER BY source, board_token, company""",
        ).fetchall()
