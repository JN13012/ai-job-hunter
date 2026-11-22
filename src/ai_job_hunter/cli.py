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
from ai_job_hunter.company_discovery import load_searches, discover, company_rows, company_stats
from ai_job_hunter.contacts import import_contacts, list_contacts, contact_stats, change_contact
from ai_job_hunter.catalog_channels import qualify_catalog, register_catalog_forms
from ai_job_hunter.contact_discovery import scan_catalog
from ai_job_hunter.channel_routing import route_jobs, route_companies
from ai_job_hunter.candidate import load_candidate
from ai_job_hunter.application_drafts import prepare_job
from ai_job_hunter.ai_suggestions import TokenBudget, propose_rewrite
from ai_job_hunter.application_queue import ApplicationQueue
from ai_job_hunter.browser_preview import BrowserPolicy, preview_one, CleanupUncertain
from ai_job_hunter.form_adapters import AdapterCatalog
from ai_job_hunter.form_filling import fill_one
from ai_job_hunter.attachment_documents import build_bundle, read_bundle
from ai_job_hunter.submission_tracking import SubmissionTracker


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
        if args.command == "submission":
            with JobStore(config.database_path) as store:
                tracker = SubmissionTracker(ApplicationQueue(store, config.data_dir))
                cmd = args.submission_command
                if cmd == "handoff":
                    result = tracker.handoff(args.id, BrowserPolicy.load(args.policy))
                elif cmd == "clicked":
                    result = tracker.clicked(args.id)
                elif cmd == "confirm":
                    result = tracker.confirm(
                        args.id, evidence=args.evidence,
                        evidence_source=args.source,
                        reference=args.reference,
                        reviewed=args.reviewed,
                    )
                elif cmd == "abandon":
                    result = tracker.abandon(args.id)
                elif cmd == "status":
                    result = tracker.get(args.id)
                elif cmd == "list":
                    result = tracker.list(args.limit)
                elif cmd == "stats":
                    result = tracker.stats()
                elif cmd == "events":
                    result = tracker.events(args.id, args.limit)
                else:
                    raise ValueError("Commande de soumission inconnue.")
                if isinstance(result, list):
                    for record in result:
                        print(json.dumps(record, ensure_ascii=False))
                else:
                    print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "documents":
            root = (config.data_dir / "drafts").resolve()
            folder = args.draft
            if (folder.is_symlink() or folder.resolve().parent != root
                    or not folder.is_dir()):
                raise ValueError("Seuls les dossiers M6 directs sous .ajh/drafts sont acceptés.")
            if args.documents_command == "build":
                # Do not modify a draft already queued: its SHA-256 is pinned
                # by M7.1, and this command must not bypass reservation safety.
                with JobStore(config.database_path) as store:
                    queue = ApplicationQueue(store, config.data_dir)
                    if any(row["draft_name"] == folder.name for row in queue.list(limit=1000000)):
                        raise ValueError("Dossier déjà en file M7.1 : préparer un nouveau dossier.")
                bundle = build_bundle(folder)
            else:
                bundle = read_bundle(folder, required=True)
            print(json.dumps({
                "status": "draft_attachments_unsubmitted",
                "documents": {
                    kind: {"file": entry.path.name, "sha256": entry.sha256,
                           "bytes": entry.size}
                    for kind, entry in bundle.items()
                },
                "review_required": True,
            }, ensure_ascii=False))
            return 0
        if args.command == "fill":
            policy = BrowserPolicy.load(args.policy)
            adapters = AdapterCatalog.load(args.adapters)
            candidate = load_candidate(args.candidate)
            with JobStore(config.database_path) as store:
                queue = ApplicationQueue(store, config.data_dir)
                result = fill_one(
                    queue, policy, adapters, candidate, max_seconds=args.max_seconds,
                )
                print(json.dumps({
                    "status": result.status, "application_id": result.application_id,
                    "filled_fields": result.filled_fields, "reason": result.reason,
                }, ensure_ascii=False))
                return 0 if result.status in ("queued", "cancelled", "empty") else 1
        if args.command == "preview":
            policy = BrowserPolicy.load(args.policy)
            with JobStore(config.database_path) as store:
                queue = ApplicationQueue(store, config.data_dir)
                result = preview_one(
                    queue, policy, max_seconds=args.max_seconds,
                )
                print(json.dumps({
                    "status": result.status, "application_id": result.application_id,
                    "reason": result.reason,
                }, ensure_ascii=False))
                return 0 if result.status in ("queued", "cancelled", "empty") else 1
        if args.command == "queue":
            with JobStore(config.database_path) as store:
                queue = ApplicationQueue(store, config.data_dir)
                if args.queue_command == "add":
                    result = queue.enqueue(args.draft)
                elif args.queue_command == "list":
                    result = queue.list(args.limit)
                elif args.queue_command == "stats":
                    result = queue.stats()
                elif args.queue_command == "events":
                    result = queue.events(args.id, args.limit)
                elif args.queue_command == "cancel":
                    result = queue.cancel(args.id)
                elif args.queue_command == "recover":
                    result = queue.recover(args.id)
                elif args.queue_command == "resume":
                    result = queue.resume(args.id)
                else:
                    raise ValueError("Commande de file non reconnue.")
                if isinstance(result, list):
                    for item in result:
                        print(json.dumps(item, ensure_ascii=False))
                else:
                    print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "prepare":
            with JobStore(config.database_path) as store:
                candidate = load_candidate(args.candidate)
                profile = load_profile(args.profile)
                catalog = load_catalog(args.catalog) if args.catalog else ()
                result = prepare_job(
                    store, profile, candidate, args.job_id,
                    output_dir=config.data_dir / "drafts", catalog=catalog,
                    include_review=args.include_review,
                )
                print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "rewrite":
            # Input is a factual letter already generated by M6, not an ATS page.
            # The response remains in a separate file, never overwrites letter.md.
            base = (config.data_dir / "drafts").resolve()
            directory = args.draft.resolve()
            if directory.parent != base or directory.is_symlink():
                raise ValueError("Le brouillon doit être un dossier direct sous .ajh/drafts.")
            original = directory / "letter.md"
            if original.is_symlink() or not original.is_file():
                raise ValueError("Brouillon invalide ou lien symbolique interdit.")
            letter = original.read_text(encoding="utf-8")
            manifest_path = directory / "manifest.json"
            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise ValueError("Manifeste invalide ou lien symbolique interdit.")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("status") != "draft_requires_human_review":
                raise ValueError("Statut du brouillon inattendu.")
            # Le texte réel contient des coordonnées. Ne transmettre à Ollama
            # que les phrases professionnelles sélectionnées, sans identité.
            safe_sentences = [
                line for line in letter.splitlines()
                if line.startswith("Parmi mes compétences")
                or line.startswith("Je suis actuellement")
                or line.startswith("Je souhaite mettre à profit")
            ]
            if not safe_sentences:
                raise ValueError("Aucun fragment professionnel sûr à reformuler.")
            minimized = "\n".join(safe_sentences)
            suggestion = propose_rewrite(
                minimized, minimized, str(manifest.get("title") or ""),
                model=args.model, budget=TokenBudget(args.token_budget),
                cache_dir=config.data_dir / "llm_cache",
            )
            destination = directory / "letter_ai_suggestion.md"
            with destination.open("x", encoding="utf-8") as stream:
                stream.write(suggestion.text + "\n")
            destination.chmod(0o600)
            print(json.dumps({
                "file": str(destination), "status": "suggestion_unverified",
                "cached": suggestion.cached,
                "estimated_reserved_tokens": suggestion.reserved_tokens,
                "original_letter_preserved": True,
            }, ensure_ascii=False))
            return 0
        if args.command == "routes":
            with JobStore(config.database_path) as store:
                if args.routes_command == "jobs":
                    profile = load_profile(args.profile)
                    targets = load_catalog(args.catalog) if args.catalog else ()
                    data = route_jobs(store, profile, catalog=targets,
                                      include_review=args.include_review)
                else:
                    data = route_companies(store)
                for item in data[:args.limit]:
                    print(json.dumps(item, ensure_ascii=False))
            return 0
        if args.command == "contacts":
            with JobStore(config.database_path) as store:
                if args.contacts_command == "discover":
                    targets = load_catalog(args.file)
                    with httpx.Client(
                        timeout=10.0, follow_redirects=False, trust_env=False,
                        headers={"User-Agent": "AI-Job-Hunter/0.1 (AI-Job-Hunter; contact discovery)",
                                 "Accept": "text/html,text/plain"},
                    ) as client:
                        report = scan_catalog(
                            store, targets, client, fetch=args.fetch,
                            register=args.register, max_pages=args.max_pages,
                        )
                    for candidate in report.candidates:
                        print(json.dumps(candidate, ensure_ascii=False))
                    for message in report.warnings:
                        print(f"[WARN] {message}", file=sys.stderr)
                    for message in report.errors:
                        print(f"[FAIL] {message}", file=sys.stderr)
                    print(
                        f"[INFO] Requêtes={report.requests}, pages={report.pages}, "
                        f"ignorées={report.skipped}, contacts={len(report.candidates)}, "
                        f"nouvelles={report.writes.new}, erreurs={len(report.errors)}",
                        file=sys.stderr,
                    )
                    return 1 if report.errors else 0
                if args.contacts_command == "catalog":
                    targets = load_catalog(args.file)
                    candidates = qualify_catalog(store, targets)
                    for candidate in candidates:
                        print(json.dumps(candidate, ensure_ascii=False))
                    if args.register:
                        counts = register_catalog_forms(store, candidates)
                        print(
                            f"[INFO] Contacts en attente — nouvelles : {counts.new}, "
                            f"mises à jour : {counts.updated}, inchangées : {counts.unchanged}",
                            file=sys.stderr,
                        )
                elif args.contacts_command == "import":
                    counts = import_contacts(args.file, store)
                    print(f"Nouvelles : {counts.new} | Mises à jour : {counts.updated} | Inchangées : {counts.unchanged}")
                elif args.contacts_command == "list":
                    for item in list_contacts(store, args.limit, args.status):
                        print(json.dumps(item, ensure_ascii=False))
                elif args.contacts_command == "stats":
                    print(json.dumps(contact_stats(store), ensure_ascii=False))
                else:
                    change_contact(store, args.id, args.contacts_command)
                    print("[OK] Statut mis à jour.")
            return 0
        if args.command == "companies":
            if args.companies_command == "discover":
                settings = load_searches(args.file)
                with JobStore(config.database_path) as store, httpx.Client(
                    timeout=settings.timeout_seconds, follow_redirects=False,
                    headers={"User-Agent": "AI-Job-Hunter/0.1 (public company search API)",
                             "Accept": "application/json"},
                ) as client:
                    report = discover(settings, store, client)
                counts = report.counts
                print(f"Requêtes : {report.requests} | Reçues : {report.received} | "
                      f"Nouvelles : {counts.new} | Mises à jour : {counts.updated} | "
                      f"Inchangées : {counts.unchanged} | Doublons : {report.duplicates} | "
                      f"Erreurs : {len(report.errors)}")
                for message in report.warnings:
                    print(f"[WARN] {message}")
                for message in report.errors:
                    print(f"[FAIL] {message}")
                return 1 if report.errors else 0
            linked_catalog = load_catalog(args.catalog) if args.catalog else ()
            with JobStore(config.database_path) as store:
                if args.companies_command == "stats":
                    print(json.dumps(company_stats(store, catalog=linked_catalog), ensure_ascii=False))
                else:
                    from itertools import islice
                    for item in islice(company_rows(store, catalog=linked_catalog), args.limit):
                        print(json.dumps(item, ensure_ascii=False))
            return 0
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
    companies = commands.add_parser("companies", help="découvrir et qualifier les entreprises françaises")
    company_commands = companies.add_subparsers(dest="companies_command", required=True)
    company_discover = company_commands.add_parser("discover", help="rechercher via l’API publique Recherche d’Entreprises")
    company_discover.add_argument("--file", type=Path, required=True, help="requêtes de recherche TOML")
    company_list = company_commands.add_parser("list", help="cibles légales en JSON Lines")
    company_list.add_argument("--limit", type=_positive_limit, default=20)
    company_list.add_argument("--catalog", type=Path, help="lier les entreprises par SIREN déclaré")
    company_stats_parser = company_commands.add_parser("stats", help="bilan des entreprises découvertes")
    company_stats_parser.add_argument("--catalog", type=Path, help="lier les entreprises par SIREN déclaré")
    contacts = commands.add_parser("contacts", help="canaux de recrutement sourcés ; aucun envoi")
    contact_commands = contacts.add_subparsers(dest="contacts_command", required=True)
    contact_import = contact_commands.add_parser("import", help="charger des contacts publics fournis par l'utilisateur")
    contact_import.add_argument("--file", type=Path, required=True)
    contact_list = contact_commands.add_parser("list", help="lister les contacts avec leurs statuts")
    contact_list.add_argument("--limit", type=_positive_limit, default=20)
    contact_list.add_argument("--status", choices=("all", "pending", "approved", "blocked"), default="all")
    contact_commands.add_parser("stats", help="compter les canaux par statut")
    catalog_contacts = contact_commands.add_parser(
        "catalog", help="qualifier les canaux du catalogue sans requête HTTP"
    )
    catalog_contacts.add_argument("--file", type=Path, required=True, help="catalogue TOML")
    catalog_contacts.add_argument(
        "--register", action="store_true",
        help="inscrire uniquement les formulaires explicites dans pending",
    )
    discovery = contact_commands.add_parser(
        "discover", help="extraire les canaux d'une seule page carrière opt-in par société"
    )
    discovery.add_argument("--file", type=Path, required=True, help="catalogue TOML avec autorisation explicite")
    discovery.add_argument("--fetch", action="store_true", help="activer les requêtes GET autorisées")
    discovery.add_argument("--register", action="store_true", help="enregistrer uniquement comme pending")
    discovery.add_argument("--max-pages", type=_positive_limit, default=10)
    for action in ("approve", "block"):
        command = contact_commands.add_parser(action, help="vérifier manuellement ou bloquer un contact")
        command.add_argument("--id", required=True)
    routes = commands.add_parser("routes", help="classer les canaux connus sans réseau ni envoi")
    route_commands = routes.add_subparsers(dest="routes_command", required=True)
    route_job = route_commands.add_parser("jobs", help="meilleur canal pour les offres M3")
    route_job.add_argument("--profile", type=Path, required=True)
    route_job.add_argument("--catalog", type=Path, help="liaison exacte board ATS / SIREN déclaré")
    route_job.add_argument("--include-review", action="store_true", help="inclure les offres M3 à vérifier")
    route_job.add_argument("--limit", type=_positive_limit, default=20)
    route_companies_parser = route_commands.add_parser("companies", help="canaux de candidature spontanée")
    route_companies_parser.add_argument("--limit", type=_positive_limit, default=20)
    preparation = commands.add_parser("prepare", help="préparer un brouillon privé M6 pour une offre M3/M5.4")
    preparation.add_argument("--profile", type=Path, required=True)
    preparation.add_argument("--candidate", type=Path, required=True, help="profil candidat TOML privé")
    preparation.add_argument("--job-id", required=True)
    preparation.add_argument("--catalog", type=Path, help="rattachement SIREN déclaré")
    preparation.add_argument("--include-review", action="store_true")
    rewriting = commands.add_parser("rewrite", help="suggestion de lettre facultative via Ollama local")
    rewriting.add_argument("--draft", type=Path, required=True, help="dossier généré sous .ajh/drafts")
    rewriting.add_argument("--model", required=True, help="modèle Ollama local déjà installé")
    rewriting.add_argument("--token-budget", type=_positive_limit, default=1000)
    queue_parser = commands.add_parser("queue", help="file locale M7.1, sans soumission")
    queue_commands = queue_parser.add_subparsers(dest="queue_command", required=True)
    queue_add = queue_commands.add_parser("add", help="ajouter un dossier M6 non soumis")
    queue_add.add_argument("--draft", required=True, type=Path)
    queue_listing = queue_commands.add_parser("list", help="lister les candidatures en file")
    queue_listing.add_argument("--limit", type=_positive_limit, default=20)
    queue_commands.add_parser("stats", help="compter les états de la file")
    queue_events = queue_commands.add_parser("events", help="historique minimal d'une candidature")
    queue_events.add_argument("--id", required=True)
    queue_events.add_argument("--limit", type=_positive_limit, default=20)
    for operation in ("cancel", "recover", "resume"):
        cmd = queue_commands.add_parser(operation, help="transition explicite de la file")
        cmd.add_argument("--id", required=True)
    preview_parser = commands.add_parser(
        "preview", help="ouvrir une seule candidature autorisée en lecture seule avec Playwright"
    )
    preview_parser.add_argument("--policy", type=Path, required=True)
    preview_parser.add_argument("--max-seconds", type=int, default=180)
    fill_parser = commands.add_parser(
        "fill", help="remplir des champs explicitement déclarés, sans soumettre"
    )
    fill_parser.add_argument("--policy", type=Path, required=True)
    fill_parser.add_argument("--adapters", type=Path, required=True)
    fill_parser.add_argument("--candidate", type=Path, required=True)
    fill_parser.add_argument("--max-seconds", type=int, default=120)
    documents_parser = commands.add_parser(
        "documents", help="générer ou vérifier les PDF locaux, jamais les soumettre"
    )
    documents_commands = documents_parser.add_subparsers(
        dest="documents_command", required=True
    )
    for action in ("build", "verify"):
        doc_command = documents_commands.add_parser(action)
        doc_command.add_argument("--draft", type=Path, required=True)
    submission = commands.add_parser(
        "submission", help="transfert manuel et suivi de confirmations locales ; aucun envoi"
    )
    submission_commands = submission.add_subparsers(dest="submission_command", required=True)
    handoff = submission_commands.add_parser("handoff", help="récupérer l'URL pour action manuelle")
    handoff.add_argument("--id", required=True)
    handoff.add_argument("--policy", type=Path, required=True)
    clicked = submission_commands.add_parser("clicked", help="déclarer un clic humain (non confirmé)")
    clicked.add_argument("--id", required=True)
    confirm = submission_commands.add_parser("confirm", help="enregistrer une preuve revue humainement")
    confirm.add_argument("--id", required=True)
    confirm.add_argument("--evidence", type=Path, required=True)
    confirm.add_argument("--source", choices=("confirmation_page", "confirmation_email"), required=True)
    confirm.add_argument("--reference", required=True)
    confirm.add_argument("--reviewed", action="store_true", help="attester la revue humaine explicite de la preuve")
    abandon = submission_commands.add_parser("abandon", help="abandonner un transfert sans clic déclaré")
    abandon.add_argument("--id", required=True)
    status = submission_commands.add_parser("status", help="état d'une candidature suivie")
    status.add_argument("--id", required=True)
    listing = submission_commands.add_parser("list", help="lister les transferts humains")
    listing.add_argument("--limit", type=_positive_limit, default=20)
    submission_commands.add_parser("stats", help="compter les statuts de suivi")
    events = submission_commands.add_parser("events", help="événements minimaux, sans données personnelles")
    events.add_argument("--id", required=True)
    events.add_argument("--limit", type=_positive_limit, default=20)
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.config)
    if args.command in {"collect", "jobs", "recruiters", "catalog", "import", "match", "companies", "contacts", "routes", "prepare", "rewrite", "queue", "preview", "fill", "documents", "submission"}:
        return _run_jobs_command(args)
    parser.print_help()
    return 0
