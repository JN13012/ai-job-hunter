"""M6 : dossiers de candidature déterministes locaux, toujours non soumis."""

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re

from ai_job_hunter.candidate import Candidate
from ai_job_hunter.channel_routing import route_jobs
from ai_job_hunter.matcher import Profile, normalize, terms_in
from ai_job_hunter.storage import JobStore


def _line(value: str | None) -> str:
    # Le texte des annonces et les faits personnels ne sont jamais des instructions
    # de contrôle. Ils ne doivent pas pouvoir créer de faux titres Markdown.
    text = " ".join((value or "").split())
    text = re.sub(r"([\\\`*_{}\[\]<>|])", r"\\\1", text)
    return text


def _bullets(items: tuple[str, ...]) -> str:
    return "\n".join("- " + _line(item) for item in items) if items else "- Non renseigné"


def _relevant_skills(candidate: Candidate, title: str, description: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    context = normalize(title + "\n" + description)
    known = tuple(s for s in candidate.skills if normalize(s) and terms_in(context, (normalize(s),)))
    learning = tuple(s for s in candidate.learning if normalize(s) and terms_in(context, (normalize(s),)))
    return known, learning


def compose_draft(candidate: Candidate, route: dict, description: str | None) -> dict:
    """Le contenu est du Markdown, pas un CV PDF ni un fichier prêt à déposer."""
    chosen = route.get("selected")
    if not route.get("ready_for_preparation") or not isinstance(chosen, dict):
        raise ValueError("Cette offre ne dispose pas d'un canal préparatoire éligible.")
    company = _line(route.get("company"))
    title = _line(route.get("title") or "poste proposé")
    relevant, learning = _relevant_skills(candidate, route.get("title") or "", description or "")
    intro = _line(candidate.motivation) if candidate.motivation else (
        "Je souhaite rejoindre votre équipe pour contribuer à ses projets et développer mes compétences."
    )
    cv = "\n\n".join([
        "# " + _line(candidate.full_name),
        _line(candidate.headline or "Candidature"),
        "**Contact :** " + _line(candidate.email)
        + (" | " + _line(candidate.city) if candidate.city else "")
        + (" | " + _line(candidate.phone) if candidate.phone else ""),
        "**Portfolio :** " + _line(candidate.portfolio_url) if candidate.portfolio_url else "",
        "## Compétences déclarées\n" + _bullets(candidate.skills),
        "## Compétences en cours d'acquisition (non acquises)\n" + _bullets(candidate.learning) if candidate.learning else "",
        "## Expériences déclarées\n" + _bullets(candidate.experiences),
        "## Formation déclarée\n" + _bullets(candidate.education),
        "## Certifications déclarées\n" + _bullets(candidate.certifications) if candidate.certifications else "",
        "## Projets déclarés\n" + _bullets(candidate.projects),
        "## Langues déclarées\n" + _bullets(candidate.languages),
    ]).strip() + "\n"
    evidence = ", ".join(_line(x) for x in relevant)
    skills_sentence = (
        "Parmi mes compétences déclarées, " + evidence + " correspondent aux éléments de votre annonce."
        if relevant else "Je souhaite mettre à profit mes compétences et développer celles nécessaires au poste."
    )
    learning_sentence = (
        "Je suis actuellement en cours d'apprentissage de : "
        + ", ".join(_line(x) for x in learning) + "."
        if learning else ""
    )
    letter = "\n\n".join(p for p in [
        f"**Objet : candidature — {title} — {company}**",
        "Madame, Monsieur,",
        f"Je vous présente ma candidature pour le poste de {title} au sein de {company}.",
        intro,
        skills_sentence,
        learning_sentence,
        ("**Disponibilité déclarée :** " + _line(candidate.availability))
        if candidate.availability else "",
        ("**Rythme d'alternance déclaré :** " + _line(candidate.alternance_rhythm))
        if candidate.alternance_rhythm else "",
        "Je reste disponible pour échanger sur les besoins du poste et mon parcours.",
        "Cordialement,\n" + _line(candidate.full_name),
    ] if p).strip() + "\n"
    return {
        "job_id": route["job_id"], "company": route["company"],
        "title": route["title"], "match_score": route["match_score"],
        "routing_status": route["routing_status"],
        "channel": chosen, "cv_markdown": cv, "letter_markdown": letter,
        "declared_relevant_skills": list(relevant),
        "learning_relevant": list(learning),
        "answers": dict(candidate.answers),
        "review_required": True, "ready_for_submission": False,
        "automation_authorized": False, "human_final_submit_required": True,
    }


def prepare_job(
    store: JobStore, profile: Profile, candidate: Candidate, job_id: str,
    *, output_dir: Path, catalog: tuple = (), include_review: bool = False,
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
        raise ValueError("Identifiant d'offre invalide.")
    routes = route_jobs(store, profile, catalog=catalog, include_review=include_review)
    selected = next((r for r in routes if r["job_id"] == job_id), None)
    if selected is None:
        raise ValueError("Offre absente, exclue ou pas éligible selon M3.")
    record = store.connection.execute("SELECT description FROM jobs WHERE id=?", (job_id,)).fetchone()
    if record is None:
        raise ValueError("Offre introuvable.")
    prepared = compose_draft(candidate, selected, record["description"])
    content = json.dumps(prepared, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()[:16]
    if output_dir.is_symlink():
        raise ValueError("Répertoire de brouillons symbolique interdit.")
    destination = output_dir / (job_id + "-" + digest)
    if destination.exists():
        raise ValueError("Dossier déjà présent : aucun fichier écrasé.")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.mkdir(mode=0o700)
    for filename, text in (
        ("cv.md", prepared["cv_markdown"]),
        ("letter.md", prepared["letter_markdown"]),
        ("answers.json", json.dumps(prepared["answers"], ensure_ascii=False, indent=2)),
        ("manifest.json", json.dumps({
            key: value for key, value in prepared.items()
            if key not in ("cv_markdown", "letter_markdown", "answers")
        } | {"status": "draft_requires_human_review",
             "created_at": datetime.now(UTC).isoformat()}, ensure_ascii=False, indent=2)),
    ):
        file = destination / filename
        with file.open("x", encoding="utf-8") as stream:
            stream.write(text + ("" if text.endswith("\n") else "\n"))
        file.chmod(0o600)
    return {
        "job_id": job_id, "directory": str(destination),
        "status": "draft_requires_human_review",
        "files": ["cv.md", "letter.md", "answers.json", "manifest.json"],
        "human_final_submit_required": True,
    }
