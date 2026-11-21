# COI Formularze

A REST service that turns paper form blanks (PDF, DOC, DOCX, ODS, XLSX, JPG/JPEG, PNG)
into JSON form configurations for e-services.

> **Status:** uploading, validation and jobs work. Form analysis is under development:
> `POST /api/v1/forms/analyze` answers `501` for valid files.

## Docker image

Requires Docker 24+ with Compose v2. From the repository root:

```sh
docker build -t coi-formularze:0.1.0 .
```

The base images are pinned in the `Dockerfile` but can be overridden at build time:

```sh
docker build --build-arg PYTHON_IMAGE=python:3.14.10-alpine3.24 -t coi-formularze:0.1.0 .
```

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
| `COI_JWT_ENABLED` | `false` | Require a JWT bearer token on every `/api/v1` endpoint |
| `COI_JWT_SECRET` | *(none)* | Key used to verify tokens; required when JWT is enabled |
| `COI_JWT_ALGORITHM` | `HS256` | Signing algorithm the tokens must use |
| `COI_JWT_AUDIENCE` | *(none)* | If set, tokens must carry this `aud` claim |
| `COI_JWT_ISSUER` | *(none)* | If set, tokens must carry this `iss` claim |

Pass secrets such as `COI_JWT_SECRET` only at runtime (`.env`, `--env-file` or `-e`).

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

Submit two files as a job, then fetch it by its `id`:

```sh
curl -F "files=@wniosek.pdf" -F "files=@skan.png" http://localhost:8000/api/v1/jobs
curl http://localhost:8000/api/v1/jobs/<id>
```
