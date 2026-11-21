"""Shared request helpers: reading uploads within the size limit, store access."""

from typing import Annotated

from fastapi import Depends, Request, UploadFile

from coi_formularze.api.schemas import FileReportOut
from coi_formularze.config import Settings, get_settings
from coi_formularze.jobs import JobStore
from coi_formularze.validation.checks import validate_file


def get_job_store(request: Request) -> JobStore:
    return request.app.state.jobs


SettingsDep = Annotated[Settings, Depends(get_settings)]
JobStoreDep = Annotated[JobStore, Depends(get_job_store)]


async def read_and_validate(upload: UploadFile, settings: Settings) -> tuple[FileReportOut, bytes]:
    """Read at most limit+1 bytes (enough to know it is too big) and validate.

    The bytes are returned for the caller to analyse within the request; nothing
    is persisted. Starlette spools large uploads to a temp file it deletes itself.
    """
    limit = settings.max_file_size_bytes
    try:
        data = await upload.read(limit + 1)
    finally:
        await upload.close()
    report = validate_file(
        upload.filename or "",
        data,
        max_size_bytes=limit,
        client_mime=upload.content_type,
    )
    return FileReportOut.of(report), data
