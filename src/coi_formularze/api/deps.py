"""Shared request helpers: reading uploads within the size limit, store access."""

from typing import Annotated

from fastapi import Depends, Request, UploadFile

from coi_formularze.api.schemas import FileReportOut
from coi_formularze.config import Settings, get_settings
from coi_formularze.jobs import JobStore
from coi_formularze.parse.llm import ChatModel, EdenAiChat
from coi_formularze.validation.checks import validate_file


def get_job_store(request: Request) -> JobStore:
    return request.app.state.jobs


SettingsDep = Annotated[Settings, Depends(get_settings)]
JobStoreDep = Annotated[JobStore, Depends(get_job_store)]


def get_chat(request: Request, settings: SettingsDep) -> ChatModel | None:
    """The LLM client (one per app, created on first use); None without an API key."""
    if settings.edenai_api_key is None:
        return None
    chat = getattr(request.app.state, "chat", None)
    if chat is None:
        chat = request.app.state.chat = EdenAiChat(
            settings.edenai_api_key.get_secret_value(), timeout=settings.llm_timeout_s
        )
    return chat


ChatDep = Annotated[ChatModel | None, Depends(get_chat)]


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
