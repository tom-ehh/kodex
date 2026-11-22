"""FastAPI app factory. Run: `uvicorn kodex.main:app`."""

from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from kodex import __version__
from kodex.api.routes import router
from kodex.api.schemas import Health
from kodex.auth import require_auth
from kodex.config import get_settings
from kodex.jobs import JobStore
from kodex.morpheus import open_store
from kodex.morpheus.api import build_router as build_morpheus_router
from kodex.morpheus.doccache import DocCache
from kodex.morpheus.sleep.api import router as sleep_router
from kodex.morpheus.sleep.api import start_scheduler, stop_scheduler
from kodex.morpheus.wake import Wake, start_worker, stop_worker
from kodex.parse.llm import EdenAiChat

_STATIC_DIR = Path(__file__).parent / "static"
_APP_INDEX = _STATIC_DIR / "app" / "index.html"  # built by frontend/ (Vite); absent in a bare checkout
_INDEX_HTML = (
    _APP_INDEX if _APP_INDEX.is_file() else files("kodex").joinpath("static/index.html")
).read_text(encoding="utf-8")
_PREVIEW_HTML = files("kodex").joinpath("static/preview.html").read_text(encoding="utf-8")

API_DESCRIPTION = """
Converts paper form blanks (PDF, DOC, DOCX, ODS, XLSX, JPG/JPEG, PNG) into JSON
form configurations for e-services.

**Files are never stored.** Each upload is held only while its request runs.

### Validation
Every upload is checked for filename (≤ 250 characters, none of `; : > < / \\ | ' * % $`),
size (`COI_MAX_FILE_SIZE_BYTES`), a supported extension, and a **content type detected
from the file's bytes** that matches the extension. The client's `Content-Type` is not trusted.

### Authentication
When the server runs with `COI_JWT_ENABLED=true`, every `/api/v1` endpoint requires
`Authorization: Bearer <JWT>`. Otherwise no authentication is needed.
""".strip()

TAGS = [
    {"name": "forms", "description": "Form analysis: file in, form configuration JSON out."},
    {"name": "validation", "description": "Pre-checks on uploads, without analysis."},
    {"name": "jobs", "description": "Batches kept in memory with per-file status and results."},
    {"name": "morpheus", "description": "Learned parser skills, review queue, sleep cycles, rollback, metrics."},
    {"name": "meta", "description": "Service health and capabilities."},
]


async def _start_morpheus(app: FastAPI, settings):
    """Store, DocCache and the wake worker (a background task, never on the request path)."""
    app.state.morpheus = open_store(settings)
    app.state.doc_cache = DocCache(settings.morpheus_doc_ttl_s)
    chat = None
    if settings.edenai_api_key is not None:
        chat = app.state.chat = EdenAiChat(settings.edenai_api_key.get_secret_value(),
                                           timeout=settings.llm_timeout_s)
    app.state.wake = Wake(app.state.morpheus, settings, chat, app.state.doc_cache)
    if settings.skills_snapshot is not None:
        return None
    return await start_worker(app.state.wake)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()  # fail fast on bad config
        app.state.jobs = JobStore(capacity=settings.max_jobs)
        task = sleeper = None
        if settings.morpheus_enabled:
            task = await _start_morpheus(app, settings)
            sleeper = start_scheduler(app, settings)  # Morpheus sleep: cron + early trigger
        yield
        await stop_scheduler(sleeper)
        await stop_worker(task)

    app = FastAPI(
        title="Kodex API",
        version=__version__,
        summary="Turns paper form blanks into e-form JSON configurations.",
        description=API_DESCRIPTION,
        openapi_tags=TAGS,
        # operationId = function name (e.g. `validate_one`): readable specs and generated clients.
        generate_unique_id_function=lambda route: route.name,
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(build_morpheus_router([Depends(require_auth)]))
    app.include_router(sleep_router)

    @app.get("/health", tags=["meta"], summary="Liveness check", response_description="Service is up.")
    def health() -> Health:
        """Returns 200 while the process serves requests. No authentication."""
        return Health(status="ok")

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(_INDEX_HTML)

    @app.get("/preview", include_in_schema=False)
    def preview() -> HTMLResponse:
        """Form Preview: renders a Form Definition as a fillable e-service form."""
        return HTMLResponse(_PREVIEW_HTML)

    # Renderer assets (JS, CSS, vendored jsonata). Served locally: no CDN at runtime.
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    return app


app = create_app()
