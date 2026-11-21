"""FastAPI app factory. Run: `uvicorn coi_formularze.main:app`."""

from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from coi_formularze import __version__
from coi_formularze.api.routes import router
from coi_formularze.api.schemas import Health
from coi_formularze.config import get_settings
from coi_formularze.jobs import JobStore

_STATIC_DIR = Path(__file__).parent / "static"
_INDEX_HTML = files("coi_formularze").joinpath("static/index.html").read_text(encoding="utf-8")
_PREVIEW_HTML = files("coi_formularze").joinpath("static/preview.html").read_text(encoding="utf-8")

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
    {"name": "meta", "description": "Service health and capabilities."},
]


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()  # fail fast on bad config
        app.state.jobs = JobStore(capacity=settings.max_jobs)
        yield

    app = FastAPI(
        title="COI Formularze API",
        version=__version__,
        summary="Turns paper form blanks into e-form JSON configurations.",
        description=API_DESCRIPTION,
        openapi_tags=TAGS,
        # operationId = function name (e.g. `validate_one`): readable specs and generated clients.
        generate_unique_id_function=lambda route: route.name,
        lifespan=lifespan,
    )
    app.include_router(router)

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
