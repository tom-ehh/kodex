# COI Formularze

A REST service that turns paper form blanks (PDF, DOC, DOCX, ODS, XLSX, JPG/JPEG, PNG)
into JSON form configurations for e-services.

> **Status:** `POST /api/v1/forms/analyze` returns the Form Definition. It reads the
> file's text (OCR for scans and images), routes it to a model tier, drafts the form with
> an open-weights LLM in passes and validates the result. Jobs don't analyse files yet.

## Docker image

Requires Docker 24+ with Compose v2. From the repository root:

```sh
docker build -t coi-formularze:0.1.0 .
```

The base images are pinned in the `Dockerfile` but can be overridden at build time:

```sh
docker build --build-arg PYTHON_IMAGE=python:3.14.10-alpine3.24 -t coi-formularze:0.1.0 .
```

### Complexity router image

`compose.yaml` also runs `router`: [basal-serve](https://github.com/rkinas/basal) with the
`Remek/basal-omni-pico` model, which picks the extractor tier for each form. It needs
PyTorch, so it has its own glibc-based image (`docker/basal/Dockerfile`, about 1.3 GB)
while the API image stays Alpine. Build it on its own with:

```sh
docker build -t coi-formularze-router:0.1.0 docker/basal
```

On first start it downloads the weights (about 1.5 GB) into the `hf-cache` volume and
loads the model for a few minutes; until then it answers `503 {"status": "starting"}` and
the API routes forms with a feature-based rule instead. Later starts reuse the volume.

## Running

With Docker Compose (builds the image if needed):

```sh
cp .env.example .env        # then adjust, see Configuration
docker compose up --build
```

With Docker only:

```sh
docker run --rm -p 8000:8000 --env-file .env coi-formularze:0.1.0
```

Without Docker (requires [uv](https://docs.astral.sh/uv/) and Python 3.14):

```sh
uv sync
uv run uvicorn coi_formularze.main:app --port 8000
```

To check that it runs, open:

| URL | What |
|-----|------|
| http://localhost:8000/ | Upload page: send files and see the JSON response |
| http://localhost:8000/preview | Form preview: load a form JSON and fill it in as the e-service form (also opened from analysis results) |
| http://localhost:8000/docs | Interactive API documentation |
| http://localhost:8000/health | Returns `{"status": "ok"}` |

## Configuration

Set through environment variables or a `.env` file (see `.env.example`). Invalid values
stop the service at startup.

| Variable | Default | Description |
|----------|---------|-------------|
| `COI_MAX_FILE_SIZE_BYTES` | `20971520` (20 MiB) | Maximum size of one uploaded file |
| `COI_MAX_BATCH_FILES` | `20` | Maximum number of files in one batch request |
| `COI_MAX_JOBS` | `100` | Jobs kept in memory; past this, the oldest is dropped |
| `COI_EDENAI_API_KEY` | *(none)* | EdenAI key for OCR of scans and images (`EDENAI_API_KEY` also accepted); without it, those files can't be read |
| `COI_OCR_MODEL` | `deepinfra/allenai/olmOCR-7B-0725-FP8` | EdenAI v3 model id used for OCR; must be an open-weights model |
| `COI_OCR_TIMEOUT_S` | `120` | Seconds to wait for one OCR page |
| `COI_ROUTER_ENABLED` | `false` | Ask the complexity router (basal-serve) which extractor tier a form needs; off, a feature-based rule decides. Compose turns it on |
| `COI_ROUTER_URL` | `http://127.0.0.1:8001` | Base URL of basal-serve (`http://router:8000` under Compose) |
| `COI_ROUTER_THRESHOLD` | `0.7` | Router answers below this confidence move the form one tier up |
| `COI_ROUTER_TIMEOUT_S` | `5` | Seconds to wait for the router; on timeout the feature-based rule decides |
| `COI_ROUTER_MAX_STATE_CHARS` | `12000` | Characters of form text sent to the router (about 4k tokens; the model's limit is ~6k) |
| `COI_TIER_SIMPLE_MODEL` | `deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731` | EdenAI model that fills the form JSON for `simple` forms (open-weights; reasoning off) |
| `COI_TIER_MEDIUM_MODEL` | `deepinfra/Qwen/Qwen3.6-35B-A3B` | Same, for `medium` forms |
| `COI_TIER_HARD_MODEL` | `qwen/deepseek-v4-pro-0813` | Same, for `hard` forms and escalations |
| `COI_LLM_TIMEOUT_S` | `120` | Seconds to wait for one LLM call |
| `COI_JWT_ENABLED` | `false` | Require a JWT bearer token on every `/api/v1` endpoint |
| `COI_JWT_SECRET` | *(none)* | Key used to verify tokens; required when JWT is enabled |
| `COI_JWT_ALGORITHM` | `HS256` | Signing algorithm the tokens must use |
| `COI_JWT_AUDIENCE` | *(none)* | If set, tokens must carry this `aud` claim |
| `COI_JWT_ISSUER` | *(none)* | If set, tokens must carry this `iss` claim |

Pass secrets such as `COI_JWT_SECRET` and `COI_EDENAI_API_KEY` only at runtime (`.env`, `--env-file` or `-e`).

## REST API

Full reference with request and response schemas: **`/docs`** (Swagger UI) on a running
server, or the raw spec at `/openapi.json`.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/forms/analyze` | Convert one form file into its JSON configuration |
| `POST` | `/api/v1/validate` | Check one file without analysing it |
| `POST` | `/api/v1/validate/batch` | Check several files without analysing them |
| `POST` | `/api/v1/jobs` | Submit several files as a job |
| `GET` | `/api/v1/jobs` | List jobs |
| `GET` | `/api/v1/jobs/{job_id}` | Get one job with per-file results |
| `DELETE` | `/api/v1/jobs/{job_id}` | Delete a job |
| `GET` | `/api/v1/formats` | Accepted formats and upload limits |
| `GET` | `/health` | Liveness check |

- Files are sent as `multipart/form-data`: field `file` for one file, field `files`
  repeated for several. Accepted: `.pdf`, `.doc`, `.docx`, `.ods`, `.xlsx`, `.jpg`,
  `.jpeg`, `.png`.
- Every file is checked for name, size, extension, and whether its content matches the
  extension. Each problem is reported in `issues`.
- When `COI_JWT_ENABLED=true`, send `Authorization: Bearer <token>`.

### Analysing a form

`POST /api/v1/forms/analyze` takes one file and answers:

| Status | Body |
|--------|------|
| `200` | The Form Definition, ready to import (`title`, `tabs`, `flow`) |
| `200` with `?envelope=true` | `{"form", "route", "attempts", "dictionary_proposals"}`: `form` is the same Form Definition; `route` is the final model tier, `attempts` lists every tier tried, `dictionary_proposals` suggests new dictionaries for reusable option lists |
| `422` | The file failed validation, or its text could not be extracted |
| `502` | No model tier, up to `hard`, produced a valid form; `attempts` says why |
| `503` | No model API key is configured |

How it works: the text is extracted, a router picks a tier (`simple`, `medium`,
`hard`), and that tier's model drafts the form in passes (facts, outline, each
section, dynamic states). Code builds the ids, layout, flow and JSONata and validates
the result. If a tier fails, the form moves one tier up.

### Examples

Validate a file whose content doesn't match its extension:

```sh
curl -F "file=@skan.png" http://localhost:8000/api/v1/validate
```

```json
{
  "filename": "skan.png",
  "size_bytes": 15,
  "extension": ".png",
  "declared_kind": "png",
  "detected_kind": "pdf",
  "detected_mime": "application/pdf",
  "client_mime": "image/png",
  "ok": false,
  "issues": [
    {"code": "content_extension_mismatch", "message": "Extension says png, content is pdf."}
  ]
}
```

Analyse a form, with the route and attempts:

```sh
curl -F "file=@wniosek.pdf" "http://localhost:8000/api/v1/forms/analyze?envelope=true"
```

Submit two files as a job, then fetch it by its `id`:

```sh
curl -F "files=@wniosek.pdf" -F "files=@skan.png" http://localhost:8000/api/v1/jobs
curl http://localhost:8000/api/v1/jobs/<id>
```
