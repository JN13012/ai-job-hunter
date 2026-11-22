"""M7.4: immutable, locally generated PDF attachments for approved drafts.

This module never opens a website, uploads, or submits an application.
The SHA-256 attestation binds generated PDFs to their original M6 Markdown.
"""

from dataclasses import dataclass
from hashlib import sha256
from html import escape
from io import BytesIO
import json
from pathlib import Path
import re

MAX_MD_BYTES = 1_048_576
MAX_PDF_BYTES = 3 * 1_048_576
MAX_METADATA_BYTES = 16_384
DOCUMENTS = {"cv": ("cv.md", "cv.pdf"), "letter": ("letter.md", "letter.pdf")}
DIGEST = re.compile(r"[a-f0-9]{64}\Z")


@dataclass(frozen=True)
class Attachment:
    kind: str
    path: Path
    sha256: str
    size: int


def _read_regular(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Pièce jointe absente ou lien symbolique interdit.")
    if path.stat().st_size > maximum:
        raise ValueError("Fichier au-dessus de la limite autorisée.")
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("Fichier au-dessus de la limite autorisée.")
    return content


def _validated_source(folder: Path) -> tuple[dict, dict[str, bytes]]:
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Dossier de candidature invalide ou symbolique.")
    raw_manifest = _read_regular(folder / "manifest.json", MAX_METADATA_BYTES)
    try:
        manifest = json.loads(raw_manifest)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Manifeste M6 invalide.") from exc
    if not isinstance(manifest, dict) or not (
        manifest.get("status") == "draft_requires_human_review"
        and manifest.get("review_required") is True
        and manifest.get("ready_for_submission") is False
        and manifest.get("automation_authorized") is False
        and manifest.get("human_final_submit_required") is True
    ):
        raise ValueError("Pièces jointes réservées aux brouillons M6 non soumis.")
    sources = {}
    for kind, (filename, _) in DOCUMENTS.items():
        data = _read_regular(folder / filename, MAX_MD_BYTES)
        if not data.strip() or b"\0" in data:
            raise ValueError("Brouillon Markdown vide ou binaire.")
        data.decode("utf-8")
        sources[kind] = data
    return manifest, sources


def _font_name() -> str:
    """Use a bundled OS font only if available; never export font files."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    ):
        if Path(path).is_file():
            if "AJHDejaVu" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("AJHDejaVu", path))
            return "AJHDejaVu"
    return "Helvetica"


def _render_pdf(markdown: bytes, title: str) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise ValueError("Installer l'extra documents : uv sync --extra documents.") from exc

    font = _font_name()
    styles = {
        "body": ParagraphStyle(
            "AJHBody", fontName=font, fontSize=10, leading=14,
            spaceAfter=7, alignment=TA_LEFT, textColor=colors.HexColor("#253242"),
        ),
        "heading": ParagraphStyle(
            "AJHHeading", fontName=font, fontSize=13, leading=18,
            spaceBefore=12, spaceAfter=8, textColor=colors.HexColor("#19385C"),
        ),
        "name": ParagraphStyle(
            "AJHName", fontName=font, fontSize=18, leading=23,
            spaceAfter=11, textColor=colors.HexColor("#19385C"),
        ),
    }
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=45, rightMargin=45,
        topMargin=42, bottomMargin=42, title=title,
        author="AI Job Hunter — brouillon candidat",
        pageCompression=1, invariant=1,
    )
    story = []
    lines = markdown.decode("utf-8").splitlines()
    if len(lines) > 1600:
        raise ValueError("Document trop long.")
    for line in lines:
        text = line.strip()
        if not text:
            if story:
                story.append(Spacer(1, 5))
            continue
        if text.startswith("# "):
            style = "name"
            text = text[2:]
        elif text.startswith("## "):
            style = "heading"
            text = text[3:]
        else:
            style = "body"
        if text.startswith("- "):
            text = "• " + text[2:]
        # Markdown emphasis is decoration, not an instruction for PDF markup.
        text = text.replace("**", "").replace("\\*", "*")
        story.append(Paragraph(escape(text), styles[style]))
    if not story:
        raise ValueError("Document vide.")
    doc.build(story)
    pdf = buffer.getvalue()
    if len(pdf) > MAX_PDF_BYTES or not pdf.startswith(b"%PDF-"):
        raise ValueError("PDF généré invalide ou trop volumineux.")
    return pdf


def read_bundle(folder: Path, *, required: bool = False) -> dict[str, Attachment]:
    """Validate all PDFs, metadata, and original sources before any upload."""
    index = folder / "attachments.json"
    orphan = any((folder / name).exists() for _, name in DOCUMENTS.values())
    if not index.exists():
        if required or orphan:
            raise ValueError("Manifeste de pièces jointes absent.")
        return {}
    _, sources = _validated_source(folder)
    raw = _read_regular(index, MAX_METADATA_BYTES)
    try:
        meta = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Manifest de pièces jointes invalide.") from exc
    if (not isinstance(meta, dict) or meta.keys() != {"version", "documents", "review_required", "submitted"}
            or meta["version"] != 1 or meta["review_required"] is not True
            or meta["submitted"] is not False or not isinstance(meta["documents"], dict)
            or set(meta["documents"]) != set(DOCUMENTS)):
        raise ValueError("Manifest de pièces jointes inattendu.")
    outputs = {}
    for kind, (md_name, pdf_name) in DOCUMENTS.items():
        info = meta["documents"][kind]
        if (not isinstance(info, dict)
                or info.keys() != {"filename", "source_sha256", "sha256", "bytes"}
                or info["filename"] != pdf_name
                or not isinstance(info["source_sha256"], str)
                or not DIGEST.fullmatch(info["source_sha256"])
                or not isinstance(info["sha256"], str)
                or not DIGEST.fullmatch(info["sha256"])
                or type(info["bytes"]) is not int
                or not 0 < info["bytes"] <= MAX_PDF_BYTES):
            raise ValueError("Métadonnées de pièce jointe invalides.")
        if sha256(sources[kind]).hexdigest() != info["source_sha256"]:
            raise ValueError("Document M6 modifié après génération PDF.")
        pdf_path = folder / pdf_name
        content = _read_regular(pdf_path, MAX_PDF_BYTES)
        if (len(content) != info["bytes"] or sha256(content).hexdigest() != info["sha256"]
                or not content.startswith(b"%PDF-") or b"%%EOF" not in content[-128:]):
            raise ValueError("Pièce jointe PDF modifiée ou invalide.")
        outputs[kind] = Attachment(kind, pdf_path, info["sha256"], info["bytes"])
    return outputs


def build_bundle(folder: Path) -> dict[str, Attachment]:
    """Generate both PDFs once; reject any overwrite or partial prior bundle."""
    _, sources = _validated_source(folder)
    if (folder / "attachments.json").exists() or any(
        (folder / name).exists() for _, name in DOCUMENTS.values()
    ):
        raise ValueError("Pièces jointes déjà présentes : ne pas écraser le dossier.")
    pdfs = {
        kind: _render_pdf(data, "Candidature — " + ("CV" if kind == "cv" else "Lettre"))
        for kind, data in sources.items()
    }
    meta = {"version": 1, "review_required": True, "submitted": False, "documents": {}}
    for kind, (md, pdf) in DOCUMENTS.items():
        meta["documents"][kind] = {
            "filename": pdf, "source_sha256": sha256(sources[kind]).hexdigest(),
            "sha256": sha256(pdfs[kind]).hexdigest(), "bytes": len(pdfs[kind]),
        }
    created = []
    try:
        for kind, (_, filename) in DOCUMENTS.items():
            path = folder / filename
            with path.open("xb") as stream:
                created.append(path)
                stream.write(pdfs[kind])
            path.chmod(0o600)
        index = folder / "attachments.json"
        with index.open("x", encoding="utf-8") as stream:
            created.append(index)
            json.dump(meta, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        index.chmod(0o600)
        return read_bundle(folder, required=True)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
