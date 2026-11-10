import pytest

from ai_job_hunter.config import ConfigError, load_config


def test_configured_company_and_board(tmp_path):
    source = tmp_path / "ajh.toml"
    source.write_text('''[greenhouse]
timeout_seconds = 20
request_interval_seconds = 2
[[greenhouse.boards]]
company = "Entreprise de test"
board_token = "fixture-board"
''', encoding="utf-8")
    config = load_config(source)
    assert config.greenhouse.boards[0].company == "Entreprise de test"
    assert config.greenhouse.boards[0].board_token == "fixture-board"
    assert config.greenhouse.timeout_seconds == 20
    assert config.greenhouse.request_interval_seconds == 2
    assert config.database_path == tmp_path / ".ajh" / "jobs.sqlite3"


@pytest.mark.parametrize("contents", [
    'greenhouse = "invalid"',
    '[greenhouse]\nunknown = true',
    '[greenhouse]\nboards = "invalid"',
    '[greenhouse]\ntimeout_seconds = 0',
    '[greenhouse]\ntimeout_seconds = 61',
    '[greenhouse]\ntimeout_seconds = true',
    '[greenhouse]\ntimeout_seconds = nan',
    '[greenhouse]\nrequest_interval_seconds = 0',
    '[greenhouse]\nrequest_interval_seconds = inf',
    '[[greenhouse.boards]]\ncompany = ""\nboard_token = "fixture"',
    '[[greenhouse.boards]]\ncompany = "Test"',
    '[[greenhouse.boards]]\ncompany = 42\nboard_token = "fixture"',
    '[[greenhouse.boards]]\ncompany = "Test"\nboard_token = "../escape"',
    '[[greenhouse.boards]]\ncompany = "Test"\nboard_token = "https://example.test"',
    '[[greenhouse.boards]]\ncompany = "Test"\nboard_token = "fixture"\nextra = true',
    '[[greenhouse.boards]]\ncompany = "A"\nboard_token = "fixture"\n[[greenhouse.boards]]\ncompany = "B"\nboard_token = "FIXTURE"',
    '\n'.join('[[greenhouse.boards]]\ncompany = "Test"\nboard_token = "b%d"' % n for n in range(11)),
])
def test_invalid_greenhouse_config(tmp_path, contents):
    source = tmp_path / "invalid.toml"
    source.write_text(contents, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(source)
