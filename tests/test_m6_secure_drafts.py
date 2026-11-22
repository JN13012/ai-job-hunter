"""M6 : assertions sur le dossier factuel et l'IA optionnelle hors réseau."""

from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
import pytest

from ai_job_hunter.application_drafts import prepare_job, compose_draft
from ai_job_hunter.ai_suggestions import TokenBudget, propose_rewrite
from ai_job_hunter.candidate import load_candidate
from ai_job_hunter.cli import main
from ai_job_hunter.matcher import Profile
from ai_job_hunter.models import Job
from ai_job_hunter.storage import JobStore


PROFILE = Profile({"ai": ("ai engineer",)}, ("python", "linux"), ("marseille",),
                  (), (), (), 60)
PROFILE_TEXT = "skills=['python','linux']\npreferred_locations=['marseille']\n[roles]\nai=['ai engineer']\n"
CANDIDATE_TEXT = """
[identity]
full_name = "Test Candidate"
email = "test.candidate@example.org"
city = "Marseille"
headline = "Étudiant en cybersécurité et IA"
[facts]
skills = ["Python", "Linux"]
learning = ["Rust"]
experiences = ["Projet Python local"]
education = ["MSc en cours"]
certifications = ["Certificat obtenu"]
projects = ["Projet IA"]
languages = ["Français"]
[application]
availability = "2027"
alternance_rhythm = "4j/1j"
motivation = "Je veux contribuer à des projets utiles."
[answers]
work_authorization = "À confirmer avec le recruteur"
"""


@pytest.fixture
def local(tmp_path):
    candidate_file = tmp_path / "candidate.toml"
    candidate_file.write_text(CANDIDATE_TEXT, encoding="utf-8")
    with JobStore(tmp_path / "jobs.sqlite3") as store:
        now = datetime.now(UTC)
        store.upsert_jobs([Job(
            id="a" * 64, external_id="example", source="lever", board_token="example",
            company="Example Org", title="Alternance AI Engineer",
            location_raw="Marseille", description="Python Linux Rust",
            application_url="https://jobs.example.org/apply/abc", updated_at=None,
            first_seen=now, last_seen=now, provenance={},
        )])
        yield store, candidate_file


def test_prepare_local_files_and_labels(local, tmp_path):
    store, candidate_file = local
    candidate = load_candidate(candidate_file)
    result = prepare_job(
        store, PROFILE, candidate, "a" * 64, output_dir=tmp_path / "private"
    )
    folder = Path(result["directory"])
    cv = (folder / "cv.md").read_text()
    letter = (folder / "letter.md").read_text()
    manifest = json.loads((folder / "manifest.json").read_text())
    answers = json.loads((folder / "answers.json").read_text())
    assert "Compétences en cours d'acquisition (non acquises)" in cv
    assert "Rust" in cv
    assert "Rust" in letter and "en cours d'apprentissage" in letter
    assert "OSCP" not in cv and "OSCP" not in letter
    assert "Python" in letter
    assert answers["work_authorization"] == "À confirmer avec le recruteur"
    assert result["status"] == "draft_requires_human_review"
    assert manifest["automation_authorized"] is False
    assert manifest["human_final_submit_required"] is True
    assert manifest["ready_for_submission"] is False
    assert sorted(p.name for p in folder.iterdir()) == ["answers.json", "cv.md", "letter.md", "manifest.json"]
    with pytest.raises(ValueError, match="déjà présent"):
        prepare_job(store, PROFILE, candidate, "a" * 64, output_dir=tmp_path / "private")


def test_no_candidate_or_invalid_id(local, tmp_path):
    store, file = local
    candidate = load_candidate(file)
    with pytest.raises(ValueError):
        prepare_job(store, PROFILE, candidate, "../escape", output_dir=tmp_path)
    with pytest.raises(ValueError):
        prepare_job(store, PROFILE, candidate, "missing", output_dir=tmp_path)
    file.write_text(CANDIDATE_TEXT.replace("test.candidate@example.org", "not-an-email"))
    with pytest.raises(ValueError):
        load_candidate(file)


def test_no_diploma_invention_from_job(local, tmp_path):
    store, file = local
    candidate = load_candidate(file)
    from ai_job_hunter.channel_routing import route_jobs
    route = route_jobs(store, PROFILE)[0]
    result = compose_draft(candidate, route, "OSCP requise, Python souhaité.")
    assert "OSCP" not in result["cv_markdown"]
    assert "OSCP" not in result["letter_markdown"]
    assert "Python" in result["declared_relevant_skills"]


def test_budget_off_and_no_network(tmp_path):
    budget = TokenBudget(1)
    with pytest.raises(ValueError, match="Budget"):
        propose_rewrite(
            "Un brouillon", "Python", "AI Engineer", model="local-model",
            budget=budget, cache_dir=tmp_path / "cache"
        )
    assert budget.reserved == 0
    assert not (tmp_path / "cache").exists()


def test_ollama_is_local_cached_and_not_autopromoted(tmp_path):
    seen = []
    def handler(request):
        assert str(request.url) == "http://127.0.0.1:11434/api/generate"
        payload = json.loads(request.content)
        assert payload["stream"] is False
        seen.append(payload)
        return httpx.Response(200, json={"response": "Proposition à relire."})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        budget = TokenBudget(10000)
        args = dict(letter="Expérience Python", candidate_facts="Python",
                    job_text="Alternance AI Engineer",
                    model="local-model", cache_dir=tmp_path / "cache", client=client)
        suggestion = propose_rewrite(**args, budget=budget)
        assert suggestion.cached is False and suggestion.reserved_tokens > 0
        second = propose_rewrite(**args, budget=TokenBudget(1))
    assert second.cached is True
    assert second.text == "Proposition à relire."
    assert len(seen) == 1


def test_cli_draft(tmp_path, local, monkeypatch, capsys):
    store, candidate_file = local
    monkeypatch.chdir(tmp_path)
    with JobStore(tmp_path / ".ajh" / "jobs.sqlite3") as target:
        target.upsert_jobs([Job(
            id="a" * 64, external_id="id", source="lever", board_token="example",
            company="Example Org", title="Alternance AI Engineer",
            location_raw="Marseille", description="Python Linux Rust",
            application_url="https://jobs.example.org/apply/abc", updated_at=None,
            first_seen=datetime.now(UTC), last_seen=datetime.now(UTC), provenance={},
        )])
    path = tmp_path / "profile.toml"
    path.write_text(PROFILE_TEXT)
    assert main(["prepare", "--profile", str(path), "--candidate",
                 str(candidate_file), "--job-id", "a" * 64]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["status"] == "draft_requires_human_review"
    assert Path(manifest["directory"]).is_dir()
