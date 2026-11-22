"""M7.5 — Manual browser handoff and human-reviewed receipt tracking.

Never submits, fills, clicks, sends email, or verifies a platform response.
The M7.2–M7.4 Chromium context blocks POST and is closed after filling;
therefore its transient form state CANNOT be carried to a real browser yet.
A handoff opens only a URL in the user's own browser on explicit request.

States are additive to the M7.1 queue, not a claim of native ATS integration.
"""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import re
import sqlite3

from ai_job_hunter.application_queue import ApplicationQueue, _now, _stamp
from ai_job_hunter.browser_preview import BrowserPolicy
from ai_job_hunter.channel_routing import _safe_application_url

SOURCE_TYPES = frozenset(("confirmation_page", "confirmation_email"))
STATES = (
    "awaiting_manual_action", "clicked_unconfirmed",
    "submitted_confirmed", "abandoned",
)
REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/# -]{4,159}\Z")
HEX = re.compile(r"[a-f0-9]{64}\Z")
MAX_PROOF_BYTES = 5 * 1024 * 1024
PROOF_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".pdf", ".eml", ".txt"))


def ensure_submission_schema(queue: ApplicationQueue) -> None:
    """Additive schema: no change to the existing M7.1 CHECK constraint."""
    with queue.store.connection:
        queue.store.connection.execute("""
            CREATE TABLE IF NOT EXISTS submission_handoffs (
                application_id TEXT PRIMARY KEY NOT NULL,
                status TEXT NOT NULL CHECK(
                    status IN ('awaiting_manual_action', 'clicked_unconfirmed',
                               'submitted_confirmed', 'abandoned')
                ),
                draft_sha256 TEXT NOT NULL,
                channel_key TEXT NOT NULL,
                destination_sha256 TEXT NOT NULL,
                evidence_source TEXT,
                evidence_sha256 TEXT,
                receipt_reference TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                clicked_at TEXT,
                confirmed_at TEXT
            )
        """)
        queue.store.connection.execute("""
            CREATE TABLE IF NOT EXISTS submission_handoff_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL,
                previous_status TEXT,
                status TEXT NOT NULL,
                event TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )
        """)


class SubmissionTracker:
    def __init__(self, queue: ApplicationQueue):
        self.queue = queue
        self.conn = queue.store.connection
        ensure_submission_schema(queue)

    def _get(self, app_id: str) -> sqlite3.Row:
        record = self.conn.execute(
            "SELECT * FROM submission_handoffs WHERE application_id=?", (app_id,),
        ).fetchone()
        if record is None:
            raise ValueError("Aucun transfert humain enregistré.")
        return record

    def _event(
        self, app_id: str, previous: str | None, status: str,
        action: str, now: datetime,
    ) -> None:
        self.conn.execute(
            "INSERT INTO submission_handoff_events "
            "(application_id,previous_status,status,event,occurred_at) "
            "VALUES(?,?,?,?,?)",
            (app_id, previous, status, action, _stamp(now)),
        )

    @staticmethod
    def _public(record: sqlite3.Row) -> dict:
        return {
            "application_id": record["application_id"],
            "status": record["status"],
            "evidence_source": record["evidence_source"],
            "evidence_sha256": record["evidence_sha256"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "clicked_at": record["clicked_at"],
            "confirmed_at": record["confirmed_at"],
            "human_final_submit_required": True,
            "platform_confirmation_automatically_verified": False,
        }

    def _locked_draft(self, app_id: str) -> tuple[sqlite3.Row, dict, str]:
        """Transaction owner must have obtained BEGIN IMMEDIATE."""
        row = self.queue._get(app_id)
        if row["status"] != "queued" or row["cancel_requested"]:
            raise ValueError("File occupée, annulée ou non préparée.")
        manifest, digest, key, _ = self.queue._validated(row["draft_name"])
        if (manifest.get("job_id") != row["job_id"]
                or digest != row["draft_sha256"] or key != row["channel_key"]):
            raise ValueError("Dossier ou destination M6/M7 modifié.")
        channel = manifest["channel"]
        if channel["kind"] not in ("offer_link", "company_form"):
            raise ValueError("Transfert navigateur non pris en charge (email exclu).")
        target = channel["target"]
        if _safe_application_url(target) is None:
            raise ValueError("Destination de candidature non autorisée.")
        return row, manifest, target

    def handoff(
        self, app_id: str, policy: BrowserPolicy,
        *, now: datetime | None = None,
    ) -> dict:
        """Atomic transition to manual-only mode; return URL to authorized user.

        The handoff is NOT "ready_for_human_submit": the M7.3 browser was
        closed and does not transfer its inputs. User may need to fill fields.
        """
        current = _now(now)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row, _, target = self._locked_draft(app_id)
            if not policy.allows(target):
                raise ValueError("Destination absente de la politique autorisée.")
            existing = self.conn.execute(
                "SELECT * FROM submission_handoffs WHERE application_id=?", (app_id,),
            ).fetchone()
            target_hash = sha256(target.encode("utf-8")).hexdigest()
            if existing is None:
                self.conn.execute("""
                    INSERT INTO submission_handoffs
                    (application_id,status,draft_sha256,channel_key,destination_sha256,
                     created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?)
                """, (app_id, "awaiting_manual_action", row["draft_sha256"],
                      row["channel_key"], target_hash, _stamp(current), _stamp(current)))
                self._event(app_id, None, "awaiting_manual_action", "manual_handoff", current)
            elif (
                existing["status"] != "awaiting_manual_action"
                or existing["draft_sha256"] != row["draft_sha256"]
                or existing["channel_key"] != row["channel_key"]
                or existing["destination_sha256"] != target_hash
            ):
                raise ValueError("Transfert déjà commencé, terminé ou différent.")
            result = self._public(self._get(app_id))
            # The destination is not persisted in event logs or CLI listings.
            result["manual_url"] = target
            result["manual_filling_required"] = True
            return result

    def clicked(self, app_id: str, *, now: datetime | None = None) -> dict:
        """A declaration by the user, NOT proof of a successful submission."""
        current = _now(now)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self._get(app_id)
            if row["status"] == "clicked_unconfirmed":
                return self._public(row)
            if row["status"] != "awaiting_manual_action":
                raise ValueError("Déclaration de clic hors état autorisé.")
            self.conn.execute(
                "UPDATE submission_handoffs SET status='clicked_unconfirmed', "
                "clicked_at=?,updated_at=? WHERE application_id=?",
                (_stamp(current), _stamp(current), app_id),
            )
            self._event(app_id, row["status"], "clicked_unconfirmed", "human_click_reported", current)
            return self._public(self._get(app_id))

    def confirm(
        self, app_id: str, *, evidence: Path, evidence_source: str,
        reference: str, reviewed: bool, now: datetime | None = None,
    ) -> dict:
        """Human-reviewed receipt, NOT independent verification by the ATS."""
        if reviewed is not True:
            raise ValueError("Lecture humaine de la preuve exigée.")
        if evidence_source not in SOURCE_TYPES:
            raise ValueError("Type de confirmation non pris en charge.")
        if (not isinstance(reference, str)
                or not REF_PATTERN.fullmatch(reference)
                or "@" in reference):
            raise ValueError("Référence de confirmation invalide.")
        if (evidence.suffix.lower() not in PROOF_SUFFIXES
                or evidence.is_symlink() or not evidence.is_file()
                or evidence.stat().st_size > MAX_PROOF_BYTES
                or evidence.stat().st_size == 0):
            raise ValueError("Preuve locale invalide, symbolique ou trop volumineuse.")
        with evidence.open("rb") as file:
            data = file.read(MAX_PROOF_BYTES + 1)
        if not 0 < len(data) <= MAX_PROOF_BYTES:
            raise ValueError("Taille de preuve interdite.")
        signature = sha256(data).hexdigest()
        current = _now(now)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self._get(app_id)
            if row["status"] == "submitted_confirmed":
                if (row["evidence_source"] != evidence_source
                        or row["evidence_sha256"] != signature
                        or row["receipt_reference"] != reference):
                    raise ValueError("Confirmation déjà enregistrée avec une autre preuve.")
                return self._public(row)
            if row["status"] != "clicked_unconfirmed":
                raise ValueError("Un clic humain déclaré est requis avant confirmation.")
            self.conn.execute("""
                UPDATE submission_handoffs SET status='submitted_confirmed',
                evidence_source=?,evidence_sha256=?,receipt_reference=?,
                confirmed_at=?,updated_at=? WHERE application_id=?
            """, (evidence_source, signature, reference,
                  _stamp(current), _stamp(current), app_id))
            self._event(
                app_id, "clicked_unconfirmed", "submitted_confirmed",
                "receipt_reviewed_by_user", current,
            )
            return self._public(self._get(app_id))

    def abandon(self, app_id: str, *, now: datetime | None = None) -> dict:
        """Stop an untouched handoff; no automatic requeue or re-send."""
        current = _now(now)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self._get(app_id)
            if row["status"] == "abandoned":
                return self._public(row)
            if row["status"] != "awaiting_manual_action":
                raise ValueError("Impossible d'abandonner après déclaration de clic.")
            queue_row = self.queue._get(app_id)
            if queue_row["status"] != "queued":
                raise ValueError("File non libre : abandon refusé.")
            self.conn.execute(
                "UPDATE submission_handoffs SET status='abandoned',updated_at=? "
                "WHERE application_id=?", (_stamp(current), app_id),
            )
            self.conn.execute(
                "UPDATE application_queue SET status='cancelled',updated_at=? "
                "WHERE id=? AND status='queued'", (_stamp(current), app_id),
            )
            self.queue._event(app_id, "queued", "cancelled", "manual_handoff_abandoned", current)
            self._event(app_id, row["status"], "abandoned", "abandon", current)
            return self._public(self._get(app_id))

    def get(self, app_id: str) -> dict:
        return self._public(self._get(app_id))

    def list(self, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Limite de résultats invalide.")
        records = self.conn.execute(
            "SELECT * FROM submission_handoffs ORDER BY created_at,id LIMIT ?".replace(
                "created_at,id", "created_at,application_id"
            ), (limit,),
        ).fetchall()
        return [self._public(record) for record in records]

    def stats(self) -> dict:
        counts = {name: 0 for name in STATES}
        for state, count in self.conn.execute(
            "SELECT status,COUNT(*) FROM submission_handoffs GROUP BY status"
        ):
            counts[state] = count
        return {"total": sum(counts.values()), "states": counts}

    def events(self, app_id: str, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Limite d'événements invalide.")
        self._get(app_id)
        return [dict(row) for row in self.conn.execute("""
            SELECT event_id,previous_status,status,event,occurred_at
            FROM submission_handoff_events WHERE application_id=?
            ORDER BY event_id DESC LIMIT ?
        """, (app_id, limit))]
