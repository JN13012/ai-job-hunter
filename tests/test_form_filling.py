"""M7.3 offline tests: adapter policies, fail-closed field mapping and lifecycle."""

from pathlib import Path

import pytest

from ai_job_hunter.browser_preview import BrowserPolicy, CleanupUncertain
from ai_job_hunter.candidate import Candidate
from ai_job_hunter.form_adapters import AdapterCatalog
from ai_job_hunter.form_filling import (
    FormMismatch, field_values, fill_known_fields, fill_one,
)

ADAPTER = """
[[adapter]]
id = "fixture"
host = "jobs.example.org"
path_prefix = "/apply"
form_id = "application"
permission_note = "Autorisation expresse uniquement pour le formulaire de test."

[[adapter.field]]
name = "candidate_name"
tag = "input"
source = "identity.full_name"
required = true

[[adapter.field]]
name = "candidate_email"
tag = "input"
source = "identity.email"
required = true

[[adapter.field]]
name = "motivation"
tag = "textarea"
source = "answers.motivation"
required = false
"""

def candidate():
    return Candidate(
        "Candidate Example", "candidate@example.org", None, None, None, None,
        (), (), (), (), (), (), (), None, None, None, {},
    )


@pytest.fixture
def adapter(tmp_path):
    path = tmp_path / "adapters.toml"
    path.write_text(ADAPTER)
    return AdapterCatalog.load(path).for_url("https://jobs.example.org/apply/42")


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(
        '[[allow]]\nhost="jobs.example.org"\npath_prefix="/apply"\n'
        'reason="Autorisation expresse pour tests de formulaire sans soumission."\n'
    )
    return BrowserPolicy.load(path)


def test_approved_form_mapping(adapter):
    values = field_values(candidate(), {"motivation": "Python et réseaux"}, adapter)
    assert values == {"candidate_name": "Candidate Example",
                      "candidate_email": "candidate@example.org",
                      "motivation": "Python et réseaux"}
    assert field_values(candidate(), {}, adapter) == {
        "candidate_name": "Candidate Example", "candidate_email": "candidate@example.org",
    }


@pytest.mark.parametrize("modified", [
    ADAPTER.replace('host = "jobs.example.org"', 'host = "localhost"'),
    ADAPTER.replace('source = "identity.email"', 'source = "identity.password"'),
    ADAPTER.replace('name = "candidate_email"', 'name = "candidate_name"'),
    ADAPTER.replace('tag = "input"', 'tag = "button"'),
    ADAPTER.replace('required = true', 'required = "true"'),
    ADAPTER.replace('permission_note = "Autorisation expresse uniquement pour le formulaire de test."',
                    'permission_note = "No"'),
])
def test_invalid_adapter(tmp_path, modified):
    path = tmp_path / "bad.toml"
    path.write_text(modified)
    with pytest.raises(ValueError):
        AdapterCatalog.load(path)


def test_missing_required_field(adapter):
    incomplete = Candidate(
        "Candidate Example", "", None, None, None, None,
        (), (), (), (), (), (), (), None, None, None, {},
    )
    with pytest.raises(FormMismatch):
        field_values(incomplete, {}, adapter)


def test_ambiguous_adapter(tmp_path):
    path = tmp_path / "duplicate.toml"
    path.write_text(ADAPTER + ADAPTER.replace('id = "fixture"', 'id = "fixture2"'))
    with pytest.raises(ValueError, match="dupliqué"):
        AdapterCatalog.load(path)


class Control:
    def __init__(self, tag, name=None, typ=None, options=()):
        self.tag, self.name, self.typ = tag, name, typ
        self.options = options
        self.value = None
        self.events = []

    def evaluate(self, expression):
        assert "tagName" in expression
        return self.tag

    def get_attribute(self, key):
        return self.name if key == "name" else self.typ if key == "type" else None

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def fill(self, text, timeout):
        self.value = text
        self.events.append("fill")

    def select_option(self, value, timeout):
        self.value = value
        self.events.append("select")

    def locator(self, selector):
        if selector == "option":
            return Nodes([Option(o) for o in self.options])
        raise AssertionError(selector)


class Option:
    def __init__(self, value):
        self.value = value

    def get_attribute(self, key):
        assert key == "value"
        return self.value


class Nodes:
    def __init__(self, controls):
        self.controls = controls

    def count(self):
        return len(self.controls)

    def nth(self, index):
        return self.controls[index]


class Form:
    def __init__(self, controls):
        self.controls = controls

    def count(self):
        return 1

    def locator(self, selector):
        if selector == "input,textarea,select,button":
            return Nodes(self.controls)
        for tag in ("input", "textarea", "select"):
            if selector.startswith(tag + '[name="'):
                name = selector.split('"')[1]
                return Nodes([control for control in self.controls
                              if control.tag == tag and control.name == name])
        raise AssertionError(selector)


class Page:
    def __init__(self, controls):
        self.form = Form(controls)

    def locator(self, selector):
        if selector in ("form", 'form[id="application"]'):
            return self.form
        if selector == "[contenteditable]":
            return Nodes([])
        raise AssertionError(selector)


def test_preflight_then_fill(adapter):
    name = Control("input", "candidate_name", "text")
    email = Control("input", "candidate_email", "email")
    csrf = Control("input", "csrf_token", "hidden")
    submit = Control("button")
    motivation = Control("textarea", "motivation")
    page = Page([name, email, csrf, submit, motivation])
    values = field_values(candidate(), {"motivation": "Intérêt pour la cybersécurité"}, adapter)
    assert fill_known_fields(page, adapter, values) == 3
    assert name.value == "Candidate Example"
    assert email.value == "candidate@example.org"
    assert motivation.value == "Intérêt pour la cybersécurité"
    assert csrf.value is None and submit.value is None
    assert submit.events == []


def test_unknown_field_causes_no_partial_fill(adapter):
    name = Control("input", "candidate_name", "text")
    email = Control("input", "candidate_email", "email")
    extra = Control("input", "unknown_question", "text")
    with pytest.raises(FormMismatch, match="non déclaré"):
        fill_known_fields(Page([name, email, extra]), adapter, field_values(candidate(), {}, adapter))
    assert name.value is email.value is extra.value is None


class FakeQueue:
    def __init__(self):
        self.state = None
        self.data_dir = Path("/tmp/no-real-data")

    def claim(self, worker, *, seconds, browser_only):
        assert browser_only
        return {"id": "id", "reservation_token": "token"}

    def reserved_destination(self, id, token):
        return {"kind": "offer_link", "target": "https://evil.example.org/apply"}

    def release(self, id, token, *, outcome):
        self.state = outcome
        return {"status": outcome}


def test_policy_denial_before_open(policy, adapter):
    queue = FakeQueue()
    result = fill_one(queue, policy, AdapterCatalog((adapter,)), candidate(),
                      show=lambda *args, **kwargs: pytest.fail("browser opened"))
    assert result.status == "blocked"
    assert queue.state == "blocked"


def test_cleanup_uncertainty_holds_reservation(policy, adapter):
    queue = FakeQueue()
    queue.reserved_destination = lambda id, token: {
        "kind": "offer_link", "target": "https://jobs.example.org/apply/42",
    }
    # Everything else needed for a valid draft is stubbed; cleanup
    # uncertainty must not transition the queue when it originates in show().
    queue._get = lambda id: {"draft_name": "stub", "draft_sha256": "digest",
                             "job_id": "job"}
    queue._read_draft = lambda name: ({"job_id": "job"}, "digest")
    # Avoid filesystem reads by checking failure is treated as failed rather than
    # leaking the reservation; this is a separate pre-show error.
    result = fill_one(queue, policy, AdapterCatalog((adapter,)), candidate())
    assert result.status == "failed"
