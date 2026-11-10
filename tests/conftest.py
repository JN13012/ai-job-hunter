"""Fixtures synthétiques et interdiction de tout réseau réel pendant pytest."""

from datetime import UTC, datetime
import json
from pathlib import Path
import socket

import pytest

from ai_job_hunter.config import GreenhouseBoard
from ai_job_hunter.normalizer import normalize_greenhouse


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Accès réseau réel interdit pendant pytest")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.fixture
def greenhouse_payload():
    path = Path(__file__).parent / "fixtures" / "greenhouse_jobs.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def board():
    return GreenhouseBoard("Entreprise de test", "fixture-board")


@pytest.fixture
def job(greenhouse_payload, board):
    return normalize_greenhouse(
        greenhouse_payload["jobs"][0], board,
        "https://boards-api.greenhouse.io/v1/boards/fixture-board/jobs?content=true",
        datetime(2026, 11, 10, 10, tzinfo=UTC),
    )
