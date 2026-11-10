import json

import httpx
import pytest

from ai_job_hunter.cli import main
from ai_job_hunter.storage import JobStore


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ajh.toml").write_text('''[[greenhouse.boards]]
company = "Entreprise de test"
board_token = "fixture-board"
''', encoding="utf-8")
    return tmp_path


def mock_client(monkeypatch, handler):
    original_client = httpx.Client
    parameters = []

    def factory(**kwargs):
        parameters.append(kwargs)
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("ai_job_hunter.cli.httpx.Client", factory)
    return parameters


def test_collect_list_and_stats_cli(configured, monkeypatch, capsys, greenhouse_payload):
    parameters = mock_client(monkeypatch, lambda request: httpx.Response(200, json=greenhouse_payload))
    assert main(["collect", "--source", "greenhouse"]) == 0
    output = capsys.readouterr().out
    assert "Reçues : 2 | Nouvelles : 2 | Mises à jour : 0 | Inchangées : 0 | Doublons : 0 | Erreurs : 0" in output
    assert parameters[0]["timeout"] == 15
    assert parameters[0]["follow_redirects"] is False
    assert main(["collect", "--source", "greenhouse"]) == 0
    assert "Inchangées : 2" in capsys.readouterr().out
    assert main(["jobs", "list", "--limit", "1"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 1
    assert rows[0]["source"] == "greenhouse"
    assert main(["jobs", "stats"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["total"] == 2
    assert stats["boards"][0]["board_token"] == "fixture-board"


def test_missing_boards_does_not_make_request_or_database(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    mock_client(monkeypatch, lambda request: pytest.fail("No request expected"))
    assert main(["collect", "--source", "greenhouse"]) == 1
    assert "Aucun board" in capsys.readouterr().out
    assert not (tmp_path / ".ajh").exists()


def test_empty_jobs_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["jobs", "list"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["jobs", "stats"]) == 0
    assert json.loads(capsys.readouterr().out) == {"total": 0, "boards": []}


def test_network_error_reports_failure(configured, monkeypatch, capsys):
    mock_client(monkeypatch, lambda request: httpx.Response(500))
    assert main(["collect", "--source", "greenhouse"]) == 1
    output = capsys.readouterr().out
    assert "Erreurs : 1" in output
    assert "HTTP 500" in output
    with JobStore(configured / ".ajh" / "jobs.sqlite3") as store:
        assert store.list_jobs() == []


def test_partial_response_reports_failure_but_saves_valid(configured, monkeypatch, capsys):
    mock_client(monkeypatch, lambda request: httpx.Response(200, json={"jobs": [{"id": 1}, {}]}))
    assert main(["collect", "--source", "greenhouse"]) == 1
    assert "Nouvelles : 1" in capsys.readouterr().out
    with JobStore(configured / ".ajh" / "jobs.sqlite3") as store:
        assert len(store.list_jobs()) == 1


def test_invalid_database_is_reported_without_traceback(configured, capsys):
    directory = configured / ".ajh"
    directory.mkdir()
    (directory / "jobs.sqlite3").write_bytes(b"not sqlite")
    assert main(["jobs", "stats"]) == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_jobs_with_missing_explicit_config(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.toml"), "jobs", "list"]) == 1
    assert "introuvable" in capsys.readouterr().out


@pytest.mark.parametrize("arguments", [
    ["jobs"], ["jobs", "list", "--limit", "0"],
    ["jobs", "list", "--limit", "1001"], ["jobs", "list", "--limit", "abc"],
    ["collect", "--source", "other"],
])
def test_invalid_command_arguments(arguments):
    with pytest.raises(SystemExit) as exc:
        main(arguments)
    assert exc.value.code == 2
