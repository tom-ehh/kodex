<p align="center">
  <img src="assets/banner.jpg" alt="Kodex" width="720">
</p>

# Kodex

A REST service that turns paper form blanks (PDF, DOC, DOCX, ODS, XLSX, JPG/JPEG, PNG)
into JSON form configurations for e-services.

## Approach

### Architecture

```mermaid
flowchart LR
  F[/"Form file<br/>PDF · DOC(X) · ODS · XLSX · JPG · PNG"/]:::io --> X["Extract<br/>text + layout<br/>(olmOCR for scans)"]
  X --> R{{"System One router<br/>basal-omni-pico"}}
  R -->|"simple · medium · hard"| T["Tier model<br/>open-weights LLM<br/>parses in passes"]
  T --> V{"Validate<br/>schema + PESEL, NIP,<br/>REGON, ZIP, EMAIL"}
  V -->|ok| P["Form Definition<br/>preview + download"]:::io
  V -->|"fail: retry, then next tier"| T
  DB[("Versioned<br/>skill store")] -.->|"matching skills"| T
  P -->|"worker correction"| W["Morpheus · wake<br/>narrow skill,<br/>replay-tested"]:::morpheus
  V -->|"failures, escalations"| W
  W --> DB
  DB --> S["Morpheus · sleep<br/>merge + prune,<br/>replay-gated"]:::morpheus
  S --> DB

classDef morpheus fill:#fde68a,stroke:#d97706,stroke-width:2px,color:#111
classDef io fill:#f1f5f9,stroke:#64748b,color:#111
```

The request path runs left to right with no agent loop. *Morpheus* (yellow) runs
asynchronously with the request: it learns from it but never slows it down.

| Part | Role |
|------|------|
| Extract | Every accepted format becomes one text document with layout and quality features. The model gets text, not page images: a fraction of the tokens. |
| System One router | `Remek/basal-omni-pico` (about 235 ms on CPU, own container) labels the form `simple`, `medium` or `hard`. Below 0.7 confidence the form goes one tier up; if the router is down, feature rules decide. |
| Tier model | The tier's open-weights model fills the form in short passes: facts, outline, each section in parallel, show/hide conditions. It says what a field *is* (`pesel`, `yes_no`, `country`); code picks widget, validator, dictionary, ids, flow and JSONata. |
| Validate | Each pass is checked against the schema and validators. A failing pass is retried once with its errors, then the form moves up a tier. |
| Skill store | Versioned set of skills (prompt hints, validator fixes) the tier model loads per section. Rollback; `COI_SKILLS_SNAPSHOT` pins a version. |
| *Morpheus* | Turns corrections and failures into skills (wake) and merges them (sleep). |

### It works today

Routing sends each form to the smallest model that can handle it. That halves the cost per
form compared with sending everything to the largest model, without losing quality.

<img src="assets/chart-router-cost.svg" alt="Routing halves the cost" width="420"> <img src="assets/chart-router-quality.svg" alt="Routing keeps quality" width="420">

### It grows with you: *Morpheus*

Correcting a field in a generated form is the only input *Morpheus* needs.

- **Awake.** Each correction is stored as a shape (`00-950` → `##-###`), never as content.
  When the same kind of fix repeats, *Morpheus* writes a narrow skill. It goes live only
  after the corrected section replays correctly with it; otherwise it waits for a human.
- **Asleep.** Nightly, or sooner once enough patterns pile up, similar skills merge into
  general ones. A merge goes live only if replaying reference forms shows no new errors.

Waking and sleeping do different jobs. Wake patches give a quick jump in accuracy, one
narrow fix at a time. Sleep folds those patches into fewer, general skills: some of the
jump is traded for a smaller skill set that is easier to review, keep consistent and
build on, and accuracy stays above where it started.

<img src="assets/chart-morpheus.svg" alt="Morpheus learns, then compresses" width="560">

The benchmark scripts and raw results behind these charts are in [benchmarks/](benchmarks/).

*Morpheus* is our own design, a hybrid of two ideas. From Nous Research's
[Hermes Agent](https://github.com/NousResearch/hermes-agent) it takes agent-written,
refinable skills behind an approval gate (not the full agent harness). From research on
sleep in AI systems, [sleep-time compute](https://arxiv.org/abs/2504.13171) and
[sleep-like replay](https://doi.org/10.1038/s41467-022-34938-7), it takes the split
between fast narrow learning while awake and slower offline merging, checked by replay.
No model weights change; only the skills do.

## Docker image

Requires Docker 24+ with Compose v2. From the repository root:

```sh
docker build -t kodex:0.1.0 .
```

The base images are pinned in the `Dockerfile` but can be overridden at build time:

```sh
docker build --build-arg PYTHON_IMAGE=python:3.14.10-alpine3.24 -t kodex:0.1.0 .
```

The web interface (`frontend/`, React + Vite) is built inside the image by a Node stage
(`NODE_IMAGE`, default `node:22.21.1-alpine3.22`). Only the built static files are copied
into the Python image, so the running container has no Node.

### Complexity router image

`compose.yaml` also runs `router`: [basal-serve](https://github.com/rkinas/basal) with the
`Remek/basal-omni-pico` model, which picks the extractor tier for each form. It needs
PyTorch, so it has its own glibc-based image (`docker/basal/Dockerfile`, about 1.3 GB)
while the API image stays Alpine. Build it on its own with:

```sh
docker build -t kodex-router:0.1.0 docker/basal
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
docker run --rm -p 8000:8000 --env-file .env kodex:0.1.0
```

Without Docker (requires [uv](https://docs.astral.sh/uv/) and Python 3.14):

```sh
uv sync
(cd frontend && npm ci && npm run build)   # web interface; optional, see below
uv run uvicorn kodex.main:app --port 8000
```

The build writes to `src/kodex/static/app/`. Without it, `/` serves a plain
fallback upload page. For frontend work, `cd frontend && npm run dev` serves the interface
on http://localhost:5173 and forwards API calls to the server on port 8000 (`KODEX_API` overrides).

To check that it runs, open:

| URL | What |
|-----|------|
| http://localhost:8000/ | Web interface: upload forms, follow progress, preview and download the JSON |
| http://localhost:8000/preview | Form preview: load a form JSON and fill it in as the e-service form (also opened from analysis results) |
| http://localhost:8000/docs | Interactive API documentation |
| http://localhost:8000/health | Returns `{"status": "ok"}` |

## Configuration

Set through environment variables or a `.env` file (see `.env.example`). Invalid values
stop the service at startup.

### Uploads and jobs

| Variable | Default | Description |
|----------|---------|-------------|
| `COI_MAX_FILE_SIZE_BYTES` | `20971520` (20 MiB) | Maximum size of one uploaded file |
| `COI_MAX_BATCH_FILES` | `20` | Maximum number of files in one batch request |
| `COI_MAX_JOBS` | `100` | Jobs kept in memory; past this, the oldest is dropped |

### Authentication (JWT)

| Variable | Default | Description |
|----------|---------|-------------|
| `COI_JWT_ENABLED` | `false` | Require a JWT bearer token on every `/api/v1` endpoint |
| `COI_JWT_SECRET` | *(none)* | Key used to verify tokens; required when JWT is enabled |
| `COI_JWT_ALGORITHM` | `HS256` | Signing algorithm the tokens must use |
| `COI_JWT_AUDIENCE` | *(none)* | If set, tokens must carry this `aud` claim |
| `COI_JWT_ISSUER` | *(none)* | If set, tokens must carry this `iss` claim |

### Models (EdenAI, open-weights only)

| Variable | Default | Description |
|----------|---------|-------------|
| `COI_EDENAI_API_KEY` | *(none)* | EdenAI key for OCR of scans and images (`EDENAI_API_KEY` also accepted); without it, those files can't be read |
| `COI_OCR_MODEL` | `deepinfra/allenai/olmOCR-7B-0725-FP8` | EdenAI v3 model id used for OCR; must be an open-weights model |
| `COI_OCR_TIMEOUT_S` | `120` | Seconds to wait for one OCR page |
| `COI_TIER_SIMPLE_MODEL` | `deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731` | EdenAI model that fills the form JSON for `simple` forms (open-weights; reasoning off) |
| `COI_TIER_MEDIUM_MODEL` | `deepinfra/Qwen/Qwen3.6-35B-A3B` | Same, for `medium` forms |
| `COI_TIER_HARD_MODEL` | `qwen/deepseek-v4-pro-0813` | Same, for `hard` forms and escalations |
| `COI_LLM_TIMEOUT_S` | `120` | Seconds to wait for one LLM call |

### Complexity router (System One)

| Variable | Default | Description |
|----------|---------|-------------|
| `COI_ROUTER_ENABLED` | `false` | Ask the complexity router (basal-serve) which extractor tier a form needs; off, a feature-based rule decides. Compose turns it on |
| `COI_ROUTER_URL` | `http://127.0.0.1:8001` | Base URL of basal-serve (`http://router:8000` under Compose) |
| `COI_ROUTER_THRESHOLD` | `0.7` | Router answers below this confidence move the form one tier up |
| `COI_ROUTER_TIMEOUT_S` | `5` | Seconds to wait for the router; on timeout the feature-based rule decides |
| `COI_ROUTER_MAX_STATE_CHARS` | `12000` | Characters of form text sent to the router (about 4k tokens; the model's limit is ~6k) |

### Morpheus: skill store

| Variable | Default | Description |
|----------|---------|-------------|
| `COI_MORPHEUS_ENABLED` | `false` | Turn on the Morpheus skill store (learned and seed skills for the parser). Off, the database is never opened. Compose turns it on |
| `COI_MORPHEUS_DB` | `/data/morpheus.sqlite` | SQLite file of the skill store (the `morpheus-data` volume under Compose) |
| `COI_SKILLS_SNAPSHOT` | *(none)* | Pin a skillset version: the active skills stay fixed and changes to them are refused (repeatable scoring) |
| `COI_EPISODIC_TTL_DAYS` | `14` | Days a narrow (episodic) learned skill stays active |
| `COI_EPISODIC_MAX` | `20` | Maximum number of episodic skills |
| `COI_GENERAL_MAX` | `50` | Maximum number of general skills |
| `COI_SKILLS_MAX_PER_SECTION` | `5` | Most skills matched to one form section |
| `COI_MORPHEUS_DOC_TTL_S` | `86400` | Seconds an analysed form's extracted text stays in memory for corrections and replay (never written to disk) |
| `COI_MORPHEUS_MODEL` | `COI_TIER_HARD_MODEL` | Model that writes Morpheus skills |
| `COI_MORPHEUS_REPLAY_MODEL` | `COI_TIER_MEDIUM_MODEL` | Model that replays a section to test a new skill |

### Morpheus: wake (learning from corrections)

| Variable | Default | Description |
|----------|---------|-------------|
| `COI_FLAG_HIST_MIN` | `2` | Episodes of the same field family and validator needed before a skill is written |
| `COI_FLAG_PATTERN_MIN` | `0.5` | System One probability that a correction is a pattern, needed to write a skill |
| `COI_FLAG_CONSISTENT_MIN` | `0.5` | System One consistency score below which a correction is not learned |
| `COI_MORPHEUS_BASAL_RETRIES` | `3` | Wake ticks to wait for System One before deciding on rules alone |
| `COI_MORPHEUS_GATE_MD_DIR` | unset | Directory with the gate forms' extracted markdown for the wake smoke test and the sleep replay gate; unset skips the smoke test, and sleep then merges nothing |
| `COI_MORPHEUS_WAKE_INTERVAL_S` | `30` | Seconds between background wake runs |

### Morpheus: sleep (merging skills)

| Variable | Default | Description |
|----------|---------|-------------|
| `COI_SLEEP_CRON` | `0 3 * * *` | When Morpheus sleeps (merges learned skills), as a 5-field cron in UTC; empty turns the schedule off |
| `COI_SLEEP_MIN_PATTERNS` | `5` | Sleep early once this many learned, not yet consolidated pattern episodes are waiting |
| `COI_SAME_PATTERN_MIN` | `0.6` | System One probability that two skills are the same pattern, needed to propose merging them |
| `COI_FAITHFUL_MIN` | `0.75` | System One probability that a merged skill keeps what each original did; below it the merge goes to review |
| `COI_SLEEP_PAIR_BUDGET` | `300` | Most skill pairs System One judges in one sleep |
| `COI_SLEEP_MAX_CANDIDATES` | `60` | Most skills considered in one sleep |

Pass secrets such as `COI_JWT_SECRET` and `COI_EDENAI_API_KEY` only at runtime (`.env`, `--env-file` or `-e`).

## REST API

Full reference with request and response schemas: **`/docs`** (Swagger UI) on a running
server, or the raw spec at `/openapi.json`.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/v1/forms/analyze` | Convert one form file into its JSON configuration |
| `POST` | `/api/v1/forms/{form_id}/corrections` | Send a worker's edits to a generated form (Morpheus) |
| `GET` | `/api/v1/morpheus/skills` | List Morpheus skills (`?layer=`, `?status=`) |
| `GET` | `/api/v1/morpheus/review` | List the human review queue (`?all=true` for decided items) |
| `POST` | `/api/v1/morpheus/review/{item_id}` | Approve or reject a review item |
| `POST` | `/api/v1/morpheus/sleep` | Run a Morpheus sleep cycle now |
| `POST` | `/api/v1/morpheus/rollback/{version}` | Restore an earlier set of active skills |
| `GET` | `/api/v1/morpheus/metrics` | One row per sleep cycle (accuracy, skill count, cost) |
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
- Every file is checked before it is read: the name (at most 250 characters, none of
  `; : > < / \ | ' * % $`), the size (`COI_MAX_FILE_SIZE_BYTES`), the extension, and
  whether the content matches the extension (MIME sniffing). Each problem is reported in
  `issues`.
- When `COI_JWT_ENABLED=true`, send `Authorization: Bearer <token>`.

### Analysing a form

`POST /api/v1/forms/analyze` takes one file and answers:

| Status | Body |
|--------|------|
| `200` | The Form Definition, ready to import (`title`, `tabs`, `flow`) |
| `200` with `?envelope=true` | `{"form_id", "form", "route", "attempts", "dictionary_proposals"}`: `form` is the same Form Definition; `route` is the final model tier, `attempts` lists every tier tried, `dictionary_proposals` suggests new dictionaries for reusable option lists |
| `422` | The file failed validation, or its text could not be extracted |
| `502` | No model tier, up to `hard`, produced a valid form: `{"detail", "attempts", "form_id", "needs_review", "review_id", "form"}`. `attempts` says why; `form` is the last draft that failed validation (not importable as is, or `null`); with Morpheus on, `needs_review` is `true` and `review_id` is the review queue item |
| `503` | No model API key is configured |

How it works: the text is extracted, a router picks a tier (`simple`, `medium`,
`hard`), and that tier's model drafts the form in passes (facts, outline, each
section, dynamic states). Code builds the ids, layout, flow and JSONata and validates
the result. If a tier fails, the form moves one tier up.

Every `200` carries an `X-Form-Id` header (also `form_id` in the envelope). With
`COI_MORPHEUS_ENABLED=true`, matching skills add hints to each section prompt and fix
validators in code, and failures, escalations and worker edits are logged as
shape-only episodes for Morpheus to learn from.

### Corrections

`POST /api/v1/forms/{form_id}/corrections` with
`{"edits": [{"field_path", "attr", "before", "after"}], "why", "learn_now"}`, while the
form is cached (`COI_MORPHEUS_DOC_TTL_S`). `attr` is `value`, `type`, `validator`,
`required`, `label` or `options`. Values are stored only as shapes (`00-950` → `##-###`);
`why` is never stored or logged (it is held in memory for the wake step only). `201`
lists the episode ids; `404` for an unknown or expired id; `503` when Morpheus is off.

A background task (every `COI_MORPHEUS_WAKE_INTERVAL_S`) turns corrections into
episodic skills; `learn_now: true` processes them right after the response. A skill is
written only for a repeated pattern (the same field family and validator seen at least
`COI_FLAG_HIST_MIN` times), a passing code check and System One's agreement, and it is
activated only after replaying the form's section with it. A first occurrence with
`learn_now`, or a skill whose replay fails, goes to the review queue:
`GET /api/v1/morpheus/review` lists it, `POST /api/v1/morpheus/review/{id}` with
`{"decision": "approve"}` or `"reject"` decides it.

The queue also gets a `needs_review_form` item for every form that failed at `hard`
(a correction to that form closes it), and a `recurring_failure` item when the wake
task sees the same field family and validator fail validation `COI_FLAG_HIST_MIN`
times: a human correction to one such form lets Morpheus learn the pattern. In the
web app, failed forms show as "Do weryfikacji"; the preview's "Popraw formularz"
sends corrections, and the Morpheus panel on the main page handles the queue and
starts a sleep.

### Sleep

Morpheus merges similar learned skills into general ones while it "sleeps": on
`COI_SLEEP_CRON`, early after `COI_SLEEP_MIN_PATTERNS` learned patterns, or on
`POST /api/v1/morpheus/sleep` (`202`; `?wait=true` returns the cycle summary). A merge
is activated only if a replay of the gate forms shows no new errors; otherwise it waits
in the review queue, and approving it retires the skills it absorbed. `409` while the
skills are pinned (`COI_SKILLS_SNAPSHOT`) or a cycle is running; `503` when Morpheus is
off. `POST /api/v1/morpheus/rollback/{version}` restores a skill set;
`GET /api/v1/morpheus/metrics` lists the cycles. Postcode (ZIP) skills only apply in
Polish address sections.

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
