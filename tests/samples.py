"""Minimal in-memory files that pass (or fail) content sniffing."""

import io
import zipfile

PDF = b"%PDF-1.7\n%fake\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
DOC = OLE_HEADER + b"\x00" * 64 + "WordDocument".encode("utf-16-le")
XLS = OLE_HEADER + b"\x00" * 64 + "Workbook".encode("utf-16-le")


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


DOCX = _zip({"[Content_Types].xml": b"<x/>", "word/document.xml": b"<x/>"})
XLSX = _zip({"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<x/>"})
ODS = _zip({"mimetype": b"application/vnd.oasis.opendocument.spreadsheet"})
ODT = _zip({"mimetype": b"application/vnd.oasis.opendocument.text"})
PPTX = _zip({"[Content_Types].xml": b"<x/>", "ppt/presentation.xml": b"<x/>"})
