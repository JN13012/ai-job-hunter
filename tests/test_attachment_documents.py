"""M7.4: PDF integrity, fail-closed controls, no network or submission."""

from hashlib import sha256
import json
from pathlib import Path

import pytest

from ai_job_hunter.attachment_documents import build_bundle, read_bundle
from ai_job_hunter.form_adapters import AdapterCatalog, UploadBinding
from ai_job_hunter.form_filling import FormMismatch, fill_known_fields
from ai_job_hunter.form_adapters import FormAdapter, FieldBinding


@pytest.fixture
def draft(tmp_path):
    folder = tmp_path / "draft"
    folder.mkdir(mode=0o700)
    manifest = {
        "job_id": "a" * 64, "status": "draft_requires_human_review",
        "review_required": True, "ready_for_submission": False,
        "automation_authorized": False, "human_final_submit_required": True,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "cv.md").write_text("# Candidate Exemple\n\n## Skills\n- Python\n- Linux\n", encoding="utf-8")
    (folder / "letter.md").write_text("Madame, Monsieur,\n\nCandidature pour une alternance IA.\n", encoding="utf-8")
    return folder


def test_generate_verify_idempotent_refusal(draft):
    pytest.importorskip("reportlab")
    bundle = build_bundle(draft)
    assert set(bundle) == {"cv", "letter"}
    assert all(entry.path.read_bytes().startswith(b"%PDF-") for entry in bundle.values())
    assert all(entry.sha256 == sha256(entry.path.read_bytes()).hexdigest()
               for entry in bundle.values())
    assert read_bundle(draft, required=True) == bundle
    with pytest.raises(ValueError, match="déjà présentes"):
        build_bundle(draft)


def test_tampered_pdf_rejected(draft):
    pytest.importorskip("reportlab")
    build_bundle(draft)
    with (draft / "cv.pdf").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValueError, match="modifiée"):
        read_bundle(draft, required=True)


def test_source_changed_after_generation_rejected(draft):
    pytest.importorskip("reportlab")
    build_bundle(draft)
    (draft / "letter.md").write_text("Fausse expérience ajoutée après signature.")
    with pytest.raises(ValueError, match="modifié"):
        read_bundle(draft, required=True)


def test_symlink_and_orphan_rejected(draft):
    pytest.importorskip("reportlab")
    build_bundle(draft)
    (draft / "cv.pdf").unlink()
    (draft / "cv.pdf").symlink_to(draft / "letter.pdf")
    with pytest.raises(ValueError, match="symbolique"):
        read_bundle(draft, required=True)
    (draft / "attachments.json").unlink()
    with pytest.raises(ValueError, match="absent"):
        read_bundle(draft)


def test_reject_already_submitted_manifest(draft):
    data = json.loads((draft / "manifest.json").read_text())
    data["ready_for_submission"] = True
    (draft / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="non soumis"):
        build_bundle(draft)


UPLOAD_CONFIG = """
[[adapter]]
id="fixture"
host="jobs.example.org"
path_prefix="/apply"
form_id="application"
permission_note="Autorisation du domaine de tests explicitement documentée."

[[adapter.field]]
name="full_name"
tag="input"
source="identity.full_name"
required=true

[[adapter.upload]]
name="cv_attachment"
document="cv"
required=true
"""


def test_upload_adapter_validation(tmp_path):
    path = tmp_path / "adapters.toml"
    path.write_text(UPLOAD_CONFIG)
    form = AdapterCatalog.load(path).for_url("https://jobs.example.org/apply/123")
    assert len(form.uploads) == 1
    assert form.uploads[0].document == "cv"
    path.write_text(UPLOAD_CONFIG.replace('document="cv"', 'document="passport"'))
    with pytest.raises(ValueError, match="non autorisée"):
        AdapterCatalog.load(path)


class NodeSet:
    def __init__(self, values):
        self.values = values

    def count(self):
        return len(self.values)

    def nth(self, i):
        return self.values[i]


class Field:
    def __init__(self, name, typ="text"):
        self.name, self.typ = name, typ
        self.filled = None
        self.attached = None

    def evaluate(self, _):
        return "input"

    def get_attribute(self, attr):
        return {"type": self.typ, "name": self.name}.get(attr)

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def fill(self, text, timeout):
        self.filled = text

    def set_input_files(self, path, timeout):
        self.attached = path


class Form:
    def __init__(self, fields):
        self.fields = fields

    def count(self):
        return 1

    def locator(self, selector):
        if selector == "input,textarea,select,button":
            return NodeSet(self.fields)
        if selector.startswith("input"):
            name = selector.split('"')[1]
            return NodeSet([field for field in self.fields if field.name == name])
        raise AssertionError(selector)


class Page:
    def __init__(self, fields):
        self.form = Form(fields)

    def locator(self, selector):
        if selector in ("form", 'form[id="application"]'):
            return self.form
        if selector == "[contenteditable]":
            return NodeSet([])
        raise AssertionError(selector)


def test_file_field_no_submit(draft):
    pytest.importorskip("reportlab")
    bundle = build_bundle(draft)
    form = FormAdapter(
        "fixture", "jobs.example.org", "/apply", "application",
        "Permission expresse pour le formulaire de test.",
        (FieldBinding("full_name", "input", "identity.full_name", True),),
        (UploadBinding("cv_attachment", "cv", True),),
    )
    name = Field("full_name")
    upload = Field("cv_attachment", "file")
    submit = Field("submit", "submit")
    page = Page([name, upload, submit])
    assert fill_known_fields(page, form, {"full_name": "Candidate"},
                             {"cv_attachment": bundle["cv"].path}) == 2
    assert upload.attached == str(bundle["cv"].path)
    assert submit.filled is None and submit.attached is None


def test_unknown_file_field_blocks_before_mutation(draft):
    pytest.importorskip("reportlab")
    bundle = build_bundle(draft)
    form = FormAdapter(
        "fixture", "jobs.example.org", "/apply", "application",
        "Permission expresse pour le formulaire de test.",
        (FieldBinding("full_name", "input", "identity.full_name", True),),
        (UploadBinding("cv_attachment", "cv", True),),
    )
    name = Field("full_name")
    hidden_upload = Field("hidden_upload", "file")
    with pytest.raises(FormMismatch, match="non déclaré"):
        fill_known_fields(Page([name, hidden_upload]), form,
                          {"full_name": "Candidate"}, {"cv_attachment": bundle["cv"].path})
    assert name.filled is None and hidden_upload.attached is None


def test_files_absent_by_default(draft):
    assert read_bundle(draft) == {}
