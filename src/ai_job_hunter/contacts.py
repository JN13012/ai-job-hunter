"""M5.1 : registre local des contacts professionnels, sans scraping ni envoi."""

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from ai_job_hunter.catalog import public_url
from ai_job_hunter.company_discovery import SIREN, ensure_schema as ensure_companies
from ai_job_hunter.storage import JobStore, WriteCounts

MAX_BYTES = 1024 * 1024
MAX_ROWS = 200
CHANNELS = ("email", "form")
ROLES = ("recruitment", "hr", "talent_acquisition", "hiring_manager", "careers")
ORIGINS = ("company_careers", "company_contact", "authorized_export")
PERSONAL_PROVIDERS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.fr",
    "outlook.com", "hotmail.com", "hotmail.fr", "live.com",
    "icloud.com", "proton.me", "protonmail.com", "aol.com",
})
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\Z")


def _nonempty(value: object, field: str, maximum: int) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"{field} absent ou invalide.")
    return value.strip()


def _public_contact_url(raw: object, field: str) -> str:
    url = public_url(raw)
    parsed = urlsplit(url)
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field} : requêtes et fragments refusés pour éviter les jetons.")
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path or "/", "", ""))


@dataclass(frozen=True)
class Contact:
    id: str
    siren: str
    channel: str
    target: str
    role: str
    source_url: str
    source_type: str
    person_name: str | None

    @property
    def content_hash(self) -> str:
        values = (self.siren, self.channel, self.target, self.role,
                  self.source_url, self.source_type, self.person_name)
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def normalize_contact(raw: object) -> Contact:
    expected = {"siren", "channel", "target", "role", "source_url", "source_type", "person_name"}
    if not isinstance(raw, dict) or raw.keys() - expected:
        raise ValueError("Contact : objet JSON invalide ou champ inconnu.")
    siren = raw.get("siren")
    if not isinstance(siren, str) or not SIREN.fullmatch(siren):
        raise ValueError("SIREN à neuf chiffres obligatoire, jamais déduit du nom.")
    channel, role, origin = raw.get("channel"), raw.get("role"), raw.get("source_type")
    if channel not in CHANNELS or role not in ROLES or origin not in ORIGINS:
        raise ValueError("Canal, rôle ou provenance non autorisé.")
    source_url = _public_contact_url(raw.get("source_url"), "source_url")
    if channel == "email":
        target = _nonempty(raw.get("target"), "email", 254).lower()
        if not EMAIL_PATTERN.fullmatch(target) or target.rsplit("@", 1)[1] in PERSONAL_PROVIDERS:
            raise ValueError("Adresse email professionnelle non reconnue.")
    else:
        target = _public_contact_url(raw.get("target"), "formulaire")
    person = raw.get("person_name")
    if person is not None:
        person = _nonempty(person, "person_name", 120)
    key = json.dumps((siren, channel, target))
    identity = hashlib.sha256(key.encode()).hexdigest()
    return Contact(identity, siren, channel, target, role, source_url, origin, person)


def ensure_contacts(store: JobStore) -> None:
    with store.connection:
        store.connection.execute("""
            CREATE TABLE IF NOT EXISTS recruitment_contacts (
                id TEXT PRIMARY KEY NOT NULL,
                siren TEXT NOT NULL,
                channel TEXT NOT NULL,
                target TEXT,
                role TEXT,
                source_url TEXT,
                source_type TEXT,
                person_name TEXT,
                status TEXT NOT NULL CHECK (status IN ('pending','approved','blocked')),
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                reviewed_at TEXT,
                content_hash TEXT,
                UNIQUE(siren,channel,target)
            )
        """)
        store.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_recruitment_contacts_siren ON recruitment_contacts(siren)"
        )


def import_contacts(path: Path, store: JobStore) -> WriteCounts:
    with path.open("rb") as stream:
        content = stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("Fichier supérieur à 1 Mio.")
    def reject_constant(_: str) -> None:
        raise ValueError("Constante JSON invalide.")
    payload = json.loads(content, parse_constant=reject_constant)
    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_ROWS:
        raise ValueError("Tableau JSON de 1 à 200 contacts attendu.")
    contacts: dict[str, Contact] = {}
    for number, item in enumerate(payload, 1):
        try:
            contact = normalize_contact(item)
        except ValueError as exc:
            raise ValueError(f"Contact {number} : {exc}") from None
        if contact.id in contacts and contacts[contact.id] != contact:
            raise ValueError(f"Contact {number} : doublon contradictoire.")
        contacts[contact.id] = contact
    ensure_companies(store)
    ensure_contacts(store)
    available = {row[0] for row in store.connection.execute("SELECT siren FROM company_targets")}
    unknown = {c.siren for c in contacts.values()} - available
    if unknown:
        raise ValueError("SIREN non présents dans M4 : " + ", ".join(sorted(unknown)))
    timestamp = datetime.now(UTC).isoformat()
    counts = WriteCounts()
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        for contact in contacts.values():
            previous = store.connection.execute(
                "SELECT status,content_hash,reviewed_at FROM recruitment_contacts WHERE id=?",
                (contact.id,),
            ).fetchone()
            if previous and previous["status"] == "blocked":
                # Empreinte conservée ; coordonnées effacées lors du blocage.
                counts.unchanged += 1
                continue
            digest = contact.content_hash
            if previous is None:
                counts.new += 1
            elif previous["content_hash"] == digest:
                counts.unchanged += 1
            else:
                counts.updated += 1
            same = previous is not None and previous["content_hash"] == digest
            status = previous["status"] if same else "pending"
            reviewed = previous["reviewed_at"] if same else None
            store.connection.execute("""
                INSERT INTO recruitment_contacts (
                    id,siren,channel,target,role,source_url,source_type,person_name,
                    status,first_seen,last_seen,reviewed_at,content_hash
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    target=excluded.target,role=excluded.role,
                    source_url=excluded.source_url,source_type=excluded.source_type,
                    person_name=excluded.person_name,status=excluded.status,
                    last_seen=excluded.last_seen,reviewed_at=excluded.reviewed_at,
                    content_hash=excluded.content_hash
            """, (contact.id, contact.siren, contact.channel, contact.target,
                  contact.role, contact.source_url, contact.source_type,
                  contact.person_name, status, timestamp, timestamp, reviewed, digest))
    return counts


def list_contacts(store: JobStore, limit: int = 20, status: str = "all") -> list[dict]:
    ensure_companies(store)
    ensure_contacts(store)
    if status not in ("all", "pending", "approved", "blocked"):
        raise ValueError("Statut invalide.")
    query = """
        SELECT c.id,c.siren,co.company,c.channel,c.target,c.role,c.source_url,
               c.source_type,c.person_name,c.status,c.first_seen,c.last_seen,c.reviewed_at
        FROM recruitment_contacts AS c JOIN company_targets AS co ON co.siren=c.siren
    """
    params: tuple = ()
    if status != "all":
        query += " WHERE c.status=?"
        params = (status,)
    query += " ORDER BY c.status,co.company,c.id LIMIT ?"
    return [dict(row) for row in store.connection.execute(query, (*params, limit))]


def contact_stats(store: JobStore) -> dict:
    ensure_contacts(store)
    counts = {"pending": 0, "approved": 0, "blocked": 0}
    for state, count in store.connection.execute(
        "SELECT status,COUNT(*) FROM recruitment_contacts GROUP BY status"
    ):
        counts[state] = count
    return {"total": sum(counts.values()), "statuses": counts}


def change_contact(store: JobStore, identifier: str, action: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise ValueError("Identifiant de contact invalide.")
    if action not in ("approve", "block"):
        raise ValueError("Action non autorisée.")
    ensure_contacts(store)
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        previous = store.connection.execute(
            "SELECT status FROM recruitment_contacts WHERE id=?", (identifier,)
        ).fetchone()
        if previous is None:
            raise ValueError("Contact introuvable.")
        if action == "approve":
            if previous["status"] == "blocked":
                raise ValueError("Contact bloqué, opposition non réversible.")
            store.connection.execute(
                "UPDATE recruitment_contacts SET status='approved',reviewed_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), identifier),
            )
        else:
            # Blocage irréversible depuis la CLI ; conservation minimale du hash.
            store.connection.execute("""
                UPDATE recruitment_contacts SET status='blocked',target=NULL,
                person_name=NULL,source_url=NULL,source_type=NULL,role=NULL,
                content_hash=NULL,reviewed_at=? WHERE id=?
            """, (datetime.now(UTC).isoformat(), identifier))
