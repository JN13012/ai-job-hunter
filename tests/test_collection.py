from copy import deepcopy
import json
from pathlib import Path

import httpx

from ai_job_hunter.collection import collect_greenhouse
from ai_job_hunter.config import GreenhouseBoard, GreenhouseConfig
from ai_job_hunter.storage import JobStore


def test_collection_is_idempotent_then_updates(tmp_path, board, greenhouse_payload):
    payload = deepcopy(greenhouse_payload)
    settings = GreenhouseConfig(boards=(board,))
    with JobStore(tmp_path / "jobs.sqlite3") as store, httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as client:
        first = collect_greenhouse(settings, store, client)
        first_seen = {row["id"]: row["first_seen"] for row in store.list_jobs()}
        second = collect_greenhouse(settings, store, client)
        assert (first.received, first.new, first.errors) == (2, 2, [])
        assert (second.new, second.updated, second.unchanged) == (0, 0, 2)
        payload["jobs"][0]["title"] = "Offre modifiée"
        third = collect_greenhouse(settings, store, client)
        assert (third.new, third.updated, third.unchanged) == (0, 1, 1)
        assert {row["id"]: row["first_seen"] for row in store.list_jobs()} == first_seen
        assert len(store.list_jobs()) == 2


def test_duplicates_are_counted_and_conflicts_reported(tmp_path, board, greenhouse_payload):
    original = greenhouse_payload["jobs"][0]
    conflict = {**original, "title": "Contradictory"}
    payload = {"jobs": [original, original, conflict], "meta": {"total": 3}}
    with JobStore(tmp_path / "jobs.sqlite3") as store, httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as client:
        summary = collect_greenhouse(GreenhouseConfig(boards=(board,)), store, client)
        assert (summary.received, summary.new, summary.duplicates) == (3, 1, 2)
        assert len(summary.errors) == 1
        assert store.list_jobs()[0]["title"] == original["title"]


def test_partial_response_keeps_valid_records(tmp_path, board):
    payload = json.loads((Path(__file__).parent / "fixtures" / "greenhouse_partial.json").read_text(encoding="utf-8"))
    with JobStore(tmp_path / "jobs.sqlite3") as store, httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as client:
        summary = collect_greenhouse(GreenhouseConfig(boards=(board,)), store, client)
        assert (summary.received, summary.new, len(summary.errors)) == (4, 1, 4)
        assert store.list_jobs()[0]["external_id"] == "201"


def test_rate_limit_stops_remaining_boards(tmp_path, board, monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr("ai_job_hunter.collection.time.sleep", sleeps.append)

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})

    settings = GreenhouseConfig(boards=(board, GreenhouseBoard("Other", "other")))
    with JobStore(tmp_path / "jobs.sqlite3") as store, httpx.Client(transport=httpx.MockTransport(handler)) as client:
        summary = collect_greenhouse(settings, store, client)
        assert summary.received == 0
        assert len(summary.errors) == 1
        assert "Retry-After=60" in summary.errors[0]
        assert len(calls) == 1
        assert not sleeps


def test_network_failure_continues_with_next_board_after_interval(tmp_path, board, monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr("ai_job_hunter.collection.time.sleep", sleeps.append)

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json={"jobs": [{"id": 5}]})

    settings = GreenhouseConfig(boards=(board, GreenhouseBoard("Other", "other")), request_interval_seconds=2)
    with JobStore(tmp_path / "jobs.sqlite3") as store, httpx.Client(transport=httpx.MockTransport(handler)) as client:
        summary = collect_greenhouse(settings, store, client)
        assert len(summary.errors) == 1
        assert summary.new == 1
        assert sleeps == [2]
        assert store.list_jobs()[0]["board_token"] == "other"


def test_transaction_failure_does_not_report_uncommitted_inserts(tmp_path, board):
    with JobStore(tmp_path / "jobs.sqlite3") as store, httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}]})
    )) as client:
        store.connection.execute("""CREATE TRIGGER deny_second BEFORE INSERT ON jobs
            WHEN NEW.external_id = '2' BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
        summary = collect_greenhouse(GreenhouseConfig(boards=(board,)), store, client)
        assert summary.received == 2
        assert (summary.new, summary.updated, summary.unchanged) == (0, 0, 0)
        assert "transaction annulée" in summary.errors[0]
        assert store.list_jobs() == []
