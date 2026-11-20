import json
from datetime import UTC, datetime

import pytest

from ai_job_hunter.cli import main
from ai_job_hunter.matcher import Profile, load_profile, score_job, rank_jobs
from ai_job_hunter.models import Job
from ai_job_hunter.storage import JobStore

PROFILE = Profile({"ia": ("ingenieur ia", "ai engineer"), "cyber": ("pentest",)},
                  ("python", "linux", "rag", "sql", "llm"), ("marseille",),
                  ("paris", "france"), ("new york",), ("thales",), 60)


def row(**changes):
    return {"id": "fixture", "source": "lever", "company": "Thales",
            "title": "Alternance — Ingénieur IA", "location_raw": "Marseille",
            "description": "Python Linux RAG SQL LLM", "application_url": None,
            "provenance_json": "{}", **changes}


def test_full_score():
    result = score_job(row(), PROFILE)
    assert result.state == "match"
    assert result.score == 100
    assert sum(result.components.values()) == result.score
    assert result.contract == "alternance"


@pytest.mark.parametrize(("title", "description", "state", "contract"), [
    ("Ingénieur IA", "Notre équipe accueille des alternants. Python", "review", "unknown"),
    ("Stage Ingénieur IA", "Python", "excluded", "other"),
    ("CDI Ingénieur IA", "Python", "excluded", "other"),
    ("Stage ou alternance Ingénieur IA", "Python", "review", "ambiguous"),
    ("Alternance Ingénieur IA", None, "review", "alternance"),
    ("Senior Ingénieur IA", "Python", "excluded", "unknown"),
    ("Alternance comptabilité", "Gestion comptable", "excluded", "alternance"),
])
def test_contract_and_role_gates(title, description, state, contract):
    result = score_job(row(title=title, description=description), PROFILE)
    assert (result.state, result.contract) == (state, contract)


def test_metadata_and_word_boundary():
    result = score_job(row(title="AI Engineer", description="pythonista linux",
                           source="lba", provenance_json=json.dumps({"contract": {"type": ["Apprentissage"]}})), PROFILE)
    assert result.contract == "alternance"
    assert result.skills == ("linux",)
    assert result.state == "match"


@pytest.mark.parametrize(("location", "state"), [
    ("New York", "excluded"), ("New York / Marseille", "review"), (None, "review"), ("Paris", "match")
])
def test_location(location, state):
    assert score_job(row(location_raw=location), PROFILE).state == state


def test_load_profile(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text('skills = ["PYTHON", "Python"]\n[roles]\nia = ["Ingénieur IA"]\n', encoding="utf-8")
    p = load_profile(path)
    assert p.skills == ("python",)
    assert p.roles["ia"] == ("ingenieur ia",)


@pytest.mark.parametrize("content", ["min_score = true\n[roles]\nia = ['a']",
                                     "[roles]\nia = []", "unexpected = true", "[roles]\nia = [' ']"])
def test_invalid_profile(tmp_path, content):
    path = tmp_path / "profile.toml"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_profile(path)


def test_rank_all_and_deduplicate(tmp_path):
    now = datetime.now(UTC)
    def job(id, source, title="Alternance Ingénieur IA", location="Marseille"):
        return Job(id, id, source, "board", "Thales", title, location,
                   "Python Linux RAG SQL LLM", "https://example.test/job/123", None,
                   now, now, {})
    with JobStore(tmp_path / "jobs.db") as store:
        store.upsert_jobs([job("a", "lever"), job("b", "greenhouse"),
                           job("c", "ashby", location="Paris")])
        results = rank_jobs(store, PROFILE)
        assert len(results) == 2
        assert results[0].duplicate_ids == ("b",)
        assert len(rank_jobs(store, PROFILE, deduplicate=False)) == 3
        assert len(store.list_jobs()) == 3


def test_cli_match_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "profile.toml"
    path.write_text("[roles]\nia = ['ai engineer']\n")
    assert main(["match", "--profile", str(path), "--stats"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0
