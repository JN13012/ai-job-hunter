"""M7.1 : ordre SQLite, barrières, leases et reprise sans envoi, hors réseau."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import json

import pytest

from ai_job_hunter.application_queue import ApplicationQueue
from ai_job_hunter.application_drafts import prepare_job
from ai_job_hunter.candidate import load_candidate
from ai_job_hunter.matcher import Profile
from ai_job_hunter.models import Job
from ai_job_hunter.storage import JobStore
from ai_job_hunter.cli import main
from ai_job_hunter.contacts import import_contacts, change_contact, list_contacts
from ai_job_hunter.company_discovery import Company, upsert_companies

AT = datetime(2026, 11, 21, 12, tzinfo=UTC)
PROFILE = Profile({"ai": ("ai engineer",)}, ("python",), ("marseille",), (), (), (), 60)
CANDIDATE = """
[identity]
full_name = "Example Candidate"
email = "candidate@example.org"
[facts]
skills = ["Python"]
education = ["Études en cours"]
"""


@pytest.fixture
def setup(tmp_path):
    dbpath = tmp_path / "jobs.sqlite3"
    with JobStore(dbpath) as store:
        now = datetime.now(UTC)
        def make_job(identifier):
            return Job(
                id=identifier, external_id=identifier, source="lever",
                board_token="unit", company="Company", title="Alternance AI Engineer",
                location_raw="Marseille", description="Python",
                application_url="https://jobs.example.org/apply/42",
                updated_at=None, first_seen=now, last_seen=now, provenance={},
            )
        store.upsert_jobs([make_job("a" * 64)])
        profile_file = tmp_path / "candidate.toml"
        profile_file.write_text(CANDIDATE, encoding="utf-8")
        candidate = load_candidate(profile_file)
        prepared = prepare_job(
            store, PROFILE, candidate, "a" * 64, output_dir=tmp_path / "drafts",
        )
        queue = ApplicationQueue(store, tmp_path)
        yield store, queue, Path(prepared["directory"])


def test_enqueue_repeat_claim_release(setup):
    _, queue, draft = setup
    a = queue.enqueue(draft, now=AT)
    b = queue.enqueue(draft, now=AT)
    assert a["id"] == b["id"]
    assert queue.stats()["total"] == 1
    lease = queue.claim("worker-1", now=AT)
    assert lease and lease["status"] == "reserved"
    assert queue.claim("worker-2", now=AT) is None
    with pytest.raises(ValueError, match="Réservation"):
        queue.release(a["id"], "0" * 48, now=AT)
    row = queue.release(a["id"], lease["reservation_token"], outcome="queued", now=AT)
    assert row["status"] == "queued"
    assert len(queue.events(a["id"])) == 3


def test_cancel_reserved_requires_worker_ack(setup):
    _, queue, draft = setup
    app = queue.enqueue(draft, now=AT)
    lease = queue.claim("test", now=AT)
    result = queue.cancel(app["id"], now=AT)
    assert result["status"] == "reserved"
    assert result["cancel_requested"]
    with pytest.raises(ValueError, match="Annulation"):
        queue.renew(app["id"], lease["reservation_token"], now=AT)
    result = queue.release(app["id"], lease["reservation_token"], now=AT)
    assert result["status"] == "cancelled"
    assert queue.claim("test", now=AT) is None


def test_expiry_recovery_and_explicit_resume(setup):
    _, queue, draft = setup
    app = queue.enqueue(draft, now=AT)
    lease = queue.claim("test", seconds=30, now=AT)
    with pytest.raises(ValueError, match="expirée"):
        queue.recover(app["id"], now=AT + timedelta(seconds=10))
    assert queue.claim("another", now=AT + timedelta(seconds=40)) is None
    recovered = queue.recover(app["id"], now=AT + timedelta(seconds=40))
    assert recovered["status"] == "interrupted"
    with pytest.raises(ValueError, match="Réservation"):
        queue.release(app["id"], lease["reservation_token"], now=AT + timedelta(seconds=40))
    assert queue.resume(app["id"], now=AT + timedelta(seconds=50))["status"] == "queued"
    second = queue.claim("another", now=AT + timedelta(seconds=50))
    assert second and second["reservation_token"] != lease["reservation_token"]


def test_changed_draft_blocks_claim(setup):
    _, queue, draft = setup
    app = queue.enqueue(draft)
    (draft / "cv.md").write_text("changed")
    assert queue.claim("test") is None
    assert queue.list()[0]["status"] == "blocked"
    with pytest.raises(ValueError):
        queue.resume(app["id"])


def test_symlink_file_rejected(setup):
    _, queue, draft = setup
    original = draft / "cv.md"
    original.unlink()
    original.symlink_to(draft / "letter.md")
    with pytest.raises(ValueError, match="symbolique"):
        queue.enqueue(draft)


def test_missing_draft_rejected(setup, tmp_path):
    _, queue, draft = setup
    with pytest.raises(ValueError):
        queue.enqueue(tmp_path / "unrelated")
    with pytest.raises(ValueError):
        queue.enqueue(draft / "manifest.json")


def test_cancel_queued_is_idempotent(setup):
    _, queue, draft = setup
    app = queue.enqueue(draft)
    assert queue.cancel(app["id"])["status"] == "cancelled"
    assert queue.cancel(app["id"])["status"] == "cancelled"
    assert queue.stats()["states"]["cancelled"] == 1
    assert queue.enqueue(draft)["status"] == "cancelled"


def test_alias_conflict_atomic(setup):
    store, queue, draft = setup
    now = datetime.now(UTC)
    store.upsert_jobs([Job(
        id="b" * 64, external_id="b", source="ashby",
        board_token="other", company="Company", title="Alternance AI Engineer",
        location_raw="Marseille", description="Python",
        application_url="https://jobs.example.org/apply/43",
        updated_at=None, first_seen=now, last_seen=now, provenance={},
    )])
    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["related_job_ids"] = ["b" * 64]
    manifest_path.write_text(json.dumps(manifest))
    queue.enqueue(draft)
    # Attempt to associate the same related job to a second application.
    # This must fail atomically; alias uniqueness is global.
    from ai_job_hunter.application_drafts import compose_draft
    manifest["job_id"] = "b" * 64
    manifest["channel"]["target"] = "https://jobs.example.org/apply/43"
    another = "b" * 64 + "-another"
    folder = draft.parent / another
    folder.mkdir()
    for name in ("cv.md", "letter.md", "answers.json", "manifest.json"):
        (folder / name).write_bytes((draft / name).read_bytes())
    (folder / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="déjà inscrit"):
        queue.enqueue(folder)
    assert queue.stats()["total"] == 1


def test_contact_revocation_blocks_worker(setup, tmp_path):
    store, queue, draft = setup
    upsert_companies(store, [
        Company("123456789", "Company", None, None, None, None, "fixture")
    ], AT)
    contact_file = tmp_path / "contacts.json"
    contact_file.write_text(json.dumps([{
        "siren": "123456789", "channel": "form",
        "target": "https://careers.example.org/apply",
        "role": "careers", "source_url": "https://careers.example.org/careers",
        "source_type": "company_careers",
    }]))
    import_contacts(contact_file, store)
    entry = list_contacts(store)[0]
    change_contact(store, entry["id"], "approve")
    manifest_path = draft / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["channel"] = {
        "kind": "company_form", "contact_id": entry["id"],
        "target": entry["target"], "verification": "contact_approved",
    }
    manifest_path.write_text(json.dumps(manifest))
    app = queue.enqueue(draft)
    change_contact(store, entry["id"], "block")
    assert queue.claim("test") is None
    assert queue.list()[0]["status"] == "blocked"
    with pytest.raises(ValueError):
        queue.resume(app["id"])


def test_cli_queue_list_stats(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["queue", "list"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["queue", "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0
    assert main(["queue", "add", "--draft", str(tmp_path / "notfound")]) == 1
