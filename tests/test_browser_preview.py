"""M7.2 navigation policy and worker lifecycle, offline only."""

import pytest
from ai_job_hunter.browser_preview import (
    BrowserPolicy, NavigationDenied, CleanupUncertain, StrictDocumentRoute,
    preview_one, _canonical_url,
)


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text('[[allow]]\nhost="jobs.example.org"\npath_prefix="/apply"\n'
                    'reason="Autorisation explicite pour consultation du domaine de test."\n')
    return BrowserPolicy.load(path)


def test_allowlist_and_navigation_denials(policy):
    assert policy.allows("https://jobs.example.org/apply/123?job=123")
    assert not policy.allows("https://jobs.example.org/applications")
    assert not policy.allows("https://evil.jobs.example.org/apply")
    for url in ("http://jobs.example.org/apply", "https://localhost/apply",
                "https://127.0.0.1/apply", "https://jobs.example.org/apply/../x",
                "https://jobs.example.org/apply#fragment", "https://a:b@jobs.example.org/apply"):
        with pytest.raises(NavigationDenied):
            _canonical_url(url)


def test_policy_requires_documented_permission(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('[[allow]]\nhost="localhost"\npath_prefix="/"\nreason="Permission documentée pour cet hôte."')
    with pytest.raises(ValueError):
        BrowserPolicy.load(path)


class Request:
    def __init__(self, url, method="GET", resource_type="document", navigation=True):
        self.url, self.method, self.resource_type, self.navigation = url, method, resource_type, navigation

    def is_navigation_request(self):
        return self.navigation


class Response:
    def __init__(self, status=200, content_type="text/html", data=b"<p>hello</p>"):
        self.status = status
        self.headers = {"content-type": content_type}
        self.data = data

    def body(self):
        return self.data


class Route:
    def __init__(self, request, response=None):
        self.request = request
        self.response = response or Response()
        self.aborted = self.fulfilled = False
        self.fetch_options = None

    def fetch(self, **options):
        self.fetch_options = options
        return self.response

    def abort(self):
        self.aborted = True

    def fulfill(self, **kwargs):
        self.fulfilled = True


@pytest.mark.parametrize("test_request", [
    Request("https://jobs.example.org/apply", method="POST"),
    Request("https://evil.example.org/apply"),
    Request("https://jobs.example.org/apply", resource_type="script", navigation=False),
])
def test_requests_blocked(test_request):
    route = Route(test_request)
    guard = StrictDocumentRoute("https://jobs.example.org/apply")
    guard(route)
    assert route.aborted and not route.fulfilled


@pytest.mark.parametrize("response", [
    Response(status=302), Response(content_type="application/json"),
    Response(data=b"x" * (2 * 1024 * 1024 + 1)),
])
def test_bad_response_blocked(response):
    guard = StrictDocumentRoute("https://jobs.example.org/apply")
    route = Route(Request("https://jobs.example.org/apply"), response)
    guard(route)
    assert route.aborted and guard.denied


def test_html_response_single_fetch():
    guard = StrictDocumentRoute("https://jobs.example.org/apply")
    route = Route(Request("https://jobs.example.org/apply"))
    guard(route)
    assert route.fulfilled and route.fetch_options["max_redirects"] == 0
    another = Route(Request("https://jobs.example.org/apply"))
    guard(another)
    assert another.aborted


class Queue:
    def __init__(self, target="https://jobs.example.org/apply"):
        self.target = target
        self.release_outcome = None
        self.cancel = False

    def claim(self, worker, *, seconds, browser_only):
        assert browser_only
        return {"id": "app", "reservation_token": "secret"}

    def reserved_destination(self, id, token):
        return {"kind": "offer_link", "target": self.target}

    def cancellation_requested(self, id, token):
        return self.cancel

    def renew(self, id, token, *, seconds):
        pass

    def release(self, id, token, *, outcome):
        self.release_outcome = outcome
        return {"status": outcome}


def test_preview_success_cancel_and_denied(policy):
    queue = Queue()
    assert preview_one(queue, policy, show=lambda target, check, **kw: "closed").status == "queued"
    assert queue.release_outcome == "queued"
    queue.cancel = True
    assert preview_one(queue, policy, show=lambda target, check, **kw: "cancelled" if check() else "closed").status == "cancelled"
    denied = Queue("https://other.example.org/apply")
    assert preview_one(denied, policy, show=lambda *args, **kw: pytest.fail("No browser")).status == "blocked"


def test_uncertain_cleanup_retains_reservation(policy):
    queue = Queue()
    def bad(*args, **kwargs):
        raise CleanupUncertain("Cannot confirm process stopped")
    with pytest.raises(CleanupUncertain):
        preview_one(queue, policy, show=bad)
    assert queue.release_outcome is None
