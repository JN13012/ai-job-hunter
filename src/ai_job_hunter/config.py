"""Configuration TOML locale, sans dépendance externe."""

from dataclasses import dataclass
from pathlib import Path
import tomllib


class ConfigError(ValueError):
    """Configuration absente explicitement demandée, illisible ou invalide."""


@dataclass(frozen=True)
class Config:
    data_dir: Path
    source: Path | None


def load_config(path: Path | None = None) -> Config:
    """Charger ajh.toml dans le dossier courant, ou un fichier explicite.

    Seule l'absence du fichier implicite autorise les valeurs par défaut.
    Les chemins relatifs sont ancrés au dossier du fichier de configuration.
    """
    try:
        source = (path if path is not None else Path("ajh.toml")).expanduser().resolve()
        try:
            with source.open("rb") as stream:
                raw = tomllib.load(stream)
        except FileNotFoundError:
            if path is not None:
                raise ConfigError(f"Fichier introuvable : {source}") from None
            return Config(data_dir=source.parent / ".ajh", source=None)

        if raw.keys() - {"paths"}:
            raise ConfigError("Sections inconnues : seules les options [paths] sont acceptées.")
        paths = raw.get("paths", {})
        if not isinstance(paths, dict):
            raise ConfigError("paths doit être une table TOML [paths].")
        if paths.keys() - {"data_dir"}:
            raise ConfigError("Option inconnue dans [paths] : seule data_dir est acceptée.")
        value = paths.get("data_dir", ".ajh")
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ConfigError("paths.data_dir doit être une chaîne non vide sans caractère nul.")
        data_dir = Path(value).expanduser()
        if not data_dir.is_absolute():
            data_dir = source.parent / data_dir
        return Config(data_dir=data_dir.resolve(), source=source)
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"Configuration illisible ou invalide : {exc}") from exc
