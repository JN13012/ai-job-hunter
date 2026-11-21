"""M5.3 : simulations HTTP uniquement, aucun accès réseau réel."""

from datetime import UTC, datetime
import json

import httpx
import pytest

from ai_job_hunter.catalog import load_catalog
from ai_job_hunter.contact_discovery import (
    ScanError, extract_contacts, scan_catalog,
)
from ai_job_hunter.contacts import contact_stats, list_contacts, change_contact
from ai_job_hunter.company_discovery import Company, upsert_companies
from ai_job_hunter.storage import JobStore
from ai_job_hunter.cli import main


CATALOG = """
[[targets]]
company = "Exemple"
source = "manual"
career_url = "https://careers.example.org/jobs"
siren = "123456789"
allow_contact_scan = true
permission_note = "Autorisation de consulter cette page carrière donnée pour le test."

[[targets]]
company = "Sans accord"
source = "manual"
career_url = "https://other.example.org/careers"
siren = "123456789"
"""

HTML = """<html><body>
<a href="mailto:recrutement@example.org">Équipe recrutement</a>
<a href="mailto:alice@example.org">Contacter Alice</a>
<a href="mailto:recruitment@gmail.com">Personnel</a>
<a href="/candidature-spontanee">Candidature spontanée</a>
<a href="https://bad.example.net/form">Candidature spontanée</a>
<a href="/contact?token=secret">Candidature spontanée</a>
<a href="/job/42">Postuler offre</a>
<script><a href="mailto:rh@example.org">Faux contact</a></script>
</body></html>"""


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.toml"
    path.write_text(CATALOG, encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path):
    with JobStore(tmp_path / "jobs.sqlite3") as db:
        upsert_companies(
            db, [Company("123456789", "Exemple", "62.01Z", "PME",
                         "Marseille", "13001", "fixture")], datetime.now(UTC),
        )
        yield db


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_dry_run_no_get_no_save(store, catalog):
    with client_for(lambda request: pytest.fail("dry run accessed network")) as client:
        report = scan_catalog(store, load_catalog(catalog), client)
    assert report.requests == 0 and report.pages == 0
    assert report.skipped == 1
    assert report.candidates[0]["status"] == "eligible_not_fetched"
    assert contact_stats(store)["total"] == 0


def test_collect_and_register_pending(store, catalog, monkeypatch):
    monkeypatch.setattr("ai_job_hunter.contact_discovery.time.sleep", lambda _: None)
    visited = []
    def handler(request):
        visited.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"Content-Type": "text/plain"},
                                  text="User-agent: *\nAllow: /\n")
        assert request.url.path == "/jobs"
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text=HTML)
    with client_for(handler) as client:
        result = scan_catalog(store, load_catalog(catalog), client, fetch=True, register=True)
        assert result.pages == 1 and result.requests == 2
        assert result.writes.new == 2
        assert len(result.candidates) == 2
        assert len(visited) == 2
        second = scan_catalog(store, load_catalog(catalog), client, fetch=True, register=True)
        assert second.writes.unchanged == 2
    contacts = list_contacts(store)
    assert len(contacts) == 2
    assert all(row["status"] == "pending" for row in contacts)
    assert all("alice@" not in str(row) for row in contacts)
    assert not any("gmail" in str(row) for row in contacts)


def test_disallowed_robots_blocks_page(store, catalog):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, headers={"Content-Type": "text/plain"},
                              text="User-agent: *\nDisallow: /jobs\n")
    with client_for(handler) as client:
        result = scan_catalog(store, load_catalog(catalog), client, fetch=True)
    assert paths == ["/robots.txt"]
    assert result.skipped == 2
    assert result.candidates == []
    assert contact_stats(store)["total"] == 0


@pytest.mark.parametrize("status", [301, 302, 403, 429])
def test_robots_non_success_stops(store, catalog, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://internal.invalid/secret"})
    with client_for(handler) as client:
        result = scan_catalog(store, load_catalog(catalog), client, fetch=True)
    assert len(calls) == 1
    assert result.requests == 1
    assert len(result.errors) == 1


def test_404_robots_fallback_and_bad_content_type(store, catalog):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"Content-Type": "application/json"}, text="{}")
    with client_for(handler) as client:
        result = scan_catalog(store, load_catalog(catalog), client, fetch=True)
    assert len(result.errors) == 1
    assert result.pages == 0


def test_large_payload_rejected(store, catalog):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"Content-Type": "text/html"},
                              content=b"x" * (1024 * 1024 + 1))
    with client_for(handler) as client:
        result = scan_catalog(store, load_catalog(catalog), client, fetch=True)
    assert len(result.errors) == 1
    assert not result.candidates


def test_banned_input_and_no_permission(tmp_path, store):
    disallowed = CATALOG.replace("allow_contact_scan = true", "allow_contact_scan = false")
    path = tmp_path / "catalog.toml"
    path.write_text(disallowed)
    with client_for(lambda request: pytest.fail("not allowed")) as client:
        result = scan_catalog(store, load_catalog(path), client, fetch=True)
    assert result.requests == 0
    assert result.skipped == 2

    path.write_text(CATALOG.replace('permission_note = "Autorisation de consulter cette page carrière donnée pour le test."\n', ""))
    with pytest.raises(ValueError, match="autorisation"):
        load_catalog(path)


def test_blocked_contact_stays_blocked(store, catalog, monkeypatch):
    monkeypatch.setattr("ai_job_hunter.contact_discovery.time.sleep", lambda _: None)
    def handler(request):
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(
            200, headers={"Content-Type": "text/html"}, text=HTML
        )
    with client_for(handler) as client:
        scan_catalog(store, load_catalog(catalog), client, fetch=True, register=True)
        contact = list_contacts(store)[0]
        change_contact(store, contact["id"], "block")
        scan_catalog(store, load_catalog(catalog), client, fetch=True, register=True)
    assert list_contacts(store, status="blocked")[0]["target"] is None


def test_only_generic_explicit_anchors():
    candidates = extract_contacts(
        "https://careers.example.org/jobs", HTML, "123456789"
    )
    assert len(candidates) == 2
    assert {c.channel for c in candidates} == {"email", "form"}


def test_cli_dry_run(store, catalog, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with JobStore(tmp_path / ".ajh" / "jobs.sqlite3") as db:
        upsert_companies(
            db, [Company("123456789", "Exemple", None, None, None, None, "fixture")],
            datetime.now(UTC),
        )
    assert main(["contacts", "discover", "--file", str(catalog)]) == 0
    data = [json.loads(row) for row in capsys.readouterr().out.splitlines()]
    assert len(data) == 1 and data[0]["status"] == "eligible_not_fetched"
    assert main(["contacts", "discover", "--file", str(catalog), "--register"]) == 1


def test_reject_internal_hosts():
    from ai_job_hunter.contact_discovery import _public_host
    for url in [
        "http://example.org", "https://127.0.0.1/path", "https://localhost/x",
        "https://work.internal/x", "https://example.org/?token=secret",
        "https://example.org/#private", "https://user:pass@example.org",
    ]:
        with pytest.raises(ValueError):
            _public_host(url)
