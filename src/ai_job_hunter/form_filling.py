"""M7.3: fail-closed, single-page form filling with no submission.

Only a declared, verified form on a policy-approved HTTPS page is eligible.
The browser has JS disabled and StrictDocumentRoute blocks every non-GET
request, including a native form POST. No action targets a submit control.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Callable

from ai_job_hunter.application_queue import ApplicationQueue
from ai_job_hunter.browser_preview import (
    BrowserPolicy, CleanupUncertain, NavigationDenied, show_visible_page,
)
from ai_job_hunter.candidate import Candidate
from ai_job_hunter.attachment_documents import read_bundle
from ai_job_hunter.form_adapters import AdapterCatalog, FormAdapter


class FormMismatch(NavigationDenied):
    """The website differs from the approved, explicitly modeled form."""


IDENTITY = {
    "identity.full_name": "full_name",
    "identity.email": "email",
    "identity.city": "city",
    "identity.phone": "phone",
    "identity.portfolio_url": "portfolio_url",
    "application.availability": "availability",
    "application.alternance_rhythm": "alternance_rhythm",
    "application.motivation": "motivation",
}
ALLOWED_INPUT_TYPES = frozenset(("text", "email", "tel", "url"))
MAX_VALUE_LENGTH = 1500


def field_values(candidate: Candidate, answers: dict, adapter: FormAdapter) -> dict[str, str]:
    if not isinstance(answers, dict):
        raise FormMismatch("Réponses M6 invalides.")
    mapped = {}
    for binding in adapter.fields:
        if binding.source.startswith("answers."):
            value = answers.get(binding.source[8:])
        else:
            value = getattr(candidate, IDENTITY[binding.source])
        if value is None or (isinstance(value, str) and not value.strip()):
            if binding.required:
                raise FormMismatch("Champ requis absent du profil déclaré.")
            continue
        if not isinstance(value, str) or not 1 <= len(value) <= MAX_VALUE_LENGTH:
            raise FormMismatch("Valeur déclarée invalide ou trop longue.")
        mapped[binding.name] = value
    return mapped


def fill_known_fields(
    page, adapter: FormAdapter, values: dict[str, str],
    uploads: dict[str, Path] | None = None,
) -> int:
    """Preflight the *entire* form before changing a single field.

    Unknown writable controls, hidden unexpected forms, contenteditable,
    submit-like controls outside the adapter, and ambiguous bindings stop work.
    Hidden CSRF inputs are read-only to this worker and never populated.
    """
    if page.locator("form").count() != 1:
        raise FormMismatch("Une seule balise form attendue.")
    form = page.locator(f'form[id="{adapter.form_id}"]')
    if form.count() != 1:
        raise FormMismatch("Identifiant du formulaire inattendu.")
    if page.locator("[contenteditable]").count():
        raise FormMismatch("Éditeurs contenteditable non pris en charge.")
    # Fail closed on any interactive controls not explicitly modeled.
    allowed_names = {binding.name for binding in adapter.fields}
    upload_names = {binding.name for binding in adapter.uploads}
    upload_files = uploads or {}
    if set(upload_files) - upload_names:
        raise FormMismatch("Pièce jointe non déclarée dans l'adaptateur.")
    controls = form.locator("input,textarea,select,button")
    if controls.count() > 60:
        raise FormMismatch("Trop de contrôles HTML.")
    for index in range(controls.count()):
        control = controls.nth(index)
        tag = control.evaluate("(e) => e.tagName.toLowerCase()")
        typ = (control.get_attribute("type") or "text").casefold()
        if tag == "button" or (tag == "input" and typ in ("submit", "button", "image", "reset")):
            continue  # Never interact; excluded from writable-field accounting.
        if tag == "input" and typ == "hidden":
            continue  # A static CSRF field may exist but is never modified.
        if tag == "input" and typ == "file":
            if control.get_attribute("name") not in upload_names:
                raise FormMismatch("Champ de fichier non déclaré.")
            continue
        if tag == "input" and typ not in ALLOWED_INPUT_TYPES:
            raise FormMismatch("Case à cocher, radio, pièce jointe ou contrôle spécial non autorisé.")
        if tag not in ("input", "textarea", "select"):
            raise FormMismatch("Contrôle inattendu.")
        name = control.get_attribute("name")
        if name not in allowed_names:
            raise FormMismatch("Champ de formulaire non déclaré.")
    # Selectors constrained to validated names and tags; require unique, enabled,
    # visible controls. Validate all options before any fill.
    operations = []
    for binding in adapter.fields:
        control = form.locator(binding.selector)
        if control.count() != 1:
            raise FormMismatch("Sélecteur de champ absent ou ambigu.")
        control = control.nth(0)
        if not control.is_visible() or not control.is_enabled():
            raise FormMismatch("Champ masqué ou désactivé.")
        if binding.tag == "input":
            typ = (control.get_attribute("type") or "text").casefold()
            if typ not in ALLOWED_INPUT_TYPES:
                raise FormMismatch("Type de champ inattendu.")
        if binding.name in values:
            if binding.tag == "select":
                # Match a declared option value exactly; never invent/select
                # an unrecognized answer.
                opts = control.locator("option")
                options = [opts.nth(i).get_attribute("value") for i in range(opts.count())]
                if values[binding.name] not in options:
                    raise FormMismatch("Valeur de liste absente des choix du site.")
            operations.append((binding, control, values[binding.name]))
    # Preflight all file controls before any actual DOM changes.
    file_operations = []
    for attachment in adapter.uploads:
        control = form.locator(attachment.selector)
        if control.count() != 1:
            raise FormMismatch("Champ de pièce jointe absent ou ambigu.")
        control = control.nth(0)
        if not control.is_enabled():
            raise FormMismatch("Champ de pièce jointe désactivé.")
        if control.get_attribute("multiple") is not None:
            raise FormMismatch("Téléversement multiple interdit.")
        accept = (control.get_attribute("accept") or "").strip().casefold()
        if accept and not any(
            token.strip() in (".pdf", "application/pdf") for token in accept.split(",")
        ):
            raise FormMismatch("Type PDF non accepté par le site.")
        file = upload_files.get(attachment.name)
        if file is None:
            if attachment.required:
                raise FormMismatch("Pièce jointe obligatoire absente.")
            continue
        if file.suffix.casefold() != ".pdf" or file.is_symlink() or not file.is_file():
            raise FormMismatch("Document PDF non vérifié.")
        file_operations.append((control, file))
    # No click, press, submit, Enter, file chooser or navigation.
    for binding, control, value in operations:
        if binding.tag == "select":
            control.select_option(value=value, timeout=3000)
        else:
            control.fill(value, timeout=3000)
    for control, file in file_operations:
        control.set_input_files(str(file), timeout=3000)
    return len(operations) + len(file_operations)


@dataclass(frozen=True)
class FillResult:
    status: str
    application_id: str | None
    filled_fields: int
    reason: str


def fill_one(
    queue: ApplicationQueue, policy: BrowserPolicy, adapters: AdapterCatalog,
    candidate: Candidate, *, max_seconds: int = 120,
    worker: str = "form-filler",
    show: Callable[..., str] = show_visible_page,
) -> FillResult:
    if type(max_seconds) is not int or not 5 <= max_seconds <= 300:
        raise ValueError("Durée de remplissage : 5 à 300 secondes.")
    lease = queue.claim(worker, seconds=60, browser_only=True)
    if lease is None:
        return FillResult("empty", None, 0, "Aucune candidature éligible.")
    app_id, token = lease["id"], lease["reservation_token"]
    outcome = "failed"
    count = 0
    reason = "Remplissage interrompu."
    last = time.monotonic()

    def heartbeat() -> bool:
        nonlocal last
        if queue.cancellation_requested(app_id, token):
            return True
        current = time.monotonic()
        if current - last >= 20:
            queue.renew(app_id, token, seconds=60)
            last = current
        return False

    try:
        target = queue.reserved_destination(app_id, token)
        url = target["target"]
        if not policy.allows(url):
            raise NavigationDenied("Destination non autorisée.")
        adapter = adapters.for_url(url)
        # A form must be authorized in two separate local configurations.
        row = queue._get(app_id)
        manifest, digest = queue._read_draft(row["draft_name"])
        if digest != row["draft_sha256"] or manifest.get("job_id") != row["job_id"]:
            raise NavigationDenied("Brouillon M6 modifié.")
        folder = queue.data_dir / "drafts" / row["draft_name"]
        answers_file = folder / "answers.json"
        if answers_file.is_symlink() or answers_file.stat().st_size > 1024 * 1024:
            raise NavigationDenied("Réponses invalides.")
        answers = json.loads(answers_file.read_text(encoding="utf-8"))
        values = field_values(candidate, answers, adapter)
        upload_map = {}
        if adapter.uploads:
            bundle = read_bundle(folder, required=True)
            upload_map = {
                attachment.name: bundle[attachment.document].path
                for attachment in adapter.uploads
            }
        def after_load(page) -> None:
            nonlocal count
            if heartbeat():
                raise NavigationDenied("Annulation demandée avant remplissage.")
            count = fill_known_fields(page, adapter, values, upload_map)
            if heartbeat():
                raise NavigationDenied("Annulation pendant le remplissage.")
        result = show(url, heartbeat, max_seconds=max_seconds, after_load=after_load)
        if result not in ("closed", "cancelled", "timeout"):
            raise RuntimeError("État navigateur invalide.")
        outcome = "cancelled" if result == "cancelled" else "queued"
        reason = "Aperçu terminé, sans soumission. Champs remplis dans une session éphémère."
    except CleanupUncertain:
        # Never release if the browser may still be running.
        raise
    except NavigationDenied:
        outcome = "blocked"
        reason = "Adaptateur, formulaire, destination ou autorisation refusé."
    except Exception:
        outcome = "failed"
        reason = "Erreur de remplissage ; aucune soumission autorisée."
    # show() closes the context and browser before returning.
    returned = queue.release(app_id, token, outcome=outcome)
    return FillResult(returned["status"], app_id, count, reason)
