"""M5.3 : extraction opt-in de canaux RH explicites sur UNE page autorisée.

Jamais de crawl, de scraping de profils, de soumission, de suivi de liens ni
de génération d'adresses. Respecte robots.txt, sans retry et sans redirection.
"""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import ipaddress
import re
import time
from urllib.parse import urljoin, urlsplit

import httpx
from urllib.robotparser import RobotFileParser

from ai_job_hunter.catalog import Target, public_url
from ai_job_hunter.contacts import Contact, normalize_contact, save_contacts
from ai_job_hunter.storage import JobStore, WriteCounts

USER_AGENT = "AI-Job-Hunter"
MAX_PAGES = 10
MAX_BYTES = 1024 * 1024
MAX_ROBOTS_BYTES = 65536
MAX_CANDIDATES = 20
FORM_PHRASES = (
    "candidature spontanée", "candidature spontanee",
    "postuler spontanément", "postuler spontanement",
    "spontaneous application", "unsolicited application",
    "apply spontaneously", "candidatura espontánea",
)
MAILBOX_NAMES = frozenset({
    "recrutement", "recruitment", "recruiting", "recrute", "recrutements",
    "rh", "hr", "careers", "career", "jobs", "emploi", "emplois",
    "talent", "talents", "alternance", "apprentissage",
})
HOST_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home",
                    ".invalid", ".test", ".onion")


class ScanError(ValueError):
    def __init__(self, message: str, *, rate_limited: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited


@dataclass
class ScanResult:
    pages: int = 0
    requests: int = 0
    skipped: int = 0
    candidates: list[dict] = field(default_factory=list)
    writes: WriteCounts = field(default_factory=WriteCounts)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _public_host(url: str) -> str:
    parsed = urlsplit(public_url(url))
    host = parsed.hostname or ""
    if (parsed.query or parsed.fragment or not HOST_PATTERN.fullmatch(host)
            or "." not in host or host == "localhost"
            or host.endswith(BLOCKED_SUFFIXES) or host.endswith(".arpa")):
        raise ScanError("Hôte public HTTPS sans paramètre requis.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ScanError("Adresse IP directe interdite.")
    return host


def _read(client: httpx.Client, url: str, *, max_bytes: int, content_types: tuple[str, ...]) -> bytes:
    try:
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code == 429:
                raise ScanError("HTTP 429 : analyse interrompue sans retry.", rate_limited=True)
            if response.status_code != 200:
                raise ScanError(f"HTTP {response.status_code} (aucune redirection suivie).")
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if content_type not in content_types:
                raise ScanError("Type de contenu non autorisé.")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ScanError("Page supérieure à la limite autorisée.")
            return bytes(data)
    except httpx.HTTPError as exc:
        raise ScanError(f"Erreur HTTP : {type(exc).__name__}.") from exc


class CareerLinks(HTMLParser):
    """Ne conserver que les ancres HTML, jamais scripts, formulaires ou DOM."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.words: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.suppressed += 1
        if tag == "a" and not self.suppressed:
            values = dict(attrs)
            self.href = values.get("href")
            self.words = []

    def handle_data(self, data: str) -> None:
        if self.href is not None and not self.suppressed:
            self.words.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href is not None and not self.suppressed:
            self.links.append((self.href, " ".join(self.words)))
            self.href = None
            self.words = []
        if tag in ("script", "style") and self.suppressed:
            self.suppressed -= 1


def extract_contacts(page_url: str, html: str, siren: str) -> tuple[Contact, ...]:
    host = _public_host(page_url)
    parser = CareerLinks()
    parser.feed(html)
    results: dict[str, Contact] = {}
    for href, label in parser.links:
        try:
            if href.lower().startswith("mailto:"):
                raw = href[7:]
                if "?" in raw or "#" in raw or "%" in raw:
                    continue
                address = raw.strip().lower()
                if address.count("@") != 1:
                    continue
                local, domain = address.rsplit("@", 1)
                # Only generic recruiting mailboxes linked from the source page,
                # never infer personal mailboxes or harvest all visible emails.
                words = re.split(r"[^a-z0-9]+", local)
                if not any(word in MAILBOX_NAMES for word in words):
                    continue
                if not (host == domain or host.endswith("." + domain)):
                    continue
                contact = normalize_contact({
                    "siren": siren, "channel": "email", "target": address,
                    "role": "recruitment", "source_url": page_url,
                    "source_type": "company_careers",
                })
            else:
                caption = " ".join(label.casefold().split())
                if not any(phrase in caption for phrase in FORM_PHRASES):
                    continue
                link = urljoin(page_url, href)
                if _public_host(link) != host:
                    continue
                contact = normalize_contact({
                    "siren": siren, "channel": "form", "target": link,
                    "role": "careers", "source_url": page_url,
                    "source_type": "company_careers",
                })
            results.setdefault(contact.id, contact)
        except ValueError:
            continue
        if len(results) == MAX_CANDIDATES:
            break
    return tuple(results.values())


def scan_catalog(
    store: JobStore, targets: tuple[Target, ...], client: httpx.Client,
    *, fetch: bool = False, register: bool = False, max_pages: int = MAX_PAGES,
    interval_seconds: float = 2.0,
) -> ScanResult:
    if register and not fetch:
        raise ValueError("L'enregistrement demande --fetch.")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise ValueError("max_pages : entier de 1 à 10 requis.")
    if (type(interval_seconds) not in (int, float) or
            not 1 <= interval_seconds <= 60):
        raise ValueError("interval_seconds : 1 à 60 requis.")
    result = ScanResult()
    exists = store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_targets'"
    ).fetchone()
    known = set()
    if exists:
        known = {row[0] for row in store.connection.execute("SELECT siren FROM company_targets")}
    already: set[str] = set()
    site_count = 0

    def get(url: str, limit: int, types: tuple[str, ...]) -> bytes:
        if result.requests:
            time.sleep(interval_seconds)
        result.requests += 1
        return _read(client, url, max_bytes=limit, content_types=types)

    for target in targets:
        if not target.allow_contact_scan or target.source != "manual":
            result.skipped += 1
            continue
        if not target.siren or target.siren not in known:
            result.skipped += 1
            result.warnings.append(f"{target.company}: SIREN absent de M4.")
            continue
        try:
            host = _public_host(target.career_url)
        except ValueError as exc:
            result.skipped += 1
            result.warnings.append(f"{target.company}: URL refusée : {exc}")
            continue
        if site_count >= max_pages:
            result.skipped += 1
            result.warnings.append("Limite globale de pages atteinte.")
            continue
        if not fetch:
            result.candidates.append({
                "company": target.company, "siren": target.siren,
                "source_url": target.career_url, "status": "eligible_not_fetched",
            })
            continue

        site_count += 1
        robots_url = "https://" + host + "/robots.txt"
        try:
            try:
                robots = get(robots_url, MAX_ROBOTS_BYTES, ("text/plain",))
            except ScanError as exc:
                if str(exc).startswith("HTTP 404"):
                    robots = None
                else:
                    raise
            if robots is not None:
                rule = RobotFileParser()
                rule.parse(robots.decode("utf-8", "replace").splitlines())
                if not rule.can_fetch(USER_AGENT, target.career_url):
                    result.skipped += 1
                    result.warnings.append(f"{target.company}: robots.txt interdit la page.")
                    continue
            page = get(target.career_url, MAX_BYTES, ("text/html", "application/xhtml+xml"))
            result.pages += 1
            contacts = extract_contacts(target.career_url, page.decode("utf-8", "replace"), target.siren)
            unique = tuple(item for item in contacts if item.id not in already)
            for item in unique:
                already.add(item.id)
                result.candidates.append({
                    "company": target.company, "siren": target.siren,
                    "channel": item.channel, "target": item.target,
                    "role": item.role, "source_url": item.source_url,
                    "status": "pending_review",
                })
            if register and unique:
                counts = save_contacts(store, unique)
                result.writes.new += counts.new
                result.writes.updated += counts.updated
                result.writes.unchanged += counts.unchanged
        except ScanError as exc:
            result.errors.append(f"{target.company}: {exc}")
            if exc.rate_limited:
                result.warnings.append("Limite HTTP 429 : arrêt du lot.")
                break
    return result
