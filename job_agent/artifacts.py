from __future__ import annotations

import html
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from .tailor import TailoredPackage


@dataclass(frozen=True)
class ArtifactResult:
    resume_path: str
    resume_pdf_path: str
    cover_letter_path: str


@dataclass(frozen=True)
class OutreachArtifactResult:
    resume_pdf_path: str


def write_outreach_artifacts(
    artifact_dir: Path | str,
    outreach_id: int,
    resume_data: dict[str, Any],
    candidate_context: dict[str, Any],
    folder_prefix: str = "outreach",
) -> OutreachArtifactResult:
    outreach_dir = Path(artifact_dir).resolve() / f"{folder_prefix}-{outreach_id}"
    outreach_dir.mkdir(parents=True, exist_ok=True)
    resume_pdf_path = outreach_dir / "resume.pdf"
    try:
        if resume_data:
            _render_resume_pdf(resume_pdf_path, resume_data, candidate_context)
    except Exception:
        resume_pdf_path = Path()
    return OutreachArtifactResult(
        resume_pdf_path=str(resume_pdf_path) if resume_pdf_path and resume_pdf_path.is_file() else "",
    )


def write_package_artifacts(
    artifact_dir: Path | str,
    package_id: int,
    package: TailoredPackage,
    candidate_context: dict[str, Any],
) -> ArtifactResult:
    package_dir = Path(artifact_dir).resolve() / f"package-{package_id}"
    package_dir.mkdir(parents=True, exist_ok=True)

    resume_path = package_dir / "tailored-resume.tex"
    cover_letter_path = package_dir / "cover-letter.txt"
    resume_path.write_text(package.tailored_resume, encoding="utf-8")
    cover_letter_path.write_text(package.cover_letter.strip() + "\n", encoding="utf-8")

    resume_pdf_path = package_dir / "tailored-resume.pdf"
    try:
        if package.resume_data:
            _render_resume_pdf(resume_pdf_path, package.resume_data, candidate_context)
    except Exception:
        resume_pdf_path = Path()

    return ArtifactResult(
        resume_path=str(resume_path),
        resume_pdf_path=str(resume_pdf_path) if resume_pdf_path and resume_pdf_path.is_file() else "",
        cover_letter_path=str(cover_letter_path),
    )


def _render_resume_pdf(path: Path, resume: dict[str, Any], context: dict[str, Any]) -> None:
    from reportlab.platypus.doctemplate import LayoutError
    from pypdf import PdfReader
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

    font_path = Path("/System/Library/Fonts/Supplemental/Charter.ttc")
    if font_path.is_file() and "Charter" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Charter", str(font_path), subfontIndex=0))
        pdfmetrics.registerFont(TTFont("Charter-Italic", str(font_path), subfontIndex=1))
        pdfmetrics.registerFont(TTFont("Charter-BoldItalic", str(font_path), subfontIndex=2))
        pdfmetrics.registerFont(TTFont("Charter-Bold", str(font_path), subfontIndex=3))
        pdfmetrics.registerFontFamily(
            "Charter", normal="Charter", bold="Charter-Bold",
            italic="Charter-Italic", boldItalic="Charter-BoldItalic",
        )
    regular = "Charter" if "Charter" in pdfmetrics.getRegisteredFontNames() else "Times-Roman"
    bold = "Charter-Bold" if "Charter-Bold" in pdfmetrics.getRegisteredFontNames() else "Times-Bold"
    italic = "Charter-Italic" if "Charter-Italic" in pdfmetrics.getRegisteredFontNames() else "Times-Italic"

    identity = context.get("identity", {})

    def safe(value: Any) -> str:
        return html.escape(str(value or ""), quote=True)

    def build(scale: float, fill: float = 1.0) -> bytes:
        output = BytesIO()
        document = SimpleDocTemplate(
            output,
            pagesize=letter,
            leftMargin=0.48 * inch,
            rightMargin=0.48 * inch,
            topMargin=0.32 * inch,
            bottomMargin=0.30 * inch,
            title=f"{identity.get('resume_name', 'Candidate')} Resume",
            author=identity.get("resume_name", ""),
        )
        body = ParagraphStyle(
            "Body", fontName=regular, fontSize=8.7 * scale, leading=10.1 * scale,
            spaceAfter=1.6 * scale * fill, alignment=TA_LEFT, textColor=colors.black,
        )
        heading = ParagraphStyle(
            "Heading", parent=body, fontName=bold, fontSize=9.7 * scale,
            leading=10.6 * scale * fill, spaceBefore=3.2 * scale * fill, spaceAfter=0.8 * scale * fill,
            keepWithNext=True,
        )
        entry = ParagraphStyle(
            "Entry", parent=body, fontName=bold, fontSize=8.8 * scale,
            leading=9.8 * scale * fill, spaceBefore=1.8 * scale * fill, spaceAfter=0,
            keepWithNext=True,
        )
        subentry = ParagraphStyle(
            "Subentry", parent=body, fontName=italic, fontSize=8.1 * scale,
            leading=9.1 * scale * fill, spaceAfter=0.5 * scale * fill, keepWithNext=True,
        )
        bullet = ParagraphStyle(
            "Bullet", parent=body, leftIndent=12 * scale, firstLineIndent=-7 * scale,
            bulletIndent=2 * scale, spaceAfter=0.7 * scale * fill,
        )
        name_style = ParagraphStyle(
            "Name", parent=body, fontName=bold, fontSize=18 * scale,
            leading=19 * scale * fill, alignment=TA_CENTER, spaceAfter=1.5 * scale * fill,
        )
        contact_style = ParagraphStyle(
            "Contact", parent=body, fontSize=7.7 * scale, leading=8.7 * scale * fill,
            alignment=TA_CENTER, spaceAfter=2.5 * scale * fill,
        )

        story: list[Any] = []
        story.append(Paragraph(safe(identity.get("resume_name", "Tushar Pathak")), name_style))
        contact_parts = [
            identity.get("phone", ""), identity.get("email", ""),
            identity.get("portfolio", ""), identity.get("linkedin", ""),
        ]
        story.append(Paragraph(" | ".join(safe(x) for x in contact_parts if x), contact_style))
        if resume.get("headline"):
            story.append(Paragraph(safe(resume["headline"]), contact_style))

        def section(title: str) -> None:
            story.append(Paragraph(safe(title.upper()), heading))
            story.append(HRFlowable(width="100%", thickness=0.55, color=colors.black, spaceBefore=0, spaceAfter=1.5 * scale * fill))

        section("Education")
        for item in resume.get("education", []):
            story.append(Paragraph(
                f"<b>{safe(item.get('institution'))}</b> — {safe(item.get('location'))}", entry
            ))
            story.append(Paragraph(
                f"{safe(item.get('degree'))} | {safe(item.get('dates'))}", subentry
            ))

        section("Work Experience")
        for item in resume.get("experience", []):
            story.append(Paragraph(
                f"<b>{safe(item.get('organization'))}</b> — {safe(item.get('location'))}", entry
            ))
            story.append(Paragraph(
                f"{safe(item.get('role'))} | {safe(item.get('dates'))}", subentry
            ))
            for text in item.get("bullets", []):
                story.append(Paragraph(safe(text), bullet, bulletText="•"))

        section("Projects")
        for item in resume.get("projects", []):
            label = safe(item.get("name"))
            technologies = safe(item.get("technologies"))
            story.append(Paragraph(f"<b>{label}</b> | {technologies}", entry))
            for text in item.get("bullets", []):
                story.append(Paragraph(safe(text), bullet, bulletText="•"))

        section("Technical Skills")
        for item in resume.get("skills", []):
            story.append(Paragraph(
                f"<b>{safe(item.get('category'))}:</b> {safe(item.get('items'))}", body
            ))
        story.append(Spacer(1, 0.5))
        document.build(story)
        return output.getvalue()

    latest = b""
    layout_candidates = [
        (1.14, 1.45),
        (1.12, 1.35),
        (1.10, 1.25),
        (1.08, 1.18),
        (1.05, 1.12),
        (1.02, 1.08),
        (1.00, 1.00),
        (0.95, 1.00),
        (0.90, 1.00),
        (0.85, 1.00),
        (0.80, 1.00),
    ]
    for scale, fill in layout_candidates:
        try:
            candidate = build(scale, fill)
        except LayoutError:
            continue
        latest = candidate
        if len(PdfReader(BytesIO(candidate)).pages) == 1:
            break
    path.write_bytes(latest)
