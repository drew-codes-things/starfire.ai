from __future__ import annotations

import io
import re

VALID_FORMATS = {"md", "txt", "pdf", "docx"}
CONTENT_TYPES = {
    "md": "text/markdown",
    "txt": "text/plain",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

def _plain_bytes(content: str) -> bytes:
    return content.encode("utf-8")

def _to_pdf(content: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in content.splitlines() or [""]:
        heading = re.match(r"^(#{1,3})\s+(.*)", line)
        if heading:
            pdf.set_font("Helvetica", "B", 16 - 2 * (len(heading.group(1)) - 1))
            pdf.multi_cell(0, 8, heading.group(2))
            pdf.set_font("Helvetica", size=11)
        elif not line.strip():
            pdf.ln(6)
        else:
            text = re.sub(r"^[-*]\s+", "-  ", line)
            safe_text = text.encode("latin-1", errors="replace").decode("latin-1")
            pdf.multi_cell(0, 6, safe_text)
    return bytes(pdf.output())

def _to_docx(content: str) -> bytes:
    from docx import Document

    doc = Document()
    for line in content.splitlines() or [""]:
        heading = re.match(r"^(#{1,3})\s+(.*)", line)
        if heading:
            doc.add_heading(heading.group(2), level=len(heading.group(1)))
        elif re.match(r"^[-*]\s+", line):
            doc.add_paragraph(re.sub(r"^[-*]\s+", "", line), style="List Bullet")
        else:
            doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

def generate(content: str, fmt: str) -> bytes:
    if fmt not in VALID_FORMATS:
        raise ValueError(f"unsupported format: {fmt}")
    if fmt in ("md", "txt"):
        return _plain_bytes(content)
    if fmt == "pdf":
        return _to_pdf(content)
    return _to_docx(content)
