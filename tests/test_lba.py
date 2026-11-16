from datetime import UTC, datetime
import json

import httpx
import pytest

from ai_job_hunter.cli import main
from ai_job_hunter.config import ConfigError, LBAConfig, load_config
from ai_job_hunter.lba import collect_lba, count_recruiters, list_recruiters
from ai_job_hunter.storage import JobStore

SETTINGS = LBAConfig(("M1805",), ("13",))
WORK = {"name": "Fixture", "siret": "12345678901234", "location": {"address": "Marseille"},
        "website": "https://example.test"}
PAYLOAD = {
    "jobs": [{"identifier": {"id": None, "partner_label": "France Travail", "partner_job_id": "a1"},
              "workplace": WORK, "offer": {"title": "Alternance IA", "description": "<p>Python</p>", "status": "Active"},
              "contract": {"type": ["Apprentissage"]},
              "apply": {"url": "https://example.test/apply", "phone": "0102030405", "recipient_id": "private-recipient"}}],
    "recruiters": [{"identifier": {"id": "r1"}, "workplace": WORK,
                    "apply": {"url": "https://example.test/spontaneous", "recipient_id": "private-recipient"}}],
    "warnings": [],
}


def test_collection_and_idempotence(tmp_path):
    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "api.apprentissage.beta.gouv.fr"
        assert request.url.path == "/api/job/v1/search"
        assert request.url.params["romes"] == "M1805"
        assert request.url.params["departements"] == "13"
        assert request.headers["Authorization"] == "Bearer fixture-key"
        return httpx.Response(200, json=PAYLOAD)
    with JobStore(tmp_path / "jobs.sqlite3") as store, httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = collect_lba(SETTINGS, store, client, "fixture-key")
        second = collect_lba(SETTINGS, store, client, "fixture-key")
        assert first.errors == second.errors == []
        assert first.jobs.new == first.recruiters.new == 1
        assert second.jobs.unchanged == second.recruiters.unchanged == 1
        assert len(store.list_jobs()) == count_recruiters(store) == 1
        row = store.list_jobs()[0]
        assert row["description"] == "Python"
        assert "private-recipient" not in row["provenance_json"]
        assert "0102030405" not in row["provenance_json"]
        assert "private-recipient" not in list_recruiters(store, 1)[0]["payload_json"]
        assert "Authorization" not in client.headers


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_errors(tmp_path, status):
    with JobStore(tmp_path / "jobs.sqlite3") as store, httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    ) as client:
        result = collect_lba(SETTINGS, store, client, "fixture-key")
        assert len(result.errors) == 1
        assert str(status) in result.errors[0]
        assert "fixture-key" not in result.errors[0]
        assert not store.list_jobs()


def test_partial_invalid_rows(tmp_path):
    payload = {**PAYLOAD, "jobs": [PAYLOAD["jobs"][0], {}],
               "recruiters": [PAYLOAD["recruiters"][0], {}]}
    with JobStore(tmp_path / "jobs.sqlite3") as store, httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        result = collect_lba(SETTINGS, store, client, "fixture-key")
        assert len(result.errors) == 2
        assert result.jobs.new == result.recruiters.new == 1


@pytest.mark.parametrize("contents", [
    '[lba]\napi_key = "secret"',
    '[lba]\nromes = ["invalid"]',
    '[lba]\ndepartements = ["bad"]',
    '[lba]\ntarget_diploma_level = 7',
    '[lba]\ntimeout_seconds = nan',
])
def test_invalid_settings(tmp_path, contents):
    path = tmp_path / "ajh.toml"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_key_no_database(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AJH_LBA_API_KEY", raising=False)
    (tmp_path / "ajh.toml").write_text('[lba]\nromes = ["M1805"]\ndepartements = ["13"]\n')
    assert main(["collect", "--source", "lba"]) == 1
    assert "AJH_LBA_API_KEY" in capsys.readouterr().out
    assert not (tmp_path / ".ajh").exists()


def test_recruiters_stats(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["recruiters", "stats"]) == 0
    assert json.loads(capsys.readouterr().out) == {"total": 0, "source": "lba"}
