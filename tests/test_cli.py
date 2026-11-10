import pytest

from ai_job_hunter.cli import main


def test_help_does_not_create_data(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "doctor" in capsys.readouterr().out
    assert not (tmp_path / ".ajh").exists()


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "doctor" in capsys.readouterr().out


def test_doctor_creates_and_checks_default_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "[FAIL]" not in output
    assert "Python" in output
    assert "Configuration" in output
    assert str(tmp_path / ".ajh") in output
    assert list((tmp_path / ".ajh").iterdir()) == []
    assert main(["doctor"]) == 0  # Réexécution sans effet cumulatif.


def test_doctor_accepts_custom_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "custom.toml"
    source.write_text('[paths]\ndata_dir = "custom-data"\n', encoding="utf-8")
    assert main(["--config", str(source), "doctor"]) == 0
    assert (tmp_path / "custom-data").is_dir()
    assert str(source) in capsys.readouterr().out


def test_doctor_missing_explicit_config_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["--config", str(tmp_path / "missing.toml"), "doctor"]) == 1
    assert "[FAIL] Configuration" in capsys.readouterr().out
    assert not (tmp_path / ".ajh").exists()


def test_doctor_invalid_config_fails_without_creating_data(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ajh.toml").write_text('[paths]\ndata_dir = 42\n', encoding="utf-8")
    assert main(["doctor"]) == 1
    assert "[FAIL] Configuration" in capsys.readouterr().out
    assert not (tmp_path / ".ajh").exists()


def test_doctor_data_path_is_a_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ajh").write_text("existing data", encoding="utf-8")
    assert main(["doctor"]) == 1
    assert "[FAIL] Répertoire de données" in capsys.readouterr().out
    assert (tmp_path / ".ajh").read_text(encoding="utf-8") == "existing data"


def test_doctor_reports_write_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    def denied(*args, **kwargs):
        raise PermissionError("write denied")

    monkeypatch.setattr("ai_job_hunter.cli.tempfile.TemporaryFile", denied)
    assert main(["doctor"]) == 1
    assert "write denied" in capsys.readouterr().out


def test_doctor_rejects_unsupported_python(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ai_job_hunter.cli.sys.version_info", (3, 11, 9))
    assert main(["doctor"]) == 1
    assert "[FAIL] Python : 3.11.9" in capsys.readouterr().out


def test_unknown_command_is_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["collect"])
    assert exc.value.code == 2
    assert "collect" in capsys.readouterr().err
