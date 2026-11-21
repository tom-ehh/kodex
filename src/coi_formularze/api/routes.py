"""REST API v1. Docstrings and `responses` here become the OpenAPI spec served at /docs."""

import asyncio
import logging
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from coi_formularze.api.deps import JobStoreDep, SettingsDep, read_and_validate
from coi_formularze.api.schemas import (
    AnalyzeNotImplemented,
    BatchReport,
    ErrorDetail,
    FileReportOut,
    FormatOut,
    Job,
    JobSummary,
    Limits,
    RouteOut,
    ValidationFailed,
)
from coi_formularze.auth import require_auth
from coi_formularze.extract import ExtractionError, extract, ocr_engine_from_settings
from coi_formularze.route import route_document, tier_models
from coi_formularze.validation.checks import (
    FORBIDDEN_FILENAME_CHARS,
    MAX_FILENAME_LENGTH,
)
from coi_formularze.validation.sniff import FORMATS

_UNAUTHORIZED = {
    401: {"model": ErrorDetail, "description": "JWT is enabled and the token is missing or invalid."}
}


def _one_of(*schemas: str) -> dict:
    refs = [{"$ref": f"#/components/schemas/{name}"} for name in schemas]
    return {"application/json": {"schema": {"anyOf": refs}}}


_BATCH_ERRORS = {
    422: {
        "description": "Batch exceeds `COI_MAX_BATCH_FILES` (`ErrorDetail`), or the `files` "
        "field is missing (`HTTPValidationError`).",
        "content": _one_of("ErrorDetail", "HTTPValidationError"),
    }
}
_NOT_FOUND = {404: {"model": ErrorDetail, "description": "No job with this id (or it was evicted)."}}

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)], responses=_UNAUTHORIZED)

FilesParam = Annotated[
    list[UploadFile], File(description="Form files. Repeat the `files` field once per file.")
]
FileParam = Annotated[UploadFile, File(description="A single form file.")]


def _check_batch_size(files: list[UploadFile], max_files: int) -> None:
    if len(files) > max_files:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Batch has {len(files)} files; the limit is {max_files}.",
        )


@router.get("/formats", tags=["meta"], summary="List accepted formats and limits",
    response_description="Formats and limits.",
)
def get_formats(settings: SettingsDep) -> Limits:
    """Accepted formats, MIME types and upload limits, so a client can pre-check files
    before uploading them. Limits reflect the server's current configuration."""
    return Limits(
        formats=[
            FormatOut(kind=s.kind, mime=s.mime, extensions=list(s.extensions))
            for s in FORMATS.values()
        ],
        max_file_size_bytes=settings.max_file_size_bytes,
        max_batch_files=settings.max_batch_files,
        filename_max_length=MAX_FILENAME_LENGTH,
        filename_forbidden_chars="".join(sorted(FORBIDDEN_FILENAME_CHARS)),
    )


@router.post("/validate", tags=["validation"], summary="Validate one file",
    response_description="Validation report; check `ok`.",
)
async def validate_one(file: FileParam, settings: SettingsDep) -> FileReportOut:
    """Check one file's name, extension, size and content type without analysing it.

    Always returns 200 when the request is well-formed: read `ok` and `issues` for the
    verdict. The file is discarded after the response."""
    report, _ = await read_and_validate(file, settings)
    return report


@router.post(
    "/validate/batch",
    tags=["validation"],
    summary="Validate several files",
    response_description="One report per file, in upload order.",
    responses=_BATCH_ERRORS,
)
async def validate_batch(files: FilesParam, settings: SettingsDep) -> BatchReport:
    """Validate up to `COI_MAX_BATCH_FILES` files in one request. One report per file,
    in upload order. Invalid files don't fail the request."""
    _check_batch_size(files, settings.max_batch_files)
    return BatchReport.of([(await read_and_validate(f, settings))[0] for f in files])


@router.post(
    "/forms/analyze",
    tags=["forms"],
    summary="Convert a form into its JSON configuration",
    response_model=dict[str, Any],
    responses={
        200: {"description": "Form configuration JSON (schema to be defined)."},
        422: {
            "description": "The file failed validation (`ValidationFailed`), or the `file` "
            "field is missing (`HTTPValidationError`).",
            "content": _one_of("ValidationFailed", "HTTPValidationError"),
        },
        501: {
            "model": AnalyzeNotImplemented,
            "description": "The file is valid but analysis is not implemented yet; `route` "
            "shows the extractor tier the complexity router picked.",
        },
    },
)
async def analyze_form(file: FileParam, settings: SettingsDep, response: Response) -> Any:
    """The service's main endpoint: one form blank in, the form configuration JSON out.

    **Current state:** the file is validated, its text extracted and routed to an
    extractor tier; a valid file gets **501** with the routing decision in `route`, an
    invalid one **422** with its report. Field extraction is under development."""
    report, data = await read_and_validate(file, settings)
    if not report.ok:
        response.status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        return ValidationFailed(detail="File failed validation.", file=report).model_dump()
    try:
        doc = await asyncio.to_thread(
            extract, data, report.detected_kind, ocr_engine_from_settings(settings)
        )
    except Exception as exc:  # routing must still answer; extraction errors surface later
        if not isinstance(exc, ExtractionError):
            logging.getLogger(__name__).exception("extraction failed")
        doc = None
    route = await route_document(doc, settings)
    response.status_code = status.HTTP_501_NOT_IMPLEMENTED
    return AnalyzeNotImplemented(
        detail="Form analysis is not implemented yet.",
        file=report,
        route=RouteOut(
            tier=route.tier,
            confidence=route.confidence,
            probabilities=route.probabilities,
            source=route.source,
            escalated=route.escalated,
            reason=route.reason,
            model=tier_models(settings)[route.tier],
        ),
    ).model_dump()


@router.post(
    "/jobs",
    tags=["jobs"],
    summary="Submit a batch as a job",
    response_description="The created job.",
    status_code=status.HTTP_201_CREATED,
    responses=_BATCH_ERRORS,
)
async def create_job(files: FilesParam, settings: SettingsDep, store: JobStoreDep) -> Job:
    """Validate the files now and keep the job in memory. Valid files are `pending`
    (queued for analysis), invalid ones `rejected`. File contents are not kept."""
    _check_batch_size(files, settings.max_batch_files)
    reports = [(await read_and_validate(f, settings))[0] for f in files]
    return store.create(reports)


@router.get("/jobs", tags=["jobs"], summary="List jobs",
    response_description="Job summaries, newest first.",
)
def list_jobs(store: JobStoreDep) -> list[JobSummary]:
    """Jobs still in memory, newest first, without per-file items. Jobs are lost on
    restart and the oldest are evicted past `COI_MAX_JOBS`."""
    return [JobSummary.model_validate(j.model_dump(exclude={"items"})) for j in store.list()]


@router.get("/jobs/{job_id}", tags=["jobs"], summary="Get a job",
    response_description="The job with per-file items.",
    responses=_NOT_FOUND,
)
def get_job(job_id: str, store: JobStoreDep) -> Job:
    """A job with each file's validation report, status and result."""
    if (job := store.get(job_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    return job


@router.delete(
    "/jobs/{job_id}",
    tags=["jobs"],
    summary="Delete a job",
    response_description="Deleted.",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
)
def delete_job(job_id: str, store: JobStoreDep) -> None:
    """Remove a job and its results from memory."""
    if not store.delete(job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
