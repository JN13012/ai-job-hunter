import httpx
import pytest

from ai_job_hunter.collectors.greenhouse import CollectorError, fetch_board


def test_public_api_request(board, greenhouse_payload):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=greenhouse_payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_board(client, board)
    assert result.jobs == greenhouse_payload["jobs"]
    assert result.errors == ()
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == "https://boards-api.greenhouse.io/v1/boards/fixture-board/jobs?content=true"
    assert "authorization" not in requests[0].headers


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
def test_network_failure(board, error_type):
    def handler(request):
        raise error_type("simulated failure", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CollectorError, match="simulated failure"):
            fetch_board(client, board)


@pytest.mark.parametrize("status", [206, 301, 403, 404, 429, 500])
def test_http_failure_without_retry(board, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "60"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CollectorError, match=f"HTTP {status}") as exc:
            fetch_board(client, board)
    assert len(calls) == 1
    assert exc.value.rate_limited == (status == 429)


@pytest.mark.parametrize("body", [b"not JSON", b'{"jobs": [', b'{"jobs": NaN}', b"\xff", b"[]", b"{}", b'{"jobs": null}'])
def test_invalid_response(board, body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))) as client:
        with pytest.raises(CollectorError):
            fetch_board(client, board)


def test_partial_response_reports_error(board):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"jobs": [{"id": 1}], "meta": {"total": 2}})
    )) as client:
        result = fetch_board(client, board)
    assert len(result.jobs) == 1
    assert "partielle" in result.errors[0]


def test_response_size_limit(board, monkeypatch):
    monkeypatch.setattr("ai_job_hunter.collectors.greenhouse.MAX_RESPONSE_BYTES", 5)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'{"jobs": []}'))) as client:
        with pytest.raises(CollectorError, match="volumineuse"):
            fetch_board(client, board)


def test_record_count_limit(board, monkeypatch):
    monkeypatch.setattr("ai_job_hunter.collectors.greenhouse.MAX_JOBS", 1)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}]}))) as client:
        with pytest.raises(CollectorError, match="limite"):
            fetch_board(client, board)
