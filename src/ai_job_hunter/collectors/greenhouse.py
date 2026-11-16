"""Accès GET à l'API publique Job Board Greenhouse, sans scraping."""


import httpx

from ai_job_hunter.config import GreenhouseBoard

from ai_job_hunter.collectors.common import BoardResponse, CollectorError, MAX_JOBS, fetch_json

# Compatibilité des imports M1.
from ai_job_hunter.collectors.common import MAX_RESPONSE_BYTES


def fetch_board(client: httpx.Client, board: GreenhouseBoard) -> BoardResponse:
    endpoint = f"https://boards-api.greenhouse.io/v1/boards/{board.board_token}/jobs?content=true"
    payload = fetch_json(client, endpoint)
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
