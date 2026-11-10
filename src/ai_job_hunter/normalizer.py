"""Normalisation Greenhouse : aucune inférence sur les données absentes."""

from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from ai_job_hunter.config import GreenhouseBoard
from ai_job_hunter.models import Job


class _DescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden.append(tag)
        elif not self.hidden and tag in {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def strip_description_html(content: str) -> str:
    # Greenhouse peut livrer du HTML encodé en entités. Décodage borné à deux
    # couches de balises, puis parsing unique pour préserver le texte littéral.
    for _ in range(2):
        if not re.search(r"&(?:amp;)?(?:lt|#0*60|#x0*3c);/?[A-Za-z]", content, re.IGNORECASE):
            break
        content = unescape(content)
    parser = _DescriptionParser()
    parser.feed(content)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def normalize_greenhouse(
    raw: Any, board: GreenhouseBoard, endpoint: str, observed_at: datetime,
) -> Job:
    if not isinstance(raw, dict):
        raise ValueError("L'offre doit être un objet JSON.")
    external = raw.get("id")
    if (isinstance(external, bool) or not isinstance(external, (int, str))
            or not re.fullmatch(r"[0-9]+", str(external)) or int(external) <= 0):
        raise ValueError("Identifiant externe absent ou invalide.")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("La date d'observation doit avoir un fuseau horaire.")

    def optional_string(value: Any, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Champ {field} invalide : chaîne attendue.")
        return value

    title = optional_string(raw.get("title"), "title")
    content = optional_string(raw.get("content"), "content")
    location = raw.get("location")
    if location is not None and not isinstance(location, dict):
        raise ValueError("Champ location invalide : objet attendu.")
    location_raw = optional_string(location.get("name") if location else None, "location.name")
    url = optional_string(raw.get("absolute_url"), "absolute_url")
    if url is not None and (urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc):
        raise ValueError("URL de candidature invalide.")
    updated_at = optional_string(raw.get("updated_at"), "updated_at")
    identity = json.dumps(["greenhouse", board.board_token, str(external)])
    return Job(
        id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        external_id=str(external), source="greenhouse", board_token=board.board_token,
        company=board.company, title=title, location_raw=location_raw,
        description=strip_description_html(content) if content is not None else None,
        application_url=url, updated_at=updated_at,
        first_seen=observed_at, last_seen=observed_at,
        provenance={"endpoint": endpoint, "company_origin": "configuration",
                    "normalizer_version": 1, "raw": raw},
    )
