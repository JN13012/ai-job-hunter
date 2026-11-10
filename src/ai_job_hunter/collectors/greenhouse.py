"""Accès GET à l'API publique Job Board Greenhouse, sans scraping."""

from dataclasses import dataclass
import json
from typing import Any

import httpx

from ai_job_hunter.config import GreenhouseBoard

MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_JOBS = 10_000


class CollectorError(ValueError):
    def __init__(self, message: str, *, rate_limited: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited


@dataclass(frozen=True)
class BoardResponse:
    jobs: list[Any]
    endpoint: str
    errors: tuple[str, ...] = ()


def fetch_board(client: httpx.Client, board: GreenhouseBoard) -> BoardResponse:
    endpoint = f"https://boards-api.greenhouse.io/v1/boards/{board.board_token}/jobs?content=true"
    try:
        with client.stream("GET", endpoint) as response:
            if response.status_code != 200:
                detail = f"HTTP {response.status_code}"
                if response.status_code == 429:
                    detail += f" ; Retry-After={response.headers.get('Retry-After', 'non fourni')}"
                raise CollectorError(detail, rate_limited=response.status_code == 429)
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise CollectorError("Réponse trop volumineuse (maximum 20 Mio).")
        # JSON non fini (NaN/Infinity) n'est pas une donnée source valide.
        def reject_constant(value: str) -> None:
            raise ValueError(f"Constante JSON invalide : {value}")

        payload = json.loads(body, parse_constant=reject_constant)
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        if isinstance(exc, CollectorError):
            raise
        raise CollectorError(f"Erreur réseau ou JSON invalide : {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise CollectorError("Réponse invalide : tableau jobs manquant.")
    jobs = payload["jobs"]
    if len(jobs) > MAX_JOBS:
        raise CollectorError("Réponse au-delà de la limite de 10000 offres.")
    errors = []
    meta = payload.get("meta")
    if meta is not None:
        total = meta.get("total") if isinstance(meta, dict) else None
        if isinstance(total, bool) or not isinstance(total, int) or total != len(jobs):
            errors.append("Réponse partielle ou meta.total invalide ; seules les offres reçues seront traitées.")
    return BoardResponse(jobs=jobs, endpoint=endpoint, errors=tuple(errors))
