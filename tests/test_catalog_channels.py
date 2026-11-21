"""M5.2 : typage strict du catalogue, provenance et persistance revue."""

import json
from datetime import UTC, datetime

import pytest

from ai_job_hunter.catalog import load_catalog
from ai_job_hunter.catalog_channels import qualify_catalog, register_catalog_forms
from ai_job_hunter.cli import main
from ai_job_hunter.company_discovery import Company, upsert_companies
from ai_job_hunter.contacts import change_contact, contact_stats, list_contacts
from ai_job_hunter.storage import JobStore

CATALOG = """
[[targets]]
company = "Entreprise Exemple"
source = "manual"
career_url = "https://example.org/carrieres"
application_form_url = "https://example.org/candidature"
siren = "123456789"

[[targets]]
company = "Entreprise sans formulaire"
source = "manual"
career_url = "https://other.example.org/jobs"
siren = "123456789"

[[targets]]
company = "Société sans SIREN"
source = "lever"
board_token = "fixture"
career_url = "https://jobs.lever.co/fixture"
"""


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.toml"
    path.write_text(CATALOG, encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path):
    with JobStore(tmp_path / "jobs.sqlite3") as db:
        upsert_companies(
            db, [Company("123456789", "Entreprise Exemple", "62.01Z", "PME",
                         "Marseille", "13001", "fixture")], datetime.now(UTC),
        )
        yield db


def test_classify_and_register_idempotently(store, catalog):
    entries = qualify_catalog(store, load_catalog(catalog))
    assert [entry["status"] for entry in entries] == [
        "ready_for_review", "form_not_declared", "missing_siren",
    ]
    assert entries[1]["channel_kind"] == "careers_page"
    assert entries[2]["channel_kind"] == "ats_board"
    assert all(not entry["verified"] for entry in entries)
    assert contact_stats(store)["total"] == 0
    assert register_catalog_forms(store, entries).new == 1
    assert register_catalog_forms(store, entries).unchanged == 1
    contact = list_contacts(store)[0]
    assert contact["status"] == "pending"
    assert contact["source_url"] == "https://example.org/carrieres"
    assert contact["target"] == "https://example.org/candidature"


def test_block_stays_blocked(store, catalog):
    entries = qualify_catalog(store, load_catalog(catalog))
    register_catalog_forms(store, entries)
    identifier = list_contacts(store)[0]["id"]
    change_contact(store, identifier, "block")
    assert register_catalog_forms(store, entries).unchanged == 1
    assert list_contacts(store)[0]["target"] is None


@pytest.mark.parametrize("replacement", [
    'application_form_url = "https://phishing.example/candidature"',
    'application_form_url = "https://example.org/candidature?token=private"',
    'application_form_url = "http://example.org/candidature"',
    'application_form_url = "https://user:pass@example.org/candidature"',
])
def test_untrusted_form_rejected(tmp_path, replacement):
    path = tmp_path / "invalid.toml"
    path.write_text(CATALOG.replace(
        'application_form_url = "https://example.org/candidature"', replacement,
    ))
    with pytest.raises(ValueError):
        load_catalog(path)


def test_does_not_promote_unlinked_companies(tmp_path, catalog):
    with JobStore(tmp_path / "db.sqlite3") as db:
        items = qualify_catalog(db, load_catalog(catalog))
        assert items[0]["status"] == "company_not_in_m4"
        assert register_catalog_forms(db, items).new == 0
        assert contact_stats(db)["total"] == 0


def test_cli_qualify_only_and_register(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    local_catalog = tmp_path / "catalog.toml"
    local_catalog.write_text(CATALOG)
    with JobStore(tmp_path / ".ajh" / "jobs.sqlite3") as db:
        upsert_companies(
            db, [Company("123456789", "Entreprise Exemple", None, None, None, None, "fixture")],
            datetime.now(UTC),
        )
    assert main(["contacts", "catalog", "--file", str(local_catalog)]) == 0
    data = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert data[0]["status"] == "ready_for_review"
    assert main(["contacts", "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0
    assert main(["contacts", "catalog", "--file", str(local_catalog), "--register"]) == 0
    capsys.readouterr()
    assert main(["contacts", "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["statuses"]["pending"] == 1


def test_existing_catalog_compatibility(tmp_path):
    path = tmp_path / "catalog.toml"
    path.write_text('''
[[targets]]
company = "Cloudflare"
source = "greenhouse"
board_token = "cloudflare"
career_url = "https://job-boards.greenhouse.io/cloudflare"
''')
    target = load_catalog(path)[0]
    assert target.application_form_url is None
    with JobStore(tmp_path / "db.sqlite") as db:
        rows = qualify_catalog(db, (target,))
    assert rows[0]["status"] == "missing_siren"


def test_jobs_links_are_not_company_form(store, catalog):
    from ai_job_hunter.models import Job
    now = datetime.now(UTC)
    with JobStore(store.connection.execute("PRAGMA database_list").fetchone()[2]) as _:
        pass
    job = Job(
        id="job-1", external_id="job-1", source="lever", board_token="fixture",
        company="Société sans SIREN", title="Alternance", location_raw="Marseille",
        description=None, application_url="https://jobs.lever.co/fixture/apply/1",
        updated_at=None, first_seen=now, last_seen=now, provenance={},
    )
    store.upsert_jobs([job])
    result = qualify_catalog(store, load_catalog(catalog))
    assert result[2]["job_application_links"] == 1
    assert result[2]["channel_kind"] == "ats_board"
    assert contact_stats(store)["total"] == 0
