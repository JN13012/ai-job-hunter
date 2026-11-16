"""La limite de taille du transport partagé reste active pour les ATS."""

import httpx
import pytest

from ai_job_hunter.collectors import common


def test_shared_response_size_limit(monkeypatch):
    monkeypatch.setattr(common, "MAX_RESPONSE_BYTES", 5)
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b'{"jobs": []}')
    )) as client:
        with pytest.raises(common.CollectorError, match="volumineuse"):
            common.fetch_json(client, "https://example.test/jobs")


def test_response_exactly_at_limit():
    body = b'{"jobs": []}'
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body)
    )) as client:
        assert common.fetch_json(
            client, "https://example.test/jobs", max_response_bytes=len(body)
        ) == {"jobs": []}
