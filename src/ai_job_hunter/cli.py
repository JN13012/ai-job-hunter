"""Point d'entrée local du socle M0."""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
import tempfile

from ai_job_hunter.config import ConfigError, load_config


def _doctor(config_path: Path | None) -> int:
    python_ok = sys.version_info[:2] >= (3, 12)
    version = ".".join(map(str, sys.version_info[:3]))
    print(f"[{'OK' if python_ok else 'FAIL'}] Python : {version} (minimum 3.12)")

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"[FAIL] Configuration : {exc}")
        return 1
    print(f"[OK] Configuration : {config.source or 'valeurs par défaut (aucun ajh.toml)'}")

    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=config.data_dir) as probe:
            probe.write(b"ajh-doctor\n")
            probe.flush()
    except OSError as exc:
        print(f"[FAIL] Répertoire de données : {config.data_dir} : {exc}")
        return 1
    print(f"[OK] Répertoire de données accessible en écriture : {config.data_dir}")
    return 0 if python_ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ajh",
        description="AI Job Hunter — script first, AI only when necessary.",
    )
    parser.add_argument(
        "--config", type=Path, metavar="FICHIER",
        help="fichier TOML local (par défaut : ./ajh.toml, facultatif)",
    )
    commands = parser.add_subparsers(dest="command", title="commandes")
    commands.add_parser("doctor", help="vérifier Python, la configuration et les chemins locaux")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.config)
    parser.print_help()
    return 0
