from datetime import UTC, datetime

import pytest

from ai_job_hunter.config import GreenhouseBoard
from ai_job_hunter.normalizer import normalize_greenhouse, strip_description_html

OBSERVED = datetime(2026, 11, 10, tzinfo=UTC)


def test_normalized_fields_and_provenance(job, greenhouse_payload):
    assert job.external_id == "101"
    assert len(job.id) == 64
    assert job.source == "greenhouse"
    assert job.board_token == "fixture-board"
    assert job.company == "Entreprise de test"
    assert job.title == "Alternance ingénierie IA"
    assert job.location_raw == "Paris, France (hybride)"
    assert job.description == "Python & SQL\nAnalyse de données\nTests automatisés"
    assert job.application_url == greenhouse_payload["jobs"][0]["absolute_url"]
    assert job.updated_at == "2026-11-09T10:00:00+01:00"
    assert job.provenance["raw"] == greenhouse_payload["jobs"][0]
    assert job.provenance["company_origin"] == "configuration"
    assert job.first_seen == job.last_seen


def test_missing_fields_are_unknown(greenhouse_payload, board):
    job = normalize_greenhouse(greenhouse_payload["jobs"][1], board, "endpoint", OBSERVED)
    assert (job.title, job.description, job.application_url, job.location_raw, job.updated_at) == (None,) * 5
    assert job.provenance["raw"]["internal_job_id"] is None


def test_identity_is_stable_and_scoped_to_board(board):
    original = normalize_greenhouse({"id": 101}, board, "endpoint", OBSERVED)
    changed = normalize_greenhouse({"id": 101, "title": "Changed"}, board, "endpoint", OBSERVED)
    other = normalize_greenhouse({"id": 101}, GreenhouseBoard("Other", "other"), "endpoint", OBSERVED)
    string_id = normalize_greenhouse({"id": "101"}, board, "endpoint", OBSERVED)
    assert original.id == changed.id == string_id.id
    assert original.content_hash != changed.content_hash
    assert original.id != other.id


@pytest.mark.parametrize(("html", "expected"), [
    ("<p>Python <strong>3.12</strong></p><p>SQL<br>Tests</p>", "Python 3.12\nSQL\nTests"),
    ("&lt;p&gt;IA &amp; sécurité&lt;/p&gt;", "IA & sécurité"),
    ("&amp;lt;p&amp;gt;IA&amp;lt;/p&amp;gt;", "IA"),
    ("<p>A</p><script>alert('bad')</script><style>body{}</style><p>B</p>", "A\nB"),
    ("<!-- comment -->Texte&nbsp; simple &amp; utile", "Texte simple & utile"),
    ("x &lt; 5 et y &gt; 3", "x < 5 et y > 3"),
    ("<p>HTML <b>incomplet", "HTML incomplet"),
    ("", ""),
])
def test_html_stripping(html, expected):
    assert strip_description_html(html) == expected


@pytest.mark.parametrize("raw", [
    None, [], {}, {"id": True}, {"id": -1}, {"id": 0}, {"id": "abc"},
    {"id": 1, "title": 42}, {"id": 1, "content": []},
    {"id": 1, "location": "Paris"}, {"id": 1, "location": {"name": 4}},
    {"id": 1, "absolute_url": "javascript:alert(1)"},
    {"id": 1, "absolute_url": "/jobs/1"}, {"id": 1, "updated_at": 42},
])
def test_malformed_records_are_rejected(raw, board):
    with pytest.raises(ValueError):
        normalize_greenhouse(raw, board, "endpoint", OBSERVED)


def test_observations_require_timezone(board):
    with pytest.raises(ValueError, match="fuseau"):
        normalize_greenhouse({"id": 1}, board, "endpoint", datetime(2026, 1, 1))
