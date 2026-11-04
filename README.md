# PipelineIQ

> **YAML-defined data pipeline orchestration** — declare pipelines in YAML, run them on Celery workers with Pandas/DuckDB execution, trace every column back to its source, and let Gemini AI propose fixes when runs fail.

[![CI](https://github.com/Siddharthk17/PipelineIQ/actions/workflows/ci.yml/badge.svg)](https://github.com/Siddharthk17/PipelineIQ/actions)
[![Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-15-000000?logo=next.js)](https://nextjs.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?logo=postgresql)](https://postgresql.org)

**Live demo** → [pipeline-iq0.vercel.app](https://pipeline-iq0.vercel.app)
Login: `demo@pipelineiq.app` / `Demo1234!`


## Table of Contents

- [Why PipelineIQ?](#why-pipelineiq)
- [Quick Start](#quick-start)
- [The Pipeline YAML](#the-pipeline-yaml)
- [Step Types](#step-types)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [API Reference](#api-reference)
- [Frontend](#frontend)
- [Observability](#observability)
- [Testing](#testing)
- [Database](#database)
- [Deployment](#deployment)
- [CI/CD](#cicd)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Postman Collection](#postman-collection)
- [Contributing](#contributing)
- [License](#license)


## Why PipelineIQ?

Data teams keep pipeline logic in notebooks, cron scripts, and spreadsheets that nobody can audit. PipelineIQ puts the whole lifecycle in one place:

- **Define** pipelines in YAML. No Python or SQL required for standard steps.
- **Execute** them asynchronously on Celery workers; the engine picks Pandas or DuckDB per step.
- **Watch** progress step by step in the browser over Server-Sent Events.
- **Trace** every output column back to its source file through automatic lineage.
- **Catch drift** when re-uploaded files change schema, and enforce data contracts on outputs.
- **Recover** with AI-proposed YAML patches that must pass a sandbox before they apply.
- **Control access** with per-pipeline roles, column masking/redaction, and an immutable audit log.


## Quick Start

### Full stack with Docker Compose (30 services)

One command on any Ubuntu/Debian host (fresh GCP VM included):

```bash
git clone https://github.com/Siddharthk17/PipelineIQ.git
cd PipelineIQ
make setup && make up
```

Or a single script that does both (clone → running):

```bash
./scripts/bootstrap.sh --up
```

`bootstrap.sh` installs Docker if missing, creates `.env` from `.env.example`, generates secrets (existing values are never overwritten), detects the public host for `PUBLIC_HOST`/`TRUSTED_HOSTS`, sets `vm.overcommit_memory=1`, and installs nightly backup + demo-reseed cron jobs. A demo account is seeded automatically (`demo@pipelineiq.app` / `Demo1234!`).

Deploying to a fresh cloud VM? Read [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) — VM sizing, static IP, firewall, SSH, AI keys, verification, updates, billing, and every failure encountered so far, with fixes.

On a **new cloud VM**, open port 80 first (example for GCP):

```bash
gcloud compute firewall-rules create pipelineiq-http --allow=tcp:80 --source-ranges=0.0.0.0/0 --target-tags=pipelineiq
```

Attach the `pipelineiq` network tag to the VM, or tick "Allow HTTP traffic" at creation. Prefer a static external IP so reboots keep the address.

| Service | URL (host = your machine or VM IP) | Auth |
|---------|------------------------------------|------|
| Application (via Nginx) | http://HOST/ | demo login |
| API docs | http://HOST/docs (also `/redoc`, `/openapi.json`) | none (public by design) |
| Celery Flower | http://HOST/flower/ | `FLOWER_USER` / `FLOWER_PASSWORD` |
| Grafana | http://HOST/grafana/ | `GRAFANA_USER` / `GRAFANA_PASSWORD` |
| Prometheus | http://HOST/prometheus/graph | none (IP-restricted `/metrics` only; UI is public) |
| Jaeger Tracing | http://HOST/jaeger/ | none |
| Yjs collaboration | ws://HOST/yjs/ (WebSocket) | same session as the app |
| MinIO Console | http://localhost:9001 | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` (loopback only) |

Live deployment (captured from a real VM):

| Login | Dashboard | API docs | Jaeger tracing |
|---|---|---|---|
| ![Login](docs/screenshots/01-login.png) | ![Dashboard](docs/screenshots/02-dashboard.png) | ![API docs](docs/screenshots/03-api-docs.png) | ![Jaeger](docs/screenshots/04-jaeger.png) |

### Backend only (development)

You need live PostgreSQL and Redis for anything beyond parser unit tests (auth revocation is fail-closed against Redis; several suites need the stack). The fastest path is the Compose stack above, then:

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="postgresql://pipelineiq:your_password@localhost:5432/pipelineiq"
export REDIS_CACHE_URL="redis://:your_password@localhost:6381/0"
alembic upgrade heads
python -m pytest tests/unit/pipeline/test_parser.py -v
```

Migrations merged to a single head (`a6fc98d90644`), so plain `alembic upgrade head` works. See [Database](#database).

### Frontend only (development)

```bash
npm ci                       # repo root: installs frontend + y-websocket workspaces
cd frontend
npm run dev                  # http://localhost:3000
```

API calls are same-origin (`/api/*`, `/auth/*`, `/health` rewrite to `NEXT_PUBLIC_API_URL`, default `http://localhost:8000` in `next.config.ts`). Auth state lives only in the `pipelineiq_token` HttpOnly cookie; `lib/api.ts` holds no token client-side.


## The Pipeline YAML

Pipelines are YAML documents: a name, an optional description, and an ordered list of steps. Steps reference earlier steps by `name`, forming a DAG. This is `backend/sample_data/simple_pipeline.yaml`, shipped in the repo:

```yaml
pipeline:
  name: simple_filter_pipeline
  description: >
    A simple pipeline that loads sales data, filters to delivered orders,
    and saves the result. Good for quick testing and demos.

  steps:
    - name: load_sales
      type: load
      file_id: "{{SALES_FILE_ID}}"

    - name: filter_delivered
      type: filter
      input: load_sales
      column: status
      operator: equals
      value: delivered

    - name: save_filtered
      type: save
      input: filter_delivered
      filename: filtered_sales.csv
```

`file_id` is the UUID returned by file upload (Files page or `POST /api/v1/files/upload`). Five ready-made templates live in `backend/templates/` and in the app under Templates (`customer_segmentation`, `data_quality_audit`, `multi_source_merge`, `sales_revenue_report`, `weekly_rollup`).

Submit a run:

```bash
curl -s -X POST http://localhost/api/v1/pipelines/run \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"yaml_config\": $(python3 -c "import json; print(json.dumps(open('my-pipeline.yaml').read()))")}"
```

The response is `202 Accepted` with a `run_id`. Progress streams at `GET /api/v1/pipelines/{run_id}/stream` (SSE, served by the dedicated `sse-service` on port 8001).


## Step Types

19 step types (`StepType` in `backend/pipeline/parser.py`). Two are restricted: `transform` parses but has no batch executor, so `PipelineParser.validate()` rejects it with a 400 telling you to use `filter` or `sql` instead; `stream_consume` has no batch executor either, so scheduled pipelines containing it are rejected at creation — run streaming pipelines from the Runs page, which routes them to the streaming daemon.

### Core steps (I/O)

| Step | Description | Key fields |
|------|-------------|------------|
| `load` | Read a CSV, JSON, Parquet, or XLSX file (`ALLOWED_EXTENSIONS`) | `file_id` |
| `save` | Write output to MinIO; returns a 48-hour presigned download URL | `input`, `filename` |

### Transform steps

| Step | Description | Key fields |
|------|-------------|------------|
| `filter` | Keep rows matching a condition (12 operators, below) | `input`, `column`, `operator`, `value` |
| `select` | Project only listed columns | `input`, `columns` |
| `rename` | Rename columns via a mapping | `input`, `mapping` |
| `join` | Merge two datasets (`inner`/`left`/`right`/`outer`) | `left`, `right`, `on`, `how` |
| `aggregate` | Group-by with 10 aggregation functions | `input`, `group_by`, `aggregations` |
| `sort` | Order rows ascending or descending | `input`, `by`, `order` |

### Reshape steps

| Step | Description | Key fields |
|------|-------------|------------|
| `pivot` | Long-to-wide reshape | `input`, `index`, `columns`, `values` |
| `unpivot` | Wide-to-long reshape (melt) | `input`, `id_vars`, `value_vars` |

### Quality steps

| Step | Description | Key fields |
|------|-------------|------------|
| `validate` | Run 12 data-quality checks; `severity: error|warning` per rule | `input`, `rules` |
| `deduplicate` | Drop duplicate rows | `input`, `subset`, `keep` |
| `fill_nulls` | Fill missing values by strategy | `input`, `columns`, `strategy` |
| `sample` | Random row sampling (`n` or `fraction`) | `input`, `n`, `fraction` |

### Advanced steps

| Step | Description | Key fields |
|------|-------------|------------|
| `sql` | Sandboxed DuckDB SQL over `{{input}}`; SELECT/WITH only | `input`, `query` |
| `wasm_compute` | Call a `f64 → f64` function from an uploaded WASM module per row | `input`, `wasm_file_id`, `function`, `input_columns`, `output_column` |

### Streaming steps (Redpanda/Kafka)

| Step | Description | Key fields |
|------|-------------|------------|
| `stream_consume` | Read micro-batches from a topic (streaming daemon only) | `topic`, `consumer_group`, `batch_size`, `batch_timeout_ms`, `deserialize` |
| `stream_publish` | Write pipeline output to a topic | `input`, `topic`, `serialize`, `key_column` |

### Filter operators (12)

`equals` · `not_equals` · `greater_than` · `less_than` · `gte` · `lte` · `contains` · `not_contains` · `starts_with` · `ends_with` · `is_null` · `is_not_null`

### Aggregation functions (10)

`sum` · `mean` · `min` · `max` · `count` · `median` · `std` · `var` · `first` · `last`

### Validation checks (12)

`not_null` · `not_empty` · `greater_than` · `less_than` · `between` · `in_values` · `matches_pattern` · `no_duplicates` · `min_rows` · `max_rows` · `positive` · `date_format`


## Features

### Hybrid execution — SmartExecutor routes Pandas vs DuckDB

Every step passes through `SmartExecutor` (`backend/execution/smart_executor.py`). Steps in `DUCKDB_CAPABLE_STEPS` (`filter`, `select`, `sort`, `aggregate`, `join`, `deduplicate`, `fill_nulls`, `sample`, `pivot`, `unpivot`, `sql`) run on DuckDB once the input reaches `DUCKDB_THRESHOLD = 50,000` rows; `load`, `save`, `validate`, `rename`, and `wasm_compute` always run on Pandas. Joins go to DuckDB when the larger side exceeds the threshold. Each worker process holds one DuckDB connection, initialized once per Celery worker. Every step records an OpenTelemetry span with engine, rows in/out, and duration; results land in `step_results` with `trace_id`/`span_id` for the frontend Gantt chart. Measured timings for the routing decision live in `backend/benchmark/results.json` (`benchmark/duckdb_vs_pandas.py` regenerates them; sizes 100K/1M/5M).

### Tiered Arrow data bus

Step outputs travel as Arrow IPC bytes through three tiers (`backend/execution/arrow_bus.py`):

| Tier | Condition | Storage |
|------|-----------|---------|
| `redis` | payload ≤ 10 MB | Redis cache, `SETEX` with TTL |
| `shm` | ≤ 500 MB and `/dev/shm` available | Arrow IPC file in shared memory |
| `spill` | anything larger | Parquet + zstd object in the `pipelineiq-spills` MinIO bucket (2-day lifecycle) |

Cleanup is idempotent and runs in the task `finally` block. Tier stats are served at `GET /api/v1/storage/stats` and `/tier-health`.

### Autonomous healing

When a step raises an error classified as healable (`ColumnNotFoundError`, `JoinKeyMissingError`, `KeyError`, `MergeError`, `ValueError`, `TypeError`, `AggregationError`, plus message patterns — `backend/execution/healing_classifier.py`), the worker computes a schema diff (rename candidates at Jaro-Winkler ≥ 0.85), asks Gemini for a JSON patch to the YAML (temperature 0.0 in `backend/ai/generation.py`), and tests the patched pipeline in a sandbox: fresh DuckDB `:memory:`, 100 sample rows (`SANDBOX_SAMPLE_ROWS`), worker connection untouched. A passing patch is applied and the run resumes as `HEALED`; after `AUTONOMOUS_HEALING_MAX_ATTEMPTS = 3` failures the run is `FAILED`. Every attempt is written to `healing_attempts`, which PostgreSQL RULEs make append-only. Progress events (`healing_started`, `healing_attempt_started`, `healing_attempt_applied`, `healing_complete`, `healing_failed`) stream over the same SSE channel as the run.

### AI pipeline generation and repair

`POST /api/ai/generate` takes a natural-language `description` (10–1000 chars) plus `file_ids`; the backend injects real column names from stored file profiles into the prompt and calls Gemini at temperature 0.1, with a deterministic self-fix retry (temperature 0.0) when the first YAML fails validation. `POST /api/ai/runs/{run_id}/repair` proposes a minimal YAML correction for a failed run and returns a diff for review. `POST /api/ai/autocomplete/column(s)` suggests column names with Jaro-Winkler similarity (pure Python, no model call). All model calls go through one Celery task on the `gemini` queue (concurrency 1, 50 calls/minute) with a 900,000-token-per-minute rolling budget in Redis and SHA256 prompt-response caching.

### Column-level lineage

Each run builds a `networkx.DiGraph` (`LineageRecorder`, `backend/pipeline/lineage.py`) with node IDs `file::{file_id}`, `col::{step}::{column}`, `step::{step}`, `output::{step}::{filename}`. The API returns the graph plus a pre-computed React Flow layout (Sugiyama-inspired layered layout); the frontend renders it without layout work. Ancestry (`.../column`) and impact (`.../impact`) queries trace single columns both directions.

### Data catalog and blast radius

`register_run_assets()` upserts every run's files, columns, pipelines, and topics into `data_assets` with edges in `asset_relationships` (`reads_from`, `writes_to`, `transforms`, `joins`), inside a `try/except` that never fails the run. Blast radius and lineage queries run as PostgreSQL recursive CTEs capped at `MAX_CTE_DEPTH = 10`, with trigram similarity search (`pg_trgm`, GIN index). Single-run OpenLineage JSON at `GET /api/runs/{run_id}/openlineage`; bulk NDJSON export at `GET /api/lineage/export`.

### Schema drift and data contracts

Re-uploaded files are diffed against the previous `schema_snapshots` row: removed columns are `breaking`, type changes are `warning`, added columns are `info`. After each run, the output table is checked against the pipeline's active contract (`backend/contracts/validator.py`). 13 rule strings exist; `null_threshold_exceeded` and `unexpected_column` record as warnings, the other 11 record as errors. With `severity: block`, error violations flip the run to `CONTRACT_VIOLATION` and push downstream schedules' `next_run_at` forward one hour. Breach rows go to `contract_violations`, append-only by RULE.

### Streaming pipelines

`tasks.run_streaming_pipeline` (streaming queue, no time limit) loops `consumer.consume()` until the run leaves `STREAMING_ACTIVE`/`STREAMING_PAUSED`. Topics default to 8 partitions with a 1-partition DLQ (`{topic}.dlq`, 7-day retention). Backpressure pauses consumption past 50,000 messages of lag; 50 consecutive batch errors stop the daemon. Control endpoints: `POST /api/streaming/runs/{id}/pause|resume|stop|restart`, stats at `GET .../stats`, topic admin and DLQ inspect/replay under `/api/streaming/topics`.

### WebAssembly UDFs

Upload a module (`POST /api/v1/wasm/upload`); validation instantiates it in Wasmtime with an empty linker (no WASI: no filesystem, network, or env) and records exports. Execution reuses one engine per worker, caches up to `MAX_CACHED_MODULES = 100` compiled modules by SHA256, and budgets `FUEL_PER_STEP = 10,000,000` plus `FUEL_PER_ROW = 1,000` per call. Functions take `f64` columns and return `f64`; a trap (fuel, divide-by-zero) yields `NULL` for that row, not a crash.

### Scheduling

Beat runs `schedules.check` every 60 seconds (Redis lock, 55 s TTL, 100 schedules per tick). Due schedules get a `schedule_runs` row and a `PipelineRun(trigger='scheduled')` dispatched to `execute_pipeline_task` on the `bulk` queue — the same task as manual runs. The task re-checks `is_active` at fire time, updates per-schedule stats (`total_runs`, `successful_runs`, `failed_runs`, `healed_runs`, `last_run_at`), and `croniter` computes the next fire. The API offers create/list/get/pause/resume/delete, run history, next-fire preview, and a legacy toggle.

### Auth, RBAC, audit

JWT (HS256, 24-hour expiry, `jti` per token) travels as the `pipelineiq_token` HttpOnly cookie (`Secure` in production, `SameSite=strict`) or `Authorization: Bearer`. Revocation is a Redis `SETEX` checked on every request — **fail-closed**: if the cache Redis is unreachable, tokens are denied and the API returns 503. Global role is `admin` (first registrant) or `viewer`; per-pipeline grants are `owner`/`runner`/`viewer` with admins overriding. Passwords are bcrypt round 12 hashed in a process pool so the event loop never blocks. Rate limits: login/register 5/min, pipeline runs 10/min, uploads 30/min, validation 60/min, reads 120/min. Every mutating request writes an `AuditLog` row on its own session (survives request rollback); `audit_logs` has a trigger blocking UPDATE/DELETE, and `webhook_deliveries`, `healing_attempts`, `contract_violations` have NO-DELETE/NO-UPDATE RULEs.

### Webhooks and notifications

Webhooks fire `pipeline_completed` / `pipeline_failed` (subscribed per webhook, default both) via `tasks.deliver_webhook` on the `critical` queue: HMAC-SHA256 body signature (`X-PipelineIQ-Signature`), JSON payload, retries with backoff, every attempt logged to `webhook_deliveries`. Notifications (`NotificationConfig`, `slack`/`email` channels with per-type config and event subscriptions) deliver through `tasks.deliver_notifications`, also `critical`.

### Versioning, cost estimate, storage admin

Each run saves its YAML as a `PipelineVersion`; list/get/diff/restore at `/api/v1/versions/{name}...`. `POST /api/runs/estimate` returns a cost estimate from historical data. `GET /api/v1/storage/stats|tier-health`, `POST .../evict`, and `POST .../cleanup-stale-shm` expose Arrow-bus and bucket state.

### Collaboration

The `y-websocket` Node.js relay (`ws` + `y-redis`) syncs Yjs documents between browsers: one document per pipeline room, JWT required on every connection (`?token=` query or the login cookie, ≥ 32-char `JWT_SECRET`, close code 4001 on failure), documents persisted to `redis-yjs` with in-memory fallback. The frontend (`yjs`, `y-websocket`, `y-codemirror.next`) shares the pipeline YAML text and React Flow nodes/edges as Yjs types with a 300 ms sync debounce and feedback guards. Presence (cursors, selection, user colors) rides the ephemeral awareness protocol.


## Architecture

```mermaid
flowchart TB
    Browser["Browser<br/>Next.js 15 · React 19<br/>Zustand · ReactFlow · CodeMirror<br/>Yjs client · SSE consumer"] --> NginxProxy
    subgraph Nginx["Nginx :80"]
        NginxProxy["Reverse proxy<br/>gzip · rate limits<br/>SSE/WebSocket passthrough"]
    end
    NginxProxy --> API["FastAPI :8000<br/>Gunicorn × 4 workers"]
    NginxProxy --> SSE["sse-service :8001<br/>dedicated SSE app"]
    NginxProxy --> FE["Frontend :3000<br/>Next.js standalone"]
    NginxProxy --> OBS["Flower · Grafana<br/>Prometheus · Jaeger"]
    NginxProxy --> YWS["y-websocket :1234"]
    API --> Workers["Celery workers<br/>critical · default · bulk<br/>gemini · streaming + beat"]
    Workers --> PG[("PostgreSQL 15<br/>primary + replica<br/>via PgBouncer")]
    Workers --> Redis[("Redis × 4<br/>broker · pubsub<br/>cache · yjs")]
    Workers --> MinIO[("MinIO S3<br/>uploads · outputs<br/>spills · wasm")]
    Workers --> RP[("Redpanda<br/>Kafka-compatible")]
    SSE --> Redis
    YWS --> Redis
    API --> OTEL["Jaeger :4317 OTLP<br/>Prometheus :9090 scrape"]
```

Request path: browser → Nginx → API (port 8000) for REST; step timers and run progress flow worker → `redis-pubsub` → `sse-service` (port 8001) → browser `EventSource`, with HMAC-signed payloads and a cached last event for reconnects. Collaboration traffic goes browser → `/yjs/` → `y-websocket` (port 1234), persisted in `redis-yjs`. Docs, Flower, Grafana, Prometheus, and Jaeger are all served through Nginx on port 80.

### Celery queues

| Queue | Concurrency (Compose and k8s) | k8s replicas | What runs here |
|-------|--------------------|--------------|----------------|
| `critical` | 2 | 1 | `tasks.deliver_webhook`, `tasks.deliver_notifications` |
| `default` | 3 | 2 | `pipeline.execute` (manual runs) |
| `bulk` | 2 | 2 | `schedules.check`, `tasks.profile_file`, `tasks.maintain_storage`, `tasks.reconcile_stuck_runs` |
| `gemini` | 1 | 1 | `tasks.call_gemini`, pipeline descriptions (50 calls/min, 900K tokens/min) |
| `streaming` | 8 | 1 | `tasks.run_streaming_pipeline` (no time limit) |

Beat is a singleton (`worker-beat`, one replica): two Beat instances would fire every schedule twice. `worker-gemini` stays at one replica for the same reason — the rate limit is enforced by having a single consumer. All workers use `acks_late` with `prefetch_multiplier = 1`, soft limit 300 s / hard limit 360 s, and results expire after one hour.

### Redis instances

| Instance | Port | Max memory | Purpose |
|----------|------|-----------|---------|
| `redis-broker` | 6379 | 512 MB, allkeys-lru, no persistence | Celery broker + result backend (db 0/1) |
| `redis-pubsub` | 6380 | 512 MB, allkeys-lru | SSE channels `pipeline_progress:{run_id}`, terminal payload cache |
| `redis-cache` | 6381 | 1 GB, allkeys-lru, persisted | JWT revocation, parsed-YAML cache (TTL 3600), column policies (TTL 60), rate limits |
| `redis-yjs` | 6382 | 128 MB | Yjs document persistence |

### MinIO buckets

| Bucket | Lifecycle |
|--------|-----------|
| `pipelineiq-uploads` | none (user source data) |
| `pipelineiq-outputs` | objects older than 7 days deleted |
| `pipelineiq-spills` | Arrow spill files older than 2 days deleted |
| `pipelineiq-wasm` | none (long-lived modules) |


## Tech Stack

### Backend

| Category | Technology | Version |
|----------|-----------|---------|
| API framework | FastAPI + Uvicorn | 0.109.1 / 0.27.0 |
| ASGI server | Gunicorn + Uvicorn workers (4 workers, preload) | 22.0.0 |
| ORM / migrations | SQLAlchemy + Alembic | 2.0.25 / 1.13.1 |
| Database | PostgreSQL 15 (primary + streaming replica; `max_connections=200`, `statement_timeout=30000`) | 15.12 |
| Pooling | PgBouncer, transaction mode, separate write/read pools | — |
| Data processing | Pandas + NumPy + DuckDB | 2.1.4 / 1.26.3 / 1.5.2 |
| Columnar format | Apache Arrow (PyArrow) | 24.0.0 |
| Lineage graphs | NetworkX | 3.2.1 |
| Task queue | Celery + Redis | 5.6.3 |
| Auth | PyJWT (HS256) + bcrypt (rounds 12, process pool) | 2.15.1 / 4.0.1 |
| Rate limiting | SlowAPI (per-endpoint tiers) | 0.1.9 |
| Serialization | Orjson | 3.11.6 |
| Object storage | MinIO SDK + boto3 | 7.2.20 / 1.34.162 |
| Streaming | Redpanda (Kafka-compatible) + confluent-kafka | v24.2.1 / 2.16.1 |
| WASM runtime | Wasmtime (sandboxed, fuel-bounded) | ≥ 18.0.0 |
| AI | Google Gemini (`gemini-2.5-flash`), `google-genai` SDK | 1.2.0 |
| Tracing | OpenTelemetry SDK + auto-instrumentation | 1.45.1 |
| Error tracking | Sentry SDK (FastAPI + Celery + SQLAlchemy, 0.1 sample rates) | 1.45.1 |
| Metrics | prometheus-client + prometheus-fastapi-instrumentator | 6.1.0 |
| Fuzzy matching | Jellyfish (Jaro-Winkler) | 1.2.1 |
| Scheduling | croniter + Celery Beat | 2.0.1 |
| Logging | Structlog (JSON, request IDs) | 24.1.0 |
| Validation | Pydantic v2 + Pydantic-Settings | 2.5.3 / 2.1.0 |

### Frontend

| Category | Technology | Version |
|----------|-----------|---------|
| Framework | Next.js (standalone output) | 15.4.9 |
| UI library | React | 19.2.1 |
| Language | TypeScript (`strict`) | 5.9.3 |
| State | Zustand (persisted to localStorage) | 5.0.11 |
| Server state | TanStack React Query (no refetch on focus, no retry on 4xx) | 5.100.14 |
| Graph canvas | ReactFlow (`@xyflow/react`) | 12.10.1 |
| Editor | CodeMirror 6 (`@codemirror/*`) | 6.x |
| Animation | Motion | 12.23.24 |
| Styling | Tailwind CSS | 4.1.11 |
| Drag and drop | dnd-kit (core / sortable / utilities) | 6.3.1 / 10.0.0 |
| Collaboration | Yjs + y-websocket + y-codemirror.next | 13.6.30 |
| Icons | Lucide React | 0.553.0 |
| Motion | `motion` package (see above) | 12.23.24 |
| Dates / utils | date-fns, clsx, tailwind-merge, use-debounce | 4.1.0 |
| Unit tests | Vitest + Testing Library + jsdom | 4.0.18 |
| E2E tests | Playwright (Chromium in CI) | 1.56.1 |

### Infrastructure

| Category | Technology |
|----------|-----------|
| Compose | Docker Compose, 30 services (primary deployment target) |
| Proxy | Nginx 1.25.5 (gzip, rate-limit zones, IP-restricted `/metrics`) |
| Metrics / dashboards | Prometheus v2.48.0 (15-day retention) + Grafana 10.2.0 (one provisioned dashboard) |
| Log shipping | Promtail 2.9.8 → Loki 2.9.8 |
| Tracing | Jaeger all-in-one 1.57 (OTLP :4317, UI `/jaeger/`, in-memory spans) |
| Streaming | Redpanda v24.2.1 + console v2.8.10 |
| Storage | MinIO (`pgsty/minio`) + Postgres 15.12 + Redis 7.4.2 |
| Alt orchestration | Kubernetes manifests in `k8s/` (Civo k3s) |
| Alt API hosting | Render.com blueprint (`render.yaml`) |
| CI/CD | GitHub Actions (7 workflows) |
| Load tests | k6 (3 scripts) + `chaos/chaos-scenarios.sh` fault injection |


## API Reference

Base path through Nginx: `/api/v1` (plus legacy `/api` aliases). Every response carries `X-Request-ID`, `X-Process-Time`, `X-API-Version`, and `X-App-Version`.

### Auth — `/auth`

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/auth/register` | Create account (first user becomes admin) | No (5/min) |
| POST | `/auth/login` | Returns JWT, sets `pipelineiq_token` cookie (24 h) | No (5/min) |
| GET | `/auth/me` | Current user profile | Yes |
| POST | `/auth/logout` | Revoke token, clear cookie | Yes |
| GET | `/auth/users` | List all users | Admin |
| PATCH | `/auth/users/{id}/role` | Set `admin` or `viewer` | Admin |

### Files — `/api/v1/files`

| Method | Path | Description | Limit |
|--------|------|-------------|-------|
| POST | `/files/request-upload-url` | Negotiate direct vs multipart upload | 30/min |
| PUT | `/files/direct-upload/{file_id}` | Large-file direct stream target | 30/min |
| POST | `/files/{file_id}/confirm` | Confirm direct upload, extract metadata | 30/min |
| POST | `/files/upload` | Small multipart upload (500 MB max) | 30/min |
| GET | `/files/` | List own files (admin: all) | 120/min |
| GET | `/files/{id}` | File metadata | 120/min |
| DELETE | `/files/{id}` | Delete file + record | Yes |
| GET | `/files/{id}/preview` | First rows as JSON (column policies applied) | 120/min |
| GET | `/files/{id}/schema/history` | Schema snapshots, newest first | 120/min |
| GET | `/files/{id}/schema/diff` | Drift between last two snapshots | 120/min |
| GET | `/files/{id}/profile` | Cached data profile | 120/min |
| POST | `/files/{id}/profile/refresh` | Requeue profiling on `bulk` | Yes |

### Pipelines — `/api/v1/pipelines`

| Method | Path | Description | Limit |
|--------|------|-------------|-------|
| POST | `/pipelines/validate` | Parse + validate YAML vs registered files, no run | 60/min |
| POST | `/pipelines/plan` | Dry-run plan with per-step row/duration estimates | 60/min |
| POST | `/pipelines/preview` | Parse and dry-run to a step, first 5 rows | 60/min |
| POST | `/pipelines/run` | Create run, queue Celery task (`202 Accepted`) | 10/min |
| GET | `/pipelines/` | List runs, paginated (page/limit, max 100) | 120/min |
| GET | `/pipelines/stats` | Counts by status, rows, durations | 120/min |
| GET | `/pipelines/{run_id}` | Run detail + step results | 120/min |
| GET | `/pipelines/{run_id}/healing-attempts` | Healing attempt list / single attempt | 120/min |
| GET | `/pipelines/{run_id}/healing-history` | Healing timeline | 120/min |
| POST | `/pipelines/{run_id}/cancel` | Cancel PENDING/RUNNING, revoke Celery task | Yes |
| GET | `/pipelines/{run_id}/download` | Presigned output URL (48 h) | Yes |
| GET | `/pipelines/{run_id}/export` | Download output file (S3 or local) | Yes |
| GET | `/pipelines/{run_id}/timing` | Per-step timing + trace IDs for the Gantt chart | 120/min |
| POST | `/api/v1/pipelines/{run_id}/stream` | SSE progress stream (via `sse-service`) | Yes |
| POST | `/api/runs/estimate` | Cost estimate from historical data | 60/min |

Legacy aliases under `/api/runs` and `/api/pipelines` behave the same; `POST /api/runs` also accepts a bare `yaml` field.

### Lineage and versions

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/lineage/{run_id}` | Full lineage graph + pre-computed React Flow data |
| GET | `/api/v1/lineage/{run_id}/column` | Column ancestry trace (`step`, `column` params) |
| GET | `/api/v1/lineage/{run_id}/impact` | Forward impact analysis |
| GET | `/api/runs/{run_id}/openlineage` | OpenLineage run event JSON |
| GET | `/api/lineage/export` | Bulk NDJSON export (DataHub/Marquez/Atlas-compatible) |
| GET | `/api/v1/versions/{name}` | All versions of a pipeline |
| GET | `/api/v1/versions/{name}/{v}` | One version's YAML |
| GET | `/api/v1/versions/{name}/diff/{a}/{b}` | Unified diff between versions |
| POST | `/api/v1/versions/{name}/restore/{v}` | Restore a version as new HEAD |

### Schedules — `/api/v1/schedules`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/schedules/` | Create cron schedule (rejects `stream_consume` with 400) |
| GET | `/schedules/` | List own schedules |
| GET | `/schedules/{id}` | Schedule detail |
| POST | `/schedules/{id}/pause` · `/{id}/resume` | Toggle `is_active` |
| DELETE | `/schedules/{id}` | Delete (cascades schedule runs) |
| GET | `/schedules/{id}/runs` | Firing history with linked runs |
| GET | `/schedules/{id}/preview` | Next 5 fire times |
| PATCH | `/schedules/{id}/toggle` | Legacy enable/disable |

### Contracts — `/api/v1/contracts`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/contracts/pipelines/{name}` | List contracts for a pipeline |
| POST | `/contracts/pipelines/{name}` | Create contract |
| GET | `/contracts/pipelines/{name}/{id}` | Contract detail |
| PUT | `/contracts/pipelines/{name}/{id}` | Update (new version) |
| DELETE | `/contracts/pipelines/{name}/{id}` | Soft-delete / deactivate |
| GET | `/contracts/pipelines/{name}/status` | Active contract status |
| GET | `/contracts/pipelines/{name}/breaches` | Breach history |
| GET | `/contracts/runs/{run_id}` | Run violations |
| GET | `/contracts/runs/{run_id}/steps/{step}` | Step violations |

### Webhooks, audit, AI, WASM, streaming, catalog, storage

| Method | Path | Description |
|--------|------|-------------|
| POST / GET | `/webhooks/` | Register / list webhooks (`events` default `["pipeline_completed", "pipeline_failed"]`) |
| DELETE | `/webhooks/{id}` | Delete (204) |
| GET / POST | `/webhooks/{id}/deliveries`, `/webhooks/{id}/test` | Delivery log / test ping |
| GET | `/audit/logs`, `/audit/logs/mine` | All logs (admin) / own logs |
| POST | `/api/ai/generate` | Natural language → YAML (`description`, `file_ids`) |
| POST | `/api/ai/runs/{run_id}/repair` | Minimal YAML fix for a failed run + diff |
| POST | `/api/ai/autocomplete/column(s)` | Jaro-Winkler column suggestions, no model call |
| POST | `/api/ai/validate-yaml` | Validate-only endpoint (used by k6 load profile) |
| POST | `/api/v1/wasm/validate`, `/upload` | Check exports/fuel/no-WASI; store to `pipelineiq-wasm` |
| GET / DELETE | `/api/v1/wasm/`, `/api/v1/wasm/{id}` | List / get / deactivate modules |
| POST | `/api/streaming/runs/{id}/pause\|resume\|stop\|restart` | Daemon control |
| GET | `/api/streaming/runs/{id}/stats` | `StreamingStats` row |
| GET / POST / DELETE | `/api/streaming/topics...` | Topic admin, DLQ inspect (`/{t}/dlq`), replay (`/dlq/replay`) |
| GET | `/api/catalog/search`, `/assets/{n}/impact`, `/assets/{n}/lineage`, `/orphans`, `/stats` | Catalog + blast radius |
| GET | `/api/catalog/pipelines`, `/{name}/description` | Pipeline catalog (Gemini descriptions, cached) |
| POST / GET / DELETE | `/api/column-policies/` (+ `GET /files/{fid}`, `DELETE /{pid}`) | Redact/mask policies |
| GET | `/api/v1/storage/stats`, `/tier-health` | Buckets + Arrow-bus tiers |
| POST | `/api/v1/storage/evict`, `/cleanup-stale-shm` | Evict tier data / clean shm (admin) |
| GET | `/api/v1/templates/`, `/{id}` | List / get template (public); `POST /{id}/fork` (auth) |
| POST / GET / DELETE | `/api/v1/notifications/` (+ `/{id}/test`) | Slack/email configs |
| GET | `/api/v1/dashboard/stats` | Personal run/file stats |
| POST / GET / DELETE | `/api/v1/pipelines/{name}/permissions...` | Grant owner/runner/viewer, list, revoke |
| GET | `/api/runs/{run_id}/timing` | Same timing payload under the runs prefix |
| GET | `/api/runs/{run_id}/openlineage` | Single-run OpenLineage event |

Health and metrics: `GET /livez` and `GET /healthz` (no dependencies), `GET /readyz` and `GET /health` (Postgres + Redis + storage checks), `GET /metrics` (Prometheus; Nginx restricts it to private ranges).

Health and metrics: `GET /livez` and `GET /healthz` (no dependencies), `GET /readyz` and `GET /health` (Postgres + Redis + storage checks), `GET /metrics` (Prometheus; Nginx restricts it to private ranges).


## Frontend

Next.js 15 App Router + TypeScript `strict`. Pages (`frontend/app`): `/` (dashboard, also aliased at `/dashboard`), `/login`, `/register`, `/files`, `/files/[fileId]`, `/pipelines/new` (visual builder + `?pipeline=` rooms + `?template=` fork deep-link), `/runs`, `/runs/[runId]` (Gantt chart, Jaeger deep-links), `/catalog`, `/catalog/pipelines`, `/templates`, `/schedules`, `/storage`, `/wasm-modules`. Route protection lives in `middleware.ts`: the `pipelineiq_token` cookie is verified as HS256 JWT, everything except `/login` and `/register` redirects when it is missing or invalid.

State: four Zustand stores persisted to localStorage (`pipeline`, `widget` with 13 widgets in a binary-split layout tree, `theme` with 7 built-in themes plus custom builder, `keybindings` — every shortcut rebindable through the Keybindings modal, `Alt+K`), React Query for server state (no refetch on window focus, no retries on 4xx), and Yjs documents for the shared pipeline YAML and canvas graph. The dashboard layout is a recursive split tree; widgets are drag-and-drop.

`frontend/lib/api.ts` is the only backend client: same-origin `fetch` with `credentials: "include"`, no token storage, request dedup for in-flight runs/me calls, and a single `ApiError(status, message, detail)` shape. On 401 it redirects non-public pages to `/login`. Environment inputs are `NEXT_PUBLIC_API_URL` (rewrite target, build time), `NEXT_PUBLIC_APP_VERSION` (displayed on login/register), `NEXT_PUBLIC_YJS_URL`, and `NEXT_PUBLIC_JAEGER_UI_URL` — each with a same-origin or localhost fallback in `lib/publicUrls.ts`.

Keyboard shortcuts (all rebindable): `Ctrl+K` command palette, `Alt+Enter` terminal launcher, `Alt+Q` close widget, `Alt+T` theme selector/builder, `Alt+K` keybindings editor, `Alt+U` upload file, `Alt+Shift+R` reset layout, `Ctrl+Enter` run pipeline, `Alt+1–5` switch workspace.


## Observability

### Prometheus metrics (`backend/metrics.py`)

| Metric | Type | Meaning |
|--------|------|---------|
| `pipelineiq_pipeline_runs_total` | Counter | Runs by status |
| `pipelineiq_pipeline_duration_seconds` | Histogram | Run durations |
| `pipelineiq_files_uploaded_total` | Counter | Uploads |
| `pipelineiq_active_users` | Gauge | Active users |
| `pipelineiq_celery_queue_depth` | Gauge | Queue depth |

Plus per-endpoint request rate, latency, and error rate from `prometheus-fastapi-instrumentator`. Scrape target `prometheus:9090`, 15-day retention, served at `/prometheus/` with the route prefix preserved.

### Grafana

One provisioned dashboard (`grafana/provisioning/dashboards/pipelineiq.json`) at `/grafana/`, covering run rate by status, API latency, request rate, success rate, execution duration, uploads, queue depth, connections, error rate, and user counts. `GF_SERVER_ROOT_URL` derives from `PUBLIC_HOST`, so the same dashboard works on localhost and on a public VM.

### Tracing

OpenTelemetry auto-instruments FastAPI, SQLAlchemy, Redis, Celery, and HTTPX, and exports OTLP/gRPC to Jaeger at `OTEL_EXPORTER_OTLP_ENDPOINT` (default `http://jaeger:4317`). `SmartExecutor` adds one span per step (`pipelineiq.step.name/type/engine`, `pipelineiq.rows.in/out`), sampled at `OTEL_SAMPLE_RATE = 0.1` by default. Jaeger UI at `/jaeger/`; the run Gantt chart links each step bar to its trace.

### Logs and errors

Structlog emits JSON logs with `request_id` (from `X-Request-ID`, generated when absent) and per-request timing (`X-Process-Time`); health, metrics, and stream polling are excluded from access logs. Promtail ships container logs to Loki (`:3100`). Sentry (FastAPI + Celery + SQLAlchemy, `traces_sample_rate = profiles_sample_rate = 0.1`) activates only when `SENTRY_DSN` is set.


## Testing

### Backend — 1,251 tests collected

```bash
cd backend
python -m pytest tests/ -v --tb=short
```

| Area | Files | What is covered |
|------|-------|-----------------|
| `unit/api/` | 10 | Auth flows, RBAC, run ownership, rate limits, public endpoints, upload encodings, plan checks |
| `unit/pipeline/` | 12 | Parser, all 19 step types, validators, versioning, planner, cache/diff |
| `unit/security/` | 14 | Auth bypass, isolation, trusted hosts, token revocation, column policies, permissions |
| `unit/ai/` | 11 | Generation, repair prompts, Gemini queue/routing/retry, rate limits, YAML cache/diff |
| `unit/catalog/` | 8 | Asset registration, isolation, lineage cache, blast-radius CTE, OpenLineage, descriptions |
| `unit/infrastructure/` | 7 | Celery queue/task registry, worker topology vs Compose, Redis wiring, SSE lifecycle, caching |
| `unit/healing/` | 8 | Classifier, prompts, patch application, sandbox, schema diff |
| `unit/contracts/` | 4 | Contract validation, enforcement, severity mapping, type categories |
| `unit/execution/` | 4 | SmartExecutor routing, DuckDB/Pandas parity, save step, error types |
| `unit/scheduling/` | 4 | Cron utils, schedule API, stream_consume rejection |
| `unit/save_step/` | 4 | Output persistence, presigned URLs, formats |
| `unit/storage/` | 3 | Arrow bus tiers, spills, eviction, S3 operations |
| `unit/webhooks/` | 2 | Delivery, HMAC signing, retry logic |
| `unit/templates/` | 2 | Template listing, fork validation |
| `unit/streaming/` | 2 | Daemon config, SSE streaming |
| `unit/wasm/` | 2 | Module validation, sandbox isolation |
| `unit/profiling/` | 2 | Data profiling, profiler task |
| `unit/notifications/` | 1 | Notification dispatch |
| `unit/telemetry/` | 1 | Tracing setup, span attributes |
| `unit/week11/` | 1 | Regression coverage |
| `unit/cost/` | 1 | Cost estimation |

`tests/integration/` (8 files: AI, stress, extreme stress, performance, profiling, healing E2E, infrastructure, week8/9/10) runs behind `RUN_INTEGRATION_TESTS=1` against the live Compose stack; `tests/e2e/` holds server-side week-3/8/9/10 suites with their own gates. SQLite in-memory is the default in `conftest.py`; never weaken a fail-closed test (auth revocation, permission checks) to make it pass locally — run those against real Redis/Postgres or in CI.

### Frontend — 241 tests, 23 files

```bash
cd frontend
npm test            # vitest run: stores, hooks, pages, widgets, builder, collaboration
npx tsc --noEmit    # blocking type gate (also runs in CI)
```

### E2E — Playwright, 25 spec files

```bash
npx playwright install --with-deps chromium || npx playwright install chromium
npx playwright test e2e/01-auth.spec.ts --reporter=list
```

Chromium is the only browser in CI (`ubuntu-24.04` runner; Firefox is appended locally only). `e2e/global-setup.ts` logs in once as the demo user into `e2e/.auth/demo.json`; feature specs reuse that storage state. Suites cover auth, upload, builder, run lifecycle, AI generation (×3 incl. standalone), schedules (×2), contracts, WASM, catalog, templates, healing, accessibility (axe), trace validation, multiplayer (isolated `?pipeline=` rooms), streaming, and seven `journeys/` end-to-end funnels. `e2e/builder/visual-builder.spec.ts` is an empty stub. Per-suite runners exist in the root `package.json` (`test:e2e:auth`, `test:e2e:healing`, …).

### Load and chaos

```bash
k6 run k6/load-auth.js            # 20→100 VUs; p95 < 500 ms, login success > 99%
k6 run k6/load-pipeline-api.js    # 200 rps constant arrival; p99 < 100 ms on validate-yaml
k6 run k6/load-file-upload.js     # 10→50 VUs multipart; p95 < 2 s, success > 95%
bash chaos/chaos-scenarios.sh     # fault injection against a live stack
bash scripts/run-load-tests.sh    # runs all three k6 profiles, defaults to the Render API URL
```


## Database

PostgreSQL 15. Migrations live in `backend/alembic/versions/` (38 files, single head `a6fc98d90644` — a no-op merge of the former `0013` and `k1a2b3c4d5e0` branches, which touch disjoint objects):

```bash
alembic upgrade head                   # run pending migrations (single head)
alembic history                       # show the branches
alembic downgrade -1                  # roll back one step
```

(The durable fix is a merge migration joining the four heads; until one lands, `heads` is the correct target in CI and locally.)

### Tables (23)

| Model | Table | Purpose |
|-------|-------|---------|
| `User` | `users` | Accounts; `role` is `admin` or `viewer` |
| `UploadedFile` | `uploaded_files` | File metadata + version chain |
| `FileProfile` | `file_profiles` | Async-computed profiles (profiler task, `bulk` queue) |
| `SchemaSnapshot` | `schema_snapshots` | Per-file, per-run schema for drift diffs |
| `PipelineRun` | `pipeline_runs` | 12 statuses: PENDING, RUNNING, HEALING, HEALED, COMPLETED, CONTRACT_VIOLATION, FAILED, CANCELLED, TIMEOUT, STREAMING_ACTIVE/PAUSED/STOPPED |
| `StepResult` | `step_results` | Per-step rows in/out, engine, timing, `trace_id`/`span_id` |
| `LineageGraph` | `lineage_graphs` | Serialized NetworkX graph + React Flow layout |
| `HealingAttempt` | `healing_attempts` | Attempts, diffs, patches, sandbox results (append-only RULEs) |
| `PipelineVersion` | `pipeline_versions` | Every run's YAML, diffable and restorable |
| `PipelineSchedule` | `pipeline_schedules` | `pipeline_name`, `yaml_config`, cron expression + human text, stats incl. `healed_runs` |
| `ScheduleRun` | `schedule_runs` | Each firing: schedule FK, run FK, status, error |
| `PipelineContract` | `pipeline_contracts` | Contract definitions (`warn`/`block`) |
| `ContractViolationRecord` | `contract_violations` | Breach rows (append-only RULEs) |
| `ColumnPolicy` | `column_policies` | Redact/mask rules per file+column (`allowed_roles` bypass) |
| `NotificationConfig` | `notification_configs` | Slack/email configs with event subscriptions |
| `Webhook` / `WebhookDelivery` | `webhooks` / `webhook_deliveries` | Registrations / attempts (append-only RULEs) |
| `AuditLog` | `audit_logs` | Immutable trigger blocks UPDATE/DELETE |
| `WasmModule` | `wasm_modules` | Module bytes ref, sha256, exports, fuel budget |
| `PipelinePermission` | `pipeline_permissions` | Per-pipeline owner/runner/viewer grants |
| `StreamingStats` | `streaming_stats` | Live streaming counters, PK is `run_id` |
| `DataAsset` | `data_assets` | Catalog assets (`file`/`column`/`pipeline`/`topic`, unique type+namespace+name, trigram index) |
| `AssetRelationship` | `asset_relationships` | `reads_from`/`writes_to`/`transforms`/`joins` edges |


## Deployment

### Production (current)

| Layer | Platform | Notes |
|-------|----------|-------|
| Backend API | Render.com | `render.yaml` blueprint: `./backend/Dockerfile`, health check `/health`, `ENVIRONMENT=production` |
| Frontend | Vercel | Live at the URL configured in `TRUSTED_HOSTS`/`CORS_ORIGINS` |
| Orchestrated alt | Civo k3s | 17 manifests in `k8s/` (see `docs/kubernetes-deployment.md`) |
| Local / VM | Docker Compose | 30 services, the primary deployment target |

Render and the VM both deploy from `main`. No demo credentials are baked into images; the demo user comes from the seed scripts.

### Self-hosted (Docker Compose, 30 services)

```bash
cp .env.example .env        # then fill in SECRET_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD, ...
docker compose up --build -d
```

| Service | Notes |
|---------|-------|
| `nginx` | :80 public; gzip, rate-limit zones, `/metrics` restricted to private ranges |
| `api` | Gunicorn + 4 Uvicorn workers, loopback :8000, 2 GB shm |
| `sse-service` | 2 workers, 600 s timeout, loopback :8001 |
| `frontend` | Next.js standalone, internal :3000 |
| `postgres` / `postgres-replica` | 15.12, WAL replica, 200 max connections |
| `pgbouncer` / `pgbouncer-replica` | Transaction mode, loopback :5432/:5433 |
| `redis-broker` / `redis-pubsub` / `redis-cache` / `redis-yjs` | 7.4.2, loopback :6379–6382 |
| `worker-critical` / `-default` / `-bulk` / `-gemini` / `-streaming` | Concurrency 2/3/2/1/8; gemini pinned to 1 replica |
| `celery-beat` | Singleton scheduler |
| `y-websocket` | Node 20, loopback :1234, 80 MB limit |
| `minio` | `pgsty/minio`, loopback :9000/:9001 |
| `flower` | Basic auth, `--url_prefix=/flower`, loopback :5555 |
| `redpanda` / `redpanda-console` | v24.2.1, 3 GB limit, loopback :9092/:8082/:8081; console :8090 |
| `prometheus` / `jaeger` / `loki` / `promtail` / `grafana` | Observability chain, loopback ports |

Backups: `make backup` (`scripts/backup.sh`, nightly `pg_dump -Fc`, 7-day retention); restore: `make restore BACKUP=...` (stops writers, reloads, migrates, restarts, verifies). MinIO objects are **not** in `pg_dump` — mirror buckets separately with a local `mc` binary (the server image ships no `mc` client). Full drill in [RUNBOOK.md](RUNBOOK.md); GCP flow in [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md).

### Kubernetes

```bash
kubectl create namespace pipelineiq
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
# create pipelineiq-secrets from k8s/secrets.yaml.template (never commit the filled file)
kubectl apply -f k8s/postgres/ k8s/redis/ k8s/minio/
kubectl apply -f k8s/backend/ k8s/workers/ k8s/y-websocket/ k8s/ingress/
```

API runs 2 replicas behind an HPA (`pipelineiq-api-hpa`); each deployment carries liveness (`/livez`) and readiness (`/readyz`) probes plus resource requests/limits. Migrations run in an init container (`alembic upgrade head`). Details: [docs/kubernetes-deployment.md](docs/kubernetes-deployment.md).


## CI/CD

`.github/workflows/` holds 7 workflows. CI runs on every push and PR:

1. **Backend Tests** (`ci.yml`) — `ubuntu-latest`, `pgvector/pg15` + `redis:7-alpine` services, `alembic upgrade head`, full `pytest tests/`.
2. **Frontend** — `tsc --noEmit`, Vitest, `npm run build`.
3. **Code Quality** — `ruff check backend/ --select E9,F63,F82` (blocking) plus full Ruff, `tsc`, and informational ESLint.
4. **Docker Compose Smoke Test** — builds the stack, then health → login → upload → run → poll to completion, `down --volumes` after.
5. **Streaming E2E** — full Compose + Redpanda health gate, then the week-9 streaming suite.
6. **Playwright E2E** — pinned `ubuntu-24.04`, Chromium only, 4 specs (`01-auth`, `02-file-upload`, `03-pipeline-builder`, `10-templates`) against `PRODUCTION_URL`/`PRODUCTION_API_URL`; `main` pushes and manual dispatch only.
7. **Security** (`security.yml`, PR paths + weekly Monday) — blocking `pip-audit`, GPL/AGPL/LGPL/EPL/MPL fail-closed license check, npm critical audit gate, `hadolint` on all 5 Dockerfiles, Trivy SARIF upload.
8. **Deploy** (`deploy.yml`, `main` only) — GHCR build (`sha-*`, `main`, `latest`), then kubectl rollout with secret rotation and `/healthz` + `/readyz` smoke; dry-run without `KUBE_CONFIG`. Concurrency group `deploy-production` cancels superseded runs.
9. **CD** (`cd.yml`) — Render deploy hook on green `main`.
10. **Release** (`release.yml`) — release-please; on a new tag it verifies `APP_VERSION` consistency across `backend/config.py`, `frontend/lib/constants.ts`, and `.env.example`.
11. **Uptime** (`uptime.yml`, every 5 min) — probes `/health`, `/`, `/docs` when `PRODUCTION_URL` is set.
12. **Nightly** (`nightly.yml`, 02:00 UTC) — the three k6 profiles against `PRODUCTION_API_URL`.

Dependabot proposes weekly grouped bumps (pip / npm root+frontend+y-websocket / Docker / Actions, Mondays, Asia/Kolkata) to reviewer `Siddharthk17`. Direct pins stay pinned; when a transitive dependency breaks CI it gets an explicit pin with a comment naming the failure (see `kombu` in `backend/requirements.txt`).


## Project Structure

```text
PipelineIQ/
├── backend/                    # FastAPI app (Python 3.11)
│   ├── main.py                 # App factory, middleware, router mounting, health checks
│   ├── config.py               # Pydantic settings (fails fast on bad config)
│   ├── auth.py                 # JWT + bcrypt (process pool), fail-closed revocation
│   ├── database.py             # Write/read engines, session factories
│   ├── celery_app.py           # Celery app, fork-safe worker init
│   ├── celery_config.py        # 5 queues, routes, beat schedule (60/300/600 s)
│   ├── sse_app.py              # Dedicated SSE FastAPI app (:8001)
│   ├── api/                    # Versioned domain REST (aggregated in router.py)
│   │   ├── pipelines.py        # Validate/plan/preview/run, runs, cancel, export, timing
│   │   ├── files.py            # Upload (multipart + direct), preview, schema, profile
│   │   ├── schedules.py        # Cron CRUD, pause/resume, runs, preview
│   │   ├── contracts.py        # Contract versions, status, breaches
│   │   ├── lineage.py          # Graph, column ancestry, impact
│   │   ├── versions.py         # Version list/get/diff/restore
│   │   ├── webhooks.py         # Webhook CRUD, deliveries, test
│   │   ├── audit.py            # Log retrieval (admin / mine)
│   │   ├── auth.py             # Register, login, logout, users, roles
│   │   ├── templates.py        # Template list/get (public), fork
│   │   ├── notifications.py    # Slack/email configs + test
│   │   ├── dashboard.py        # Personal stats
│   │   ├── permissions.py      # Per-pipeline grants
│   │   ├── sse.py              # SSE handlers (mounted by sse_app, not main)
│   │   ├── debug.py            # Non-production only (Sentry probe)
│   │   └── router.py           # api_router (/api/v1) + legacy (/api)
│   ├── routers/                # Standalone routers with absolute prefixes
│   │   ├── ai.py               # Generate, repair, autocomplete, validate-yaml
│   │   ├── wasm.py             # Module validate/upload/list/delete
│   │   ├── streaming.py        # Daemon control, stats, topics, DLQ
│   │   ├── catalog.py          # Search, impact, lineage, orphans, stats
│   │   ├── catalog_pipelines.py# Pipeline catalog + cached AI descriptions
│   │   ├── column_policies.py  # Policy CRUD
│   │   ├── storage.py          # Bucket/tier stats, evict, shm cleanup
│   │   ├── runs.py             # Timing under the /api/runs prefix
│   │   └── lineage_export.py   # OpenLineage single + bulk export
│   ├── pipeline/               # Core engine
│   │   ├── parser.py           # StepType enum (19), dataclasses, ~17 validation checks
│   │   ├── runner.py           # Sequential execution, consumer-count eviction
│   │   ├── steps/              # StepExecutor package (re-exports _core surface)
│   │   ├── lineage.py          # NetworkX recorder + React Flow layout
│   │   ├── validators.py       # 12 data-quality checks
│   │   ├── planner.py          # Dry-run row/duration estimates
│   │   ├── cache.py            # Parsed-YAML cache (local LRU 256 + Redis TTL 3600)
│   │   ├── contracts.py        # Inline step-level contract checks
│   │   ├── versioning.py       # Version save/restore/diff
│   │   ├── schema_drift.py     # breaking/warning/info drift severities
│   │   ├── exceptions.py       # StepExecutionError hierarchy
│   │   └── definitions.py      # UI metadata per step type
│   ├── execution/              # DuckDB/Pandas/WASM engines, Arrow bus, healing
│   ├── tasks/                  # 9 Celery modules (pipeline, streaming, schedules,
│   │                           #   gemini, webhooks, notifications, profiling,
│   │                           #   reconciliation, storage maintenance)
│   ├── scheduling/             # croniter validation + next-run math
│   ├── models/                 # 23 tables (column_policy, contract_breach, data_contract…)
│   ├── services/               # audit, webhook, storage, notification, pipeline_validation
│   ├── ai/                     # Prompts (generation, repair, healing) + file-schema builder
│   ├── streaming/              # Redpanda client (8-partition default, DLQ helper)
│   ├── storage/                # Bucket lifecycle (7-day outputs, 2-day spills)
│   ├── security/               # Column policies (60 s cache), masking patterns
│   ├── profiling/              # Analyzer (semantic types incl. PII), profiler hooks
│   ├── contracts/              # Post-run contract validator
│   ├── openlineage/            # OL 1.0 event builder
│   ├── clients/                # Gemini client (budget, retries, response cache)
│   ├── repositories/          # Catalog registration, search, CTE blast radius
│   ├── benchmark/              # duckdb_vs_pandas.py + measured results.json
│   ├── templates/              # 5 curated pipeline YAMLs
│   ├── sample_data/            # 4 CSVs + simple/complex/broken pipeline YAMLs
│   ├── scripts/                # seed_demo, seed_showcase (50 pipelines), wait_for_db
│   ├── tests/                  # 117 files: unit (22 areas) + integration + e2e
│   └── alembic/versions/       # 37 migrations (four heads — see Database)
├── frontend/                   # Next.js 15 + React 19 + TypeScript strict
│   ├── app/                    # Routes (dashboard, login/register, files, pipelines/new,
│   │                           #   runs, catalog, templates, schedules, storage, wasm-modules)
│   ├── components/             # widgets (13) + layout + pipeline-builder + lineage +
│   │                           #   collaboration + theme + runs
│   ├── hooks/                  # usePipelineRuns(s), useLineage, useCollaborativePipeline, …
│   ├── store/                  # Zustand: pipeline, widget, theme, keybinding
│   ├── lib/                    # api.ts (sole client), constants, yjsUtils, yamlGraphSync, …
│   ├── __tests__/              # 23 Vitest spec files
│   └── Dockerfile              # Multi-stage standalone (ARGs for NEXT_PUBLIC_*)
├── y-websocket/                # Yjs relay (server.js, y-redis persistence, JWT gate)
│   └── __tests__/              # node:test suites (server unit + shared-room regression)
├── e2e/                        # 25 Playwright specs (auth → journeys) + fixtures + global-setup
├── k6/                         # load-auth, load-pipeline-api, load-file-upload
├── chaos/                      # chaos-scenarios.sh fault injection
├── k8s/                        # 17 manifests (namespace → ingress)
├── docs/                       # Per-area guides (diagrams + screenshots)
├── postman/                    # Collection (7 folders, 31 requests) + environment
├── nginx/                      # Reverse proxy (user nginx, gzip, rate zones, /metrics ACL)
├── prometheus/ grafana/ promtail/ loki configs
├── pgbouncer/ redis/postgres/minio support files
├── scripts/                    # bootstrap, backup, restore, demo-reseed, load/chaos runners
├── .github/workflows/          # 7 workflows (ci, security, deploy, cd, release, uptime, nightly)
├── docker-compose.yml          # 30 services (primary deployment target)
├── render.yaml                 # Render.com backend blueprint
├── playwright.config.ts        # Chromium (CI) / +Firefox (local), webServer skips in CI
├── pytest.ini / pyproject.toml # Pytest paths; Ruff + mypy config
└── .env.example                # 69 settings with defaults
```

Approximate size: ~35K lines backend (plus ~20K lines tests) · ~22K lines frontend.


## Configuration

All 69 settings with defaults in `.env.example`. `*` marks values you must replace before any shared deployment.

### Application

| Variable | Default | Purpose |
|----------|---------|---------|
| `APP_NAME` | `PipelineIQ` | Service name in logs and UI |
| `APP_VERSION` | `12.8.4` | Release-please owned; verified against tags on release |
| `DEBUG` | `false` | Debug mode (never `true` in production) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `ENVIRONMENT` | `development` | `production` enforces secure cookies, refuses weak secrets |

### Database

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` * | see note [^1] | Primary via PgBouncer (write pool) |
| `DATABASE_WRITE_URL` | → `DATABASE_URL` | Write endpoint override |
| `DATABASE_READ_URL` | → write URL | Read-replica endpoint |
| `POSTGRES_USER` | `pipelineiq` | Database user |
| `POSTGRES_PASSWORD` * | `your_password` | Database password — replace before any shared deployment |
| `POSTGRES_DB` | `pipelineiq` | Database name |
| `DB_CONNECT_TIMEOUT_SECONDS` | `5` | Connect timeout (seconds) |
| `REPLICATION_USER` | `replicator` | Streaming-replica user |
| `REPLICATION_PASSWORD` * | `your_replication_password` | Streaming-replica password — replace before any shared deployment |
| `SECRET_KEY` * | auto-generated (dev only) | JWT signing + session secret, ≥ 32 bytes |

[^1]: `postgresql://pipelineiq:your_password@pgbouncer:5432/pipelineiq` (from `.env.example`).

`*` = must be replaced before any shared deployment.

### Redis and Celery

| Variable | Default | Purpose |
|----------|---------|---------|
| `REDIS_PASSWORD` | * | Shared by all five Redis URLs below |
| `REDIS_BROKER_URL` | `redis://:...@redis-broker:6379/0` | Celery broker |
| `REDIS_BACKEND_URL` | `redis://:...@redis-broker:6379/1` | Result backend |
| `REDIS_PUBSUB_URL` | `redis://:...@redis-pubsub:6380/0` | SSE pub/sub |
| `REDIS_CACHE_URL` | `redis://:...@redis-cache:6381/0` | Revocation, YAML/policy caches, rate limits |
| `REDIS_YJS_URL` | `redis://:...@redis-yjs:6382/0` | Yjs document persistence |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | (default to the broker/backend URLs) | Celery overrides |
| `CELERY_WORKERS_CRITICAL` | `2` | Critical-queue concurrency |
| `CELERY_WORKERS_DEFAULT` | `3` | Default-queue concurrency |
| `CELERY_WORKERS_BULK` | `2` | Bulk-queue concurrency |

### Storage

| Variable | Default | Purpose |
|----------|---------|---------|
| `UPLOAD_DIR` | `./uploads` | Local-dev upload dir (`STORAGE_TYPE=local`) |
| `MAX_UPLOAD_SIZE` | `524288000` (500 MB) | Upload cap |
| `STORAGE_TYPE` | `s3` | `s3` (MinIO) or `local` |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | * | Console + S3 credentials |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | * (must match MinIO pair) | S3 API credentials |
| `S3_BUCKET` | `pipelineiq-uploads` | Source-file bucket |
| `S3_ENDPOINT_URL` | `http://minio:9000` | In-cluster endpoint |
| `S3_PUBLIC_ENDPOINT_URL` | (empty = reuse endpoint URL) | Browser-reachable URL for presigned links |
| `WASM_BUCKET` | `pipelineiq-wasm` | Module bucket |

### Pipeline execution

| Variable | Default | Purpose |
|----------|---------|---------|
| `MAX_PIPELINE_STEPS` | `50` | Steps per pipeline cap |
| `MAX_ROWS_PER_FILE` | `1000000` | Rows per uploaded file cap |
| `PROFILE_MAX_ROWS` / `PROFILE_SAMPLE_ROWS` | `1000000` / `100000` | Profiler bounds |
| `WORKER_MEMORY_LIMIT_GB` | `2` | Worker memory budget |
| `AUTONOMOUS_HEALING_MAX_ATTEMPTS` | `3` | Healing retries per failure |

### API, auth, rate limits

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_PREFIX` | `/api/v1` | Versioned mount point |
| `CORS_ORIGINS` | localhost + Vercel + Render hosts | Browser allow-list |
| `TRUSTED_HOSTS` | localhost variants + containers + Vercel + Render hosts | `TrustedHostMiddleware` |
| `RATE_LIMIT_PIPELINE_RUN` | `10/minute` | Run submissions |
| `RATE_LIMIT_FILE_UPLOAD` | `30/minute` | Uploads |
| `RATE_LIMIT_VALIDATION` | `60/minute` | Validate/plan/preview |
| `RATE_LIMIT_READ` | `120/minute` | Reads |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | JWT lifetime (24 h) |

### Workers and AI

| Variable | Default | Purpose |
|----------|---------|---------|
| `FLOWER_USER` / `FLOWER_PASSWORD` | `admin` / * | Flower basic auth |
| `GRAFANA_USER` / `GRAFANA_PASSWORD` | `admin` / * | Grafana login |
| `GEMINI_API_KEY` | (empty = AI/healing calls fail) | Google AI Studio key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Primary model |
| `GEMINI_FALLBACK_MODELS` | `gemini-2.0-flash,gemini-1.5-flash` | Fallbacks |
| `AUTO_CREATE_TABLES` | `false` | Create tables at startup (dev convenience) |

### Observability

| Variable | Default | Purpose |
|----------|---------|---------|
| `SENTRY_DSN` | (empty = disabled) | Sentry project DSN |
| `OTEL_ENABLED` | `true` | Toggle tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://jaeger:4317` | OTLP/gRPC intake |
| `OTEL_SAMPLE_RATE` | `0.1` | Trace sampling |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | (empty) / `587` / … | Email notifications + Grafana alerts |
| `SMTP_USE_TLS` / `SMTP_USE_SSL` / `SMTP_TIMEOUT` | `true` / `false` / `10` | SMTP transport |
| `SMTP_ENABLED` | `false` | Grafana email alerts master switch |

### Deployment

| Variable | Default | Purpose |
|----------|---------|---------|
| `PUBLIC_HOST` | `localhost` | External hostname/IP (Grafana URL, trusted hosts); written by bootstrap |
| `MINIO_IMAGE_TAG` | `latest` | MinIO server image tag |
| `JWT_SECRET` | (must be ≥ 32 chars for y-websocket) | Collab relay signing key; must match the JWT issuer |


## Postman Collection

`postman/PipelineIQ.postman_collection.json` — 7 folders, 31 requests (`postman_environment.json` holds `baseUrl`, auto-extracted `token`/`file_id`/`run_id`/`webhook_id`):

| Folder | Requests |
|--------|----------|
| Authentication | Register, Login, Get Me, Logout |
| Files | Upload, List, Preview, Schema History, Delete |
| Pipelines | Validate, Plan, Run, Get, List, SSE Stream |
| Lineage | Full Graph, Column Lineage, Impact Analysis |
| Versioning | List, Get, Diff, Restore |
| Webhooks | Create, List, Test, Deliveries, Delete |
| Observability | Health, Metrics, Audit Logs, My Audit Logs |


## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup, test gates, and conventions, and note the [Code of Conduct](CODE_OF_CONDUCT.md) and [Security Policy](SECURITY.md):

1. Create a feature branch (`git checkout -b fix/short-description`).
2. Add or update tests for every behavior change.
3. Run the gates: backend pytest file(s), `npx tsc --noEmit`, relevant Vitest specs.
4. Open a PR against `main` with the template filled in (Summary, Root cause, Change, Verification).


## License

[Apache 2.0](LICENSE) — Copyright 2026 Siddharth


