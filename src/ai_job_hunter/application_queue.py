"""M7.1 : file SQLite locale, réservations exclusives, reprise explicite.

Aucun navigateur, email, formulaire, POST ou soumission. Le worker M7.2
consommera des réservations ; seul le clic humain M7.5 soumettra un dossier.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import secrets
from typing import Any

from ai_job_hunter.channel_routing import _safe_application_url
from ai_job_hunter.storage import JobStore

ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
TOKEN = re.compile(r"[a-f0-9]{48}\Z")
DRAFT_DIR = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
WORKER = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
STATES = ("queued", "reserved", "interrupted", "cancelled", "failed", "blocked")
FILES = ("manifest.json", "cv.md", "letter.md", "answers.json")
MAX_FILE_BYTES = 1_048_576


def _now(now: datetime | None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Date UTC avec fuseau obligatoire.")
    return current.astimezone(UTC)


def _stamp(current: datetime) -> str:
    return current.isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ensure_schema(store: JobStore) -> None:
    """Tables additives ; conserver PRAGMA user_version et les anciennes données."""
    with store.connection:
        store.connection.execute("""
            CREATE TABLE IF NOT EXISTS application_queue (
                id TEXT PRIMARY KEY NOT NULL,
                job_id TEXT NOT NULL UNIQUE,
                draft_name TEXT NOT NULL,
                draft_sha256 TEXT NOT NULL,
                channel_key TEXT NOT NULL,
                channel_kind TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('queued','reserved','interrupted','cancelled','failed','blocked')
                ),
                reservation_token TEXT,
                worker_id TEXT,
                lease_until TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0
                    CHECK (cancel_requested IN (0,1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        store.connection.execute("""
            CREATE TABLE IF NOT EXISTS application_job_aliases (
                job_id TEXT PRIMARY KEY NOT NULL,
                application_id TEXT NOT NULL
            )
        """)
        store.connection.execute("""
            CREATE TABLE IF NOT EXISTS application_queue_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL,
                previous_status TEXT,
                status TEXT NOT NULL,
                action TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )
        """)
        store.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_application_queue_status "
            "ON application_queue(status,created_at,id)"
        )


def _read_regular_attachment(path: Path) -> bytes:
    from ai_job_hunter.attachment_documents import MAX_PDF_BYTES, _read_regular
    return _read_regular(path, MAX_PDF_BYTES)


class ApplicationQueue:
    def __init__(self, store: JobStore, data_dir: Path):
        self.store = store
        self.data_dir = data_dir
        ensure_schema(store)

    def _read_draft(self, draft_name: str) -> tuple[dict[str, Any], str]:
        """Bounded file reads; reject path traversal and symlinks."""
        if not DRAFT_DIR.fullmatch(draft_name):
            raise ValueError("Nom du dossier M6 invalide.")
        root = self.data_dir / "drafts"
        folder = root / draft_name
        if (self.data_dir.is_symlink() or root.is_symlink()
                or folder.is_symlink() or not folder.is_dir()
                or folder.resolve().parent != root.resolve()):
            raise ValueError("Dossier M6 absent, hors périmètre ou symbolique.")
        fingerprint = hashlib.sha256()
        manifest_data: bytes | None = None
        for filename in FILES:
            file = folder / filename
            if not file.is_file() or file.is_symlink():
                raise ValueError(f"Fichier M6 absent ou symbolique : {filename}.")
            if file.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"Fichier M6 trop volumineux : {filename}.")
            with file.open("rb") as handle:
                raw = handle.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError(f"Fichier M6 trop volumineux : {filename}.")
            fingerprint.update(filename.encode("utf-8") + b"\0" + raw + b"\0")
            if filename == "manifest.json":
                manifest_data = raw
        # M7.4: optional attachments become part of the immutable queue hash.
        # Old M6 dossiers remain valid without a bundle.
        from ai_job_hunter.attachment_documents import read_bundle
        attachments = read_bundle(folder)
        if attachments:
            for filename in ("attachments.json", "cv.pdf", "letter.pdf"):
                extra = _read_regular_attachment(folder / filename)
                fingerprint.update(filename.encode("utf-8") + b"\0" + extra + b"\0")
        if manifest_data is None:
            raise ValueError("Manifeste M6 absent.")
        try:
            def reject_constant(value: str) -> None:
                raise ValueError(f"Constante JSON interdite : {value}")
            manifest = json.loads(manifest_data, parse_constant=reject_constant)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Manifeste M6 illisible ou invalide.") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Manifeste M6 non objet.")
        return manifest, fingerprint.hexdigest()

    def _validated(self, draft_name: str) -> tuple[dict, str, str, tuple[str, ...]]:
        manifest, fingerprint = self._read_draft(draft_name)
        job_id = manifest.get("job_id")
        if not isinstance(job_id, str) or not ID.fullmatch(job_id):
            raise ValueError("ID de l'offre M6 invalide.")
        if not (
            manifest.get("status") == "draft_requires_human_review"
            and manifest.get("review_required") is True
            and manifest.get("ready_for_submission") is False
            and manifest.get("automation_authorized") is False
            and manifest.get("human_final_submit_required") is True
        ):
            raise ValueError("Dossier non factuel ou barrière de soumission absente.")
        channel = manifest.get("channel")
        if not isinstance(channel, dict):
            raise ValueError("Canal M5.4 absent.")
        kind, target = channel.get("kind"), channel.get("target")
        if not isinstance(target, str) or len(target) > 4096:
            raise ValueError("Destination de candidature invalide.")
        job = self.store.connection.execute(
            "SELECT application_url FROM jobs WHERE id=?", (job_id,),
        ).fetchone()
        if job is None:
            raise ValueError("Offre M3 introuvable en SQLite.")
        if kind == "offer_link":
            known = _safe_application_url(job["application_url"])
            if (known is None or target != known
                    or channel.get("verification") != "link_from_job_source"):
                raise ValueError("Lien direct M5.4 différent du lien source enregistré.")
        elif kind in ("company_form", "company_email"):
            contact_id = channel.get("contact_id")
            if not isinstance(contact_id, str) or not re.fullmatch(r"[a-f0-9]{64}", contact_id):
                raise ValueError("Contact vérifié requis.")
            record = self.store.connection.execute(
                "SELECT channel,target,status FROM recruitment_contacts WHERE id=?",
                (contact_id,),
            ).fetchone() if self.store.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recruitment_contacts'"
            ).fetchone() else None
            expected = "form" if kind == "company_form" else "email"
            if (record is None or record["status"] != "approved"
                    or record["channel"] != expected or record["target"] != target
                    or channel.get("verification") != "contact_approved"):
                raise ValueError("Contact non approuvé, modifié ou bloqué.")
        else:
            raise ValueError("Canal de candidature inconnu.")
        related = manifest.get("related_job_ids", [])
        if (not isinstance(related, list) or len(related) > 50
                or any(not isinstance(x, str) or not ID.fullmatch(x) for x in related)):
            raise ValueError("Doublons M3 invalides.")
        aliases = tuple(dict.fromkeys((job_id, *related)))
        if len(aliases) > 50:
            raise ValueError("Trop d'alias d'offres.")
        for alias in aliases:
            if self.store.connection.execute(
                "SELECT 1 FROM jobs WHERE id=?", (alias,),
            ).fetchone() is None:
                raise ValueError("Doublon M3 non enregistré : " + alias)
        channel_key = _digest(json.dumps([kind, target], ensure_ascii=False))
        return manifest, fingerprint, channel_key, aliases

    def _event(
        self, application_id: str, previous: str | None, state: str,
        action: str, current: datetime,
    ) -> None:
        self.store.connection.execute("""
            INSERT INTO application_queue_events
            (application_id,previous_status,status,action,occurred_at)
            VALUES (?,?,?,?,?)
        """, (application_id, previous, state, action, _stamp(current)))

    def _get(self, app_id: str):
        row = self.store.connection.execute(
            "SELECT * FROM application_queue WHERE id=?", (app_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Candidature inconnue.")
        return row

    def enqueue(self, draft: Path, *, now: datetime | None = None) -> dict:
        current = _now(now)
        root = self.data_dir / "drafts"
        if (draft.is_symlink() or not DRAFT_DIR.fullmatch(draft.name)
                or draft.resolve().parent != root.resolve()):
            raise ValueError("Seul un dossier M6 direct sous .ajh/drafts est accepté.")
        manifest, fingerprint, channel_key, aliases = self._validated(draft.name)
        job_id = manifest["job_id"]
        app_id = _digest("m7.1:" + job_id)
        with self.store.connection:
            self.store.connection.execute("BEGIN IMMEDIATE")
            existing = self.store.connection.execute(
                "SELECT * FROM application_queue WHERE job_id=?", (job_id,),
            ).fetchone()
            if existing is not None:
                if (existing["draft_sha256"] != fingerprint
                        or existing["channel_key"] != channel_key):
                    raise ValueError("Candidature déjà inscrite avec un dossier/canal différent.")
                for alias in aliases:
                    row = self.store.connection.execute(
                        "SELECT application_id FROM application_job_aliases WHERE job_id=?",
                        (alias,),
                    ).fetchone()
                    if row is not None and row["application_id"] != app_id:
                        raise ValueError("Alias déjà réservé à une autre candidature.")
                    self.store.connection.execute(
                        "INSERT OR IGNORE INTO application_job_aliases(job_id,application_id) VALUES(?,?)",
                        (alias, app_id),
                    )
                return self._view(existing)
            for alias in aliases:
                row = self.store.connection.execute(
                    "SELECT application_id FROM application_job_aliases WHERE job_id=?",
                    (alias,),
                ).fetchone()
                if row is not None:
                    raise ValueError("Offre ou doublon déjà inscrit dans la file.")
            self.store.connection.execute("""
                INSERT INTO application_queue
                (id,job_id,draft_name,draft_sha256,channel_key,channel_kind,
                 status,created_at,updated_at)
                VALUES(?,?,?,?,? ,?,'queued',?,?)
            """, (app_id, job_id, draft.name, fingerprint, channel_key,
                  manifest["channel"]["kind"], _stamp(current), _stamp(current)))
            for alias in aliases:
                self.store.connection.execute(
                    "INSERT INTO application_job_aliases(job_id,application_id) VALUES(?,?)",
                    (alias, app_id),
                )
            self._event(app_id, None, "queued", "enqueue", current)
            return self._view(self._get(app_id))

    @staticmethod
    def _view(row) -> dict:
        # No personal documents, destinations or reservation capabilities in listings.
        return {
            "id": row["id"], "job_id": row["job_id"],
            "draft_name": row["draft_name"], "status": row["status"],
            "channel_kind": row["channel_kind"], "cancel_requested": bool(row["cancel_requested"]),
            "lease_until": row["lease_until"], "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list(self, limit: int = 20) -> list[dict]:
        return [self._view(row) for row in self.store.connection.execute(
            "SELECT * FROM application_queue ORDER BY created_at,id LIMIT ?", (limit,),
        )]

    def stats(self) -> dict:
        counts = {status: 0 for status in STATES}
        for status, count in self.store.connection.execute(
            "SELECT status,COUNT(*) FROM application_queue GROUP BY status"
        ):
            counts[status] = count
        return {"total": sum(counts.values()), "states": counts}

    def events(self, app_id: str, limit: int = 20) -> list[dict]:
        self._get(app_id)
        return [dict(row) for row in self.store.connection.execute("""
            SELECT event_id,previous_status,status,action,occurred_at
            FROM application_queue_events
            WHERE application_id=? ORDER BY event_id DESC LIMIT ?
        """, (app_id, limit))]

    def claim(
        self, worker: str, *, seconds: int = 300, now: datetime | None = None,
        browser_only: bool = False,
    ) -> dict | None:
        if not isinstance(worker, str) or not WORKER.fullmatch(worker):
            raise ValueError("Identifiant worker invalide.")
        if type(seconds) is not int or not 30 <= seconds <= 3600:
            raise ValueError("Bail entre 30 et 3600 secondes requis.")
        current = _now(now)
        with self.store.connection:
            self.store.connection.execute("BEGIN IMMEDIATE")
            query = "SELECT * FROM application_queue WHERE status='queued' "
            tracking_exists = self.store.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='submission_handoffs'"
            ).fetchone() is not None
            if tracking_exists:
                query += (
                    "AND id NOT IN (SELECT application_id FROM submission_handoffs) "
                )
            if browser_only:
                query += "AND channel_kind IN ('offer_link','company_form') "
            rows = self.store.connection.execute(
                query + "ORDER BY created_at,id"
            ).fetchall()
            for row in rows:
                # A changed M6 dossier or contact revocation must fail closed.
                try:
                    manifest, digest, key, _ = self._validated(row["draft_name"])
                    if (manifest["job_id"] != row["job_id"]
                            or digest != row["draft_sha256"]
                            or key != row["channel_key"]):
                        raise ValueError("Dossier M6 changé après inscription.")
                except (ValueError, OSError):
                    self.store.connection.execute(
                        "UPDATE application_queue SET status='blocked',updated_at=? WHERE id=?",
                        (_stamp(current), row["id"]),
                    )
                    self._event(row["id"], "queued", "blocked", "validation_failed", current)
                    continue
                token = secrets.token_hex(24)
                until = _stamp(current + timedelta(seconds=seconds))
                self.store.connection.execute("""
                    UPDATE application_queue
                    SET status='reserved',reservation_token=?,worker_id=?,
                        lease_until=?,updated_at=?,cancel_requested=0
                    WHERE id=? AND status='queued'
                """, (token, worker, until, _stamp(current), row["id"]))
                self._event(row["id"], "queued", "reserved", "claim", current)
                return {
                    **self._view(self._get(row["id"])),
                    "reservation_token": token, "worker": worker,
                }
        return None

    def reserved_destination(self, app_id: str, token: str) -> dict:
        """Recheck document integrity and contact approval after claim.

        The URL is exposed only to a holder of the exact reservation token;
        queue listings and events remain free of destination query secrets.
        """
        row = self._get(app_id)
        self._check_reservation(row, token)
        manifest, digest, key, _ = self._validated(row["draft_name"])
        if (manifest["job_id"] != row["job_id"]
                or digest != row["draft_sha256"] or key != row["channel_key"]):
            raise ValueError("Brouillon ou canal modifié pendant la réservation.")
        kind = manifest["channel"]["kind"]
        if kind not in ("offer_link", "company_form"):
            raise ValueError("Canal navigateur interdit.")
        return {"kind": kind, "target": manifest["channel"]["target"]}

    def cancellation_requested(self, app_id: str, token: str) -> bool:
        row = self._get(app_id)
        self._check_reservation(row, token)
        return bool(row["cancel_requested"])

    def renew(self, app_id: str, token: str, *, seconds: int = 300,
              now: datetime | None = None) -> dict:
        if type(seconds) is not int or not 30 <= seconds <= 3600:
            raise ValueError("Bail entre 30 et 3600 secondes requis.")
        current = _now(now)
        with self.store.connection:
            self.store.connection.execute("BEGIN IMMEDIATE")
            row = self._get(app_id)
            self._check_reservation(row, token)
            if row["cancel_requested"]:
                raise ValueError("Annulation demandée : arrêter le worker.")
            until = _stamp(current + timedelta(seconds=seconds))
            self.store.connection.execute(
                "UPDATE application_queue SET lease_until=?,updated_at=? WHERE id=?",
                (until, _stamp(current), app_id),
            )
            self._event(app_id, "reserved", "reserved", "renew", current)
            return self._view(self._get(app_id))

    @staticmethod
    def _check_reservation(row, token: str) -> None:
        if (not isinstance(token, str) or not TOKEN.fullmatch(token)
                or row["status"] != "reserved"
                or not secrets.compare_digest(row["reservation_token"] or "", token)):
            raise ValueError("Réservation absente ou périmée.")

    def release(
        self, app_id: str, token: str, *,
        outcome: str = "queued", now: datetime | None = None,
    ) -> dict:
        if outcome not in ("queued", "failed", "blocked", "cancelled"):
            raise ValueError("État de sortie worker non autorisé.")
        current = _now(now)
        with self.store.connection:
            self.store.connection.execute("BEGIN IMMEDIATE")
            row = self._get(app_id)
            self._check_reservation(row, token)
            final = "cancelled" if row["cancel_requested"] else outcome
            self.store.connection.execute("""
                UPDATE application_queue
                SET status=?,reservation_token=NULL,worker_id=NULL,lease_until=NULL,
                    cancel_requested=0,updated_at=? WHERE id=?
            """, (final, _stamp(current), app_id))
            self._event(app_id, "reserved", final, "release", current)
            return self._view(self._get(app_id))

    def cancel(self, app_id: str, *, now: datetime | None = None) -> dict:
        current = _now(now)
        with self.store.connection:
            self.store.connection.execute("BEGIN IMMEDIATE")
            tracking_exists = self.store.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='submission_handoffs'"
            ).fetchone() is not None
            if tracking_exists and self.store.connection.execute(
                "SELECT 1 FROM submission_handoffs WHERE application_id=?", (app_id,),
            ).fetchone():
                raise ValueError("Transfert humain commencé ; utiliser submission abandon ou status.")
            row = self._get(app_id)
            if row["status"] == "reserved":
                if not row["cancel_requested"]:
                    self.store.connection.execute(
                        "UPDATE application_queue SET cancel_requested=1,updated_at=? WHERE id=?",
                        (_stamp(current), app_id),
                    )
                    self._event(app_id, "reserved", "reserved", "cancel_requested", current)
            elif row["status"] != "cancelled":
                self.store.connection.execute(
                    "UPDATE application_queue SET status='cancelled',updated_at=? WHERE id=?",
                    (_stamp(current), app_id),
                )
                self._event(app_id, row["status"], "cancelled", "cancel", current)
            return self._view(self._get(app_id))

    def recover(self, app_id: str, *, now: datetime | None = None) -> dict:
        """No automatic reclaim: require an explicit decision on expired lease."""
        current = _now(now)
        with self.store.connection:
            self.store.connection.execute("BEGIN IMMEDIATE")
            row = self._get(app_id)
            if (row["status"] != "reserved" or row["lease_until"] is None
                    or datetime.fromisoformat(row["lease_until"]) > current):
                raise ValueError("Seule une réservation expirée peut être interrompue.")
            next_status = "cancelled" if row["cancel_requested"] else "interrupted"
            self.store.connection.execute("""
                UPDATE application_queue SET status=?,reservation_token=NULL,
                    worker_id=NULL,lease_until=NULL,cancel_requested=0,
                    updated_at=? WHERE id=?
            """, (next_status, _stamp(current), app_id))
            self._event(app_id, "reserved", next_status, "recover_expired", current)
            return self._view(self._get(app_id))

    def resume(self, app_id: str, *, now: datetime | None = None) -> dict:
        current = _now(now)
        with self.store.connection:
            self.store.connection.execute("BEGIN IMMEDIATE")
            tracking_exists = self.store.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='submission_handoffs'"
            ).fetchone() is not None
            if tracking_exists and self.store.connection.execute(
                "SELECT 1 FROM submission_handoffs WHERE application_id=?", (app_id,),
            ).fetchone():
                raise ValueError("Transfert humain déjà commencé : reprise automatique interdite.")
            row = self._get(app_id)
            if row["status"] not in ("interrupted", "failed", "blocked", "cancelled"):
                raise ValueError("Candidature non reprenable.")
            manifest, digest, key, _ = self._validated(row["draft_name"])
            if (manifest["job_id"] != row["job_id"]
                    or digest != row["draft_sha256"] or key != row["channel_key"]):
                raise ValueError("Dossier M6 modifié : préparer un nouveau dossier avant reprise.")
            self.store.connection.execute(
                "UPDATE application_queue SET status='queued',updated_at=? WHERE id=?",
                (_stamp(current), app_id),
            )
            self._event(app_id, row["status"], "queued", "resume_explicit", current)
            return self._view(self._get(app_id))
