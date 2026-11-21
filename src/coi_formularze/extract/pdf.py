"""PDF: native text layer with positions; pages without a usable layer go to OCR."""

import io
from collections.abc import Callable

import pdfplumber
import pypdfium2 as pdfium
from pdfminer.pdfparser import PDFSyntaxError
from pdfplumber.utils.exceptions import PdfminerException

from coi_formularze.extract.model import Document, ExtractionError, Line, Page, Source
from coi_formularze.extract.quality import needs_ocr

OCR_DPI = 150
_BOX_MIN, _BOX_MAX = 5.0, 16.0  # side of a drawn checkbox, in points

Ocr = Callable[[bytes, str], str]


def extract_pdf(data: bytes, ocr: Ocr | None, max_pages: int = 50) -> Document:
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except (PdfminerException, PDFSyntaxError, ValueError, TypeError) as exc:
        raise ExtractionError("PDF could not be opened") from exc
    doc = Document(kind="pdf", pages=[])
    with pdf:
        if not pdf.pages:  # page tree pdfminer cannot walk; pdfium is more lenient
            return _ocr_only(data, ocr, max_pages, doc)
        if len(pdf.pages) > max_pages:
            doc.warnings.append(
                f"only the first {max_pages} of {len(pdf.pages)} pages were read"
            )
        for i, page in enumerate(pdf.pages[:max_pages], start=1):
            native = _native_page(page, i)
            if not needs_ocr(native.markdown):
                doc.pages.append(native)
                continue
            if ocr is None:
                doc.warnings.append(f"page {i} has no usable text layer and OCR is off")
                doc.pages.append(native)
                continue
            png = _render(data, i - 1)
            doc.pages.append(
                Page(
                    number=i,
                    source=Source.OCR,
                    markdown=ocr(png, "image/png"),
                    width=float(page.width),
                    height=float(page.height),
                )
            )
    return doc


def _render(data: bytes, index: int) -> bytes:
    pdf = pdfium.PdfDocument(data)
    try:
        img = pdf[index].render(scale=OCR_DPI / 72).to_pil().convert("RGB")
    finally:
        pdf.close()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _ocr_only(data: bytes, ocr: Ocr | None, max_pages: int, doc: Document) -> Document:
    try:
        pdf = pdfium.PdfDocument(data)
        count = len(pdf)
        pdf.close()
    except pdfium.PdfiumError as exc:
        raise ExtractionError("PDF could not be opened") from exc
    if ocr is None:
        doc.warnings.append("PDF text could not be read and OCR is off")
        return doc
    for i in range(min(count, max_pages)):
        doc.pages.append(
            Page(
                number=i + 1,
                source=Source.OCR,
                markdown=ocr(_render(data, i), "image/png"),
            )
        )
    return doc


def _native_page(page, number: int) -> Page:
    tables = [t for t in page.find_tables() if len(t.rows) > 1]
    boxes = [t.bbox for t in tables]

    def in_table(x0: float, top: float, x1: float, bottom: float) -> bool:
        cx, cy = (x0 + x1) / 2, (top + bottom) / 2
        return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)

    lines: list[Line] = []
    for tl in page.extract_text_lines(keep_blank_chars=False):
        text = tl["text"].strip()
        if text:
            lines.append(Line(text, (tl["x0"], tl["top"], tl["x1"], tl["bottom"])))

    # Drawn checkboxes (small squares) have no text; show them as ☐ in place.
    for r in page.rects:
        w, h = r["width"], r["height"]
        if _BOX_MIN <= w <= _BOX_MAX and abs(w - h) <= 1.5:
            lines.append(Line("☐", (r["x0"], r["top"], r["x1"], r["bottom"])))

    # Reading order: blocks by top, then x; items on one visual row are joined.
    items: list[tuple[float, float, str]] = [
        (ln.bbox[1], ln.bbox[0], ln.text) for ln in lines if not in_table(*ln.bbox)
    ]
    for t in tables:
        items.append((t.bbox[1], t.bbox[0], _table_md(t.extract())))
    items.sort()
    out: list[str] = []
    row_top: float | None = None
    for top, _x, text in items:
        if text.startswith("|") or row_top is None or abs(top - row_top) > 3:
            out.append(text)
            row_top = None if text.startswith("|") else top
        else:
            out[-1] += " " + text
    return Page(
        number=number,
        source=Source.NATIVE,
        markdown="\n".join(out),
        width=float(page.width),
        height=float(page.height),
        lines=[ln for ln in lines if ln.text != "☐"]
        + [ln for ln in lines if ln.text == "☐"],
    )


def _table_md(rows: list[list[str | None]]) -> str:
    rows = [
        [(c or "").replace("\n", " ").replace("|", "/").strip() for c in r]
        for r in rows
    ]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    md = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(md)
