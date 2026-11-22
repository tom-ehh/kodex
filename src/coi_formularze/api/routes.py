"""REST API v1. Docstrings and `responses` here become the OpenAPI spec served at /docs."""

import asyncio
import logging
import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from coi_formularze.api.deps import (
    ChatDep,
    DocCacheDep,
    JobStoreDep,
    MorpheusDep,
    SettingsDep,
    read_and_validate,
)
from coi_formularze.api.schemas import (
    AnalyzeEnvelope,
    AnalyzeFailed,
    AttemptOut,
    BatchReport,
    CorrectionsIn,
    CorrectionsOut,
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
from coi_formularze.morpheus.hooks import Hooks, record_corrections
from coi_formularze.parse.pipeline import ParseFailed, parse
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
    response_model=dict[str, Any] | AnalyzeEnvelope,
    responses={
        200: {
            "description": "The Form Definition, ready to import (the organisers' schema). "
            "With `?envelope=true`: an `AnalyzeEnvelope` holding it plus the route, the "
            "tiers tried and dictionary proposals.",
        },
        422: {
            "description": "The file failed validation or its text could not be extracted "
            "(`ValidationFailed`), or the `file` field is missing (`HTTPValidationError`).",
            "content": _one_of("ValidationFailed", "HTTPValidationError"),
        },
        502: {"model": AnalyzeFailed, "description": "No tier up to `hard` produced a valid form."},
        503: {"model": ErrorDetail, "description": "No model API key is configured."},
    },
)
async def analyze_form(
    file: FileParam,
    settings: SettingsDep,
    chat: ChatDep,
    morpheus: MorpheusDep,
    doc_cache: DocCacheDep,
    response: Response,
    envelope: Annotated[bool, Query(description="Wrap the form with its route, attempts "
                                                "and dictionary proposals.")] = False,
) -> Any:
    """The service's main endpoint: one form blank in, the form configuration JSON out.

    The file is validated, its text extracted, and a complexity router picks a model
    tier. The model drafts the form in passes (facts, outline, one draft per section,
    dynamic states); code assembles and validates the Form Definition. A tier that
    fails moves the form one tier up; failing at `hard` gives **502**."""
    report, data = await read_and_validate(file, settings)
    if not report.ok:
        response.status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        return ValidationFailed(detail="File failed validation.", file=report).model_dump()
    if chat is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No model API key is configured.")
    try:
        doc = await asyncio.to_thread(
            extract, data, report.detected_kind, ocr_engine_from_settings(settings)
        )
    except Exception as exc:
        if not isinstance(exc, ExtractionError):
            logging.getLogger(__name__).exception("extraction failed")
        response.status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        return ValidationFailed(detail=f"Text extraction failed: {exc}", file=report).model_dump()
    route = await route_document(doc, settings)
    form_id = uuid.uuid4().hex
    response.headers["X-Form-Id"] = form_id
    hooks = Hooks(morpheus, form_id, settings.skills_max_per_section) if morpheus else None
    try:
        parsed = await parse(doc.markdown, route, settings, chat, hooks=hooks)
    except ParseFailed as exc:
        if morpheus:
            doc_cache.put(form_id, doc)
        response.status_code = status.HTTP_502_BAD_GATEWAY
        return AnalyzeFailed(detail="No model tier produced a valid form.",
                             attempts=[_attempt(a) for a in exc.attempts]).model_dump()
    if morpheus:
        doc_cache.put(form_id, doc, parsed.result.form)
    if not envelope:
        return parsed.result.form
    r = parsed.route
    return AnalyzeEnvelope(
        form_id=form_id,
        form=parsed.result.form,
        route=RouteOut(tier=r.tier, confidence=r.confidence, probabilities=r.probabilities,
                       source=r.source, escalated=r.escalated, reason=r.reason,
                       model=tier_models(settings)[r.tier]),
        attempts=[_attempt(a) for a in parsed.attempts],
        dictionary_proposals=[p.model_dump() for p in parsed.result.dictionary_proposals],
    ).model_dump()


@router.post(
    "/forms/{form_id}/corrections",
    tags=["forms"],
    summary="Send a worker's corrections to a generated form",
    response_description="The episodes recorded.",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ErrorDetail, "description": "Unknown or expired form id."},
        503: {"model": ErrorDetail, "description": "Morpheus is disabled."},
    },
)
def post_corrections(form_id: str, body: CorrectionsIn, morpheus: MorpheusDep,
                     doc_cache: DocCacheDep) -> CorrectionsOut:
    """Structured edits to a form from `POST /forms/analyze` (its `X-Form-Id` header or
    envelope `form_id`), kept while the form is cached (`COI_MORPHEUS_DOC_TTL_S`).

    Each edit becomes one Morpheus episode holding **shapes only** (a value `00-950`
    is stored as `##-###`). `why` is **never stored or logged**. `learn_now` is
    recorded on the episodes; live learning from it comes with the wake phase."""
    if morpheus is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Morpheus is disabled.")
    cached = doc_cache.get(form_id)
    if cached is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown or expired form id.")
    eps = record_corrections(morpheus, form_id, cached.form, body.edits, body.learn_now)
    return CorrectionsOut(form_id=form_id, episode_ids=[e.id for e in eps], learn_now=body.learn_now)


def _attempt(a) -> AttemptOut:
    return AttemptOut(tier=a.tier, model=a.model, calls=a.calls, cost_usd=round(a.cost, 6), error=a.error)


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
