"""M7.5 — deterministic offline handoff and receipt tracking tests.

These tests do not submit applications and do not access the network.
"""

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path

import pytest

from ai_job_hunter.application_queue import ApplicationQueue
from ai_job_hunter.browser_preview import AllowedPath, BrowserPolicy
from ai_job_hunter.models import Job
from ai_job_hunter.storage import JobStore
from ai_job_hunter.submission_tracking import SubmissionTracker


URL = "https://jobs.example.org/apply/42?job=123"
NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)


@pytest.fixture
def setup(tmp_path):
    data_dir = tmp_path / ".ajh"
    folder = data_dir / "drafts" / ("a" * 64 + "-example")
    folder.mkdir(parents=True)
    with JobStore(data_dir / "jobs.sqlite3") as db:
        db.upsert_jobs([Job(
            id="a" * 64, external_id="unit", source="lever",
            board_token="example", company="Example",
            title="Alternance AI Engineer", location_raw="Marseille",
            description="Python", application_url=URL,
            updated_at=None, first_seen=NOW, last_seen=NOW, provenance={},
        )])
        manifest = {
            "job_id": "a" * 64,
            "status": "draft_requires_human_review",
            "review_required": True,
            "ready_for_submission": False,
            "automation_authorized": False,
            "human_final_submit_required": True,
            "channel": {
                "kind": "offer_link", "target": URL,
                "verification": "link_from_job_source",
            },
        }
        (folder / "manifest.json").write_text(json.dumps(manifest))
        (folder / "cv.md").write_text("# Candidate\n")
        (folder / "letter.md").write_text("Application letter\n")
        (folder / "answers.json").write_text("{}")
        queue = ApplicationQueue(db, data_dir)
        app = queue.enqueue(folder, now=NOW)
        policy = BrowserPolicy((AllowedPath(
            "jobs.example.org", "/apply", "Permission expresse de test pour ce domaine",
        ),))
        yield db, queue, SubmissionTracker(queue), app["id"], policy, folder


def test_handoff_does_not_submit_and_prevents_claim(setup):
    db, queue, tracker, app_id, policy, folder = setup
    baseline = db.connection.total_changes
    handoff = tracker.handoff(app_id, policy, now=NOW)
    assert handoff["status"] == "awaiting_manual_action"
    assert handoff["manual_url"] == URL
    assert handoff["manual_filling_required"] is True
    assert handoff["platform_confirmation_automatically_verified"] is False
    assert tracker.handoff(app_id, policy, now=NOW)["status"] == "awaiting_manual_action"
    assert queue.claim("worker", now=NOW, browser_only=True) is None
    with pytest.raises(ValueError, match="Transfert humain"):
        queue.cancel(app_id, now=NOW)
    with pytest.raises(ValueError, match="Transfert humain"):
        queue.resume(app_id, now=NOW)
    assert queue._get(app_id)["status"] == "queued"
    assert db.connection.total_changes > baseline


def test_clicked_not_equal_confirmed(setup):
    _, queue, tracker, app_id, policy, _ = setup
    tracker.handoff(app_id, policy, now=NOW)
    click = tracker.clicked(app_id, now=NOW)
    assert click["status"] == "clicked_unconfirmed"
    assert click["confirmed_at"] is None
    assert tracker.clicked(app_id, now=NOW)["status"] == "clicked_unconfirmed"
    assert queue.claim("worker", now=NOW) is None
    assert tracker.stats()["states"]["submitted_confirmed"] == 0
    with pytest.raises(ValueError, match="hors état"):
        tracker.abandon(app_id, now=NOW)


def test_confirmation_requires_source_and_review(setup, tmp_path):
    _, queue, tracker, app_id, policy, _ = setup
    evidence = tmp_path / "confirmation.txt"
    evidence.write_text("Your application has been submitted. Confirmation ID REQ-234.")
    with pytest.raises(ValueError, match="clic humain"):
        tracker.confirm(app_id, evidence=evidence, evidence_source="confirmation_page",
                        reference="REQ-234", reviewed=True, now=NOW)
    tracker.handoff(app_id, policy, now=NOW)
    tracker.clicked(app_id, now=NOW)
    with pytest.raises(ValueError, match="revue"):
        tracker.confirm(app_id, evidence=evidence, evidence_source="confirmation_page",
                        reference="REQ-234", reviewed=False, now=NOW)
    result = tracker.confirm(app_id, evidence=evidence,
                             evidence_source="confirmation_page",
                             reference="REQ-234", reviewed=True, now=NOW)
    assert result["status"] == "submitted_confirmed"
    assert result["platform_confirmation_automatically_verified"] is False
    assert result["evidence_sha256"] == sha256(evidence.read_bytes()).hexdigest()
    assert result["confirmed_at"] is not None
    assert tracker.stats()["total"] == 1
    assert queue.claim("worker", now=NOW) is None
    assert len(tracker.events(app_id)) == 3
    assert tracker.get(app_id)["status"] == "submitted_confirmed"
    assert tracker.confirm(app_id, evidence=evidence, evidence_source="confirmation_page",
                           reference="REQ-234", reviewed=True, now=NOW)["status"] == "submitted_confirmed"
    evidence.write_text("Different receipt")
    with pytest.raises(ValueError, match="autre preuve"):
        tracker.confirm(app_id, evidence=evidence, evidence_source="confirmation_page",
                        reference="REQ-234", reviewed=True, now=NOW)


def test_proof_validation(setup, tmp_path):
    _, _, tracker, app_id, policy, _ = setup
    tracker.handoff(app_id, policy)
    tracker.clicked(app_id)
    image = tmp_path / "proof.png"
    image.write_bytes(b"not a png")
    with pytest.raises(ValueError, match="PNG"):
        tracker.confirm(app_id, evidence=image, evidence_source="confirmation_page",
                        reference="REQ-456", reviewed=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"abc")
    result = tracker.confirm(app_id, evidence=image, evidence_source="confirmation_page",
                             reference="REQ-456", reviewed=True)
    assert result["status"] == "submitted_confirmed"


def test_handoff_denied_for_wrong_site(setup):
    _, queue, tracker, app_id, _, _ = setup
    policy = BrowserPolicy((AllowedPath("jobs.other.org", "/apply", "Permission du site différent"),))
    with pytest.raises(ValueError, match="politique"):
        tracker.handoff(app_id, policy)
    assert tracker.stats()["total"] == 0
    assert queue.claim("worker") is not None


def test_abandon_does_not_requeue(setup):
    _, queue, tracker, app_id, policy, _ = setup
    tracker.handoff(app_id, policy)
    abandoned = tracker.abandon(app_id)
    assert abandoned["status"] == "abandoned"
    assert queue._get(app_id)["status"] == "cancelled"
    assert tracker.abandon(app_id)["status"] == "abandoned"
    assert queue.claim("browser") is None
    with pytest.raises(ValueError, match="Transfert humain"):
        queue.resume(app_id)


def test_handoff_rejects_tampered_document(setup):
    _, _, tracker, app_id, policy, folder = setup
    (folder / "cv.md").write_text("Modified after queueing")
    with pytest.raises(ValueError, match="modifié"):
        tracker.handoff(app_id, policy)
    assert tracker.stats()["total"] == 0


def test_tracking_table_not_required_for_old_queue(tmp_path):
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        queue = ApplicationQueue(store, tmp_path)
        assert queue.claim("worker") is None
        assert queue.stats()["total"] == 0
