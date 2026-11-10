"""Offre normalisée indépendante des collecteurs et de SQLite."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class Job:
    id: str
    external_id: str
    source: str
    board_token: str
    company: str
    title: str | None
    location_raw: str | None
    description: str | None
    application_url: str | None
    updated_at: str | None  # Valeur source, sans la confondre avec une observation.
    first_seen: datetime
    last_seen: datetime
    provenance: dict[str, Any]

    @property
    def content_hash(self) -> str:
        # Les observations ne changent pas le contenu métier d'une offre.
        payload = {
            "company": self.company, "title": self.title,
            "location_raw": self.location_raw, "description": self.description,
            "application_url": self.application_url, "updated_at": self.updated_at,
            "provenance": self.provenance,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
