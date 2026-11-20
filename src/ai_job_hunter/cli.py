"""Point d'entrée local et chaîne déterministe M1."""

import argparse
from dataclasses import asdict
from collections.abc import Sequence
from pathlib import Path
import sys
import os
import tempfile
import json
import sqlite3

import httpx

from ai_job_hunter.collection import collect_source
from ai_job_hunter.config import ConfigError, SOURCES, load_config
from ai_job_hunter.storage import JobStore
from ai_job_hunter.lba import collect_lba, list_recruiters, count_recruiters
from ai_job_hunter.catalog import load_catalog, catalog_settings
from ai_job_hunter.imports import import_jobs
from ai_job_hunter.matcher import load_profile, rank_jobs, match_dict, STATES


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
        if args.command == "catalog":
            for target in load_catalog(args.file):
                print(json.dumps(asdict(target), ensure_ascii=False))
            return 0
        if args.command == "collect":
            settings = getattr(config, args.source)
            if args.catalog is not None:
                settings = catalog_settings(load_catalog(args.catalog), args.source, settings)
            if args.source == "lba":
                if not settings.romes or not settings.departements:
                    print("[FAIL] Configurer lba.romes et lba.departements.")
                    return 1
                key = os.environ.get("AJH_LBA_API_KEY", "").strip()
                if not key or any(ch.isspace() for ch in key):
                    print("[FAIL] Variable AJH_LBA_API_KEY absente ou invalide.")
                    return 1
            elif not settings.boards:
                print(f"[FAIL] Aucun board configuré dans [[{args.source}.boards]].")
                return 1
        if args.command == "match":
            profile = load_profile(args.profile)
        with JobStore(config.database_path) as store:
            if args.command == "match":
                ranked = rank_jobs(store, profile, deduplicate=not args.no_dedup)
                if args.stats:
                    counts = {state: sum(m.state == state for m in ranked) for state in STATES}
                    print(json.dumps({"total": len(ranked), "states": counts,
                                      "duplicates_grouped": sum(len(m.duplicate_ids) for m in ranked)}, ensure_ascii=False))
                else:
                    selected = (m for m in ranked if args.state == "all" or m.state == args.state)
                    from itertools import islice
                    for match in islice(selected, args.limit):
                        print(json.dumps(match_dict(match), ensure_ascii=False))
                return 0
            if args.command == "import":
                result = import_jobs(args.file, store)
                counts = result.counts
                print(f"Reçues : {result.received} | Nouvelles : {counts.new} | "
                      f"Mises à jour : {counts.updated} | Inchangées : {counts.unchanged} | "
                      f"Doublons : {result.duplicates} | Erreurs : {len(result.errors)}")
                for error in result.errors:
                    print(f"[FAIL] {error}")
                return 1 if result.errors else 0
            if args.command == "collect":
                with httpx.Client(
                    timeout=settings.timeout_seconds, follow_redirects=False,
                    headers={"User-Agent": "AI-Job-Hunter/0.1 (public Job Board API)",
                             "Accept": "application/json"},
                ) as client:
                    if args.source == "lba":
                        result = collect_lba(settings, store, client, key)
                    else:
                        summary = collect_source(args.source, settings, store, client)
                if args.source == "lba":
                    for label, received, counts in (
                        ("Offres", result.received_jobs, result.jobs),
                        ("Entreprises à potentiel", result.received_recruiters, result.recruiters),
                    ):
                        print(f"{label} : {received} reçues | {counts.new} nouvelles | "
                              f"{counts.updated} mises à jour | {counts.unchanged} inchangées")
                    print(f"Doublons : {result.duplicates} | Erreurs : {len(result.errors)}")
                    for warning in result.warnings:
                        print(f"[WARN] {warning}")
                    for error in result.errors:
                        print(f"[FAIL] {error}")
                    return 1 if result.errors else 0
                print(
                    f"Reçues : {summary.received} | Nouvelles : {summary.new} | "
                    f"Mises à jour : {summary.updated} | Inchangées : {summary.unchanged} | "
                    f"Doublons : {summary.duplicates} | Erreurs : {len(summary.errors)}"
                )
                for error in summary.errors:
                    print(f"[FAIL] {error}")
                return 1 if summary.errors else 0
            if args.command == "recruiters":
                if args.recruiters_command == "list":
                    for row in list_recruiters(store, args.limit):
                        print(json.dumps(dict(row), ensure_ascii=False))
                else:
                    print(json.dumps({"total": count_recruiters(store), "source": "lba"}))
                return 0
            if args.jobs_command == "list":
                # JSON Lines : valeurs null explicites, caractères de contrôle échappés.
                for row in store.list_jobs(args.limit):
                    print(json.dumps(dict(row), ensure_ascii=False))
            else:
                rows = [dict(row) for row in store.stats()]
                print(json.dumps({"total": sum(row["count"] for row in rows), "boards": rows}, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error) as exc:
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
    collect.add_argument("--catalog", type=Path, metavar="FICHIER", help="utiliser les boards du catalogue pour cette source")
    catalog = commands.add_parser("catalog", help="consulter un catalogue local de sociétés")
    catalog.add_argument("--file", type=Path, required=True)
    importer = commands.add_parser("import", help="importer des annonces sélectionnées depuis un fichier JSON")
    importer.add_argument("--file", type=Path, required=True)
    jobs = commands.add_parser("jobs", help="consulter les offres locales")
    job_commands = jobs.add_subparsers(dest="jobs_command", required=True)
    listing = job_commands.add_parser("list", help="offres en JSON Lines (20 par défaut)")
    listing.add_argument("--limit", type=_positive_limit, default=20)
    job_commands.add_parser("stats", help="compter les offres par source et board")
    recruiters = commands.add_parser("recruiters", help="entreprises LBA à potentiel, pas des offres")
    recruiter_commands = recruiters.add_subparsers(dest="recruiters_command", required=True)
    recruiter_list = recruiter_commands.add_parser("list", help="entreprises en JSON Lines")
    recruiter_list.add_argument("--limit", type=_positive_limit, default=20)
    recruiter_commands.add_parser("stats", help="compter les entreprises à potentiel")
    matching = commands.add_parser("match", help="classer les offres avec un profil TOML local")
    matching.add_argument("--profile", type=Path, required=True)
    matching.add_argument("--state", choices=(*STATES, "all"), default="match")
    matching.add_argument("--limit", type=_positive_limit, default=20)
    matching.add_argument("--stats", action="store_true", help="bilan de toutes les catégories")
    matching.add_argument("--no-dedup", action="store_true", help="ne pas regrouper les doublons intersources")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.config)
    if args.command in {"collect", "jobs", "recruiters", "catalog", "import", "match"}:
        return _run_jobs_command(args)
    parser.print_help()
    return 0
