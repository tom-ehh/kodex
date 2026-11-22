"""Content-based file type detection (magic bytes), stdlib only.

Detects only the formats the service accepts. Returns None for anything else,
so an unknown file is rejected rather than guessed at.
"""

import io
import zipfile
from dataclasses import dataclass
from enum import StrEnum


class FileKind(StrEnum):
    PDF = "pdf"
    DOC = "doc"
    DOCX = "docx"
    ODS = "ods"
    XLSX = "xlsx"
    JPEG = "jpeg"
    PNG = "png"


@dataclass(frozen=True, slots=True)
class FormatSpec:
    kind: FileKind
    mime: str
    extensions: tuple[str, ...]


FORMATS: dict[FileKind, FormatSpec] = {
    spec.kind: spec
    for spec in (
        FormatSpec(FileKind.PDF, "application/pdf", (".pdf",)),
        FormatSpec(FileKind.DOC, "application/msword", (".doc",)),
        FormatSpec(
            FileKind.DOCX,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            (".docx",),
        ),
        FormatSpec(FileKind.ODS, "application/vnd.oasis.opendocument.spreadsheet", (".ods",)),
        FormatSpec(
            FileKind.XLSX,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            (".xlsx",),
        ),
        FormatSpec(FileKind.JPEG, "image/jpeg", (".jpg", ".jpeg")),
        FormatSpec(FileKind.PNG, "image/png", (".png",)),
    )
}

EXTENSION_TO_KIND: dict[str, FileKind] = {
    ext: spec.kind for spec in FORMATS.values() for ext in spec.extensions
}

_PDF = b"%PDF-"
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # Compound File Binary (legacy Office)
_ZIP = b"PK\x03\x04"
# Name of the main stream in a Word 97-2003 file, as stored (UTF-16LE) in the CFB directory.
_WORD_STREAM = "WordDocument".encode("utf-16-le")
_ODS_MIME = b"application/vnd.oasis.opendocument.spreadsheet"


def sniff(data: bytes) -> FileKind | None:
    """Detect the file kind from content. `data` must be the whole file for ZIP/OLE formats."""
    if data.startswith(_PDF):
        return FileKind.PDF
    if data.startswith(_PNG):
        return FileKind.PNG
    if data.startswith(_JPEG):
        return FileKind.JPEG
    if data.startswith(_OLE):
        # Excel 97 and other CFB files share the header; require the Word stream name.
        return FileKind.DOC if _WORD_STREAM in data else None
    if data.startswith(_ZIP):
        return _sniff_zip(data)
    return None


def _sniff_zip(data: bytes) -> FileKind | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            # ODF spec: an uncompressed `mimetype` entry holds the exact MIME type.
            # Size guard keeps a crafted archive from inflating a huge `mimetype` entry.
            if (
                "mimetype" in names
                and zf.getinfo("mimetype").file_size <= 128
                and zf.read("mimetype").strip() == _ODS_MIME
            ):
                return FileKind.ODS
            if "[Content_Types].xml" in names:
                if "word/document.xml" in names:
                    return FileKind.DOCX
                if "xl/workbook.xml" in names:
                    return FileKind.XLSX
    except zipfile.BadZipFile, KeyError:
        return None
    return None
