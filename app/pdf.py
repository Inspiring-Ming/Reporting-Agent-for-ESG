"""Generate a PDF from the user-edited report sections.

Uses reportlab (pure Python, no system deps). Renders Markdown sections by
parsing common constructs: headings (`## `, `### `), bullets (`- `), bold
(`**...**`), and paragraphs. Good enough for a clean professional booth deliverable.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    PageBreak,
    Image,
    KeepTogether,
)


AMP_BLUE = HexColor("#003C71")
AMP_ACCENT = HexColor("#0072CE")
GREY_FAINT = HexColor("#666666")


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "title", parent=base["Title"],
            fontName="Helvetica-Bold", fontSize=22, leading=28,
            textColor=AMP_BLUE, spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"],
            fontName="Helvetica", fontSize=11, leading=14,
            textColor=GREY_FAINT, spaceAfter=20,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            fontName="Helvetica-Bold", fontSize=15, leading=19,
            textColor=AMP_BLUE, spaceBefore=14, spaceAfter=8,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"],
            fontName="Helvetica-Bold", fontSize=12, leading=15,
            textColor=AMP_ACCENT, spaceBefore=10, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"],
            fontName="Helvetica", fontSize=10.5, leading=15,
            spaceAfter=8, alignment=0,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base["BodyText"],
            fontName="Helvetica", fontSize=10.5, leading=15,
            leftIndent=14, bulletIndent=2, spaceAfter=4,
        ),
        "footer": ParagraphStyle(
            "footer", parent=base["Normal"],
            fontName="Helvetica-Oblique", fontSize=8.5, leading=11,
            textColor=GREY_FAINT, spaceBefore=20,
        ),
    }
    return styles


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """Convert a subset of Markdown inline syntax to ReportLab's mini-HTML."""
    out = text
    out = out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = _BOLD_RE.sub(r"<b>\1</b>", out)
    out = _ITALIC_RE.sub(r"<i>\1</i>", out)
    out = _CODE_RE.sub(r"<font face='Courier'>\1</font>", out)
    return out


def _markdown_to_flowables(md: str, styles: dict) -> list:
    """Render a Markdown blob into a list of reportlab flowables."""
    flow: list = []
    paragraph_buf: list[str] = []

    def flush_paragraph():
        if paragraph_buf:
            txt = " ".join(paragraph_buf).strip()
            if txt:
                flow.append(Paragraph(_inline(txt), styles["body"]))
            paragraph_buf.clear()

    for raw_line in md.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush_paragraph()
            continue
        if line.startswith("### "):
            flush_paragraph()
            flow.append(Paragraph(_inline(line[4:].strip()), styles["h3"]))
        elif line.startswith("## "):
            flush_paragraph()
            flow.append(Paragraph(_inline(line[3:].strip()), styles["h2"]))
        elif line.startswith("# "):
            flush_paragraph()
            flow.append(Paragraph(_inline(line[2:].strip()), styles["h2"]))
        elif line.lstrip().startswith(("- ", "* ")):
            flush_paragraph()
            txt = line.lstrip()[2:]
            flow.append(Paragraph(_inline(txt), styles["bullet"], bulletText="•"))
        else:
            paragraph_buf.append(line.strip())
    flush_paragraph()
    return flow


def render_pdf(*, title: str, subtitle: str, sections: list[dict]) -> bytes:
    """sections: list of {"section_id", "title", "markdown", optional "chart_png_bytes"}"""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=title, author="AMP AI Expo demo",
    )
    styles = _styles()
    story: list = []

    story.append(Paragraph(_inline(title), styles["title"]))
    if subtitle:
        story.append(Paragraph(_inline(subtitle), styles["subtitle"]))

    for sec in sections:
        sec_title = sec.get("title", "")
        md = sec.get("markdown", "")
        chart_png = sec.get("chart_png_bytes")

        flow = []
        # If section markdown doesn't start with a heading, add the title.
        stripped = md.lstrip()
        if not (stripped.startswith("##") or stripped.startswith("# ")):
            flow.append(Paragraph(_inline(sec_title or sec.get("section_id", "")), styles["h2"]))
        flow.extend(_markdown_to_flowables(md, styles))

        if chart_png:
            try:
                img = Image(io.BytesIO(chart_png), width=15 * cm, height=8 * cm, kind="proportional")
                flow.append(Spacer(1, 0.3 * cm))
                flow.append(img)
            except Exception:
                pass  # silently skip a malformed chart rather than break the PDF

        story.append(KeepTogether(flow))
        story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%d %B %Y')} — AMP AI Expo demo "
        f"(grounded in SASB Industry Standards and Clarity AI ESG dataset). "
        f"This document is an AI-assisted draft for educational purposes; "
        f"verify all figures before external use.",
        styles["footer"],
    ))

    doc.build(story)
    return buf.getvalue()
