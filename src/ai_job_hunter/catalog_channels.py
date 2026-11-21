"""M5.2 : qualification statique des canaux du catalogue, zéro accès réseau."""

from ai_job_hunter.catalog import Target
from ai_job_hunter.contacts import normalize_contact, save_contacts
from ai_job_hunter.storage import JobStore, WriteCounts


def qualify_catalog(store: JobStore, targets: tuple[Target, ...]) -> list[dict]:
    """Distinguer page carrières, ATS et formulaire explicitement déclaré.

    Jamais de déduction d'URL de formulaire ni de correspondance floue de
    raisons sociales. Le champ application_form_url est une déclaration, pas
    une preuve d'existence ou d'autorisation d'automatisation.
    """
    exists = store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_targets'"
    ).fetchone()
    known = set()
    if exists:
        known = {row[0] for row in store.connection.execute("SELECT siren FROM company_targets")}
    reports = []
    for target in targets:
        if not target.siren:
            status = "missing_siren"
        elif target.siren not in known:
            status = "company_not_in_m4"
        elif not target.application_form_url:
            status = "form_not_declared"
        else:
            status = "ready_for_review"
        # ATS jobs can offer individual apply links; they are not generic
        # company application forms and are never inserted here.
        application_jobs = 0
        if target.source != "manual" and target.board_token:
            application_jobs = store.connection.execute(
                """SELECT COUNT(*) FROM jobs
                   WHERE source=? AND board_token=?
                   AND application_url IS NOT NULL AND application_url != ''""",
                (target.source, target.board_token),
            ).fetchone()[0]
        reports.append({
            "company": target.company, "siren": target.siren,
            "source": target.source, "career_url": target.career_url,
            "channel_kind": ("declared_form" if target.application_form_url else
                             "ats_board" if target.source != "manual" else "careers_page"),
            "application_form_url": target.application_form_url,
            "job_application_links": application_jobs,
            "status": status,
            "verified": False,
        })
    return reports


def register_catalog_forms(store: JobStore, candidates: list[dict]) -> WriteCounts:
    """Enregistrer seulement les formulaires explicites, toujours en attente."""
    records = []
    for candidate in candidates:
        if candidate["status"] != "ready_for_review":
            continue
        records.append(normalize_contact({
            "siren": candidate["siren"], "channel": "form",
            "target": candidate["application_form_url"], "role": "careers",
            "source_url": candidate["career_url"],
            "source_type": "company_careers",
        }))
    return save_contacts(store, tuple(records))
