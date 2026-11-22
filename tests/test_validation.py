import pytest

from kodex.validation.checks import validate_file
from kodex.validation.sniff import FileKind, sniff
from tests import samples

LIMIT = 1024


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (samples.PDF, FileKind.PDF),
        (samples.PNG, FileKind.PNG),
        (samples.JPEG, FileKind.JPEG),
        (samples.DOC, FileKind.DOC),
        (samples.DOCX, FileKind.DOCX),
        (samples.XLSX, FileKind.XLSX),
        (samples.ODS, FileKind.ODS),
    ],
)
def test_sniff_supported(data, kind):
    assert sniff(data) == kind


@pytest.mark.parametrize(
    "data",
    [samples.XLS, samples.ODT, samples.PPTX, b"PK\x03\x04broken", b"hello", b""],
)
def test_sniff_unsupported(data):
    assert sniff(data) is None


def codes(report):
    return {i.code for i in report.issues}


def test_valid_file():
    r = validate_file("wniosek.pdf", samples.PDF, max_size_bytes=LIMIT)
    assert r.ok and r.detected_mime == "application/pdf"


def test_jpeg_both_extensions():
    assert validate_file("a.jpg", samples.JPEG, max_size_bytes=LIMIT).ok
    assert validate_file("a.JPEG", samples.JPEG, max_size_bytes=LIMIT).ok


def test_mismatch():
    r = validate_file("fake.pdf", samples.PNG, max_size_bytes=LIMIT)
    assert codes(r) == {"content_extension_mismatch"}


def test_unsupported_extension():
    r = validate_file("a.exe", samples.PDF, max_size_bytes=LIMIT)
    assert "extension_unsupported" in codes(r)


@pytest.mark.parametrize("ch", list(";:><\\/|'*%$"))
def test_forbidden_chars(ch):
    r = validate_file(f"a{ch}b.pdf", samples.PDF, max_size_bytes=LIMIT)
    assert "filename_forbidden_chars" in codes(r)


def test_filename_length_boundary():
    assert validate_file("a" * 246 + ".pdf", samples.PDF, max_size_bytes=LIMIT).ok  # 250
    r = validate_file("a" * 247 + ".pdf", samples.PDF, max_size_bytes=LIMIT)  # 251
    assert codes(r) == {"filename_too_long"}


def test_too_large_and_empty():
    assert codes(validate_file("a.pdf", samples.PDF, max_size_bytes=5)) == {"file_too_large"}
    assert codes(validate_file("a.pdf", b"", max_size_bytes=LIMIT)) == {"file_empty"}
