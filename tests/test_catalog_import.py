import json

import httpx
import pytest

from ai_job_hunter.catalog import load_catalog
from ai_job_hunter.cli import main
from ai_job_hunter.imports import import_jobs
from ai_job_hunter.storage import JobStore

CATALOG = '''
[[targets]]
company = "Fixture"
source = "lever"
career_url = "https://jobs.lever.co/fixture"
board_token = "fixture"

[[targets]]
company = "Manual"
source = "manual"
career_url = "https://example.test/careers"
'''
ROW = {"source": "linkedin", "url": "https://www.linkedin.com/jobs/view/123#top",
       "company": "Fixture", "title": "Alternance IA", "location": "Paris"}


def test_catalog_collect_only_selected_source(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "catalog.toml"
    path.write_text(CATALOG)
    assert main(["catalog", "--file", str(path)]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2
    assert not (tmp_path / ".ajh").exists()
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == "api.lever.co"
        assert request.url.path == "/v0/postings/fixture"
        return httpx.Response(200, json=[{"id": "xyz", "text": "AI"}])
    original = httpx.Client
    monkeypatch.setattr("ai_job_hunter.cli.httpx.Client",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert main(["collect", "--source", "lever", "--catalog", str(path)]) == 0
    assert len(calls) == 1
    assert "Nouvelles : 1" in capsys.readouterr().out


@pytest.mark.parametrize("catalog", [
    CATALOG.replace("board_token = \"fixture\"", "board_token = \"different\""),
    CATALOG.replace("jobs.lever.co", "evil.example"),
    CATALOG.replace("https://example.test", "http://example.test"),
    CATALOG + CATALOG,
])
def test_bad_catalog(tmp_path, catalog):
    path = tmp_path / "catalog.toml"
    path.write_text(catalog)
    with pytest.raises(ValueError):
        load_catalog(path)


def test_import_and_idempotence(tmp_path):
    path = tmp_path / "offers.json"
    path.write_text(json.dumps([ROW, {**ROW, "url": "https://www.linkedin.com/jobs/view/123#other"}]))
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        first = import_jobs(path, store)
        second = import_jobs(path, store)
        assert (first.counts.new, first.duplicates, first.errors) == (1, 1, [])
        assert second.counts.unchanged == 1
        row = store.list_jobs()[0]
        assert row["source"] == "import_linkedin"
        assert row["application_url"] is None
        assert json.loads(row["provenance_json"])["availability"] == "unverified"
        path.write_text(json.dumps([{**ROW, "title": "New title"}]))
        assert import_jobs(path, store).counts.updated == 1
        assert len(store.list_jobs()) == 1


@pytest.mark.parametrize("bad", [
    {**ROW, "url": "javascript:alert(1)"},
    {**ROW, "url": "https://user:secret@example.test/"},
    {**ROW, "source": "unknown"},
    {**ROW, "title": ""},
    {**ROW, "extra": "unknown"},
])
def test_import_keeps_valid_rows(tmp_path, bad):
    path = tmp_path / "offers.json"
    path.write_text(json.dumps([ROW, bad]))
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        result = import_jobs(path, store)
        assert result.counts.new == 1
        assert len(result.errors) == 1


def test_import_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "offers.json"
    path.write_text(json.dumps([ROW]))
    assert main(["import", "--file", str(path)]) == 0
    assert "Nouvelles : 1" in capsys.readouterr().out


def test_no_ats_for_manual_targets(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "catalog.toml"
    path.write_text(CATALOG)
    assert main(["collect", "--source", "ashby", "--catalog", str(path)]) == 1
    assert not (tmp_path / ".ajh").exists()
