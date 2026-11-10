import pytest

from ai_job_hunter.config import ConfigError, load_config


def test_defaults_without_local_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.source is None
    assert config.data_dir == tmp_path / ".ajh"
    assert not config.data_dir.exists()


def test_implicit_local_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "ajh.toml"
    source.write_text('[paths]\ndata_dir = "local-data"\n', encoding="utf-8")
    config = load_config()
    assert config.source == source
    assert config.data_dir == tmp_path / "local-data"


def test_relative_path_uses_config_directory(tmp_path, monkeypatch):
    source = tmp_path / "configuration" / "custom.toml"
    source.parent.mkdir()
    source.write_text('[paths]\ndata_dir = "données candidat"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = load_config(source)
    assert config.source == source
    assert config.data_dir == source.parent / "données candidat"


def test_absolute_data_directory(tmp_path):
    source = tmp_path / "custom.toml"
    destination = tmp_path / "absolute-data"
    source.write_text(f"[paths]\ndata_dir = '{destination}'\n", encoding="utf-8")
    assert load_config(source).data_dir == destination


def test_empty_config_uses_default_directory(tmp_path):
    source = tmp_path / "custom.toml"
    source.write_text("", encoding="utf-8")
    assert load_config(source).data_dir == tmp_path / ".ajh"


def test_explicit_missing_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="introuvable"):
        load_config(tmp_path / "missing.toml")


@pytest.mark.parametrize("contents", [
    "[paths",  # Syntaxe TOML invalide.
    '[paths]\ndata_dir = "',
    "[path]\n",  # Faute de frappe dans une section.
    '[paths]\ndata_path = "data"\n',
    'paths = "data"\n',
    "[paths]\ndata_dir = 42\n",
    '[paths]\ndata_dir = ""\n',
    '[paths]\ndata_dir = "  "\n',
    '[paths]\ndata_dir = "\\u0000"\n',
])
def test_invalid_configuration_is_rejected(tmp_path, contents):
    source = tmp_path / "invalid.toml"
    source.write_text(contents, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(source)


def test_invalid_utf8_is_reported_as_config_error(tmp_path):
    source = tmp_path / "invalid.toml"
    source.write_bytes(b"\xff")
    with pytest.raises(ConfigError):
        load_config(source)


def test_directory_is_not_a_config_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path)
