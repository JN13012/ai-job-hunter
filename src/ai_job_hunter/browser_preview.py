"""M7.2 : prévisualisation navigateur isolée, *sans* saisie ni soumission.

Only an explicitly approved GET HTML document is loaded. JavaScript,
subresources, popups, downloads and redirects are blocked at this stage.
M7.3 will need a separate, site-specific permission and filling engine.
"""

from dataclasses import dataclass
from pathlib import Path
import ipaddress
import re
import time
import tomllib
from typing import Callable
from urllib.parse import unquote, urlsplit

from ai_job_hunter.application_queue import ApplicationQueue

HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home",
                    ".test", ".invalid", ".onion", ".arpa")
MAX_POLICY_BYTES = 65536
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_WAIT_SECONDS = 300


class NavigationDenied(ValueError):
    """This URL or response must not be opened by the preview worker."""


class CleanupUncertain(RuntimeError):
    """The browser was not verifiably closed; preserve the queue reservation."""


def _host(host: object) -> str:
    if not isinstance(host, str):
        raise NavigationDenied("Hôte absent.")
    value = host.casefold().rstrip(".")
    if (not HOST.fullmatch(value) or "." not in value
            or value.endswith(BLOCKED_SUFFIXES)):
        raise NavigationDenied("Hôte non public.")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise NavigationDenied("Adresse IP directe interdite.")


def _canonical_url(url: object) -> tuple[str, str, str]:
    """Scheme, host and path; query retained in target but never emitted to logs."""
    if (not isinstance(url, str) or not url or len(url) > 4096
            or "\\" in url or any(ch.isspace() or ord(ch) < 32 for ch in url)):
        raise NavigationDenied("Destination HTTPS invalide.")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.fragment):
            raise NavigationDenied("URL HTTPS stricte sans identifiant ni fragment attendue.")
        host = _host(parsed.hostname)
    except ValueError as exc:
        raise NavigationDenied("Destination non publique ou mal formée.") from exc
    path = unquote(parsed.path or "/")
    if (not path.startswith("/") or "\\" in path or "\x00" in path
            or any(segment in (".", "..") for segment in path.split("/"))):
        raise NavigationDenied("Chemin suspect refusé.")
    return host, path, parsed.query


@dataclass(frozen=True)
class AllowedPath:
    host: str
    prefix: str
    reason: str


@dataclass(frozen=True)
class BrowserPolicy:
    rules: tuple[AllowedPath, ...]

    @classmethod
    def load(cls, path: Path) -> "BrowserPolicy":
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_POLICY_BYTES:
            raise ValueError("Fichier de politique absent, symbolique ou trop volumineux.")
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        items = raw.get("allow")
        if (raw.keys() != {"allow"} or not isinstance(items, list)
                or not 1 <= len(items) <= 50):
            raise ValueError("Déclarer entre 1 et 50 [[allow]] dans la politique.")
        rules = []
        identities = set()
        for item in items:
            if not isinstance(item, dict) or item.keys() != {"host", "path_prefix", "reason"}:
                raise ValueError("Une règle exige host, path_prefix et reason.")
            host = _host(item.get("host"))
            prefix = item.get("path_prefix")
            reason = item.get("reason")
            if (not isinstance(prefix, str) or not re.fullmatch(r"/[A-Za-z0-9_/-]{0,255}", prefix)
                    or "//" in prefix):
                raise ValueError("path_prefix : chemin absolu simple requis.")
            if (not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 300
                    or any(ord(ch) < 32 for ch in reason)):
                raise ValueError("reason : justification d'autorisation explicite requise.")
            identity = (host, prefix)
            if identity in identities:
                raise ValueError("Règle dupliquée.")
            identities.add(identity)
            rules.append(AllowedPath(host, prefix.rstrip("/") or "/", reason.strip()))
        return cls(tuple(rules))

    def allows(self, url: str) -> bool:
        host, path, _ = _canonical_url(url)
        return any(
            host == rule.host and (
                rule.prefix == "/" or path == rule.prefix
                or path.startswith(rule.prefix + "/")
            ) for rule in self.rules
        )


class StrictDocumentRoute:
    """BrowserContext route: one approved GET document, every other request aborted.

    route.fetch(max_redirects=0) prevents browser-side redirect bypasses that
    route.continue_ alone cannot prevent. Fulfillment strips Set-Cookie.
    """

    def __init__(self, target: str):
        self.target = target
        self.accepted = False
        self.denied = False

    def __call__(self, route) -> None:
        request = route.request
        if (
            self.accepted
            or request.method != "GET"
            or request.resource_type != "document"
            or not request.is_navigation_request()
            or request.url != self.target
        ):
            route.abort()
            # Subresources are expected to be refused. Track only attempts at
            # a second navigation or non-GET action as a relevant violation.
            if request.is_navigation_request() or request.method != "GET":
                self.denied = True
            return
        # Set accepted before fetch: a second request cannot race through.
        self.accepted = True
        try:
            response = route.fetch(max_redirects=0, max_retries=0, timeout=15000)
            content_type = response.headers.get("content-type", "").split(";")[0].lower().strip()
            if (response.status != 200
                    or content_type not in ("text/html", "application/xhtml+xml")):
                self.denied = True
                route.abort()
                return
            body = response.body()
            if len(body) > MAX_HTML_BYTES:
                self.denied = True
                route.abort()
                return
            # Important: do NOT forward Location, Set-Cookie, CSP or third-party
            # URL headers, and do not let browser execute scripts or fetch assets.
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
        except Exception:
            self.denied = True
            try:
                route.abort()
            except Exception:
                pass


def show_visible_page(
    target: str, should_stop: Callable[[], bool], *,
    max_seconds: int = 180,
) -> str:
    """Run one real headed Chromium context, verified closure before return."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError("Installer l'extra Playwright : uv sync --extra browser.") from exc

    outcome = "closed"
    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(
                java_script_enabled=False, accept_downloads=False,
                service_workers="block", ignore_https_errors=False,
                permissions=[],
            )
            guard = StrictDocumentRoute(target)
            context.route("**/*", guard)
            page = context.new_page()
            # Other tabs/popups have their first request blocked at context level.
            context.on("page", lambda opened: opened.close() if opened is not page else None)
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=20000)
            except Exception as exc:
                if guard.denied:
                    raise NavigationDenied("Page ou redirection refusée.") from exc
                raise
            if guard.denied or not guard.accepted:
                raise NavigationDenied("Réponse HTML non conforme.")
            deadline = time.monotonic() + max_seconds
            while time.monotonic() < deadline and not page.is_closed():
                if should_stop():
                    outcome = "cancelled"
                    break
                page.wait_for_timeout(1000)
            else:
                if not page.is_closed():
                    outcome = "timeout"
        finally:
            try:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
            except Exception as exc:
                raise CleanupUncertain(
                    "Fermeture du navigateur non confirmée ; réservation conservée."
                ) from exc
    return outcome


@dataclass(frozen=True)
class BrowserResult:
    status: str
    application_id: str | None
    reason: str


def preview_one(
    queue: ApplicationQueue, policy: BrowserPolicy,
    *, worker: str = "browser-preview", max_seconds: int = 180,
    show: Callable[..., str] = show_visible_page,
) -> BrowserResult:
    if type(max_seconds) is not int or not 5 <= max_seconds <= MAX_WAIT_SECONDS:
        raise ValueError("Durée de prévisualisation attendue : 5 à 300 secondes.")
    lease = queue.claim(worker, seconds=60, browser_only=True)
    if lease is None:
        return BrowserResult("empty", None, "Aucun dossier formulaire éligible dans la file.")
    identifier = lease["id"]
    token = lease["reservation_token"]
    outcome = "failed"
    reason = "Prévisualisation non terminée."
    last_renew = time.monotonic()

    def heartbeat() -> bool:
        nonlocal last_renew
        if queue.cancellation_requested(identifier, token):
            return True
        current = time.monotonic()
        if current - last_renew >= 20:
            queue.renew(identifier, token, seconds=60)
            last_renew = current
        return False

    try:
        destination = queue.reserved_destination(identifier, token)
        if destination["kind"] not in ("offer_link", "company_form"):
            raise NavigationDenied("Canal non pris en charge.")
        if not policy.allows(destination["target"]):
            raise NavigationDenied("Destination absente de la politique autorisée.")
        result = show(destination["target"], heartbeat, max_seconds=max_seconds)
        if result not in ("closed", "timeout", "cancelled"):
            raise ValueError("État navigateur inattendu.")
        outcome = "cancelled" if result == "cancelled" else "queued"
        reason = {
            "closed": "Fenêtre fermée ; aucune candidature envoyée.",
            "timeout": "Durée écoulée ; aperçu terminé sans soumission.",
            "cancelled": "Annulation reçue ; fenêtre fermée.",
        }[result]
    except CleanupUncertain:
        # Reservation intentionally stays held; explicit operator recovery
        # requires verifying there is no running browser first.
        raise
    except NavigationDenied:
        outcome = "blocked"
        reason = "Page refusée (politique, URL ou redirection)."
    except Exception:
        outcome = "failed"
        reason = "Échec navigateur : aucun envoi attesté."
    # show() promises cleanup completed before returning or raising other errors.
    result = queue.release(identifier, token, outcome=outcome)
    return BrowserResult(result["status"], identifier, reason)
