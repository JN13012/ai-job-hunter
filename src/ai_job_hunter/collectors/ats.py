"""Collecte read-only des autres ATS, sur des boards déclarés."""

import re
import time

import httpx

from ai_job_hunter.collectors.common import BoardResponse, CollectorError, MAX_JOBS, fetch_json
from ai_job_hunter.config import GreenhouseBoard

PAGE_SIZE = 100
MAX_PAGES = 100
# Les détails SmartRecruiters coûtent un GET par offre : limite explicite.
MAX_SMART_DETAILS = 100


def fetch_ats(client: httpx.Client, board: GreenhouseBoard, source: str,
              interval: float) -> BoardResponse:
    token = board.board_token
    jobs, errors = [], []
    if source in {"lever", "lever_eu"}:
        host = "api.eu.lever.co" if source == "lever_eu" else "api.lever.co"
        base = f"https://{host}/v0/postings/{token}"
        for page in range(MAX_PAGES):
            if page:
                time.sleep(interval)
            data = fetch_json(client, f"{base}?mode=json&skip={page * PAGE_SIZE}&limit={PAGE_SIZE}")
            if not isinstance(data, list) or len(data) > PAGE_SIZE:
                raise CollectorError("Réponse Lever invalide ou page trop grande.")
            jobs.extend(data)
            if len(data) < PAGE_SIZE:
                break
        else:
            errors.append("Limite de pagination Lever atteinte ; collecte potentiellement partielle.")
        return BoardResponse(jobs, base, tuple(errors))
    if source == "ashby":
        base = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
        data = fetch_json(client, base)
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise CollectorError("Réponse Ashby invalide : jobs manquant.")
        if len(data["jobs"]) > MAX_JOBS:
            raise CollectorError("Réponse Ashby trop volumineuse.")
        # Les annonces non listées sont destinées aux liens directs uniquement.
        jobs = [item for item in data["jobs"]
                if not isinstance(item, dict) or item.get("isListed") is not False]
        return BoardResponse(jobs, base)
    if source != "smartrecruiters":
        raise ValueError("Source ATS inconnue.")
    base = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    # M2.1 : pagination des listes ; enrichissement borné à 100 offres.
    # Au-delà de cette limite, conserver les métadonnées publiques sans inventer
    # de description ou d'URL de candidature.
    for page in range(MAX_PAGES):
        if page:
            time.sleep(interval)
        data = fetch_json(client, f"{base}?destination=PUBLIC&limit={PAGE_SIZE}&offset={page * PAGE_SIZE}")
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise CollectorError("Réponse SmartRecruiters invalide : content manquant.")
        batch = data["content"]
        if len(batch) > PAGE_SIZE:
            raise CollectorError("Page SmartRecruiters trop grande.")
        jobs.extend(batch)
        total = data.get("totalFound")
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0 and len(jobs) >= total:
            break
        if len(batch) < PAGE_SIZE:
            if isinstance(total, int) and total > len(jobs):
                errors.append("Réponse SmartRecruiters partielle.")
            break
    else:
        errors.append("Limite de pagination SmartRecruiters atteinte.")
    enriched = []
    for index, item in enumerate(jobs):
        if index >= MAX_SMART_DETAILS or not isinstance(item, dict):
            enriched.append(item)
            continue
        posting_id = item.get("id")
        if not isinstance(posting_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", posting_id):
            enriched.append(item)
            continue
        time.sleep(interval)
        try:
            detail = fetch_json(client, f"{base}/{posting_id}")
            if not isinstance(detail, dict) or detail.get("id") != posting_id:
                raise CollectorError("Détail invalide ou identité différente.")
            enriched.append({**item, **detail})
        except CollectorError as exc:
            errors.append(f"{posting_id} : {exc}")
            enriched.append(item)
            if exc.rate_limited:
                enriched.extend(jobs[index + 1:])
                return BoardResponse(enriched, base, tuple(errors), rate_limited=True)
    if len(jobs) > MAX_SMART_DETAILS:
        errors.append("Limite de 100 détails SmartRecruiters atteinte ; offres restantes non enrichies.")
    return BoardResponse(enriched, base, tuple(errors))
