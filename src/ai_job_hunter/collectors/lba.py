"""GET officiel La Bonne Alternance ; aucune soumission."""

from urllib.parse import urlencode

import httpx

from ai_job_hunter.collectors.common import CollectorError, fetch_json, MAX_JOBS
from ai_job_hunter.config import LBAConfig

ENDPOINT = "https://api.apprentissage.beta.gouv.fr/api/job/v1/search"


def fetch_opportunities(client: httpx.Client, settings: LBAConfig, api_key: str) -> dict:
    if not settings.romes or not settings.departements:
        raise CollectorError("Configurer lba.romes et lba.departements avant la collecte.")
    if not api_key or any(ch.isspace() for ch in api_key):
        raise CollectorError("Clé API LBA absente ou invalide.")
    params = [("romes", ",".join(settings.romes))]
    params.extend(("departements", code) for code in settings.departements)
    if settings.target_diploma_level is not None:
        params.append(("target_diploma_level", settings.target_diploma_level))
    # Client dédié : l'authentification ne doit jamais être envoyée vers un autre hôte.
    client.headers["Authorization"] = f"Bearer {api_key}"
    try:
        payload = fetch_json(client, f"{ENDPOINT}?{urlencode(params)}")
    except CollectorError as exc:
        # Ne jamais publier une exception susceptible de contenir un secret.
        message = str(exc).replace(api_key, "[MASQUÉ]")
        if exc.rate_limited:
            raise CollectorError("HTTP 429 : quota atteint ; aucun retry.", rate_limited=True) from None
        if message.startswith(("HTTP 401", "HTTP 403")):
            raise CollectorError(message[:8] + " : vérifier la clé et les droits.") from None
        raise CollectorError(message) from None
    finally:
        client.headers.pop("Authorization", None)
    if not isinstance(payload, dict) or any(
        not isinstance(payload.get(field), list) for field in ("jobs", "recruiters")
    ):
        raise CollectorError("Réponse LBA invalide : tableaux jobs et recruiters requis.")
    if sum(len(payload[field]) for field in ("jobs", "recruiters")) > MAX_JOBS:
        raise CollectorError("Réponse LBA trop volumineuse.")
    return payload
