"""Extraction per format, with small files built in memory and a fake OCR engine."""

import io

import docx
import pytest
from openpyxl import Workbook
from openpyxl.styles import Border, Side
from openpyxl.worksheet.datavalidation import DataValidation

from coi_formularze.extract import ExtractionError, Source, extract
from coi_formularze.extract.ocr import _collapse_leaders, _strip_fence
from coi_formularze.extract.quality import needs_ocr
from coi_formularze.validation.sniff import FileKind


class FakeOcr:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def ocr(self, image: bytes, mime: str) -> str:
        self.calls.append(mime)
        return "OCR TEXT"


def _pdf(text: str | None) -> bytes:
    """A one-page PDF; text=None gives a page without a text layer."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
            b" /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objs) + 1, xref)
    )
    return out.getvalue()


LONG = "Wniosek o wydanie zaswiadczenia. Imie i nazwisko wnioskodawcy oraz adres zamieszkania"


def test_pdf_native_text_without_ocr():
    ocr = FakeOcr()
    doc = extract(_pdf(LONG), FileKind.PDF, ocr)
    assert doc.pages[0].source is Source.NATIVE
    assert "Wniosek" in doc.markdown
    assert doc.pages[0].lines and doc.pages[0].lines[0].bbox[0] == pytest.approx(
        72, abs=1
    )
    assert ocr.calls == []


def test_pdf_without_text_layer_goes_to_ocr():
    ocr = FakeOcr()
    doc = extract(_pdf(None), FileKind.PDF, ocr)
    assert doc.pages[0].source is Source.OCR
    assert doc.markdown == "OCR TEXT"
    assert ocr.calls == ["image/png"]


def test_pdf_without_text_layer_and_no_ocr_warns():
    doc = extract(_pdf(None), FileKind.PDF, None)
    assert doc.warnings


def test_corrupt_pdf_raises():
    with pytest.raises(ExtractionError):
        extract(b"%PDF-1.4 garbage", FileKind.PDF)


def test_image_uses_ocr_with_mime():
    ocr = FakeOcr()
    doc = extract(b"\xff\xd8\xff", FileKind.JPEG, ocr)
    assert doc.used_ocr and ocr.calls == ["image/jpeg"]


def test_image_without_ocr_raises():
    with pytest.raises(ExtractionError):
        extract(b"\x89PNG", FileKind.PNG, None)


def test_docx_headings_paragraphs_tables():
    d = docx.Document()
    d.add_heading("Wniosek", level=1)
    d.add_paragraph("Dane wnioskodawcy")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Imię", "Nazwisko"
    buf = io.BytesIO()
    d.save(buf)
    md = extract(buf.getvalue(), FileKind.DOCX).markdown
    assert "# Wniosek" in md
    assert "Dane wnioskodawcy" in md
    assert "| Imię | Nazwisko |" in md
    assert md.index("Wniosek") < md.index("Dane") < md.index("| Imię")


def test_xlsx_cells_merges_borders_dropdowns():
    wb = Workbook()
    ws = wb.active
    ws.title = "Wniosek"
    ws["A1"] = "Wniosek o dodatek"
    ws.merge_cells("A1:D1")
    ws["A3"] = "PESEL"
    thin = Side(style="thin")
    ws["B3"].border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws["A4"] = "Stan cywilny"
    dv = DataValidation(type="list", formula1='"wolny,żonaty"')
    ws.add_data_validation(dv)
    dv.add("B4")
    buf = io.BytesIO()
    wb.save(buf)
    page = extract(buf.getvalue(), FileKind.XLSX).pages[0]
    cells = {c.ref: c for c in page.cells}
    assert cells["A1"].span == (1, 4)
    assert cells["B3"].bordered and cells["B3"].value == ""
    assert cells["B4"].choices == ["wolny", "żonaty"]
    assert "[B3] ____" in page.markdown
    assert "{wolny / żonaty}" in page.markdown


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", True),
        ("abc", True),
        (LONG * 3, False),
        ("(cid:12)(cid:40)(cid:7)" * 20, True),
        (" " * 30, True),
    ],
)
def test_needs_ocr(text, expected):
    assert needs_ocr(text) is expected


def test_strip_fence():
    assert _strip_fence("```markdown\n# A\n```") == "# A"
    assert _strip_fence("# A") == "# A"


def test_collapse_leaders():
    assert _collapse_leaders("Imię: " + "." * 500 + " koniec") == "Imię: ____ koniec"
    assert _collapse_leaders("a . . . b") == "a . . . b"
