from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

from ai_job_hunter.storage import JobStore


def test_insert_then_identical_observation_preserves_first_seen(tmp_path, job):
    database = tmp_path / "data" / "jobs.sqlite3"
    later = job.last_seen + timedelta(days=1)
    with JobStore(database) as store:
        assert store.upsert_jobs([job]).new == 1
        counts = store.upsert_jobs([replace(job, first_seen=later, last_seen=later)])
        assert (counts.new, counts.updated, counts.unchanged) == (0, 0, 1)
        row, = store.list_jobs()
        assert row["first_seen"] == job.first_seen.isoformat()
        assert row["last_seen"] == later.isoformat()
        assert json.loads(row["provenance_json"]) == job.provenance
    with JobStore(database) as store:
        assert store.stats()[0]["count"] == 1
        assert store.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_update_preserves_identity_and_first_seen(tmp_path, job):
    later = job.last_seen + timedelta(hours=1)
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        store.upsert_jobs([job])
        counts = store.upsert_jobs([replace(job, title="Nouveau titre", first_seen=later, last_seen=later)])
        assert (counts.new, counts.updated, counts.unchanged) == (0, 1, 0)
        row, = store.list_jobs()
        assert row["title"] == "Nouveau titre"
        assert row["id"] == job.id
        assert row["first_seen"] == job.first_seen.isoformat()
        assert row["last_seen"] == later.isoformat()


def test_batch_rolls_back_updates_and_inserts_on_constraint_error(tmp_path, job):
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        store.upsert_jobs([job])
        new = replace(job, id="new", external_id="202")
        collision = replace(job, external_id="203")  # Même clé primaire, autre ID externe.
        with pytest.raises(sqlite3.IntegrityError):
            store.upsert_jobs([replace(job, title="Must roll back"), new, collision])
        row, = store.list_jobs()
        assert row["title"] == job.title
        assert store.upsert_jobs([new]).new == 1  # Connexion utilisable après rollback.


def test_missing_from_later_batch_is_never_deleted(tmp_path, job):
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        store.upsert_jobs([job])
        store.upsert_jobs([])
        assert len(store.list_jobs()) == 1


def test_uniqueness_by_source_board_external_id(tmp_path, job):
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        store.upsert_jobs([job, replace(job, id="different-local-id")])
        assert len(store.list_jobs()) == 1
        assert store.list_jobs()[0]["id"] == job.id


def test_last_seen_never_goes_backwards(tmp_path, job):
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        store.upsert_jobs([job])
        earlier = job.first_seen - timedelta(days=1)
        store.upsert_jobs([replace(job, first_seen=earlier, last_seen=earlier)])
        row, = store.list_jobs()
        assert row["first_seen"] == earlier.isoformat()
        assert row["last_seen"] == job.last_seen.isoformat()


def test_unknown_schema_is_not_modified(tmp_path):
    database = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(sqlite3.DatabaseError, match="non supportée"):
        JobStore(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
        assert connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0


def test_corrupt_database_is_not_overwritten(tmp_path):
    database = tmp_path / "jobs.sqlite3"
    original = b"This is not a SQLite database"
    database.write_bytes(original)
    with pytest.raises(sqlite3.DatabaseError):
        JobStore(database)
    assert database.read_bytes() == original
