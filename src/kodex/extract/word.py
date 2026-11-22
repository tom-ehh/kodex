"""DOCX (and DOC via LibreOffice): paragraphs and tables in document order."""

import io
import zipfile

import docx
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from kodex.extract.model import Document, ExtractionError, Page, Source
from kodex.extract.office import convert

_CHECKBOX_TAG = "{http://schemas.microsoft.com/office/word/2010/wordml}checkbox"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx(data: bytes) -> Document:
    try:
        d = docx.Document(io.BytesIO(data))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ExtractionError("DOCX could not be opened") from exc
    parts = [_block_md(b) for b in _blocks(d)]
    md = "\n".join(p for p in parts if p)
    return Document(
        kind="docx", pages=[Page(number=1, source=Source.NATIVE, markdown=md)]
    )


def extract_doc(data: bytes) -> Document:
    doc = extract_docx(convert(data, "doc", "docx"))
    doc.kind = "doc"
    return doc


def _blocks(d: DocxDocument):
    body = d.element.body
    for child in body.iterchildren():
        if child.tag == f"{_W}p":
            yield Paragraph(child, d)
        elif child.tag == f"{_W}tbl":
            yield Table(child, d)


def _para_md(p: Paragraph) -> str:
    text = _para_text(p).strip()
    if not text:
        return ""
    style = (p.style.name or "").lower() if p.style is not None else ""
    if style.startswith(("heading", "nagłówek")):
        level = next((c for c in style if c.isdigit()), "2")
        return f"{'#' * min(int(level), 6)} {text}"
    if style == "title" or style == "tytuł":
        return f"# {text}"
    return text


def _para_text(p: Paragraph) -> str:
    # Content-control and legacy form-field checkboxes carry no visible text in runs.
    out: list[str] = []
    for el in p._p.iter():
        if el.tag == f"{_W}t" and el.text:
            out.append(el.text)
        elif el.tag == f"{_W}tab":
            out.append("\t")
        elif el.tag in (_CHECKBOX_TAG, f"{_W}checkBox"):
            out.append("☐ ")
    return "".join(out)


def _block_md(b) -> str:
    if isinstance(b, Paragraph):
        return _para_md(b)
    rows: list[list[str]] = []
    for row in b.rows:
        cells: list[str] = []
        seen: set[int] = set()
        for cell in row.cells:
            if id(cell._tc) in seen:  # merged cells repeat
                continue
            seen.add(id(cell._tc))
            text = " ".join(_para_text(p).strip() for p in cell.paragraphs).strip()
            cells.append(text.replace("|", "/").replace("\n", " "))
        rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    md = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(md)
