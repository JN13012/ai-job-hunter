"""M5.4 : priorité explicable, SIREN non deviné et aucun envoi."""

import json
from datetime import UTC, datetime

import pytest

from ai_job_hunter.catalog import load_catalog
from ai_job_hunter.channel_routing import route_jobs, route_companies
from ai_job_hunter.cli import main
from ai_job_hunter.company_discovery import Company, upsert_companies
from ai_job_hunter.contacts import import_contacts, change_contact, list_contacts
from ai_job_hunter.matcher import Profile
from ai_job_hunter.models import Job
from ai_job_hunter.storage import JobStore

PROFILE = Profile({"ai": ("ai engineer",)}, ("python", "linux", "rag", "sql", "llm"),
                  ("marseille",), (), (), (), 60)
CONTACT = {"siren": "123456789", "channel": "form",
           "target": "https://careers.example.org/apply",
           "role": "careers", "source_url": "https://careers.example.org/jobs",
           "source_type": "company_careers"}


def save_contact(store, tmp_path, contact, *, approve=True):
    path = tmp_path / "contacts.json"
    path.write_text(json.dumps([contact]))
    assert import_contacts(path, store).new == 1
    item = next(r for r in list_contacts(store) if r["target"] == contact["target"])
    if approve:
        change_contact(store, item["id"], "approve")
    return item["id"]


def add_job(store, job_id="a", source="lever", board="example", *,
            url="https://jobs.example.org/apply/123?job=abc", provenance=None,
            title="Alternance AI Engineer", description="Python Linux RAG SQL LLM"):
    now = datetime.now(UTC)
    store.upsert_jobs([Job(
        id=job_id, external_id=job_id, source=source, board_token=board,
        company="Entreprise Exemple", title=title, location_raw="Marseille",
        description=description, application_url=url, updated_at=None,
        first_seen=now, last_seen=now, provenance=provenance or {},
    )])


@pytest.fixture
def db(tmp_path):
    with JobStore(tmp_path / "db.sqlite3") as store:
        upsert_companies(store, [
            Company("123456789", "Entreprise Exemple", "62.01Z",
                    "PME", "Marseille", "13001", "fixture"),
        ], datetime.now(UTC))
        yield store


def test_priority_offer_over_approved_form(db, tmp_path):
    save_contact(db, tmp_path, CONTACT)
    add_job(db)
    path = tmp_path / "catalog.toml"
    path.write_text('''
[[targets]]
company = "Entreprise Exemple"
source = "lever"
career_url = "https://jobs.lever.co/example"
board_token = "example"
siren = "123456789"
''')
    item = route_jobs(db, PROFILE, catalog=load_catalog(path))[0]
    assert item["routing_status"] == "source_link_unverified"
    assert item["selected"]["kind"] == "offer_link"
    assert item["selected"]["target"].endswith("?job=abc")
    assert item["siren"] == "123456789"
    assert item["company_link_evidence"] == "catalog_explicit_board_siren"
    assert item["alternatives"][0]["kind"] == "company_form"
    assert item["ready_for_preparation"] is True
    assert item["automation_authorized"] is False
    assert item["human_final_submit_required"] is True


def test_approved_form_over_email(db, tmp_path):
    save_contact(db, tmp_path, CONTACT)
    email = {**CONTACT, "channel": "email", "target": "rh@example.org", "role": "hr"}
    save_contact(db, tmp_path, email)
    companies = route_companies(db)
    assert companies[0]["selected"]["kind"] == "company_form"
    assert companies[0]["alternatives"][0]["kind"] == "company_email"
    assert companies[0]["recruitment_confirmed"] is False


def test_pending_and_blocked_never_selected(db, tmp_path):
    identifier = save_contact(db, tmp_path, CONTACT, approve=False)
    item = route_companies(db)[0]
    assert item["routing_status"] == "needs_contact_review"
    assert item["selected"] is None and item["pending_contacts"] == 1
    change_contact(db, identifier, "block")
    item = route_companies(db)[0]
    assert item["routing_status"] == "no_channel"
    assert item["selected"] is None


def test_no_inferred_company_from_identical_name(db, tmp_path):
    save_contact(db, tmp_path, CONTACT)
    add_job(db, url=None)
    item = route_jobs(db, PROFILE)[0]
    assert item["siren"] is None
    assert item["routing_status"] == "no_channel"
    assert item["company_link_evidence"] == "missing_verified_siren"


def test_lba_explicit_siret_joins_company(db, tmp_path):
    save_contact(db, tmp_path, CONTACT)
    add_job(db, source="lba", board="api", url=None,
            provenance={"workplace": {"siret": "12345678900001"}})
    item = route_jobs(db, PROFILE)[0]
    assert item["siren"] == "123456789"
    assert item["selected"]["kind"] == "company_form"
    assert item["routing_status"] == "approved_contact"


def test_conflicting_catalog_siren_never_joins(db, tmp_path):
    save_contact(db, tmp_path, CONTACT)
    upsert_companies(db, [Company("987654321", "Autre société", None, None,
                                  None, None, "fixture")], datetime.now(UTC))
    add_job(db, url=None)
    path = tmp_path / "catalog.toml"
    path.write_text('''
[[targets]]
company = "Exemple"
source = "lever"
board_token = "example"
career_url = "https://jobs.lever.co/example"
siren = "123456789"
[[targets]]
company = "Homonyme"
source = "lever"
board_token = "Example"
career_url = "https://jobs.lever.co/Example"
siren = "987654321"
''')
    # Le catalogue lui-même refuse les doublons de tokens ; une liaison
    # contradictoire n'est donc jamais acceptée silencieusement.
    with pytest.raises(ValueError, match="dupliquée"):
        load_catalog(path)


def test_other_contract_excluded(db):
    add_job(db, title="CDI AI Engineer")
    assert route_jobs(db, PROFILE) == []


@pytest.mark.parametrize("url", [
    "http://jobs.example.org/apply", "https://localhost/apply",
    "https://127.0.0.1/apply", "https://internal.test/apply",
    "javascript:alert(1)", "https://person:password@example.org/apply",
])
def test_unsafe_direct_url_rejected(db, url):
    add_job(db, url=url)
    row = route_jobs(db, PROFILE)[0]
    assert row["selected"] is None
    assert row["ready_for_preparation"] is False


def test_cli_routes_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["routes", "companies"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["routes", "jobs", "--profile", "examples/nonexistent.toml"]) == 1
    assert "[FAIL]" in capsys.readouterr().out
    profile = tmp_path / "profile.toml"
    profile.write_text("[roles]\nai=['ai engineer']\n")
    assert main(["routes", "jobs", "--profile", str(profile)]) == 0
    assert capsys.readouterr().out == ""


def test_routes_do_not_change_db(db, tmp_path):
    save_contact(db, tmp_path, CONTACT)
    add_job(db)
    before = db.connection.total_changes
    route_companies(db)
    route_jobs(db, PROFILE)
    assert db.connection.total_changes == before
