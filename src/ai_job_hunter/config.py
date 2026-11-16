"""Configuration TOML locale, sans dépendance externe."""

from dataclasses import dataclass
from pathlib import Path
import math
import re
import tomllib


class ConfigError(ValueError):
    """Configuration absente explicitement demandée, illisible ou invalide."""


@dataclass(frozen=True)
class GreenhouseBoard:
    company: str
    board_token: str


@dataclass(frozen=True)
class GreenhouseConfig:
    boards: tuple[GreenhouseBoard, ...] = ()
    timeout_seconds: float = 15.0
    request_interval_seconds: float = 1.0


BOARD_SOURCES = ("greenhouse", "lever", "lever_eu", "ashby", "smartrecruiters")
SOURCES = (*BOARD_SOURCES, "lba")


def _greenhouse_config(raw: object, source: str = "greenhouse") -> GreenhouseConfig:
    if not isinstance(raw, dict) or raw.keys() - {
        "boards", "timeout_seconds", "request_interval_seconds"
    }:
        raise ConfigError(f"Configuration [{source}] invalide ou option inconnue.")
    boards = raw.get("boards", [])
    if not isinstance(boards, list) or len(boards) > 10:
        raise ConfigError(f"{source}.boards doit contenir au maximum 10 boards.")
    parsed = []
    tokens = set()
    for board in boards:
        if not isinstance(board, dict) or board.keys() != {"company", "board_token"}:
            raise ConfigError("Chaque board exige uniquement company et board_token.")
        company, token = board["company"], board["board_token"]
        if not isinstance(company, str) or not company.strip():
            raise ConfigError("company doit être une chaîne non vide.")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", token):
            raise ConfigError("board_token invalide : utiliser le token officiel, sans URL.")
        if token.casefold() in tokens:
            raise ConfigError(f"Board dupliqué : {token}")
        tokens.add(token.casefold())
        parsed.append(GreenhouseBoard(company.strip(), token))
    values = {}
    for key, default, minimum, maximum in (
        ("timeout_seconds", 15.0, 1.0, 60.0),
        ("request_interval_seconds", 1.0, 1.0, 60.0),
    ):
        value = raw.get(key, default)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not minimum <= value <= maximum or not math.isfinite(value)):
            raise ConfigError(f"{source}.{key} doit être entre {minimum} et {maximum}.")
        values[key] = float(value)
    return GreenhouseConfig(boards=tuple(parsed), **values)


@dataclass(frozen=True)
class LBAConfig:
    romes: tuple[str, ...] = ()
    departements: tuple[str, ...] = ()
    target_diploma_level: str | None = None
    timeout_seconds: float = 20.0


def _lba_config(raw: object) -> LBAConfig:
    if not isinstance(raw, dict) or raw.keys() - {
        "romes", "departements", "target_diploma_level", "timeout_seconds"
    }:
        raise ConfigError("Configuration [lba] invalide ou option inconnue.")
    def codes(key: str, pattern: str, maximum: int) -> tuple[str, ...]:
        values = raw.get(key, [])
        if (not isinstance(values, list) or len(values) > maximum
                or any(not isinstance(v, str) or not re.fullmatch(pattern, v) for v in values)
                or len(set(values)) != len(values)):
            raise ConfigError(f"lba.{key} : liste de codes uniques invalide.")
        return tuple(values)
    romes = codes("romes", r"[A-Z]\d{4}", 10)
    departements = codes("departements", r"(?:\d{2}|2[AB]|97[1-6])", 10)
    diploma = raw.get("target_diploma_level")
    if diploma is not None and diploma not in ("3", "4", "5", "6", "7"):
        raise ConfigError("lba.target_diploma_level doit être une chaîne de 3 à 7.")
    timeout = raw.get("timeout_seconds", 20)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 60:
        raise ConfigError("lba.timeout_seconds doit être entre 1 et 60.")
    return LBAConfig(romes, departements, diploma, float(timeout))


@dataclass(frozen=True)
class Config:
    data_dir: Path
    source: Path | None
    greenhouse: GreenhouseConfig = GreenhouseConfig()
    lever: GreenhouseConfig = GreenhouseConfig()
    lever_eu: GreenhouseConfig = GreenhouseConfig()
    ashby: GreenhouseConfig = GreenhouseConfig()
    smartrecruiters: GreenhouseConfig = GreenhouseConfig()
    lba: LBAConfig = LBAConfig()

    @property
    def database_path(self) -> Path:
        return self.data_dir / "jobs.sqlite3"


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

        if raw.keys() - {"paths", *SOURCES}:
            raise ConfigError("Sections inconnues : paths et sources déclarées uniquement.")
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
        return Config(
            data_dir=data_dir.resolve(), source=source,
            lba=_lba_config(raw.get("lba", {})),
            **{name: _greenhouse_config(raw.get(name, {}), name) for name in BOARD_SOURCES},
        )
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"Configuration illisible ou invalide : {exc}") from exc
