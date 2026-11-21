"""Pre-analysis validation of an uploaded file: name, extension, size, content type.

Pure functions over (filename, bytes); no web framework imports, so they are
testable on their own and reusable from a CLI or batch worker.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath

from coi_formularze.validation.sniff import EXTENSION_TO_KIND, FORMATS, FileKind, sniff

MAX_FILENAME_LENGTH = 250
FORBIDDEN_FILENAME_CHARS = frozenset(";:><\\/|'*%$")


class IssueCode(StrEnum):
    FILENAME_EMPTY = "filename_empty"
    FILENAME_TOO_LONG = "filename_too_long"
    FILENAME_FORBIDDEN_CHARS = "filename_forbidden_chars"
    FILE_EMPTY = "file_empty"
    FILE_TOO_LARGE = "file_too_large"
    EXTENSION_UNSUPPORTED = "extension_unsupported"
    CONTENT_UNRECOGNIZED = "content_unrecognized"
    CONTENT_EXTENSION_MISMATCH = "content_extension_mismatch"


@dataclass(slots=True)
class ValidationIssue:
    code: IssueCode
    message: str


@dataclass(slots=True)
class FileReport:
    filename: str
    size_bytes: int
    extension: str | None = None
    declared_kind: FileKind | None = None  # from the extension
    detected_kind: FileKind | None = None  # from the content
    client_mime: str | None = None  # as sent by the client; informational only
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def detected_mime(self) -> str | None:
        return FORMATS[self.detected_kind].mime if self.detected_kind else None


def check_filename(name: str) -> list[ValidationIssue]:
    issues = []
    if not name:
        issues.append(ValidationIssue(IssueCode.FILENAME_EMPTY, "Filename is empty."))
    if len(name) > MAX_FILENAME_LENGTH:
        issues.append(
            ValidationIssue(
                IssueCode.FILENAME_TOO_LONG,
                f"Filename has {len(name)} characters; the limit is {MAX_FILENAME_LENGTH}.",
            )
        )
    if bad := sorted(set(name) & FORBIDDEN_FILENAME_CHARS):
        issues.append(
            ValidationIssue(
                IssueCode.FILENAME_FORBIDDEN_CHARS,
                f"Filename contains forbidden characters: {' '.join(bad)}",
            )
        )
    return issues


def validate_file(
    filename: str,
    data: bytes,
    *,
    max_size_bytes: int,
    client_mime: str | None = None,
) -> FileReport:
    """Run all checks and collect every issue (does not stop at the first)."""
    report = FileReport(filename=filename, size_bytes=len(data), client_mime=client_mime)
    report.issues.extend(check_filename(filename))

    if len(data) == 0:
        report.issues.append(ValidationIssue(IssueCode.FILE_EMPTY, "File is empty."))
    elif len(data) > max_size_bytes:
        report.issues.append(
            ValidationIssue(
                IssueCode.FILE_TOO_LARGE,
                f"File exceeds the size limit of {max_size_bytes} bytes.",
            )
        )

    ext = PurePath(filename).suffix.lower() or None
    report.extension = ext
    report.declared_kind = EXTENSION_TO_KIND.get(ext) if ext else None
    if report.declared_kind is None:
        allowed = ", ".join(sorted(EXTENSION_TO_KIND))
        report.issues.append(
            ValidationIssue(IssueCode.EXTENSION_UNSUPPORTED, f"Extension {ext!r} is not one of: {allowed}.")
        )

    # Sniff only files within the size limit: sniffing reads the whole archive.
    if 0 < len(data) <= max_size_bytes:
        report.detected_kind = sniff(data)
        if report.detected_kind is None:
            report.issues.append(
                ValidationIssue(IssueCode.CONTENT_UNRECOGNIZED, "File content is not a supported format.")
            )
        elif report.declared_kind and report.detected_kind != report.declared_kind:
            report.issues.append(
                ValidationIssue(
                    IssueCode.CONTENT_EXTENSION_MISMATCH,
                    f"Extension says {report.declared_kind}, content is {report.detected_kind}.",
                )
            )
    return report
