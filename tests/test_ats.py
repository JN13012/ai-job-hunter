"""Tests hors ligne des collecteurs supplémentaires."""

from datetime import UTC, datetime

import httpx
import pytest

from ai_job_hunter.ats_normalizer import normalize_ats
from ai_job_hunter.collection import collect_source
from ai_job_hunter.collectors.ats import fetch_ats
from ai_job_hunter.collectors.common import CollectorError
from ai_job_hunter.config import GreenhouseBoard, GreenhouseConfig, load_config
from ai_job_hunter.storage import JobStore

BOARD = GreenhouseBoard("Fixture", "fixture")
LEVER = {"id": "a-1", "text": "AI Intern", "categories": {"location": "Paris"},
         "descriptionPlain": "Python", "applyUrl": "https://jobs.lever.co/fixture/a-1/apply"}
ASHBY = {"title": "Cyber Intern", "location": "Aix", "descriptionHtml": "<p>Audit</p>",
         "isListed": True, "jobUrl": "https://jobs.ashbyhq.com/fixture/b-1",
         "applyUrl": "https://jobs.ashbyhq.com/fixture/b-1/application"}


@pytest.mark.parametrize(("source", "payload", "host"), [
    ("lever", [LEVER], "api.lever.co"),
    ("lever_eu", [LEVER], "api.eu.lever.co"),
    ("ashby", {"jobs": [ASHBY, {**ASHBY, "isListed": False}]}, "api.ashbyhq.com"),
])
def test_integrated_collect_twice(source, payload, host, tmp_path):
    calls = []
    def response(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.host == host
        return httpx.Response(200, json=payload)
    with httpx.Client(transport=httpx.MockTransport(response)) as client, JobStore(tmp_path / "jobs.db") as store:
        settings = GreenhouseConfig(boards=(BOARD,))
        first = collect_source(source, settings, store, client)
        second = collect_source(source, settings, store, client)
        assert (first.new, first.errors, second.unchanged) == (1, [], 1)
        assert len(store.list_jobs()) == 1
        assert store.list_jobs()[0]["source"] == source
        assert len(calls) == 2


def test_smartrecruiters_details(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_job_hunter.collectors.ats.time.sleep", lambda _: None)
    def response(request):
        if request.url.path.endswith("/postings"):
            assert request.url.params["destination"] == "PUBLIC"
            return httpx.Response(200, json={"totalFound": 1, "content": [
                {"id": "123", "name": "Cyber", "location": {"city": "Marseille", "country": "FR"}}]})
        assert request.url.path.endswith("/123")
        return httpx.Response(200, json={"id": "123", "applyUrl": "https://jobs.smartrecruiters.com/fixture/123",
            "jobAd": {"sections": {"jobDescription": {"text": "<p>Audit</p>"}}}})
    with httpx.Client(transport=httpx.MockTransport(response)) as client, JobStore(tmp_path / "jobs.db") as store:
        summary = collect_source("smartrecruiters", GreenhouseConfig(boards=(BOARD,)), store, client)
        assert (summary.new, summary.errors) == (1, [])
        row = store.list_jobs()[0]
        assert row["source"] == "smartrecruiters"
        assert row["description"] == "Audit"


def test_rate_limited(monkeypatch):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
        with pytest.raises(CollectorError) as caught:
            fetch_ats(client, BOARD, "lever", 1)
        assert caught.value.rate_limited


def test_config_new_sources(tmp_path):
    file = tmp_path / "ajh.toml"
    file.write_text('[[ashby.boards]]\ncompany = "Fixture"\nboard_token = "fixture"\n', encoding="utf-8")
    assert load_config(file).ashby.boards == (BOARD,)


def test_ashby_identity_and_missing_update_date():
    job = normalize_ats(ASHBY, BOARD, "https://api.ashbyhq.com", datetime.now(UTC), "ashby")
    assert job.external_id == "b-1"
    assert job.description == "Audit"
    assert job.updated_at is None


def test_lever_pagination(monkeypatch):
    monkeypatch.setattr("ai_job_hunter.collectors.ats.time.sleep", lambda _: None)
    def response(request):
        skip = int(request.url.params["skip"])
        return httpx.Response(200, json=[{**LEVER, "id": str(i)} for i in range(skip, min(skip + 100, 101))])
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        assert len(fetch_ats(client, BOARD, "lever", 1).jobs) == 101
