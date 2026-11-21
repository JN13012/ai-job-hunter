"""M5.1 : tests hors réseau ; aucune soumission ni réidentification."""

import json
from datetime import UTC, datetime

import pytest

from ai_job_hunter.cli import main
from ai_job_hunter.company_discovery import Company, upsert_companies
from ai_job_hunter.contacts import (
    change_contact, contact_stats, import_contacts, list_contacts,
)
from ai_job_hunter.storage import JobStore

CONTACT = {
    "siren": "123456789", "channel": "email",
    "target": "Recrutement@Example.org", "role": "recruitment",
    "source_url": "https://example.org/carrieres", "source_type": "company_careers",
}


@pytest.fixture
def store(tmp_path):
    with JobStore(tmp_path / "jobs.sqlite3") as db:
        upsert_companies(db, [
            Company("123456789", "Entreprise Exemple", "62.01Z", "PME",
                    "Marseille", "13001", "fixture")
        ], datetime.now(UTC))
        yield db


def write_json(tmp_path, payload):
    file = tmp_path / "contacts.json"
    file.write_text(json.dumps(payload), encoding="utf-8")
    return file


def test_import_idempotent_and_approval_reset(store, tmp_path):
    path = write_json(tmp_path, [CONTACT])
    assert import_contacts(path, store).new == 1
    assert import_contacts(path, store).unchanged == 1
    data = list_contacts(store)[0]
    assert data["target"] == "recrutement@example.org"
    assert data["status"] == "pending"
    assert data["company"] == "Entreprise Exemple"
    change_contact(store, data["id"], "approve")
    assert contact_stats(store)["statuses"]["approved"] == 1
    assert import_contacts(path, store).unchanged == 1
    assert contact_stats(store)["statuses"]["approved"] == 1
    write_json(tmp_path, [{**CONTACT, "source_url": "https://example.org/recrutement"}])
    assert import_contacts(path, store).updated == 1
    assert list_contacts(store)[0]["status"] == "pending"


def test_block_prevents_restore(store, tmp_path):
    path = write_json(tmp_path, [{**CONTACT, "person_name": "Test Employee"}])
    import_contacts(path, store)
    identifier = list_contacts(store)[0]["id"]
    change_contact(store, identifier, "block")
    item = list_contacts(store)[0]
    assert item["target"] is item["source_url"] is item["person_name"] is None
    assert item["status"] == "blocked"
    assert import_contacts(path, store).unchanged == 1
    assert list_contacts(store)[0]["target"] is None
    with pytest.raises(ValueError, match="bloqué"):
        change_contact(store, identifier, "approve")


def test_unknown_company_rolls_back(store, tmp_path):
    path = write_json(tmp_path, [CONTACT, {**CONTACT, "siren": "987654321"}])
    with pytest.raises(ValueError, match="non présents"):
        import_contacts(path, store)
    assert contact_stats(store)["total"] == 0


@pytest.mark.parametrize("changes", [
    {"source_url": "http://example.org/contact"},
    {"source_url": "https://example.org/contact?secret=token"},
    {"target": "person@gmail.com"},
    {"target": "missing-at-sign"},
    {"role": "marketing"},
    {"channel": "phone"},
    {"siren": "invalid"},
    {"person_name": "\nsecret"},
    {"extra": "unexpected"},
])
def test_invalid_rows_are_atomic(store, tmp_path, changes):
    path = write_json(tmp_path, [CONTACT, {**CONTACT, **changes}])
    with pytest.raises(ValueError):
        import_contacts(path, store)
    assert contact_stats(store)["total"] == 0


def test_conflicting_duplicates_are_rejected(store, tmp_path):
    path = write_json(tmp_path, [CONTACT, {**CONTACT, "role": "hr"}])
    with pytest.raises(ValueError, match="doublon contradictoire"):
        import_contacts(path, store)


def test_cli_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with JobStore(tmp_path / ".ajh/jobs.sqlite3") as db:
        upsert_companies(db, [Company("123456789", "Exemple", None, None, "Marseille", None, "fixture")], datetime.now(UTC))
    path = write_json(tmp_path, [CONTACT])
    assert main(["contacts", "import", "--file", str(path)]) == 0
    assert "Nouvelles : 1" in capsys.readouterr().out
    assert main(["contacts", "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 1
    assert main(["contacts", "list"]) == 0
    identifier = json.loads(capsys.readouterr().out)["id"]
    assert main(["contacts", "approve", "--id", identifier]) == 0
    assert main(["contacts", "block", "--id", identifier]) == 0
    assert main(["contacts", "approve", "--id", identifier]) == 1


def test_empty_and_oversized_files(store, tmp_path):
    path = write_json(tmp_path, [])
    with pytest.raises(ValueError):
        import_contacts(path, store)
    path.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="1 Mio"):
        import_contacts(path, store)
