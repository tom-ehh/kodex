"""Response models for the REST API. Field descriptions and examples feed the OpenAPI spec."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from coi_formularze.validation.checks import FileReport, IssueCode
from coi_formularze.validation.sniff import FileKind

_REPORT_EXAMPLE = {
    "filename": "wniosek.pdf",
    "size_bytes": 48211,
    "extension": ".pdf",
    "declared_kind": "pdf",
    "detected_kind": "png",
    "detected_mime": "image/png",
    "client_mime": "application/pdf",
    "ok": False,
    "issues": [
        {
            "code": "content_extension_mismatch",
            "message": "Extension says pdf, content is png.",
        }
    ],
}


class Issue(BaseModel):
    """One reason a file was rejected."""

    code: IssueCode = Field(description="Stable machine-readable code.")
    message: str = Field(description="Human-readable explanation (English).")


class FileReportOut(BaseModel):
    """Result of validating one uploaded file. `ok` is true when `issues` is empty."""

    model_config = ConfigDict(json_schema_extra={"examples": [_REPORT_EXAMPLE]})

    filename: str = Field(description="Filename as sent by the client.")
    size_bytes: int = Field(
        description="Bytes read. Capped at the size limit + 1 for oversized files."
    )
    extension: str | None = Field(description="Lower-cased extension incl. the dot, or null.")
    declared_kind: FileKind | None = Field(
        description="Format implied by the extension; null if unsupported."
    )
    detected_kind: FileKind | None = Field(
        description="Format detected from the file's bytes; null if unrecognized or not checked."
    )
    detected_mime: str | None = Field(description="MIME type of `detected_kind`.")
    client_mime: str | None = Field(
        description="Content-Type sent by the client. Recorded only, never trusted."
    )
    ok: bool = Field(description="True when the file passed every check.")
    issues: list[Issue] = Field(description="Every failed check; empty when `ok` is true.")

    @classmethod
    def of(cls, r: FileReport) -> FileReportOut:
        return cls(
            filename=r.filename,
            size_bytes=r.size_bytes,
            extension=r.extension,
            declared_kind=r.declared_kind,
            detected_kind=r.detected_kind,
            detected_mime=r.detected_mime,
            client_mime=r.client_mime,
            ok=r.ok,
            issues=[Issue(code=i.code, message=i.message) for i in r.issues],
        )


class BatchReport(BaseModel):
    """Validation results for several files, in upload order."""

    total: int = Field(description="Number of files received.")
    accepted: int = Field(description="Files that passed every check.")
    rejected: int = Field(description="Files with at least one issue.")
    files: list[FileReportOut] = Field(description="One report per file, in upload order.")

    @classmethod
    def of(cls, reports: list[FileReportOut]) -> BatchReport:
        accepted = sum(r.ok for r in reports)
        return cls(
            total=len(reports), accepted=accepted, rejected=len(reports) - accepted, files=reports
        )


class FormatOut(BaseModel):
    """One accepted input format."""

    kind: FileKind = Field(description="Format identifier used throughout the API.")
    mime: str = Field(description="Canonical MIME type.")
    extensions: list[str] = Field(description="Accepted filename extensions (case-insensitive).")


class Limits(BaseModel):
    """Accepted formats and upload limits currently configured on the server."""

    formats: list[FormatOut]
    max_file_size_bytes: int = Field(description="Per-file size limit (COI_MAX_FILE_SIZE_BYTES).")
    max_batch_files: int = Field(description="Max files per batch request (COI_MAX_BATCH_FILES).")
    filename_max_length: int = Field(description="Max filename length in characters.")
    filename_forbidden_chars: str = Field(
        description="Characters not allowed in filenames, concatenated."
    )


class ErrorDetail(BaseModel):
    """Generic error body."""

    detail: str = Field(description="Human-readable reason.")


class ValidationFailed(BaseModel):
    """Error body that carries the file's validation report."""

    detail: str = Field(description="Human-readable reason.")
    file: FileReportOut


class RouteOut(BaseModel):
    """Complexity router's choice of extractor tier for a form."""

    tier: str = Field(description="`simple`, `medium` or `hard`.")
    confidence: float = Field(
        description="Router model's confidence (0-1); 1.0 for the deterministic fallback."
    )
    probabilities: dict[str, float] = Field(description="Per-tier probabilities; empty for the fallback.")
    source: str = Field(description="`basal` (router model) or `heuristic` (feature rules).")
    escalated: bool = Field(description="True when the tier was bumped above the raw decision.")
    reason: str | None = Field(description="Why the fallback was used or the tier was bumped.")
    model: str | None = Field(description="Extractor model configured for this tier, if any.")


class AnalyzeNotImplemented(ValidationFailed):
    """501 body: the file is valid; carries the router's decision until extraction exists."""

    route: RouteOut


class ItemStatus(StrEnum):
    REJECTED = "rejected"  # failed validation; never analysed
    PENDING = "pending"  # valid; waiting for analysis
    DONE = "done"
    FAILED = "failed"


class JobStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"  # every item is done, failed or rejected


class JobItem(BaseModel):
    """One file within a job."""

    index: int = Field(description="Position of the file in the upload, from 0.")
    file: FileReportOut
    status: ItemStatus = Field(
        description="`rejected`: failed validation, never analysed. `pending`: waiting for "
        "analysis. `done`: `result` holds the form JSON. `failed`: see `error`."
    )
    result: dict[str, Any] | None = Field(
        None, description="Form configuration JSON once analysed; otherwise null."
    )
    error: str | None = Field(None, description="Why analysis failed; otherwise null.")


class JobSummary(BaseModel):
    """A job without its per-file items."""

    id: str = Field(description="Job identifier (32 hex characters).")
    created_at: datetime = Field(description="Creation time, UTC.")
    status: JobStatus = Field(
        description="`pending` while any item is pending; `done` once none are."
    )
    total: int = Field(description="Number of files in the job.")
    accepted: int = Field(description="Files that passed validation.")
    rejected: int = Field(description="Files that failed validation.")


class Job(JobSummary):
    """A batch of files with per-file validation reports and analysis results."""

    items: list[JobItem]


class Health(BaseModel):
    status: str = Field(description="Always `ok` when the process is serving requests.")
