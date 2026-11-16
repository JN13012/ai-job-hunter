"""Normalisation déterministe des offres ATS M2.1."""

from datetime import datetime
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from ai_job_hunter.config import GreenhouseBoard
from ai_job_hunter.models import Job
from ai_job_hunter.normalizer import strip_description_html


def _string(value: Any, field: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Champ {field} invalide.")
    return value


def _object(value: Any, field: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Champ {field} invalide.")
    return value


def _description(raw: dict, plain: str, html: str) -> str | None:
    text = _string(raw.get(plain), plain)
    if text is not None:
        return text
    text = _string(raw.get(html), html)
    return strip_description_html(text) if text is not None else None


def normalize_ats(raw: Any, board: GreenhouseBoard, endpoint: str,
                  observed_at: datetime, source: str) -> Job:
    if not isinstance(raw, dict):
        raise ValueError("L'offre doit être un objet JSON.")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Date d'observation sans fuseau.")
    external = raw.get("id")
    if source in {"lever", "lever_eu"}:
        title = _string(raw.get("text"), "text")
        location = _string(_object(raw.get("categories"), "categories").get("location"), "location")
        description = _description(raw, "descriptionPlain", "description")
        url = _string(raw.get("applyUrl"), "applyUrl") or _string(raw.get("hostedUrl"), "hostedUrl")
    elif source == "ashby":
        title = _string(raw.get("title"), "title")
        location = _string(raw.get("location"), "location")
        description = _description(raw, "descriptionPlain", "descriptionHtml")
        job_url = _string(raw.get("jobUrl"), "jobUrl")
        url = _string(raw.get("applyUrl"), "applyUrl") or job_url
        # L'API publique Ashby ne garantit pas un champ id ; le dernier segment
        # du lien officiel reste stable quand le titre change.
        if external is None and job_url:
            parsed = urlsplit(job_url)
            if parsed.scheme == "https" and parsed.hostname == "jobs.ashbyhq.com":
                segments = parsed.path.strip("/").split("/")
                if len(segments) == 2 and segments[0].casefold() == board.board_token.casefold():
                    external = segments[-1]
    elif source == "smartrecruiters":
        title = _string(raw.get("name"), "name")
        loc = _object(raw.get("location"), "location")
        location = ", ".join(value for key in ("city", "region", "country")
                             if (value := _string(loc.get(key), key))) or None
        sections = _object(_object(raw.get("jobAd"), "jobAd").get("sections"), "sections")
        parts = []
        for name in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
            text = _string(_object(sections.get(name), name).get("text"), "text")
            if text:
                parts.append(strip_description_html(text))
        description = "\n\n".join(parts) or None
        url = _string(raw.get("applyUrl"), "applyUrl")
    else:
        raise ValueError("Source ATS inconnue.")
    if not isinstance(external, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", external):
        raise ValueError("Identifiant externe absent ou invalide.")
    if url is not None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("URL de candidature invalide.")
    identity = json.dumps([source, board.board_token, external])
    return Job(id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
               external_id=external, source=source, board_token=board.board_token,
               company=board.company, title=title, location_raw=location,
               description=description, application_url=url, updated_at=None,
               first_seen=observed_at, last_seen=observed_at,
               provenance={"endpoint": endpoint, "company_origin": "configuration",
                           "normalizer_version": 1,
                           "raw": {k: v for k, v in raw.items() if k != "creator"}})
