"""M4 : tests offline avec API simulée et coexistence SQLite M0-M3."""

import json

import httpx
import pytest

from ai_job_hunter.catalog import load_catalog
from ai_job_hunter.cli import main
from ai_job_hunter.company_discovery import (
    DiscoveryConfig, Search, company_rows, company_stats, discover, load_searches,
    normalize_company,
)
from ai_job_hunter.storage import JobStore

FIRST = {"siren": "123456789", "nom_complet": "Entreprise cybersécurité",
         "activite_principale": "62.02A", "categorie_entreprise": "PME",
         "siege": {"libelle_commune": "MARSEILLE", "code_postal": "13001"},
         "dirigeants": [{"nom": "PERSONNE A NE PAS STOCKER"}],
         "matching_etablissements": [{"adresse": "ADRESSE À NE PAS STOCKER"}]}
SECOND = {**FIRST, "siren": "987654321", "nom_complet": "Société logicielle"}


def test_discover_replay_and_private_data_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_job_hunter.company_discovery.time.sleep", lambda _: None)
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == "recherche-entreprises.api.gouv.fr"
        assert request.url.path == "/search"
        assert request.url.params["etat_administratif"] == "A"
        assert request.url.params["est_entrepreneur_individuel"] == "false"
        assert request.url.params["departement"] == "13"
        assert request.url.params["activite_principale"] == "62.02A"
        assert request.url.params["q"] == "informatique"
        return httpx.Response(200, json={"results": [FIRST], "total_pages": 1})
    settings = DiscoveryConfig((Search("informatique", "13", "62.02A"),))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, JobStore(tmp_path / "jobs.sqlite3") as store:
        first = discover(settings, store, client)
        second = discover(settings, store, client)
        assert (first.counts.new, second.counts.unchanged, len(calls)) == (1, 1, 2)
        assert not first.errors and not second.errors
        item = company_rows(store)[0]
        assert (item["siren"], item["naf"], item["city"]) == ("123456789", "62.02A", "MARSEILLE")
        assert "PERSONNE A NE PAS STOCKER" not in json.dumps(item)
        assert "ADRESSE À NE PAS STOCKER" not in json.dumps(item)
        assert store.list_jobs() == []


def test_pagination_cap_warns_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_job_hunter.company_discovery.time.sleep", lambda _: None)
    pages = []
    def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(200, json={"results": [FIRST if page == 1 else SECOND],
                                          "total_pages": 8})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, JobStore(tmp_path / "db.sqlite") as store:
        report = discover(DiscoveryConfig((Search("logiciel", max_pages=2),), per_page=1), store, client)
        assert pages == [1, 2]
        assert report.counts.new == 2
        assert any("partielle" in warning for warning in report.warnings)
        assert company_stats(store)["total"] == 2


def test_rate_limit_stops_following_searches(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_job_hunter.company_discovery.time.sleep", lambda _: None)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "30"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, JobStore(tmp_path / "db.sqlite") as store:
        report = discover(DiscoveryConfig((Search("logiciel"), Search("cyber"))), store, client)
        assert len(calls) == 1
        assert report.requests == 1
        assert len(report.errors) == 1
        assert "HTTP 429" in report.errors[0]
        assert company_stats(store)["total"] == 0


def test_invalid_company_does_not_break_valid(tmp_path):
    payload = {"results": [FIRST, {"siren": "oops"}], "total_pages": 1}
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as client, JobStore(tmp_path / "db.sqlite") as store:
        report = discover(DiscoveryConfig((Search("cyber"),)), store, client)
        assert report.counts.new == 1
        assert len(report.errors) == 1
        assert company_stats(store)["total"] == 1


def test_lba_and_catalog_link_only_by_siren(tmp_path):
    catalog_file = tmp_path / "catalog.toml"
    catalog_file.write_text('''
[[targets]]
company = "Entreprise cybersécurité"
source = "manual"
career_url = "https://example.test/careers"
siren = "123456789"
''')
    catalog = load_catalog(catalog_file)
    with JobStore(tmp_path / "db.sqlite") as store:
        from ai_job_hunter.company_discovery import upsert_companies
        from datetime import UTC, datetime
        upsert_companies(store, [normalize_company(FIRST, Search("cyber"))], datetime.now(UTC))
        with store.connection:
            store.connection.execute("""
                CREATE TABLE lba_recruiters (siret TEXT)
            """)
            store.connection.execute("INSERT INTO lba_recruiters VALUES ('12345678901234')")
            store.connection.execute("INSERT INTO lba_recruiters VALUES ('98765432101234')")
        row = company_rows(store, catalog=catalog)[0]
        assert row["lba_potential"] is True
        assert row["lba_locations"] == 1
        assert row["catalog_sources"] == ["manual"]
        assert company_stats(store, catalog=catalog) == {
            "total": 1, "lba_potential": 1, "catalog_linked": 1,
        }


def test_catalog_without_siren_never_guesses(tmp_path):
    file = tmp_path / "catalog.toml"
    file.write_text('''
[[targets]]
company = "Entreprise cybersécurité"
source = "manual"
career_url = "https://example.test/careers"
''')
    target = load_catalog(file)[0]
    assert target.siren is None
    assert "catalog_siren_declared" not in company_rows_from_example(tmp_path, target)


def company_rows_from_example(tmp_path, target):
    from datetime import UTC, datetime
    from ai_job_hunter.company_discovery import upsert_companies
    with JobStore(tmp_path / "db.sqlite") as store:
        upsert_companies(store, [normalize_company(FIRST, Search("cyber"))], datetime.now(UTC))
        return company_rows(store, catalog=(target,))[0]["signals"]


@pytest.mark.parametrize("config", [
    '[discovery]\nper_page = 26\n[[searches]]\nq = "cyber"',
    '[discovery]\ninterval_seconds = 0.01\n[[searches]]\nq = "cyber"',
    '[[searches]]\nq = "123456789"',
    '[[searches]]\nq = "cyber"\ncode_naf = "62"',
    '[[searches]]\nq = "cyber"\nmax_pages = 0',
    '[[searches]]\nq = "cyber"\ndepartement = "../13"',
    '[[searches]]\nq = "cyber"\nmax_pages = true',
])
def test_invalid_queries(tmp_path, config):
    path = tmp_path / "queries.toml"
    path.write_text(config)
    with pytest.raises(ValueError):
        load_searches(path)


def test_cli_offline_discovery(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    file = tmp_path / "queries.toml"
    file.write_text('[[searches]]\nq="cyber"\nmax_pages=1\n')
    original = httpx.Client
    monkeypatch.setattr("ai_job_hunter.cli.httpx.Client", lambda **kw: original(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"results": [FIRST], "total_pages": 1})
        ), **kw
    ))
    assert main(["companies", "discover", "--file", str(file)]) == 0
    assert "Nouvelles : 1" in capsys.readouterr().out
    assert main(["companies", "stats"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "total": 1, "lba_potential": 0, "catalog_linked": 0,
    }
    assert main(["companies", "list"]) == 0
    assert json.loads(capsys.readouterr().out)["siren"] == "123456789"
