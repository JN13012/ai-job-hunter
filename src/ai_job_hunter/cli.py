"""Point d'entrée local et chaîne déterministe M1."""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
import tempfile
import json
import sqlite3

import httpx

from ai_job_hunter.collection import collect_source
from ai_job_hunter.config import ConfigError, SOURCES, load_config
from ai_job_hunter.storage import JobStore


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


def _positive_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("limite entière attendue") from None
    if not 1 <= limit <= 1000:
        raise argparse.ArgumentTypeError("limite attendue entre 1 et 1000")
    return limit


def _run_jobs_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        if args.command == "collect":
            settings = getattr(config, args.source)
            if not settings.boards:
                print(f"[FAIL] Aucun board configuré dans [[{args.source}.boards]].")
                return 1
        with JobStore(config.database_path) as store:
            if args.command == "collect":
                with httpx.Client(
                    timeout=settings.timeout_seconds, follow_redirects=False,
                    headers={"User-Agent": "AI-Job-Hunter/0.1 (public Job Board API)",
                             "Accept": "application/json"},
                ) as client:
                    summary = collect_source(args.source, settings, store, client)
                print(
                    f"Reçues : {summary.received} | Nouvelles : {summary.new} | "
                    f"Mises à jour : {summary.updated} | Inchangées : {summary.unchanged} | "
                    f"Doublons : {summary.duplicates} | Erreurs : {len(summary.errors)}"
                )
                for error in summary.errors:
                    print(f"[FAIL] {error}")
                return 1 if summary.errors else 0
            if args.jobs_command == "list":
                # JSON Lines : valeurs null explicites, caractères de contrôle échappés.
                for row in store.list_jobs(args.limit):
                    print(json.dumps(dict(row), ensure_ascii=False))
            else:
                rows = [dict(row) for row in store.stats()]
                print(json.dumps({"total": sum(row["count"] for row in rows), "boards": rows}, ensure_ascii=False))
    except (ConfigError, OSError, sqlite3.Error) as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0


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
    collect = commands.add_parser("collect", help="collecter les boards explicitement configurés")
    collect.add_argument("--source", choices=SOURCES, required=True)
    jobs = commands.add_parser("jobs", help="consulter les offres locales")
    job_commands = jobs.add_subparsers(dest="jobs_command", required=True)
    listing = job_commands.add_parser("list", help="offres en JSON Lines (20 par défaut)")
    listing.add_argument("--limit", type=_positive_limit, default=20)
    job_commands.add_parser("stats", help="compter les offres par source et board")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.config)
    if args.command in {"collect", "jobs"}:
        return _run_jobs_command(args)
    parser.print_help()
    return 0
