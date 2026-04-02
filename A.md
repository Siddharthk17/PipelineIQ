# AGENTS.md — PipelineIQ
# The Holy Grail Reference Document for This Codebase

> Every statement in this document is sourced from the actual codebase — from
> source files, migration scripts, test files, configuration, audit reports, and
> changelogs. Nothing is inferred or invented. If you read this document completely,
> you do not need to read the source code to understand, navigate, or extend this system.
>
> Current version: 2.1.4 (CHANGELOG.md)
> Note: render.yaml declares APP_VERSION "3.6.2" — this is a documentation bug in
> render.yaml. The authoritative version is 2.1.4 per CHANGELOG.md.
> Codebase: ~22,151 lines · 186 tracked files · 125 text files

---

## Table of Contents

1.  [What This System Is](#1-what-this-system-is)
2.  [Architecture](#2-architecture)
3.  [Technology Stack — Every Library and Version](#3-technology-stack)
4.  [Repository Structure — Every File](#4-repository-structure)
5.  [Data Model — All 14 Entities, Every Field](#5-data-model)
6.  [Database Schema — Full Column Definitions](#6-database-schema)
7.  [Alembic Migration History — All 8 Revisions](#7-alembic-migration-history)
8.  [API Reference — All 13 Routers, Every Endpoint](#8-api-reference)
9.  [Authentication and Authorisation](#9-authentication-and-authorisation)
10. [Pipeline Engine — Complete Deep Reference](#10-pipeline-engine)
11. [Exception Hierarchy — All 14 Classes](#11-exception-hierarchy)
12. [Business Logic — Every Rule](#12-business-logic)
13. [Configuration Reference — All 55+ Variables](#13-configuration-reference)
14. [Testing — All 299 Tests](#14-testing)
15. [Frontend Architecture — Every Component](#15-frontend-architecture)
16. [Infrastructure and Deployment](#16-infrastructure-and-deployment)
17. [Observability — Metrics, Dashboards, Logging](#17-observability)
18. [Security Posture](#18-security-posture)
19. [Performance Profile](#19-performance-profile)
20. [Known Issues and Technical Debt](#20-known-issues-and-technical-debt)
21. [Development Setup](#21-development-setup)
22. [Agent Instructions](#22-agent-instructions)

---

## 1. What This System Is

PipelineIQ is a tabular-data pipeline orchestration engine and observability platform.
It solves the "black box" problem in data engineering by providing complete, automated,
column-level traceability for every transformation applied to every dataset.

Users write a YAML file describing a data pipeline. PipelineIQ validates that YAML
against a 13-rule parser, queues the execution asynchronously via Celery and Redis,
executes each step with Pandas, streams real-time progress to the browser via
Server-Sent Events, builds a Directed Acyclic Graph (DAG) of column-level lineage
using NetworkX, detects schema drift when files change, and versions every pipeline
configuration with git-style diffs.

The complete user workflow:
1. Upload a CSV or JSON file via the UI or API
2. Write a pipeline YAML in the CodeMirror editor
3. Validate the YAML (optional — catches typos with fuzzy suggestions)
4. Generate a dry-run execution plan (optional — row count estimates per step)
5. Execute the pipeline (async — Celery worker runs it)
6. Watch real-time step progress via SSE in the Run Monitor widget
7. Explore the column-level lineage graph in the Lineage Graph widget
8. Download the output file from the API or Run History widget
9. Review schema drift if the source file was re-uploaded

### The core value proposition explained precisely

Column-level lineage is the product. Everything else supports it. As the pipeline
executes, `LineageRecorder` adds nodes and edges to a `networkx.DiGraph`:

- Every **node** is one column at one pipeline step: `"{step_name}.{column_name}"`
- Every **edge** is a transformation: `(source_node → target_node)` labelled with
  the transformation type and parameters
- Source file nodes are: `"file::{file_id}"`
- Output file nodes are: `"output::{step_name}::{filename}"`
- Join key edges carry `is_join_key=True` attribute

This graph enables two analytical queries:
- **Backward ancestry** (`get_column_ancestry`): uses `nx.ancestors()` to walk
  backward from any output column to its exact source file and source column,
  through every transformation step that produced it
- **Forward impact** (`get_impact_analysis`): uses `nx.descendants()` to walk
  forward from any source column to every downstream step and output column
  that depends on it

The React Flow visualization layout is computed once after execution using a
Sugiyama-inspired algorithm: topological sort → longest-path layer assignment
→ 300px horizontal spacing × 80px vertical spacing. The result is stored in the
`lineage_graphs.react_flow_data` JSONB column. It is never recomputed on API calls.

### Hard boundaries — enforced in code, not aspirational

**Tabular data only.** CSV and JSON files are first-class. No Parquet, no Avro,
no binary files, no images, no PDFs, no unstructured data. The `files.py` upload
handler validates extensions against a frozenset of `{".csv", ".json"}`.

**Batch processing, not streaming.** Pipeline execution is discrete and queue-based.
Not Apache Flink. Not Kafka Streams. Progress is streamed in real time, but the
data transformation itself is batch-oriented: load full file → transform → save.

**In-memory processing.** The Pandas engine loads entire datasets into the Celery
worker's RAM. Not Apache Spark. Not Dask. The hard limits are:
- `MAX_UPLOAD_SIZE` = 52,428,800 bytes (50MB)
- `MAX_ROWS_PER_FILE` = 1,000,000 rows
- Effective RAM limit per job ≈ 2GB–4GB depending on worker configuration

**Single-worker per run.** Each pipeline execution is handled by one Celery worker.
There is no intra-run parallelism. Steps execute sequentially.

**No token refresh.** JWT tokens expire after 24 hours. There is no refresh mechanism.
Users must re-authenticate.

### Codebase metrics

| Area | Lines | Files |
|---|---|---|
| Backend source (Python) | 9,186 | 48 |
| Backend tests (Python) | 5,098 | 16 |
| Frontend (TypeScript/TSX/CSS) | 6,317 | 45 |
| Infrastructure (Docker, Nginx, YAML configs) | 761 | 10 |
| Database migrations (Python) | 789 | 6 |
| **Total** | **~22,151** | **~125** |

### Largest files (backend)

| File | Lines | What it does |
|---|---|---|
| `backend/pipeline/parser.py` | 703 | YAML → typed dataclasses, 13 validation rules |
| `backend/pipeline/lineage.py` | 634 | NetworkX DAG construction + React Flow layout |
| `backend/pipeline/steps.py` | 606 | 9 step type executors using Pandas |
| `backend/pipeline/exceptions.py` | 443 | 14-class exception hierarchy with fuzzy suggestions |
| `backend/api/files.py` | 458 | File upload, preview, schema history, drift |
| `backend/api/pipelines.py` | 421 | Validate, plan, run, SSE stream, cancel, export |
| `backend/models.py` | 348 | All 14 SQLAlchemy ORM models |
| `backend/schemas.py` | 334 | All 20+ Pydantic request/response schemas |

### Largest files (frontend)

| File | Lines | What it does |
|---|---|---|
| `components/widgets/PipelineEditorWidget.tsx` | 318 | CodeMirror YAML editor with validation |
| `app/register/page.tsx` | 280 | Registration form with password complexity |
| `app/login/page.tsx` | 275 | Login form with demo login button |
| `components/widgets/VersionHistoryWidget.tsx` | 265 | Pipeline version diffing UI |
| `lib/api.ts` | 252 | Unified fetch wrapper + all API endpoint functions |
| `components/lineage/LineageGraph.tsx` | 251 | ReactFlow graph rendering |
| `store/widgetStore.ts` | 225 | Binary tree layout engine for 5 workspaces |

---

## 2. Architecture

### Pattern

PipelineIQ is a **modular layered monolith** with a **distributed task execution**
worker tier. This is an explicit architectural choice over microservices — it minimises
deployment complexity and inter-service network latency while still allowing the
worker tier to scale independently of the API tier.

The four layers:

**API Layer** (`backend/api/`) — HTTP routing, request validation, JWT authentication,
rate limiting, SSE streaming. 13 router modules registered under `/api/v1/` via
`backend/api/router.py`. Never contains business logic. Delegates immediately
to the pipeline engine or services.

**Domain Layer** (`backend/pipeline/`, `backend/services/`) — All business logic.
YAML parsing, step execution, lineage recording, schema drift detection, pipeline
versioning, dry-run planning, data quality validation. Audit logging, webhook delivery,
notification delivery live in services. Completely decoupled from HTTP transport.

**Worker Layer** (`backend/tasks/`) — Celery task entry points. Long-running
pipeline execution, async webhook delivery, scheduled pipeline checks. Never
reached directly from HTTP — always via the Celery broker (Redis).

**Persistence Layer** (`backend/models.py`, `backend/alembic/`) — 14 SQLAlchemy
ORM models, 8 Alembic migrations, PostgreSQL as primary store, SQLite for tests.

### Full system architecture diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Browser                                    │
│   Next.js 15 · React 19 · Zustand · ReactFlow · CodeMirror · SSE       │
│   5 Workspaces · 8 Widgets · 7 Themes · CommandPalette · Keybindings   │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ HTTP / SSE
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Nginx (Port 80)                                 │
│  Reverse proxy · Security headers · SSE passthrough (buffering OFF)     │
│  /metrics → internal networks only (10.x, 172.16.x, 192.168.x, 127.x) │
│  /api/ → api:8000  |  / → frontend:3000  |  /grafana/ → grafana:3000   │
└──────┬──────────────────┬─────────────────────────┬─────────────────────┘
       │                  │                          │
       ▼                  ▼                          ▼
┌────────────┐  ┌──────────────────┐       ┌────────────────┐
│  FastAPI   │  │    Next.js 15    │       │    Grafana     │
│  Uvicorn   │  │  App Router SSR  │       │  Port 3001     │
│  Port 8000 │  │  Port 3000       │       │  10-panel dash │
└─────┬──────┘  └──────────────────┘       └───────┬────────┘
      │                                            │
      ├─────────── Celery Task Queue ──────┐       │
      │                                   │       │
      ▼                                   ▼       ▼
┌────────────┐  ┌────────────┐  ┌──────────────────┐
│ PostgreSQL │  │   Redis 7  │  │   Prometheus     │
│  Port 5432 │  │  Port 6379 │  │   Port 9090      │
│  Primary DB│  │  Broker +  │  │  Scrapes /metrics│
│  JSONB     │  │  Cache +   │  │  every 10s       │
│  UUID PKs  │  │  Pub/Sub   │  └──────────────────┘
└────────────┘  └────────────┘
```

### Component map — every component

**Backend API** (`backend/api/`)
- Location: 13 files in `backend/api/`
- Responsibility: HTTP routing, validation, auth, rate limiting, SSE streaming
- Communicates with: PostgreSQL (via SQLAlchemy), Redis (SSE pub/sub), Celery (task dispatch)
- Owns: Public API contract, session management, request/response lifecycle
- Files: `router.py`, `files.py`, `pipelines.py`, `lineage.py`, `versions.py`,
  `webhooks.py`, `audit.py`, `schedules.py`, `templates.py`, `notifications.py`,
  `dashboard.py`, `permissions.py`, `debug.py`

**Pipeline Engine** (`backend/pipeline/`)
- Location: 9 files in `backend/pipeline/`
- Responsibility: YAML parsing, static planning, step execution via dispatch dict,
  column-level lineage recording, schema drift detection, data quality validation,
  pipeline versioning and diff generation
- Communicates with: disk (file I/O via Pandas), NetworkX graph (in-memory)
- Owns: Transformation logic, Column Lineage DAG, validation rules
- Files: `parser.py`, `runner.py`, `steps.py`, `lineage.py`, `exceptions.py`,
  `validators.py`, `schema_drift.py`, `planner.py`, `versioning.py`

**Workers** (`backend/tasks/`)
- Location: 4 files in `backend/tasks/`
- Responsibility: Celery task entry points for all long-running operations
- Communicates with: Redis (broker + result backend), PostgreSQL (result persistence)
- Owns: Job lifecycle, state transitions (PENDING → RUNNING → COMPLETED/FAILED)
- Files: `pipeline_tasks.py`, `webhook_tasks.py`, `schedule_tasks.py`,
  `notification_tasks.py`

**Services** (`backend/services/`)
- Location: 3 files in `backend/services/`
- Responsibility: External communication — audit persistence, webhook HTTP delivery,
  Slack/email notification delivery
- Communicates with: PostgreSQL (audit writes), external HTTP APIs (via httpx)
- Owns: Outbound communication logic, HMAC signing for webhooks
- Files: `audit_service.py`, `webhook_service.py`, `notification_service.py`

**Persistence** (`backend/models.py`, `backend/alembic/`)
- Location: single `models.py` file (348 lines), 8 migration scripts
- Responsibility: 14 ORM models, schema evolution
- Communicates with: PostgreSQL (production), SQLite (tests)

**Frontend Workspace** (`frontend/`)
- Location: Next.js 15 App Router application
- Responsibility: Widget-based keyboard-driven UI, pipeline authoring, run monitoring,
  lineage visualisation, file management, settings
- Communicates with: Backend API via REST and SSE
- Owns: Client-side state (4 Zustand stores), interactive lineage visualisations (React Flow)

**Infrastructure** (`docker-compose.yml`, `nginx/`, `prometheus/`, `grafana/`)
- 9 Docker Compose services, Nginx reverse proxy, Prometheus metrics collection,
  Grafana dashboards, cAdvisor container metrics, node-exporter system metrics

### Primary data flow — pipeline execution (step by step)

```
Step 1: Client POSTs to /api/v1/pipelines/run
  Body: { yaml_config: "...", name: "optional" }
  Auth: Bearer JWT required

Step 2: pipelines.py router validates YAML
  → parse_pipeline_config(yaml_string, registered_file_ids)
  → Raises PipelineConfigError subclass if invalid (with fuzzy suggestions)
  → Returns typed PipelineConfig dataclass

Step 3: PipelineRun record created in PostgreSQL
  → Status: PENDING
  → yaml_config stored as TEXT
  → user_id from JWT

Step 4: Celery task dispatched
  → execute_pipeline_task.delay(str(run.id))
  → Task ID stored in PipelineRun.celery_task_id
  → API returns immediately: { run_id, status: "PENDING" }

Step 5: Celery worker picks up task
  → Marks PipelineRun.status = RUNNING
  → Sets PipelineRun.started_at = datetime.now(timezone.utc)
  → Loads all files referenced in YAML (only referenced files, not all files)
  → Initialises PipelineRunner with ProgressCallback

Step 6: PipelineRunner iterates config.steps
  For each step:
    → Emits StepProgressEvent(status=RUNNING) via ProgressCallback
    → Calls StepExecutor._dispatch[step.type](step, df)
    → Pandas operation executes in-memory
    → LineageRecorder.record_{type}_step() adds nodes + edges to NetworkX DiGraph
    → StepExecutionResult captured (rows_in, rows_out, columns_in, columns_out, duration_ms)
    → Emits StepProgressEvent(status=COMPLETED) via ProgressCallback
    → ProgressCallback publishes event JSON to Redis channel: pipeline_progress:{run_id}

Step 7: SSE endpoint delivers events to browser
  → GET /api/v1/pipelines/{run_id}/stream subscribes to Redis channel
  → Nginx passes SSE through (proxy_buffering off, chunked_transfer_encoding on)
  → Browser EventSource receives: step_started, step_completed, pipeline_completed
  → Frontend usePipelineRun hook updates Run Monitor widget in real time

Step 8: Worker finalises after all steps complete
  → StepResult records persisted for each step
  → LineageGraph record created:
      graph_data: NetworkX node-link JSON
      react_flow_data: pre-computed layout (Sugiyama algorithm)
  → PipelineRun.status = COMPLETED
  → PipelineRun.completed_at = datetime.now(timezone.utc)
  → PipelineVersion record saved with auto-incremented version number

Step 9: Async side effects dispatched as separate Celery tasks
  → webhook_tasks.py: HTTP POST to registered webhook URLs (HMAC signed)
  → notification_tasks.py: Slack/email delivery if configured
  → audit_service.log_action() for pipeline.run event
```

### Architectural boundaries and known leaks

**Respected boundaries:**
- YAML never passes the `parse_pipeline_config()` boundary as raw dicts. After
  parsing, only typed `PipelineConfig` dataclasses enter the engine.
- `PipelineRunner`, `StepExecutor`, and `LineageRecorder` have zero imports of
  Redis, SQLAlchemy, httpx, or any external service. All side effects are injected
  through the `ProgressCallback` protocol.
- Output data blobs (CSV/JSON output files) are never stored in the database.
  They live on disk at `UPLOAD_DIR`. The database stores only metadata and graphs.

**Documented leaks (accepted, not accidental):**
- `StepExecutor._execute_save()` writes output files directly to disk, bypassing
  the database layer for data blobs. This is by design for performance.
- `audit_service.log_action()` is called directly from routers, creating tight
  coupling between API actions and audit persistence. Acknowledged in AUDIT_REPORT.md.
- Schema drift detection logic is split: `api/files.py` calls into `schema_drift.py`
  for the computation but does some reporting itself. This is acknowledged technical debt.

---

## 3. Technology Stack

### Backend Python packages (`backend/requirements.txt`) — 28 packages

| Package | Version | Used for |
|---|---|---|
| `fastapi` | 0.109.0 | HTTP framework, dependency injection, OpenAPI generation |
| `uvicorn[standard]` | 0.27.0 | ASGI server, runs the FastAPI app |
| `pydantic` | 2.5.3 | Request/response validation, settings, dataclasses |
| `pydantic-settings` | 2.1.0 | `BaseSettings` for environment variable config |
| `sqlalchemy` | 2.0.25 | ORM, query building, connection pooling |
| `alembic` | 1.13.1 | Database schema migrations |
| `psycopg2-binary` | 2.9.9 | PostgreSQL driver for SQLAlchemy |
| `pandas` | 2.1.4 | Data transformation engine for all step types |
| `numpy` | 1.26.3 | Numerical support for Pandas operations |
| `pyyaml` | 6.0.1 | YAML parsing (via `yaml.safe_load` only) |
| `networkx` | 3.2.1 | Column lineage DAG construction and traversal |
| `celery` | 5.3.6 | Distributed task queue for async pipeline execution |
| `croniter` | 2.0.1 | Cron expression validation and next-run computation |
| `redis` | 5.0.1 | Redis client (Celery broker + cache + pub/sub) |
| `slowapi` | 0.1.9 | Rate limiting middleware for FastAPI |
| `python-multipart` | 0.0.6 | Multipart form data parsing for file uploads |
| `python-dotenv` | 1.0.0 | `.env` file loading in development |
| `python-jose[cryptography]` | 3.3.0 | JWT creation and validation |
| `passlib[bcrypt]` | 1.7.4 | Password hashing utilities |
| `bcrypt` | 4.0.1 | bcrypt algorithm implementation |
| `flower` | 2.0.1 | Celery monitoring web UI |
| `prometheus-fastapi-instrumentator` | 6.1.0 | Auto HTTP metrics for FastAPI |
| `sentry-sdk[fastapi]` | 1.39.1 | Error tracking with FastAPI + Celery integration |
| `pytest` | 7.4.4 | Test runner |
| `pytest-asyncio` | 0.23.3 | Async test support |
| `httpx` | 0.26.0 | Async HTTP client (webhook delivery + test client) |
| `factory-boy` | 3.3.0 | Test data factories |

**Removed dependencies (do not re-add):**
- `aioredis` — deprecated, replaced by `redis.asyncio` (removed v2.1.3)
- `aiofiles` — not imported anywhere (removed v2.1.3)

### Frontend npm packages (`frontend/package.json`) — 33 production + dev

**Production dependencies:**

| Package | Version | Used for |
|---|---|---|
| `next` | 15.4.9 | React framework, App Router, SSR, middleware |
| `react` | 19.2.1 | UI library |
| `react-dom` | 19.2.1 | DOM rendering |
| `zustand` | 5.0.11 | State management for all 4 stores |
| `@xyflow/react` | 12.10.1 | Interactive graph visualization (ReactFlow) |
| `@uiw/react-codemirror` | 4.25.5 | Code editor wrapper (YAML editor widget) |
| `@codemirror/lang-yaml` | 6.1.2 | YAML syntax highlighting for CodeMirror |
| `@codemirror/state` | 6.5.4 | CodeMirror state management |
| `@codemirror/view` | 6.39.16 | CodeMirror view layer |
| `@dnd-kit/core` | 6.3.1 | Drag-and-drop for widget repositioning |
| `@dnd-kit/sortable` | 10.0.0 | Sortable list drag-and-drop |
| `@dnd-kit/utilities` | 3.2.2 | DnD kit utilities |
| `@hookform/resolvers` | 5.2.1 | React Hook Form validation resolvers |
| `@tanstack/react-query` | 5.90.21 | Server state management and caching |
| `motion` | 12.23.24 | Animations for modals, transitions, counters |
| `lucide-react` | 0.553.0 | Icon set used throughout the UI |
| `date-fns` | 4.1.0 | Date formatting and manipulation |
| `clsx` | 2.1.1 | Conditional class name construction |
| `tailwind-merge` | 3.3.1 | Merge Tailwind classes without conflicts |
| `class-variance-authority` | 0.7.1 | Component variant management |
| `tailwindcss` | 4.1.11 | Utility-first CSS framework |
| `postcss` | 8.5.6 | CSS processing |
| `autoprefixer` | 10.4.21 | CSS vendor prefixing |

**Dev dependencies:**

| Package | Version | Used for |
|---|---|---|
| `vitest` | 4.0.18 | Test runner for frontend tests |
| `@testing-library/react` | 16.3.2 | React component testing utilities |
| `@testing-library/user-event` | 14.6.1 | User interaction simulation |
| `@testing-library/jest-dom` | 6.9.1 | Custom DOM matchers |
| `@vitejs/plugin-react` | 5.1.4 | Vite React plugin for Vitest |
| `jsdom` | 28.1.0 | DOM simulation for tests |
| `typescript` | 5.9.3 | TypeScript compiler |
| `eslint` | 9.39.1 | Linting |
| `eslint-config-next` | 16.0.8 | Next.js ESLint rules |
| `@tailwindcss/typography` | 0.5.19 | Prose styling plugin |
| `tw-animate-css` | 1.4.0 | Tailwind animation utilities |
| `@types/node` | 20+ | Node.js type definitions |
| `@types/react` | 19+ | React type definitions |
| `@types/react-dom` | 19+ | ReactDOM type definitions |

**Removed dependencies (do not re-add):**
- `@google/genai` — unused, was 500KB+ bundle overhead (removed v2.1.3)
- `firebase-tools` — unused devDependency (removed v2.1.3)

### Data stores

**PostgreSQL 15** (primary store, production on Neon.tech)
Stores: users, uploaded file metadata, pipeline run metadata, step results, serialized
lineage graphs (JSONB), audit logs, webhook configurations, webhook delivery records,
pipeline schedules, notification configs, pipeline permissions, pipeline version
snapshots, schema snapshots for drift detection, file version chains.

Connection pool (PostgreSQL only):
- `pool_size` = 20
- `max_overflow` = 10
- `pool_pre_ping` = True (validates connections before use)
- `pool_recycle` = 3600 (recycles connections every hour)

**Redis 7** (three distinct uses, production on Upstash with TLS)
1. Celery task broker — `REDIS_URL` database 0
2. Celery result backend — task state storage
3. SSE pub/sub bridge — `pipeline_progress:{run_id}` channels during execution
4. Application cache — lineage graphs (1h TTL), dashboard stats (30s TTL)

Upstash TLS handling (in `celery_app.py`): when `REDIS_URL` starts with `rediss://`,
the app conditionally adds:
```python
broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
```

**SQLite** (tests only)
Used by the `conftest.py` test fixture via `check_same_thread=False`. The UUID
primary key workaround exists because `gen_random_uuid()` is a PostgreSQL function
that does not exist in SQLite — tests use Python-level UUID generation instead.

**Local disk** (`UPLOAD_DIR`, default `/app/uploads`)
Source CSV/JSON files uploaded by users; output files produced by `save` steps.
In Docker Compose, this is a named volume (`uploads`) shared between `api` and
`worker` containers so both can access files. File paths are always UUID-generated —
never user-supplied filenames.

### Runtime environments

| Component | Technology | Version |
|---|---|---|
| Backend language | Python | 3.11+ |
| Backend server | Uvicorn + ASGI | 0.27.0 |
| Frontend build | Node.js | 20+ |
| Database | PostgreSQL | 15 |
| Cache/Queue | Redis | 7 |
| Reverse proxy | Nginx | 1.25-alpine |
| Metrics collection | Prometheus | 2.48.0 |
| Metrics dashboards | Grafana | 10.2.0 |

---

## 4. Repository Structure

**186 tracked files. 30 directories. Every file described.**

```
pipelineiq/                              ← Repository root
│
├── AGENTS.md                            ← This document (the holy grail)
├── AUDIT_REPORT.md                      ← Full codebase audit (February 2026)
├── CHANGELOG.md                         ← Version history 0.1.2 → 2.1.4
├── README.md                            ← Public documentation
├── LICENSE                              ← Apache 2.0
├── alembic.ini                          ← Root-level Alembic config
│                                          NOTE: DUPLICATE of backend/alembic.ini
│                                          Known issue — do not add a third
├── docker-compose.yml                   ← 9-service Docker orchestration
├── render.yaml                          ← Render.com deployment blueprint
├── .env.example                         ← Environment variable template
├── .gitignore                           ← Excludes: .env, *.db, uploads/, __pycache__/
├── pipelineiq.db                        ← Root SQLite dev database
│                                          Should be gitignored — known issue
│
├── backend/                             ← FastAPI + Celery application root
│   │
│   ├── api/                             ← 13 FastAPI router modules
│   │   ├── __init__.py                  ← Empty package init
│   │   ├── router.py                    ← Registers all routers under /api/v1/
│   │   ├── files.py                     ← Upload, list, preview, schema history, drift (458 lines)
│   │   ├── pipelines.py                 ← Validate, plan, preview, run, stream, cancel, export (421 lines)
│   │   ├── lineage.py                   ← Graph retrieval, column ancestry, impact analysis
│   │   ├── versions.py                  ← Pipeline version list, diff, restore
│   │   ├── webhooks.py                  ← Webhook CRUD, test endpoint, delivery log
│   │   ├── audit.py                     ← Audit log viewer (admin: all, user: own)
│   │   ├── schedules.py                 ← Cron-based pipeline scheduling CRUD
│   │   ├── templates.py                 ← 5 pre-built pipeline templates (read-only)
│   │   ├── notifications.py             ← Slack/email notification config + test
│   │   ├── dashboard.py                 ← Per-user analytics (cached 30s)
│   │   ├── permissions.py               ← Per-pipeline RBAC management
│   │   └── debug.py                     ← Sentry test, config dump (non-production only)
│   │
│   ├── pipeline/                        ← Core transformation engine (9 files, ~3,000 lines)
│   │   ├── __init__.py                  ← Empty package init
│   │   ├── parser.py                    ← YAML → typed PipelineConfig dataclasses (703 lines)
│   │   │                                  Validates 13 rules, collects all errors (not fail-fast)
│   │   │                                  Uses difflib.get_close_matches for typo suggestions
│   │   ├── runner.py                    ← Step orchestration, ProgressCallback injection
│   │   │                                  df_registry: Dict[str, pd.DataFrame]
│   │   │                                  Zero Redis/DB dependencies — pure computation
│   │   ├── steps.py                     ← 9 step executors via dispatch dict (606 lines)
│   │   │                                  StepExecutor._dispatch: Dict[str, Callable]
│   │   ├── lineage.py                   ← NetworkX DiGraph construction (634 lines)
│   │   │                                  LineageRecorder class with per-step-type methods
│   │   │                                  Sugiyama-inspired React Flow layout algorithm
│   │   ├── exceptions.py                ← 14-class exception hierarchy (443 lines)
│   │   │                                  PipelineIQError base with to_dict() method
│   │   │                                  All carry specific error_code strings
│   │   ├── validators.py                ← 12 data quality check implementations
│   │   │                                  ValidateStep runs checks, returns ValidationResult
│   │   ├── schema_drift.py              ← Breaking/warning/info schema change detection
│   │   │                                  Compares column dicts between snapshots
│   │   ├── planner.py                   ← Dry-run execution planner (212 lines)
│   │   │                                  8 heuristics for row count estimation
│   │   └── versioning.py                ← Pipeline YAML version save + diff computation
│   │                                      Unified diff via Python difflib
│   │
│   ├── services/                        ← External communication services
│   │   ├── __init__.py                  ← Empty package init
│   │   ├── audit_service.py             ← log_action() — append-only audit persistence
│   │   │                                  Every state change flows through here
│   │   ├── webhook_service.py           ← HMAC-SHA256 signing + HTTP delivery + retry
│   │   │                                  X-PipelineIQ-Signature header on all deliveries
│   │   └── notification_service.py      ← Slack incoming webhook + SMTP email delivery
│   │
│   ├── tasks/                           ← Celery task definitions
│   │   ├── __init__.py                  ← Empty package init
│   │   ├── pipeline_tasks.py            ← execute_pipeline_task (primary execution task)
│   │   │                                  Manages PENDING→RUNNING→COMPLETED/FAILED transitions
│   │   ├── webhook_tasks.py             ← Async HTTP webhook delivery (separate from pipeline task)
│   │   ├── schedule_tasks.py            ← Celery Beat recurring schedule checker
│   │   └── notification_tasks.py        ← Async Slack/email notification delivery
│   │
│   ├── utils/                           ← Shared utilities
│   │   ├── __init__.py                  ← Empty package init
│   │   ├── cache.py                     ← Redis-backed cache (TTL, SCAN-based deletion)
│   │   │                                  All operations catch RedisError gracefully
│   │   │                                  Module-level ConnectionPool (not per-request)
│   │   ├── rate_limiter.py              ← slowapi Limiter + 4 rate limit tier dependencies
│   │   ├── uuid_utils.py                ← as_uuid(), validate_uuid_format()
│   │   │                                  Was copy-pasted 3-4× before v2.1.3 extraction
│   │   ├── string_utils.py              ← sanitize_pipeline_name(), sanitize_step_name()
│   │   │                                  Also: fuzzy matching helpers
│   │   └── time_utils.py                ← utcnow() wrapper (uses datetime.now(timezone.utc))
│   │                                      Do NOT use datetime.utcnow() — deprecated
│   │
│   ├── alembic/                         ← Database migration scripts
│   │   ├── env.py                       ← Alembic environment configuration
│   │   ├── script.py.mako               ← Migration script template
│   │   └── versions/                    ← 8 migration scripts
│   │       ├── 97385cb62e0a_*.py        ← Initial schema
│   │       ├── 14a9b359a361_*.py        ← Schema snapshots + pipeline versions
│   │       ├── c3f5e7a8b901_*.py        ← UUID + JSONB migration
│   │       ├── d4e6f8a1b2c3_*.py        ← Users table + RBAC
│   │       ├── e5f6a7b8c9d0_*.py        ← Webhooks tables
│   │       ├── f6a7b8c9d0e1_*.py        ← Audit logs + immutability trigger
│   │       ├── a1b2c3d4e5f6_*.py        ← Performance indexes
│   │       └── b2c3d4e5f6a7_*.py        ← CANCELLED status + schedules + permissions
│   │
│   ├── tests/                           ← 206 tests across 14 files + conftest
│   │   ├── conftest.py                  ← Shared fixtures (see Testing section)
│   │   ├── test_api.py                  ← 34 endpoint integration tests
│   │   ├── test_steps.py                ← 25 StepExecutor unit tests
│   │   ├── test_validators.py           ← 22 validation check tests
│   │   ├── test_parser.py               ← 18 YAML parser tests
│   │   ├── test_lineage.py              ← 18 lineage graph + query tests
│   │   ├── test_auth.py                 ← 17 auth + JWT + role tests
│   │   ├── test_planner.py              ← 15 dry-run estimation tests
│   │   ├── test_versioning.py           ← 12 version + diff tests
│   │   ├── test_schema_drift.py         ← 10 drift detection tests
│   │   ├── test_webhooks.py             ← 9 webhook CRUD + HMAC + delivery tests
│   │   ├── test_caching.py              ← 8 Redis cache operation tests
│   │   ├── test_security.py             ← 7 security penetration tests
│   │   ├── test_rate_limiting.py        ← 6 per-tier rate enforcement tests
│   │   └── test_performance.py          ← 5 response time + concurrent load tests
│   │
│   ├── scripts/
│   │   ├── __init__.py                  ← Empty package init
│   │   └── seed_demo.py                 ← Creates demo@pipelineiq.app / Demo1234!
│   │                                      Inserts sample pipeline runs and files
│   │
│   ├── sample_data/                     ← 4 CSV files + 3 YAML pipeline examples
│   │                                      Used by seed_demo.py and test fixtures
│   │
│   ├── vendor/                          ← Previously contained 86 .whl files
│   │                                      Removed in v2.1.3 (bloated repo)
│   │                                      pip install now runs in CI
│   │
│   ├── main.py                          ← App factory function
│   │                                      Registers middleware: CORS, rate limiting,
│   │                                      request_id injection, timing headers
│   │                                      Registers routers, Sentry init, health endpoint
│   │                                      Exception handlers: PipelineIQError, ValidationError, Exception
│   │
│   ├── config.py                        ← Pydantic BaseSettings (55+ variables)
│   │                                      Groups: app, database, Redis, files, pipeline,
│   │                                      rate limiting, auth, monitoring, deployment
│   │                                      Production validator: fails startup if SECRET_KEY
│   │                                      matches default placeholder
│   │
│   ├── models.py                        ← ALL 14 SQLAlchemy ORM models (348 lines)
│   │                                      Single file — never split across multiple files
│   │                                      PgJSONB custom type: JSONB on PostgreSQL, JSON on SQLite
│   │
│   ├── schemas.py                       ← ALL Pydantic schemas (334 lines, 20+ models)
│   │                                      Single file — never split across multiple files
│   │
│   ├── metrics.py                       ← ALL Prometheus metric definitions
│   │                                      Single file — circular import if defined elsewhere
│   │
│   ├── auth.py                          ← JWT creation/validation, bcrypt password hashing
│   │                                      get_current_user, get_current_admin, get_optional_user
│   │                                      NOTE: has overlapping concerns with api/auth.py
│   │                                      This duplication is known tech debt
│   │
│   ├── celery_app.py                    ← Celery application configuration
│   │                                      JSON serializer (not pickle)
│   │                                      UTC timezone, task tracking enabled
│   │                                      prefetch_multiplier=1 (one task per worker)
│   │                                      Upstash TLS handling for rediss:// URLs
│   │
│   ├── database.py                      ← SQLAlchemy engine + connection pool
│   │                                      get_db() FastAPI dependency (yields session)
│   │                                      PostgreSQL: pool_size=20, max_overflow=10
│   │                                      SQLite: check_same_thread=False (tests only)
│   │
│   ├── dependencies.py                  ← Shared FastAPI dependency functions
│   │
│   ├── alembic.ini                      ← DUPLICATE of root alembic.ini — known issue
│   │
│   ├── pipelineiq.db                    ← SQLite dev database — should be gitignored
│   │
│   ├── requirements.txt                 ← 28 Python packages with pinned versions
│   │
│   └── Dockerfile                       ← Python 3.11-slim base
│                                          Non-root user: appuser (uid 1000)
│                                          CMD: alembic upgrade head → seed_demo →
│                                               celery worker (background) → uvicorn
│
├── frontend/                            ← Next.js 15 + React 19 application
│   │
│   ├── app/                             ← Next.js App Router pages
│   │   ├── globals.css                  ← Global CSS + CSS variable theme system
│   │   ├── layout.tsx                   ← Root layout with providers wrapper
│   │   ├── providers.tsx                ← React Query + Auth + Theme providers
│   │   ├── page.tsx                     ← Dashboard home (redirects or renders workspace)
│   │   ├── login/page.tsx               ← Login form with demo button (275 lines)
│   │   └── register/page.tsx            ← Registration form with complexity validation (280 lines)
│   │
│   ├── components/
│   │   │
│   │   ├── layout/                      ← Application shell components
│   │   │   ├── TopBar.tsx               ← Navigation bar: user info, theme picker, presence
│   │   │   ├── WidgetGrid.tsx           ← Binary tree panel layout renderer
│   │   │   ├── CommandPalette.tsx       ← Cmd+K palette with 7 theme entries + actions
│   │   │   ├── TerminalLauncher.tsx     ← Alt+Enter widget quick-add launcher
│   │   │   ├── PresenceIndicator.tsx    ← Online user indicator (WebSocket-ready, not yet wired)
│   │   │   └── KeybindingsModal.tsx     ← Alt+K keyboard shortcut reference modal
│   │   │
│   │   ├── widgets/                     ← 8 workspace widgets + shell + step DAG
│   │   │   ├── WidgetShell.tsx          ← Common wrapper: title bar, resize, controls
│   │   │   ├── FileUploadWidget.tsx     ← Drag-and-drop file upload interface
│   │   │   ├── FileRegistryWidget.tsx   ← File list with preview, schema, drift badge
│   │   │   ├── PipelineEditorWidget.tsx ← CodeMirror YAML editor (318 lines)
│   │   │   │                              Debounced validation (800ms), inline plan preview
│   │   │   ├── RunMonitorWidget.tsx     ← Real-time SSE step progress display
│   │   │   ├── LineageGraphWidget.tsx   ← React Flow lineage DAG with sidebar
│   │   │   ├── RunHistoryWidget.tsx     ← Historical run list with status filters
│   │   │   ├── QuickStatsWidget.tsx     ← Platform statistics and counters
│   │   │   ├── VersionHistoryWidget.tsx ← Pipeline version list + diff viewer (265 lines)
│   │   │   └── StepDAG.tsx              ← Horizontal step dependency flow diagram
│   │   │
│   │   ├── lineage/                     ← React Flow lineage visualisation
│   │   │   ├── LineageGraph.tsx         ← Main graph component (251 lines)
│   │   │   ├── LineageSidebar.tsx       ← Column detail panel (ancestry + impact)
│   │   │   └── nodes/                   ← 4 custom ReactFlow node type components
│   │   │       ├── SourceFileNode.tsx   ← Renders source file nodes: file::{id}
│   │   │       ├── StepNode.tsx         ← Renders pipeline step nodes: step::{name}
│   │   │       ├── ColumnNode.tsx       ← Renders column nodes: col::{step}::{col}
│   │   │       └── OutputFileNode.tsx   ← Renders output nodes: output::{step}::{file}
│   │   │
│   │   ├── ui/                          ← shadcn/ui base components
│   │   │                                  NEVER MODIFY THESE FILES
│   │   │                                  Button, Input, Dialog, Toast, Badge, etc.
│   │   │
│   │   ├── ErrorBoundary.tsx            ← App-level React error boundary
│   │   └── theme/
│   │       ├── ThemeSelector.tsx        ← BUILT_IN_THEMES array (must have 7 entries)
│   │       └── ThemeBuilder.tsx         ← Custom theme creation with 28 CSS variables
│   │
│   ├── hooks/                           ← Custom React hooks
│   │   ├── usePipelineRun.ts            ← SSE connection + exponential backoff reconnect
│   │   │                                  1s→2s→4s→8s→16s, max 5 retries
│   │   │                                  Handles step_started, step_completed, pipeline_completed
│   │   ├── useKeybindings.ts            ← Global keyboard shortcut handler registration
│   │   ├── useLineage.ts                ← Lineage graph data fetching + caching
│   │   ├── useTheme.ts                  ← Theme application to CSS variables
│   │   ├── use-mobile.ts                ← Responsive breakpoint detection
│   │   └── useWidgetLayout.ts           ← Widget layout toggle helpers
│   │
│   ├── store/                           ← 4 Zustand stores (all persisted to localStorage)
│   │   ├── widgetStore.ts               ← Binary tree layout, 5 workspaces (225 lines)
│   │   │                                  Tracks: layout tree, active workspace, active widget
│   │   ├── pipelineStore.ts             ← Active run ID, YAML editor content, run data
│   │   ├── themeStore.ts                ← Active theme name, custom theme definitions
│   │   └── keybindingStore.ts           ← All 18 keyboard shortcut registrations
│   │
│   ├── lib/                             ← Shared client utilities
│   │   ├── api.ts                       ← fetchWithAuth() + all API endpoint functions (252 lines)
│   │   │                                  Single unified function (merged from fetchApi/fetchAuth v2.1.3)
│   │   │                                  Handles: auth header injection, 401 redirect, ApiError parsing
│   │   ├── auth-context.tsx             ← AuthProvider, useAuth hook
│   │   │                                  JWT stored in localStorage as 'pipelineiq_token'
│   │   │                                  Cookie 'piq_auth=1' for Next.js middleware
│   │   ├── types.ts                     ← TypeScript types for all API responses
│   │   │                                  Must be updated when backend/schemas.py changes
│   │   ├── constants.ts                 ← Widget IDs, API base URL, default configurations
│   │   └── utils.ts                     ← cn() classname merging helper
│   │
│   ├── __tests__/                       ← 93 frontend tests across 8 files
│   │   ├── setup.ts                     ← jest-dom matchers, cleanup, EventSource mock,
│   │   │                                  ResizeObserver mock, motion/react mock
│   │   ├── api.test.ts                  ← 26 tests: fetchWithAuth, API functions, errors
│   │   ├── stores.test.ts               ← 26 tests: all 4 Zustand stores
│   │   ├── pages.test.tsx               ← 12 tests: login/register forms
│   │   ├── widgets.test.tsx             ← 11 tests: QuickStats, FileUpload, RunHistory, FileRegistry
│   │   ├── utils.test.ts                ← 7 tests: cn() utility, constants
│   │   ├── middleware.test.ts           ← 4 tests: auth redirect logic
│   │   ├── auth-context.test.tsx        ← 4 tests: AuthProvider flows
│   │   └── hooks.test.ts                ← 3 tests: widget layout, workspace switching
│   │
│   ├── public/                          ← Static assets (fonts)
│   ├── middleware.ts                    ← Next.js auth redirect (checks piq_auth cookie)
│   ├── next.config.ts                   ← Next.js config + API proxy rewrites
│   │                                      /api/* → backend:8000
│   │                                      /auth/* → backend:8000
│   ├── tsconfig.json                    ← strict: true, @ alias → project root, ES2017 target
│   ├── vitest.config.ts                 ← Vitest + jsdom + @ alias + setup file
│   ├── eslint.config.mjs                ← ESLint 9 flat config
│   ├── postcss.config.mjs               ← PostCSS with Tailwind plugin
│   ├── package.json                     ← Dependencies, scripts: dev, build, test, lint, typecheck
│   └── Dockerfile                       ← Multi-stage: builder (npm ci + next build) → runner
│                                          Non-root user: nextjs
│                                          Output: standalone mode (node server.js)
│
├── nginx/
│   ├── Dockerfile                       ← FROM nginx:1.25-alpine, copies conf files
│   ├── nginx.conf                       ← Worker processes auto, keepalive 65s
│   └── conf.d/pipelineiq.conf           ← Main server block
│                                          Upstreams: api:8000, frontend:3000, flower:5555, grafana:3000
│                                          SSE path: proxy_buffering off, 3600s timeout
│                                          Security headers: X-Frame-Options, X-Content-Type-Options,
│                                            X-XSS-Protection, Referrer-Policy
│                                          /metrics: restricted to 10.x, 172.16.x, 192.168.x, 127.x
│
├── prometheus/
│   └── prometheus.yml                   ← Scrape config
│                                          job: pipelineiq-api → api:8000/metrics every 10s
│                                          job: prometheus → localhost:9090
│
├── grafana/
│   └── provisioning/
│       ├── dashboards/
│       │   ├── dashboard.yml            ← Dashboard provider (folder: PipelineIQ)
│       │   └── pipelineiq.json          ← 10-panel dashboard definition
│       └── datasources/
│           └── prometheus.yml           ← Prometheus datasource at http://prometheus:9090
│
├── .github/
│   ├── SECRETS_REQUIRED.md              ← Documents required GitHub secrets
│   └── workflows/
│       └── ci.yml                       ← 3-job CI pipeline (see Testing section)
│
└── postman/
    ├── PipelineIQ.postman_collection.json  ← 23 requests in 6 folders
    │                                          Auto-extracts: token, file_id, run_id
    └── PipelineIQ.postman_environment.json ← base_url, token, run_id, file_id, webhook_id
```

---

## 5. Data Model

All 14 ORM models are defined in `backend/models.py` (348 lines).
No model is defined anywhere else. Every model uses SQLAlchemy 2.0 `Mapped`/`mapped_column` syntax.
JSON columns use `PgJSONB` — a custom type that maps to JSONB on PostgreSQL and JSON on SQLite.

### Model 1: `User`

A registered platform account with role-based access.

```python
__tablename__ = "users"

id: Mapped[str]          = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
email: Mapped[str]       = mapped_column(String(255), unique=True, nullable=False, index=True)
username: Mapped[str]    = mapped_column(String(100), unique=True, nullable=False, index=True)
hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
role: Mapped[str]        = mapped_column(String(20), nullable=False, default="viewer")
                           # CHECK constraint: role IN ('admin', 'viewer')
is_active: Mapped[bool]  = mapped_column(Boolean, nullable=False, default=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                              onupdate=func.now())
```

**Relationships:** Has many `PipelineRun`, `Webhook`, `PipelineSchedule`,
`NotificationConfig`, `PipelinePermission`.

**Lifecycle:** Created on registration. First user to register automatically becomes
`admin` (if `users` table was empty at registration time). Persists indefinitely.
`is_active=False` prevents login without deletion.

**Sensitive fields:** `hashed_password` is a bcrypt hash — never log or expose.
`email` is PII — never log.

**Password requirements** (enforced at registration in `api/auth.py` validator):
- Minimum 8 characters
- At least one uppercase letter (A-Z)
- At least one digit (0-9)
- At least one special character

---

### Model 2: `UploadedFile`

A source dataset (CSV or JSON) available for pipeline input.

```python
__tablename__ = "uploaded_files"

id: Mapped[str]             = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
                                 # Display only — NEVER used as a filesystem path
stored_path: Mapped[str]    = mapped_column(String(512), nullable=False)
                              # Always a UUID-based path: {UPLOAD_DIR}/{uuid}.{ext}
file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
row_count: Mapped[int]       = mapped_column(Integer, nullable=False)
column_count: Mapped[int]    = mapped_column(Integer, nullable=False)
columns: Mapped[dict]        = mapped_column(PgJSONB, nullable=False)
                              # {"column_name": "dtype_string", ...}
dtypes: Mapped[dict]         = mapped_column(PgJSONB, nullable=False)
                              # {"column_name": "int64", "amount": "float64", ...}
version: Mapped[int]         = mapped_column(Integer, nullable=False, default=1)
previous_version_id: Mapped[Optional[str]] = mapped_column(
    Uuid, ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True)
upload_status: Mapped[str]   = mapped_column(String(20), nullable=False, default="completed")
error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
uploaded_by: Mapped[Optional[str]] = mapped_column(
    Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**Relationships:** Has many `SchemaSnapshot` (cascade delete on file delete).
Self-referential FK `previous_version_id` forms version chain.

**Lifecycle:** Created on upload; logically immutable after creation.
"Versioning" is implemented by creating a new record with incremented `version`
and a `previous_version_id` pointing to the previous record.

**File storage rule:** `stored_path` is always `{UPLOAD_DIR}/{uuid4()}.{ext}`.
`original_filename` is stored for display. Using `original_filename` as a
filesystem path is a path traversal vulnerability.

---

### Model 3: `PipelineRun`

A single execution of a user-defined pipeline YAML.

```python
__tablename__ = "pipeline_runs"

id: Mapped[str]         = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
name: Mapped[str]       = mapped_column(String(255), nullable=False)
status: Mapped[str]     = mapped_column(SQLEnum(PipelineStatus), nullable=False,
                                        default=PipelineStatus.PENDING, index=True)
yaml_config: Mapped[str] = mapped_column(Text, nullable=False)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                             server_default=func.now(), index=True)
started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
total_rows_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
total_rows_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
user_id: Mapped[Optional[str]] = mapped_column(
    Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
                 # Stored so cancellation can call celery_app.control.revoke(task_id)
```

**`PipelineStatus` enum values:**
`PENDING` → `RUNNING` → `COMPLETED` or `FAILED` or `CANCELLED`

**Computed property `duration_ms`:** `(completed_at - started_at).total_seconds() * 1000`
(returns None if either timestamp is None)

**Relationships:**
- Has many `StepResult` (cascade all, delete-orphan)
- Has one `LineageGraph` (cascade all, delete-orphan, unique constraint)
- Belongs to `User` (nullable — anonymous runs possible)

---

### Model 4: `StepResult`

The outcome of a single transformation step within a pipeline run.

```python
__tablename__ = "step_results"

id: Mapped[str]              = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
pipeline_run_id: Mapped[str] = mapped_column(
    Uuid, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False, index=True)
step_name: Mapped[str]   = mapped_column(String(255), nullable=False)
step_type: Mapped[str]   = mapped_column(String(50), nullable=False)
step_index: Mapped[int]  = mapped_column(Integer, nullable=False)
status: Mapped[str]      = mapped_column(String(20), nullable=False)
                           # "pending" | "running" | "completed" | "failed" | "skipped"
rows_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
rows_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
columns_in: Mapped[Optional[dict]] = mapped_column(PgJSONB, nullable=True)
                                     # {"col_name": "dtype", ...}
columns_out: Mapped[Optional[dict]] = mapped_column(PgJSONB, nullable=True)
                                      # {"col_name": "dtype", ...}
duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
warnings: Mapped[Optional[dict]] = mapped_column(PgJSONB, nullable=True)
                                   # Validation warnings from validate step
error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**Lifecycle:** Created by the Celery worker after each step completes or fails.
Multiple StepResult records per PipelineRun — one per step.

---

### Model 5: `LineageGraph`

The serialized column-level lineage trace for a completed pipeline run.

```python
__tablename__ = "lineage_graphs"

id: Mapped[str]              = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
pipeline_run_id: Mapped[str] = mapped_column(
    Uuid, ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
    nullable=False, unique=True)  # UNIQUE: one graph per run
graph_data: Mapped[dict]      = mapped_column(PgJSONB, nullable=False)
                                # NetworkX node-link format: {"directed": true, "nodes": [...], "links": [...]}
react_flow_data: Mapped[dict] = mapped_column(PgJSONB, nullable=False)
                                # React Flow format: {"nodes": [...], "edges": [...]}
                                # Pre-computed layout — never recomputed on API calls
created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**Critical design decision:** The React Flow layout is computed once during execution
and stored here permanently. It is never recomputed on subsequent API calls.

---

### Model 6: `SchemaSnapshot`

A point-in-time record of a file's column schema, used for drift detection.

```python
__tablename__ = "schema_snapshots"

id: Mapped[str]          = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
file_id: Mapped[str]     = mapped_column(
    Uuid, ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False, index=True)
run_id: Mapped[Optional[str]] = mapped_column(
    Uuid, ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True)
columns: Mapped[dict]    = mapped_column(PgJSONB, nullable=False)
                           # {"column_name": "dtype_string", ...}
dtypes: Mapped[dict]     = mapped_column(PgJSONB, nullable=False)
                           # Same as columns — kept separate for query clarity
row_count: Mapped[int]   = mapped_column(Integer, nullable=False)
captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**How drift detection uses this:** On upload, `schema_drift.py` fetches the most
recent snapshot for the same `original_filename`, compares column sets, and
classifies changes as `breaking` (removed), `warning` (type changed), or `info` (added).

---

### Model 7: `PipelineVersion`

A versioned snapshot of a pipeline's YAML configuration.

```python
__tablename__ = "pipeline_versions"

id: Mapped[str]              = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
pipeline_name: Mapped[str]   = mapped_column(String(255), nullable=False, index=True)
version_number: Mapped[int]  = mapped_column(Integer, nullable=False)
yaml_config: Mapped[str]     = mapped_column(Text, nullable=False)
run_id: Mapped[Optional[str]] = mapped_column(
    Uuid, ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True)
change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

# Unique constraint: (pipeline_name, version_number)
__table_args__ = (UniqueConstraint("pipeline_name", "version_number"),)
```

**Versioning behavior:** Each pipeline run auto-saves a version. `version_number`
auto-increments per `pipeline_name`. Diffs are computed on-demand by `versioning.py`
using Python `difflib` — not stored.

---

### Model 8: `Webhook`

An outbound webhook endpoint configuration for pipeline event notifications.

```python
__tablename__ = "webhooks"

id: Mapped[str]           = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
user_id: Mapped[str]      = mapped_column(
    Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
url: Mapped[str]          = mapped_column(String(2048), nullable=False)
                            # Validated: must start with http:// or https://
secret: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
                                # Raw secret stored — NEVER returned in API responses
                                # API returns has_secret: bool only
events: Mapped[list]      = mapped_column(PgJSONB, nullable=False,
                            default=["pipeline_completed", "pipeline_failed"])
is_active: Mapped[bool]   = mapped_column(Boolean, nullable=False, default=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**Per-user limit:** Maximum 10 webhooks per user. Enforced in the create endpoint.

---

### Model 9: `WebhookDelivery`

An individual outbound webhook delivery attempt record.

```python
__tablename__ = "webhook_deliveries"

id: Mapped[str]              = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
webhook_id: Mapped[str]      = mapped_column(
    Uuid, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
run_id: Mapped[Optional[str]] = mapped_column(
    Uuid, ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True)
event_type: Mapped[str]      = mapped_column(String(50), nullable=False)
payload: Mapped[dict]        = mapped_column(PgJSONB, nullable=False)
response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
response_body: Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
attempt_number: Mapped[int]            = mapped_column(Integer, nullable=False, default=1)
delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
failed_at: Mapped[Optional[datetime]]    = mapped_column(DateTime(timezone=True), nullable=True)
error_message: Mapped[Optional[str]]     = mapped_column(Text, nullable=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**Retry logic:** Maximum 3 attempts with increasing delays. Handled by `webhook_tasks.py`.

---

### Model 10: `AuditLog`

An immutable record of every significant action in the system.

```python
__tablename__ = "audit_logs"

id: Mapped[str]          = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
user_id: Mapped[Optional[str]] = mapped_column(
    Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
action: Mapped[str]      = mapped_column(String(100), nullable=False, index=True)
                           # Format: "resource.action" e.g. "pipeline.run", "file.upload"
resource_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
resource_id: Mapped[Optional[str]]   = mapped_column(Uuid, nullable=True)
details: Mapped[Optional[dict]]      = mapped_column(PgJSONB, nullable=True, default={})
ip_address: Mapped[Optional[str]]    = mapped_column(String(45), nullable=True)
user_agent: Mapped[Optional[str]]    = mapped_column(Text, nullable=True)
created_at: Mapped[datetime]         = mapped_column(DateTime(timezone=True),
                                      server_default=func.now(), index=True)
```

**IMMUTABILITY ENFORCED AT DATABASE LEVEL.**
Migration `f6a7b8c9d0e1` installs a PostgreSQL trigger that raises an exception
on any `UPDATE` or `DELETE` against this table. Records are permanently append-only.
Never attempt to modify or delete audit records — the database will reject it.

**Action strings used in this codebase:**
`file.upload`, `file.delete`, `pipeline.run`, `pipeline.cancel`, `pipeline.validate`,
`user.register`, `user.login`, `user.logout`, `role.change`, `webhook.create`,
`webhook.delete`, `webhook.test`, `schedule.create`, `schedule.delete`,
`permission.grant`, `permission.revoke`, `notification.create`, `notification.delete`

---

### Model 11: `PipelineSchedule`

A cron-based recurring pipeline execution configuration.

```python
__tablename__ = "pipeline_schedules"

id: Mapped[str]               = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
user_id: Mapped[str]          = mapped_column(
    Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
pipeline_name: Mapped[str]    = mapped_column(String(255), nullable=False)
yaml_config: Mapped[str]      = mapped_column(Text, nullable=False)
cron_expression: Mapped[str]  = mapped_column(String(100), nullable=False)
                                # Validated by croniter at creation time
is_active: Mapped[bool]       = mapped_column(Boolean, nullable=False, default=True)
last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
                                          # Computed from cron_expression + current time
run_count: Mapped[int]        = mapped_column(Integer, nullable=False, default=0)
created_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**Celery Beat integration:** `schedule_tasks.py` runs periodically, checks all
active schedules where `next_run_at <= now`, dispatches `execute_pipeline_task`,
and updates `last_run_at` and `next_run_at`.

---

### Model 12: `NotificationConfig`

A Slack or email notification destination for pipeline events.

```python
__tablename__ = "notification_configs"

id: Mapped[str]          = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
user_id: Mapped[str]     = mapped_column(
    Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
type: Mapped[str]        = mapped_column(SQLEnum("slack", "email"), nullable=False)
config: Mapped[dict]     = mapped_column(PgJSONB, nullable=False, default={})
                           # Slack: {"slack_webhook_url": "https://hooks.slack.com/..."}
                           # Email: {"email_to": "user@example.com"}
events: Mapped[list]     = mapped_column(PgJSONB, nullable=False,
                           default=["pipeline_completed", "pipeline_failed"])
is_active: Mapped[bool]  = mapped_column(Boolean, nullable=False, default=True)
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

---

### Model 13: `PipelinePermission`

Per-pipeline role-based access control entry.

```python
__tablename__ = "pipeline_permissions"

id: Mapped[str]                = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
pipeline_name: Mapped[str]     = mapped_column(String(255), nullable=False, index=True)
user_id: Mapped[str]           = mapped_column(
    Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
permission_level: Mapped[str]  = mapped_column(
    SQLEnum("owner", "runner", "viewer"), nullable=False)
created_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())

# Unique constraint: one permission entry per (pipeline_name, user_id)
__table_args__ = (UniqueConstraint("pipeline_name", "user_id"),)
```

**Permission semantics:**
- `owner` — can manage permissions, run, view, modify this pipeline name
- `runner` — can execute and view pipelines with this name
- `viewer` — can view run history and lineage for this pipeline name

`admin` role users bypass all permission checks via `get_current_admin` dependency.

---

### Model 14: `PipelineTemplate`

Pre-seeded read-only pipeline templates.

```python
# Not a database table — templates are pre-seeded in-memory or at startup
# The 5 templates are served from templates.py as static data:

TEMPLATES = [
    {
        "id": "basic-etl",
        "name": "Basic ETL",
        "description": "Load, filter, aggregate, and save",
        "category": "etl",
        "yaml_template": "..."
    },
    {
        "id": "data-cleaning",
        "name": "Data Cleaning",
        "description": "Validate, rename, and select columns",
        "category": "cleaning",
        "yaml_template": "..."
    },
    {
        "id": "data-validation",
        "name": "Data Validation",
        "description": "Run quality checks across all columns",
        "category": "validation",
        "yaml_template": "..."
    },
    {
        "id": "aggregation",
        "name": "Aggregation Report",
        "description": "Group by region and compute stats",
        "category": "aggregation",
        "yaml_template": "..."
    },
    {
        "id": "merge-join",
        "name": "Merge and Join",
        "description": "Join two datasets and enrich",
        "category": "join",
        "yaml_template": "..."
    }
]
```
---

## 6. Database Schema

Full column-level schema for every table. All primary keys are native PostgreSQL UUID.
All timestamps are `DateTime(timezone=True)` with `server_default=func.now()`.

### `users`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | Auto-generated via Python uuid4 |
| email | VARCHAR(255) | UNIQUE, NOT NULL, INDEX | RFC 5322 validated at registration |
| username | VARCHAR(100) | UNIQUE, NOT NULL, INDEX | 3-50 chars, alphanumeric + underscore |
| hashed_password | VARCHAR(255) | NOT NULL | bcrypt hash via passlib |
| role | VARCHAR(20) | NOT NULL, DEFAULT 'viewer' | CHECK: 'admin' or 'viewer' |
| is_active | BOOLEAN | NOT NULL, DEFAULT true | False prevents login |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |
| updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | Triggers on update |

### `uploaded_files`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| original_filename | VARCHAR(255) | NOT NULL | Display only, never used in paths |
| stored_path | VARCHAR(512) | NOT NULL | Always UUID-based: {UPLOAD_DIR}/{uuid}.{ext} |
| file_size_bytes | INTEGER | NOT NULL | Bytes |
| row_count | INTEGER | NOT NULL | Detected during upload |
| column_count | INTEGER | NOT NULL | Detected during upload |
| columns | JSONB | NOT NULL | {"col_name": "dtype"} |
| dtypes | JSONB | NOT NULL | Same structure as columns |
| version | INTEGER | NOT NULL, DEFAULT 1 | Increments per original_filename |
| previous_version_id | UUID | FK(uploaded_files.id) SET NULL, NULL | Self-ref for version chain |
| upload_status | VARCHAR(20) | NOT NULL, DEFAULT 'completed' | 'processing', 'completed', 'failed' |
| error_message | TEXT | NULL | Set if upload fails |
| uploaded_by | UUID | FK(users.id) SET NULL, NULL | |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

### `pipeline_runs`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| name | VARCHAR(255) | NOT NULL | Display name |
| status | ENUM | NOT NULL, DEFAULT 'PENDING', INDEX | PENDING/RUNNING/COMPLETED/FAILED/CANCELLED |
| yaml_config | TEXT | NOT NULL | Full YAML stored verbatim |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now(), INDEX | |
| started_at | TIMESTAMPTZ | NULL | Set when worker picks up task |
| completed_at | TIMESTAMPTZ | NULL | Set when run finishes |
| total_rows_in | INTEGER | NULL | Sum of all load step rows |
| total_rows_out | INTEGER | NULL | Rows in final save step |
| error_message | TEXT | NULL | First error that caused failure |
| user_id | UUID | FK(users.id) SET NULL, NULL, INDEX | |
| celery_task_id | VARCHAR(255) | NULL | For task revocation on cancel |

**Indexes added in migration `a1b2c3d4e5f6`:**
- `pipeline_runs.status` (for filtering by status)
- `pipeline_runs.created_at` (for time-based ordering)
- `pipeline_runs.user_id` (for user-scoped queries)

### `step_results`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| pipeline_run_id | UUID | FK(pipeline_runs.id) CASCADE, NOT NULL, INDEX | |
| step_name | VARCHAR(255) | NOT NULL | From YAML step name |
| step_type | VARCHAR(50) | NOT NULL | load/filter/select/etc. |
| step_index | INTEGER | NOT NULL | 0-based position in pipeline |
| status | VARCHAR(20) | NOT NULL | pending/running/completed/failed/skipped |
| rows_in | INTEGER | NULL | Rows entering this step |
| rows_out | INTEGER | NULL | Rows exiting this step |
| columns_in | JSONB | NULL | {"col": "dtype"} entering |
| columns_out | JSONB | NULL | {"col": "dtype"} exiting |
| duration_ms | INTEGER | NULL | Execution time milliseconds |
| warnings | JSONB | NULL | Validation step warnings |
| error_message | TEXT | NULL | Error if step failed |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Index added in migration `a1b2c3d4e5f6`:**
- `step_results.pipeline_run_id`

### `lineage_graphs`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| pipeline_run_id | UUID | FK(pipeline_runs.id) CASCADE, NOT NULL, UNIQUE | One per run |
| graph_data | JSONB | NOT NULL | NetworkX node-link serialization |
| react_flow_data | JSONB | NOT NULL | Pre-computed {nodes, edges} layout |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

### `schema_snapshots`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| file_id | UUID | FK(uploaded_files.id) CASCADE, NOT NULL, INDEX | |
| run_id | UUID | FK(pipeline_runs.id) SET NULL, NULL | Which run triggered snapshot |
| columns | JSONB | NOT NULL | Column names and dtypes |
| dtypes | JSONB | NOT NULL | Same |
| row_count | INTEGER | NOT NULL | Row count at snapshot time |
| captured_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

### `pipeline_versions`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| pipeline_name | VARCHAR(255) | NOT NULL, INDEX | Groups versions by name |
| version_number | INTEGER | NOT NULL | Auto-increments per name |
| yaml_config | TEXT | NOT NULL | Full YAML at this version |
| run_id | UUID | FK(pipeline_runs.id) SET NULL, NULL | Run that triggered this version |
| change_summary | TEXT | NULL | Human-readable change description |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |
| UNIQUE | | (pipeline_name, version_number) | |

### `webhooks`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| user_id | UUID | FK(users.id) CASCADE, NOT NULL | |
| url | VARCHAR(2048) | NOT NULL | Validated: http:// or https:// prefix |
| secret | VARCHAR(255) | NULL | For HMAC signing — never returned in API |
| events | JSONB | NOT NULL, DEFAULT ["pipeline_completed","pipeline_failed"] | |
| is_active | BOOLEAN | NOT NULL, DEFAULT true | |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

### `webhook_deliveries`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| webhook_id | UUID | FK(webhooks.id) CASCADE, NOT NULL, INDEX | |
| run_id | UUID | FK(pipeline_runs.id) SET NULL, NULL | |
| event_type | VARCHAR(50) | NOT NULL | e.g. "pipeline_completed" |
| payload | JSONB | NOT NULL | Full event payload |
| response_status | INTEGER | NULL | HTTP response code |
| response_body | TEXT | NULL | Response body (truncated) |
| attempt_number | INTEGER | NOT NULL, DEFAULT 1 | 1-3 (max retries) |
| delivered_at | TIMESTAMPTZ | NULL | Set on success |
| failed_at | TIMESTAMPTZ | NULL | Set on final failure |
| error_message | TEXT | NULL | Network error message |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Index added in migration `a1b2c3d4e5f6`:**
- `webhook_deliveries.webhook_id`

### `audit_logs`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK, IMMUTABLE TRIGGER | Cannot be updated or deleted |
| user_id | UUID | FK(users.id) SET NULL, NULL, INDEX | |
| action | VARCHAR(100) | NOT NULL, INDEX | "resource.action" format |
| resource_type | VARCHAR(50) | NULL | e.g. "pipeline_run", "uploaded_file" |
| resource_id | UUID | NULL | ID of affected resource |
| details | JSONB | NULL, DEFAULT {} | Additional context |
| ip_address | VARCHAR(45) | NULL | Supports IPv6 |
| user_agent | TEXT | NULL | Full user agent string |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now(), INDEX | |

**Composite index added in migration `a1b2c3d4e5f6`:**
- `audit_logs.(user_id, created_at)` — for user-scoped time-ordered queries

### `pipeline_schedules`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| user_id | UUID | FK(users.id) CASCADE, NOT NULL | |
| pipeline_name | VARCHAR(255) | NOT NULL | For display and grouping |
| yaml_config | TEXT | NOT NULL | YAML to execute |
| cron_expression | VARCHAR(100) | NOT NULL | Validated by croniter |
| is_active | BOOLEAN | NOT NULL, DEFAULT true | |
| last_run_at | TIMESTAMPTZ | NULL | Updated after each execution |
| next_run_at | TIMESTAMPTZ | NULL | Computed from cron + now |
| run_count | INTEGER | NOT NULL, DEFAULT 0 | Total successful executions |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

### `notification_configs`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| user_id | UUID | FK(users.id) CASCADE, NOT NULL | |
| type | ENUM | NOT NULL | "slack" or "email" |
| config | JSONB | NOT NULL, DEFAULT {} | Channel-specific config |
| events | JSONB | NOT NULL, DEFAULT [...] | Subscribed event types |
| is_active | BOOLEAN | NOT NULL, DEFAULT true | |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

### `pipeline_permissions`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | UUID | PK | |
| pipeline_name | VARCHAR(255) | NOT NULL, INDEX | |
| user_id | UUID | FK(users.id) CASCADE, NOT NULL | |
| permission_level | ENUM | NOT NULL | "owner", "runner", or "viewer" |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |
| UNIQUE | | (pipeline_name, user_id) | One permission per user per pipeline |

---

## 7. Alembic Migration History

All 8 revisions in chronological order. Each must have a working `downgrade()` function.

### Revision 1: `97385cb62e0a` — Initial schema

**What it created:**
- `pipeline_runs` table with VARCHAR(36) IDs, JSON columns (not JSONB)
- `uploaded_files` table
- `lineage_graphs` table
- `step_results` table
- `pipeline_versions` table
- `schema_snapshots` table

**Notes:** All IDs were VARCHAR(36) strings, not native UUID. JSON columns
used TEXT-level JSON, not PostgreSQL JSONB. Some FK constraints missing.

---

### Revision 2: `14a9b359a361` — Schema snapshots and pipeline versions

**What it changed:** Added missing constraints and columns around schema snapshot
and pipeline version tracking. Placeholder for schema-level additions.

---

### Revision 3: `c3f5e7a8b901` — CRITICAL: Native PostgreSQL types

**What it changed:**
- Migrated all VARCHAR(36) ID columns to native PostgreSQL UUID type
- Migrated all JSON columns to JSONB (binary, indexed, queryable)
- Dropped and restored all FK constraints under new UUID types
- Added unique constraints that require UUID columns

**CRITICAL WARNING:** This is a destructive migration.
- Cannot be run on SQLite (UUID type is PostgreSQL-specific)
- Tests use SQLite — this migration does not run in the test suite
- Requires `downgrade()` to reverse all UUID and JSONB column changes

---

### Revision 4: `d4e6f8a1b2c3` — Users table and RBAC

**What it created:**
- `users` table with email, username, hashed_password, role, is_active
- `user_id` FK column on `pipeline_runs` (with SET NULL on user delete)
- CHECK constraint on `users.role` (must be 'admin' or 'viewer')
- Indexes on `users.email` and `users.username`

---

### Revision 5: `e5f6a7b8c9d0` — Webhooks tables

**What it created:**
- `webhooks` table with url, secret (nullable), events (JSONB), is_active
- `webhook_deliveries` table with full delivery tracking columns
- CASCADE delete from webhooks → webhook_deliveries

---

### Revision 6: `f6a7b8c9d0e1` — Audit logs with immutability trigger

**What it created:**
- `audit_logs` table with all audit tracking columns
- PostgreSQL trigger function that raises an exception on any UPDATE or DELETE
- Trigger attached to the `audit_logs` table

**The immutability trigger code (conceptual):**
```sql
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit logs are immutable — UPDATE and DELETE are not permitted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_logs_immutable
BEFORE UPDATE OR DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
```

This trigger is enforced at the database level independently of application code.
Even if application code contains a bug that tries to update or delete an audit
record, the database will reject it.

---

### Revision 7: `a1b2c3d4e5f6` — Performance indexes

**What it added:**
- `pipeline_runs.status` index (for filtering by status)
- `pipeline_runs.created_at` index (for time-based sorting)
- `pipeline_runs.user_id` index (for user-scoped queries)
- `step_results.pipeline_run_id` index (for loading steps by run)
- `webhook_deliveries.webhook_id` index (for delivery history queries)
- `audit_logs.(user_id, created_at)` composite index (for user audit queries)

**Impact:** This was the performance optimization migration from the codebase audit.
Before this migration, dashboard queries and run history queries were full table scans.

---

### Revision 8: `b2c3d4e5f6a7` — Feature expansion

**What it added:**
- `CANCELLED` status value to the `PipelineStatus` enum
- `pipeline_schedules` table (cron-based scheduling)
- `notification_configs` table (Slack/email notifications)
- `pipeline_permissions` table (per-pipeline RBAC)
- `version` column on `uploaded_files` (default 1)
- `previous_version_id` FK column on `uploaded_files` (self-referential)

**Migration commands:**
```bash
alembic upgrade head              # Apply all pending migrations
alembic downgrade -1              # Roll back one migration
alembic current                   # Show current revision
alembic history                   # Show all revisions in order
alembic revision --autogenerate -m "description"  # Create new migration
```

---

## 8. API Reference

All routes are prefixed `/api/v1/` and registered in `backend/api/router.py`.
Auth routes use `/auth/` prefix registered directly in `main.py`.
Every endpoint has rate limiting. Every write endpoint audits to `audit_logs`.

### Router 1: Auth (`backend/api/auth.py` + `backend/auth.py`)

---

#### `POST /auth/register`

**Purpose:** Create a new user account. First user becomes admin automatically.
**Authentication:** None required
**Rate limit:** 5 requests/minute per IP
**Request body:**
```json
{
  "email": "user@example.com",
  "username": "myusername",
  "password": "MyStr0ng@Pass"
}
```
**Validation rules:**
- `email`: must match RFC 5322 pattern, max 255 chars
- `username`: 3-50 characters, alphanumeric and underscores only
- `password`: minimum 8 chars, must contain: 1 uppercase letter, 1 digit, 1 special character
**Response:** `201 Created`
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "username": "myusername",
  "role": "viewer",
  "is_active": true,
  "created_at": "2026-02-01T12:00:00Z"
}
```
**Error responses:**
- `400` — email or username already exists
- `422` — validation failed (password too weak, invalid email, username too short)
**Side effects:** Creates `User` record, logs `user.register` audit entry

---

#### `POST /auth/login`

**Purpose:** Authenticate and return a JWT access token.
**Authentication:** None required
**Rate limit:** 5 requests/minute per IP
**Request body:**
```json
{
  "email": "user@example.com",
  "password": "MyStr0ng@Pass"
}
```
**Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "username": "myusername",
    "role": "admin"
  }
}
```
**Error responses:**
- `401` — invalid credentials or account disabled
**Side effects:** Updates `User.last_login`, logs `user.login` audit entry

---

#### `GET /auth/me`

**Purpose:** Get the current authenticated user's profile.
**Authentication:** Required (Bearer JWT)
**Response:** `200 OK` — User object (same shape as register response)
**Error responses:**
- `401` — invalid or expired token

---

#### `POST /auth/logout`

**Purpose:** Logout (client-side token invalidation — no server-side token store).
**Authentication:** Required (Bearer JWT)
**Response:** `200 OK`
```json
{"message": "Logged out successfully"}
```
**Side effects:** Logs `user.logout` audit entry. Token remains technically valid
until expiry (24h) — there is no token revocation list.

---

#### `GET /auth/users`

**Purpose:** List all registered users.
**Authentication:** Required (Bearer JWT, admin role only)
**Response:** `200 OK` — Array of user objects
**Error responses:**
- `403` — user is not admin

---

#### `PATCH /auth/users/{user_id}/role`

**Purpose:** Change a user's role.
**Authentication:** Required (Bearer JWT, admin role only)
**Request body:**
```json
{"role": "admin"}
```
Valid values: `"admin"` or `"viewer"`
**Response:** `200 OK` — Updated user object
**Side effects:** Logs `role.change` audit entry

---

### Router 2: Files (`backend/api/files.py`) — 458 lines

---

#### `POST /api/v1/files/upload`

**Purpose:** Upload a CSV or JSON data file.
**Authentication:** Required (Bearer JWT)
**Rate limit:** 30 requests/minute per user
**Content-Type:** `multipart/form-data`
**Max size:** `MAX_UPLOAD_SIZE` = 52,428,800 bytes (50MB)
**Max rows:** `MAX_ROWS_PER_FILE` = 1,000,000

**Processing sequence:**
1. Validate file extension (must be `.csv` or `.json`)
2. Generate UUID-based stored filename: `{uuid4()}.{ext}`
3. Save file to `{UPLOAD_DIR}/{stored_filename}`
4. Read with Pandas to detect dtypes and count rows
5. Check if `original_filename` exists in DB → if yes, increment version
6. Create `UploadedFile` record
7. Create `SchemaSnapshot` record
8. Detect schema drift against previous snapshot
9. Log audit entry

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "original_filename": "sales_data.csv",
  "stored_path": "/app/uploads/a1b2c3d4-...-uuid.csv",
  "row_count": 50000,
  "column_count": 8,
  "columns": {
    "order_id": "int64",
    "customer_id": "object",
    "amount": "float64",
    "status": "object",
    "region": "object",
    "date": "object",
    "product_id": "int64",
    "discount": "float64"
  },
  "dtypes": {
    "order_id": "int64",
    "amount": "float64"
  },
  "file_size_bytes": 3145728,
  "version": 1,
  "schema_drift": null
}
```

If schema drift detected (file was re-uploaded):
```json
{
  "schema_drift": {
    "has_changes": true,
    "changes": [
      {
        "column": "customer_id",
        "change_type": "removed",
        "severity": "breaking"
      },
      {
        "column": "discount_pct",
        "change_type": "added",
        "severity": "info"
      },
      {
        "column": "amount",
        "change_type": "type_changed",
        "old_type": "float64",
        "new_type": "object",
        "severity": "warning"
      }
    ]
  }
}
```

**Error responses:**
- `400` — unsupported file extension
- `413` — file exceeds MAX_UPLOAD_SIZE
- `422` — file could not be parsed as CSV/JSON

---

#### `GET /api/v1/files/`

**Purpose:** List all uploaded files.
**Authentication:** None (publicly accessible — known security gap for multi-tenant)
**Rate limit:** 120/minute
**Query params:**
- `page` (integer, default 1, min 1)
- `limit` (integer, default 20, max 100)
**Response:** `200 OK`
```json
{
  "files": [
    {
      "id": "uuid",
      "original_filename": "sales_data.csv",
      "row_count": 50000,
      "column_count": 8,
      "file_size_bytes": 3145728,
      "version": 2,
      "created_at": "2026-02-01T12:00:00Z"
    }
  ],
  "total": 10
}
```

---

#### `GET /api/v1/files/{file_id}`

**Purpose:** Get metadata for a specific uploaded file.
**Authentication:** None (publicly accessible)
**Rate limit:** 120/minute
**Response:** `200 OK` — Full `UploadedFile` object
**Error responses:**
- `404` — file not found
- `422` — invalid UUID format

---

#### `DELETE /api/v1/files/{file_id}`

**Purpose:** Delete an uploaded file (disk + database record).
**Authentication:** Required (Bearer JWT)
**Processing:** Removes disk file at `stored_path`, deletes DB record (cascades to `SchemaSnapshot`)
**Response:** `204 No Content`
**Side effects:** Logs `file.delete` audit entry

---

#### `GET /api/v1/files/{file_id}/preview`

**Purpose:** Preview the first N rows of a file without loading the full file.
**Authentication:** None (publicly accessible)
**Rate limit:** 120/minute
**Query params:**
- `rows` (integer, default 20, max 100)
**Implementation:** `pd.read_csv(file_path, nrows=rows)` — never loads full file
**Response:** `200 OK`
```json
{
  "columns": ["order_id", "amount", "status"],
  "rows": [
    [1001, 150.0, "delivered"],
    [1002, 89.99, "pending"]
  ],
  "total_rows_in_file": 50000
}
```

---

#### `GET /api/v1/files/{file_id}/schema/history`

**Purpose:** Get all schema snapshots for a file (point-in-time schema history).
**Authentication:** None (publicly accessible)
**Response:** Array of snapshots ordered by `captured_at` descending
```json
[
  {
    "id": "uuid",
    "columns": {"order_id": "int64", "amount": "float64"},
    "row_count": 50000,
    "captured_at": "2026-02-15T10:00:00Z"
  }
]
```

---

#### `GET /api/v1/files/{file_id}/schema/diff`

**Purpose:** Compare the two most recent schema snapshots for drift.
**Authentication:** None (publicly accessible)
**Response:**
```json
{
  "has_changes": true,
  "previous_snapshot_at": "2026-02-01T12:00:00Z",
  "current_snapshot_at": "2026-02-15T10:00:00Z",
  "changes": [
    {
      "column": "customer_id",
      "change_type": "removed",
      "severity": "breaking",
      "old_type": "object",
      "new_type": null
    }
  ]
}
```

---

### Router 3: Pipelines (`backend/api/pipelines.py`) — 421 lines

---

#### `POST /api/v1/pipelines/validate`

**Purpose:** Validate a pipeline YAML without executing it. Returns all errors at once.
**Authentication:** Required (Bearer JWT)
**Rate limit:** 60/minute
**Request body:**
```json
{
  "yaml_config": "pipeline:\n  name: test\n  steps:\n    - ..."
}
```
Minimum length: 10 characters.
**Response (valid):** `200 OK`
```json
{
  "is_valid": true,
  "errors": [],
  "warnings": [
    {
      "field": "steps",
      "message": "No save step found — pipeline output will not be exported"
    }
  ]
}
```
**Response (invalid):**
```json
{
  "is_valid": false,
  "errors": [
    {
      "step_name": "filter_data",
      "field": "operator",
      "message": "Invalid operator 'equls'. Did you mean: 'equals'?",
      "suggestion": "equals"
    },
    {
      "step_name": "aggregate_step",
      "field": "input",
      "message": "Step 'load_dta' not found. Did you mean: 'load_data'?",
      "suggestion": "load_data"
    }
  ],
  "warnings": []
}
```
**Side effects:** None (read-only)

---

#### `POST /api/v1/pipelines/plan`

**Purpose:** Generate a dry-run execution plan with row count and duration estimates.
**Authentication:** Required (Bearer JWT)
**Rate limit:** 60/minute
**Request body:** Same as validate
**Response:** `200 OK`
```json
{
  "pipeline_name": "sales_report",
  "total_steps": 5,
  "estimated_total_duration_ms": 250,
  "files_read": ["uuid-of-sales-file"],
  "files_written": ["quarterly_report.csv"],
  "estimated_rows_processed": 35000,
  "will_succeed": true,
  "warnings": [],
  "steps": [
    {
      "step_index": 0,
      "step_name": "load_sales",
      "step_type": "load",
      "estimated_rows_in": 50000,
      "estimated_rows_out": 50000,
      "estimated_columns": ["order_id", "amount", "status", "region"],
      "estimated_duration_ms": 50,
      "will_fail": false
    },
    {
      "step_index": 1,
      "step_name": "delivered_only",
      "step_type": "filter",
      "estimated_rows_in": 50000,
      "estimated_rows_out": 35000,
      "estimated_duration_ms": 30,
      "will_fail": false
    },
    {
      "step_index": 2,
      "step_name": "by_region",
      "step_type": "aggregate",
      "estimated_rows_in": 35000,
      "estimated_rows_out": 5,
      "estimated_duration_ms": 40,
      "will_fail": false
    },
    {
      "step_index": 3,
      "step_name": "sorted",
      "step_type": "sort",
      "estimated_rows_in": 5,
      "estimated_rows_out": 5,
      "estimated_duration_ms": 10,
      "will_fail": false
    },
    {
      "step_index": 4,
      "step_name": "save_report",
      "step_type": "save",
      "estimated_rows_in": 5,
      "estimated_rows_out": 5,
      "estimated_duration_ms": 20,
      "will_fail": false
    }
  ]
}
```

---

#### `POST /api/v1/pipelines/run`

**Purpose:** Execute a pipeline asynchronously via Celery.
**Authentication:** Required (Bearer JWT)
**Rate limit:** 10/minute per user
**Authorisation:** User must have `runner` or `owner` permission for the pipeline name,
OR user is `admin`.
**Request body:**
```json
{
  "yaml_config": "pipeline:\n  name: my_pipeline\n  steps:\n    ...",
  "name": "Q1 Sales Report"
}
```
`name` is optional (max 255 chars). Defaults to pipeline name from YAML if not provided.
**Response:** `202 Accepted`
```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING"
}
```
**Side effects:**
1. Creates `PipelineRun` record with status `PENDING`
2. Calls `execute_pipeline_task.delay(run_id)`
3. Logs `pipeline.run` audit entry

---

#### `GET /api/v1/pipelines/`

**Purpose:** List pipeline runs with pagination and status filter.
**Authentication:** Required (Bearer JWT) — Note: currently returns all users' runs
(known open issue — should be user-scoped)
**Rate limit:** 120/minute
**Query params:**
- `page` (integer, default 1)
- `limit` (integer, default 20, max 100)
- `status_filter` (optional): `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`
**Response:** `200 OK`
```json
{
  "runs": [
    {
      "id": "uuid",
      "name": "Q1 Sales Report",
      "status": "COMPLETED",
      "created_at": "2026-02-01T12:00:00Z",
      "completed_at": "2026-02-01T12:01:30Z",
      "total_rows_in": 50000,
      "total_rows_out": 5,
      "duration_ms": 90000
    }
  ],
  "total": 42
}
```

---

#### `GET /api/v1/pipelines/stats`

**Purpose:** Aggregate pipeline execution statistics.
**Authentication:** Required (Bearer JWT)
**Rate limit:** 120/minute
**Caching:** Redis, 30-second TTL
**Response:** `200 OK`
```json
{
  "total_runs": 42,
  "completed": 35,
  "failed": 5,
  "pending": 1,
  "running": 1,
  "cancelled": 0,
  "success_rate": 87.5,
  "total_files": 10
}
```

---

#### `GET /api/v1/pipelines/{run_id}`

**Purpose:** Get full details of a specific pipeline run including all step results.
**Authentication:** Required (Bearer JWT)
**Rate limit:** 120/minute
**Response:** `200 OK`
```json
{
  "id": "uuid",
  "name": "Q1 Sales Report",
  "status": "COMPLETED",
  "yaml_config": "pipeline:\n  name: ...",
  "created_at": "2026-02-01T12:00:00Z",
  "started_at": "2026-02-01T12:00:02Z",
  "completed_at": "2026-02-01T12:01:32Z",
  "total_rows_in": 50000,
  "total_rows_out": 5,
  "duration_ms": 90000,
  "step_results": [
    {
      "step_name": "load_sales",
      "step_type": "load",
      "step_index": 0,
      "status": "completed",
      "rows_in": 0,
      "rows_out": 50000,
      "columns_out": {"order_id": "int64", "amount": "float64"},
      "duration_ms": 320
    }
  ]
}
```
**Error responses:**
- `404` — run not found
- `422` — invalid UUID format

---

#### `GET /api/v1/pipelines/{run_id}/stream`

**Purpose:** Server-Sent Events endpoint for real-time pipeline execution progress.
**Authentication:** Optional (supports anonymous viewing for shared runs)
**Content-Type:** `text/event-stream`

**SSE event types:**
```
event: step_started
data: {"step_name": "load_sales", "step_index": 0, "total_steps": 5}

event: step_completed
data: {"step_name": "load_sales", "step_index": 0, "rows_in": 0, "rows_out": 50000, "duration_ms": 320, "status": "completed"}

event: step_failed
data: {"step_name": "filter_data", "step_index": 1, "error": "Column 'statuss' not found. Did you mean: 'status'?"}

event: pipeline_completed
data: {"status": "COMPLETED", "total_duration_ms": 90000, "total_rows_out": 5}

event: pipeline_failed
data: {"status": "FAILED", "error": "Column 'statuss' not found. Did you mean: 'status'?"}
```

**Redis pub/sub channel:** `pipeline_progress:{run_id}`
**Nginx config for SSE passthrough:**
```
proxy_buffering off;
proxy_cache off;
chunked_transfer_encoding on;
proxy_read_timeout 3600s;
```
**Keepalive:** Comment lines sent every 500ms to prevent proxy timeout:
```
: keepalive

```
**Reconnection:** Frontend `usePipelineRun` hook implements exponential backoff:
1s → 2s → 4s → 8s → 16s with maximum 5 retries.

---

#### `POST /api/v1/pipelines/{run_id}/cancel`

**Purpose:** Cancel a running pipeline.
**Authentication:** Required (Bearer JWT)
**Processing:**
1. Loads `PipelineRun` record, verifies status is `PENDING` or `RUNNING`
2. Calls `celery_app.control.revoke(run.celery_task_id, terminate=True)`
3. Updates `PipelineRun.status = CANCELLED`
4. Logs audit entry
**Response:** `200 OK`
```json
{"run_id": "uuid", "status": "CANCELLED"}
```
**Error responses:**
- `404` — run not found
- `409` — run is already COMPLETED or FAILED (cannot cancel)

---

#### `POST /api/v1/pipelines/preview`

**Purpose:** Preview sample data at a specific step in the pipeline without full execution.
**Authentication:** Required (Bearer JWT)
**Rate limit:** 60/minute
**Query params:** `step_index` (integer, default 0)
**Response:** Preview data for the specified step

---

#### `GET /api/v1/pipelines/{run_id}/export`

**Purpose:** Download the output file from a completed pipeline run.
**Authentication:** Required (Bearer JWT)
**Processing:** Finds the output file written by the `save` step, returns as file download
**Response:** File download (CSV or JSON, as produced by the save step)
**Error responses:**
- `400` — run is not in COMPLETED status
- `404` — no output file found for this run

---

### Router 4: Lineage (`backend/api/lineage.py`)

All lineage endpoints are cached in Redis with 1-hour TTL.

---

#### `GET /api/v1/lineage/{run_id}`

**Purpose:** Get the pre-computed React Flow graph for a pipeline run.
**Authentication:** None (publicly accessible)
**Rate limit:** 120/minute
**Caching:** Redis, 1-hour TTL
**Response:** `200 OK`
```json
{
  "run_id": "uuid",
  "nodes": [
    {
      "id": "file::abc-123",
      "type": "sourceFile",
      "position": {"x": 0, "y": 0},
      "data": {"label": "sales_data.csv", "file_id": "abc-123"}
    },
    {
      "id": "step::load_sales",
      "type": "stepNode",
      "position": {"x": 300, "y": 0},
      "data": {"label": "load_sales", "step_type": "load"}
    },
    {
      "id": "col::load_sales::amount",
      "type": "columnNode",
      "position": {"x": 600, "y": 80},
      "data": {"label": "amount", "step": "load_sales", "dtype": "float64"}
    }
  ],
  "edges": [
    {
      "id": "file::abc-123->step::load_sales",
      "source": "file::abc-123",
      "target": "step::load_sales",
      "type": "default"
    },
    {
      "id": "col::load_sales::amount->col::filter::amount",
      "source": "col::load_sales::amount",
      "target": "col::filter::amount",
      "type": "smoothstep",
      "data": {"transformation": "filter"}
    }
  ]
}
```

---

#### `GET /api/v1/lineage/{run_id}/column`

**Purpose:** Trace a specific column backward to its source file and column.
**Authentication:** None (publicly accessible)
**Rate limit:** 120/minute
**Query params:**
- `step` (required) — step name containing the column
- `column` (required) — column name to trace
**Response:** `200 OK`
```json
{
  "column": "amount_sum",
  "step": "by_region",
  "source_file": "sales_data.csv",
  "source_column": "amount",
  "transformation_chain": [
    {"step": "load_sales", "step_type": "load", "column": "amount"},
    {"step": "delivered_only", "step_type": "filter", "column": "amount"},
    {"step": "by_region", "step_type": "aggregate", "column": "amount_sum"}
  ]
}
```

---

#### `GET /api/v1/lineage/{run_id}/impact`

**Purpose:** Forward impact analysis — what outputs does a column affect?
**Authentication:** None (publicly accessible)
**Rate limit:** 120/minute
**Query params:**
- `step` (required) — step name containing the source column
- `column` (required) — column name to analyse
**Response:** `200 OK`
```json
{
  "source_column": "amount",
  "source_step": "load_sales",
  "affected_steps": ["delivered_only", "by_region", "sorted", "save_report"],
  "affected_output_columns": ["amount_sum"],
  "affected_output_files": ["quarterly_report.csv"]
}
```

---

### Router 5: Versions (`backend/api/versions.py`)

---

#### `GET /api/v1/versions/{pipeline_name}`

**Purpose:** List all saved versions of a pipeline by name.
**Authentication:** None (publicly accessible)
**Response:** Array of version objects ordered by `version_number` descending
```json
[
  {
    "id": "uuid",
    "pipeline_name": "sales_report",
    "version_number": 3,
    "change_summary": "Added region filter step",
    "created_at": "2026-02-15T10:00:00Z"
  }
]
```

---

#### `GET /api/v1/versions/{pipeline_name}/{version_number}`

**Purpose:** Get a specific pipeline version with full YAML.
**Authentication:** None (publicly accessible)
**Response:** Version object including `yaml_config` field

---

#### `GET /api/v1/versions/{pipeline_name}/diff/{version_a}/{version_b}`

**Purpose:** Compute a diff between two pipeline versions.
**Authentication:** None (publicly accessible)
**Response:** `200 OK`
```json
{
  "pipeline_name": "sales_report",
  "version_a": 1,
  "version_b": 3,
  "steps_added": ["region_filter"],
  "steps_removed": [],
  "steps_modified": [
    {
      "step_name": "delivered_only",
      "field_changed": "value",
      "old_value": "completed",
      "new_value": "delivered"
    }
  ],
  "unified_diff": "--- version_1\n+++ version_3\n@@ -5,3 +5,4 @@\n ..."
}
```

---

#### `POST /api/v1/versions/{pipeline_name}/restore/{version_number}`

**Purpose:** Restore a pipeline to a previous version (creates a new version record).
**Authentication:** None (publicly accessible — known gap)
**Response:** The restored YAML config as a new version object
**Side effects:** Creates a new `PipelineVersion` record (does not overwrite existing)

---

### Router 6: Webhooks (`backend/api/webhooks.py`)

---

#### `POST /api/v1/webhooks/`

**Purpose:** Register a new webhook endpoint.
**Authentication:** Required (Bearer JWT)
**Validation:** URL must start with `http://` or `https://`. Max 10 webhooks per user.
**Request body:**
```json
{
  "url": "https://myserver.com/pipeline-events",
  "secret": "optional-hmac-secret-string",
  "events": ["pipeline_completed", "pipeline_failed"]
}
```
Valid event types: `pipeline_completed`, `pipeline_failed`, `pipeline_started`,
`pipeline_cancelled`
**Response:** `201 Created`
```json
{
  "id": "uuid",
  "url": "https://myserver.com/pipeline-events",
  "has_secret": true,
  "events": ["pipeline_completed", "pipeline_failed"],
  "is_active": true,
  "created_at": "2026-02-01T12:00:00Z"
}
```
Note: `has_secret: bool` — raw secret NEVER returned. This was fixed in v2.1.3.
**Side effects:** Logs `webhook.create` audit entry

---

#### `GET /api/v1/webhooks/`

**Purpose:** List the current user's webhooks.
**Authentication:** Required (Bearer JWT) — returns own webhooks only
**Response:** Array of webhook objects

---

#### `DELETE /api/v1/webhooks/{webhook_id}`

**Purpose:** Delete a webhook.
**Authentication:** Required (Bearer JWT) — own webhooks only
**Error responses:**
- `403` — webhook belongs to a different user
- `404` — webhook not found
**Side effects:** Cascades to delete all `WebhookDelivery` records, logs `webhook.delete`

---

#### `GET /api/v1/webhooks/{webhook_id}/deliveries`

**Purpose:** List delivery history for a webhook.
**Authentication:** Required (Bearer JWT) — own webhooks only
**Response:** Array of up to 50 most recent delivery records

---

#### `POST /api/v1/webhooks/{webhook_id}/test`

**Purpose:** Send a test delivery to verify the endpoint.
**Authentication:** Required (Bearer JWT)
**Processing:** Sends a test payload signed with HMAC-SHA256 (if secret configured)
**Response:** `200 OK`
```json
{"delivered": true, "response_status": 200}
```
**Error responses:**
- `502` — delivery failed (target server unreachable or returned error)

---

### Router 7: Audit (`backend/api/audit.py`)

---

#### `GET /api/v1/audit/logs`

**Purpose:** Get all audit log records.
**Authentication:** Required (Bearer JWT, admin role only)
**Rate limit:** 120/minute
**Query params:**
- `page` (default 1), `limit` (default 50, max 100)
- `action` (filter by action string)
- `user_id` (filter by user)
**Response:** Array of audit log entries + total count
**Error responses:**
- `403` — user is not admin

---

#### `GET /api/v1/audit/logs/mine`

**Purpose:** Get the authenticated user's audit log entries.
**Authentication:** Required (Bearer JWT)
**Query params:** `page`, `limit`
**Response:** Array of audit log entries for current user only

---

### Router 8: Schedules (`backend/api/schedules.py`)

---

#### `POST /api/v1/schedules/`

**Purpose:** Create a new cron-based recurring pipeline schedule.
**Authentication:** Required (Bearer JWT)
**Request body:**
```json
{
  "pipeline_name": "daily_etl",
  "yaml_config": "pipeline:\n  name: daily_etl\n  steps:\n    ...",
  "cron_expression": "0 2 * * *"
}
```
Cron expression validated by `croniter` library at creation time.
`next_run_at` is computed and stored immediately.
**Response:** `201 Created`
```json
{
  "id": "uuid",
  "pipeline_name": "daily_etl",
  "cron_expression": "0 2 * * *",
  "is_active": true,
  "next_run_at": "2026-02-02T02:00:00Z",
  "last_run_at": null,
  "run_count": 0
}
```
**Side effects:** Logs `schedule.create` audit entry

---

#### `GET /api/v1/schedules/`

**Purpose:** List the current user's pipeline schedules.
**Authentication:** Required (Bearer JWT) — own schedules only
**Response:** Array of schedule objects

---

#### `PATCH /api/v1/schedules/{schedule_id}/toggle`

**Purpose:** Enable or disable a pipeline schedule.
**Authentication:** Required (Bearer JWT)
**Processing:** Flips `is_active`, recomputes `next_run_at` if enabling
**Response:** Updated schedule object

---

#### `DELETE /api/v1/schedules/{schedule_id}`

**Purpose:** Delete a pipeline schedule.
**Authentication:** Required (Bearer JWT) — own schedules only
**Side effects:** Logs `schedule.delete` audit entry

---

### Router 9: Templates (`backend/api/templates.py`)

---

#### `GET /api/v1/templates/`

**Purpose:** List all 5 pre-built pipeline templates.
**Authentication:** None (publicly accessible)
**Response:** Array of template objects (without full YAML)
```json
[
  {
    "id": "basic-etl",
    "name": "Basic ETL",
    "description": "Load, filter, aggregate, and save",
    "category": "etl"
  }
]
```

---

#### `GET /api/v1/templates/{template_id}`

**Purpose:** Get a specific template with full YAML.
**Authentication:** None (publicly accessible)
**Response:** Template object including `yaml_template` field
**Error responses:**
- `404` — template ID not found

---

### Router 10: Notifications (`backend/api/notifications.py`)

---

#### `POST /api/v1/notifications/`

**Purpose:** Create a Slack or email notification configuration.
**Authentication:** Required (Bearer JWT)
**Request body (Slack):**
```json
{
  "type": "slack",
  "config": {"slack_webhook_url": "https://hooks.slack.com/services/T.../B.../..."},
  "events": ["pipeline_completed", "pipeline_failed"]
}
```
**Request body (email):**
```json
{
  "type": "email",
  "config": {"email_to": "analyst@example.com"},
  "events": ["pipeline_failed"]
}
```
**Validation:** Slack requires `slack_webhook_url`. Email requires `email_to`.
**Response:** `201 Created` — Config object
**Side effects:** Logs `notification.create` audit entry

---

#### `GET /api/v1/notifications/`

**Purpose:** List the current user's notification configurations.
**Authentication:** Required (Bearer JWT) — own configs only

---

#### `DELETE /api/v1/notifications/{config_id}`

**Purpose:** Delete a notification configuration.
**Authentication:** Required (Bearer JWT) — own configs only
**Side effects:** Logs `notification.delete` audit entry

---

#### `POST /api/v1/notifications/{config_id}/test`

**Purpose:** Send a test notification to verify the configuration.
**Authentication:** Required (Bearer JWT)
**Response:** `200 OK`
```json
{"detail": "Test notification sent successfully"}
```
**Error responses:**
- `502` — delivery failed (Slack rejected, SMTP error, etc.)

---

### Router 11: Dashboard (`backend/api/dashboard.py`)

---

#### `GET /api/v1/dashboard/stats`

**Purpose:** Get personalised analytics for the authenticated user.
**Authentication:** Required (Bearer JWT)
**Rate limit:** 120/minute
**Caching:** Redis, 30-second TTL
**Response:** `200 OK`
```json
{
  "total_runs": 42,
  "completed": 35,
  "failed": 5,
  "pending": 1,
  "running": 1,
  "cancelled": 0,
  "success_rate": 87.5,
  "total_files": 10,
  "pipelines_by_status": {
    "COMPLETED": 35,
    "FAILED": 5,
    "PENDING": 1,
    "RUNNING": 1,
    "CANCELLED": 0
  },
  "most_used_pipelines": [
    {"name": "daily_etl", "run_count": 15},
    {"name": "sales_report", "run_count": 12}
  ],
  "recent_activity": [
    {
      "run_id": "uuid",
      "name": "Q1 Report",
      "status": "COMPLETED",
      "created_at": "2026-02-15T10:00:00Z"
    }
  ]
}
```

---

### Router 12: Permissions (`backend/api/permissions.py`)

---

#### `POST /api/v1/pipelines/{pipeline_name}/permissions`

**Purpose:** Grant a user access to a specific pipeline name.
**Authentication:** Required (Bearer JWT, `owner` permission or admin)
**Request body:**
```json
{
  "user_id": "uuid-of-user-to-grant",
  "permission_level": "runner"
}
```
Valid values: `"owner"`, `"runner"`, `"viewer"`
**Processing:** Upserts — updates existing permission if already exists
**Response:** `201 Created` — Permission object
**Side effects:** Logs `permission.grant` audit entry

---

#### `GET /api/v1/pipelines/{pipeline_name}/permissions`

**Purpose:** List all permission entries for a pipeline name.
**Authentication:** Required (Bearer JWT)
**Response:** Array of permission objects

---

#### `DELETE /api/v1/pipelines/{pipeline_name}/permissions/{user_id}`

**Purpose:** Revoke a user's permission for a pipeline name.
**Authentication:** Required (Bearer JWT, `owner` permission or admin)
**Side effects:** Logs `permission.revoke` audit entry

---

### Router 13: Debug (`backend/api/debug.py`)

**Only active when `ENVIRONMENT != "production"`. Never reaches production.**

---

#### `GET /api/v1/debug/sentry-test`

**Purpose:** Trigger a test exception to verify Sentry integration.
**Authentication:** None
**Processing:** Raises `ValueError("Sentry test error")` to be captured by Sentry
**Response:** Raises exception (no success response)

---

#### `GET /api/v1/debug/config`

**Purpose:** Return non-sensitive configuration values for debugging.
**Authentication:** None
**Response:** Subset of settings (excludes SECRET_KEY, passwords, DSNs)

---

### System endpoints (registered directly in `main.py`)

---

#### `GET /health`

**Purpose:** Verify system readiness.
**Authentication:** None
**Processing:**
1. Executes `SELECT 1` against PostgreSQL
2. Pings Redis (`redis.ping()`)
3. Verifies `UPLOAD_DIR` is writable

**Response (healthy):** `200 OK`
```json
{
  "status": "ok",
  "version": "2.1.4",
  "db": "ok",
  "redis": "ok"
}
```
**Response (degraded):** `503 Service Unavailable`
```json
{
  "status": "degraded",
  "version": "2.1.4",
  "db": "error",
  "redis": "ok"
}
```

---

#### `GET /metrics`

**Purpose:** Prometheus metrics in text format.
**Authentication:** None (but restricted by Nginx to internal networks)
**Nginx restriction:** Only accessible from `10.x`, `172.16.x`, `192.168.x`, `127.x`
**Response:** Prometheus text format with custom metrics + HTTP metrics

---

## 9. Authentication and Authorisation

### JWT mechanism

- **Library:** `python-jose[cryptography]` (v3.3.0)
- **Algorithm:** HS256 (HMAC-SHA256)
- **Signing key:** `settings.SECRET_KEY` — must be 32+ characters minimum
- **Token lifetime:** `settings.ACCESS_TOKEN_EXPIRE_MINUTES` (default 1440 = 24 hours)
- **Token payload:**
  ```json
  {
    "sub": "user-uuid",
    "role": "admin",
    "exp": 1738428000
  }
  ```
- **Storage (frontend):** `localStorage` key `pipelineiq_token` — acknowledged XSS risk
- **Cookie:** `piq_auth=1` (SameSite=Strict) — used by Next.js middleware for routing
- **No refresh tokens:** Users must re-authenticate when token expires

### FastAPI dependency chain

```
oauth2_scheme: OAuth2PasswordBearer
    ↓ extracts "Bearer {token}" from Authorization header

get_current_user(token, db):
    1. jose.jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
       → Raises 401 if expired, malformed, or invalid signature
    2. Extract user_id from payload["sub"]
    3. db.get(User, user_id)
       → Raises 401 if user not found
    4. Check user.is_active
       → Raises 401 if disabled
    5. Returns User object

get_current_admin(current_user):
    → Raises 403 if current_user.role != "admin"

get_optional_user(token=None, db):
    → Returns None if no token provided
    → Returns User if valid token
    → Used for endpoints that support both authenticated and anonymous access
```

### Role access matrix

| Operation | No Auth | VIEWER | RUNNER/OWNER | ADMIN |
|---|---|---|---|---|
| Register / Login | ✓ | ✓ | ✓ | ✓ |
| View public runs/lineage | ✓ | ✓ | ✓ | ✓ |
| Upload files | ✗ | ✓ | ✓ | ✓ |
| View own audit log | ✗ | ✓ | ✓ | ✓ |
| Create webhooks | ✗ | ✓ | ✓ | ✓ |
| Create schedules | ✗ | ✓ | ✓ | ✓ |
| Run pipeline | ✗ | ✗ | ✓ (if permitted) | ✓ |
| Cancel pipeline | ✗ | ✗ | ✓ (own runs) | ✓ |
| Grant permissions | ✗ | ✗ | ✓ (if owner) | ✓ |
| View all audit logs | ✗ | ✗ | ✗ | ✓ |
| Manage all users | ✗ | ✗ | ✗ | ✓ |
| Change roles | ✗ | ✗ | ✗ | ✓ |

### Known auth gaps

- **Public read endpoints:** `GET /files/`, `GET /pipelines/`, `GET /lineage/*`,
  `GET /versions/*` are publicly accessible. In a multi-user deployment, any user
  can see any other user's data. Acceptable for single-tenant deployments.
- **No token revocation:** Logout is client-side only. Token remains technically
  valid at the server until expiry.
- **localStorage vulnerability:** JWT in localStorage can be stolen by XSS.
  Mitigated by SameSite=Strict cookie, but not eliminated.

---

## 10. Pipeline Engine — Complete Deep Reference

### YAML specification — complete contract

Every pipeline is a YAML document conforming to this structure:

```yaml
pipeline:
  name: "pipeline_display_name"       # Required. Display name only — not unique.
  description: "optional"             # Optional. Human description.
  steps:
    - name: step_name                 # Required. Unique within pipeline.
                                      # Pattern: [a-zA-Z0-9_] only
      type: load                      # Required. Must be one of the 9 valid step types.
      # ... step-type-specific fields
```

### All 9 step types — complete specification

---

#### Step type: `load`

Reads a CSV or JSON file into a Pandas DataFrame.

**YAML schema:**
```yaml
- name: load_sales           # Required, unique
  type: load
  file_id: "uuid-string"     # Required. UUID of an UploadedFile in the database.
                             # Validated by parser: must exist and be registered.
  alias: my_dataset          # Optional. Alternative name for this dataset.
                             # If provided, other steps can reference by alias.
```

**Pandas operation:**
- CSV: `pd.read_csv(stored_path, dtype=detected_dtypes)`
- JSON: `pd.read_json(stored_path)`

**Lineage recording:** Adds a `file::{file_id}` source node and `col::{step_name}::{col}`
nodes for every column in the file. Edges: `file::{file_id} → step::{step_name}` and
`step::{step_name} → col::{step_name}::{col}` for each column.

**Step result fields:**
- `rows_in`: 0 (no input rows — this is the source)
- `rows_out`: total rows in file
- `columns_out`: all columns with detected dtypes

**Planner estimate:** Row count from `UploadedFile.row_count` in database.

---

#### Step type: `filter`

Keeps rows where a column matches a condition. Does not modify column structure.

**YAML schema:**
```yaml
- name: delivered_orders     # Required, unique
  type: filter
  input: load_sales           # Required. Name of a previous step's output.
  column: status              # Required. Column to apply condition to.
                              # Validated: must exist in input DataFrame.
  operator: equals            # Required. One of the 12 valid operators.
  value: "delivered"          # Required for most operators. Type is coerced.
                              # Not required for: is_null, is_not_null.
```

**All 12 filter operators and their Pandas implementations:**

| Operator | Condition | Pandas |
|---|---|---|
| `equals` | column == value | `df[col] == value` |
| `not_equals` | column != value | `df[col] != value` |
| `greater_than` | column > value | `df[col] > value` |
| `greater_than_or_equal` | column >= value | `df[col] >= value` |
| `less_than` | column < value | `df[col] < value` |
| `less_than_or_equal` | column <= value | `df[col] <= value` |
| `contains` | string contains value | `df[col].str.contains(value, na=False)` |
| `not_contains` | string does not contain | `~df[col].str.contains(value, na=False)` |
| `starts_with` | string starts with | `df[col].str.startswith(value, na=False)` |
| `ends_with` | string ends with | `df[col].str.endswith(value, na=False)` |
| `is_null` | value is NaN/None | `df[col].isna()` |
| `is_not_null` | value is not NaN/None | `df[col].notna()` |

**Note:** `gte` is an alias for `greater_than_or_equal`, `lte` for `less_than_or_equal`.
Check `validators.py` for the current operator set if extending.

**Lineage recording:** Filter is a passthrough — input columns become output columns.
Each input column gets an edge: `col::{prev_step}::{col} → col::{this_step}::{col}`
with `transformation: "filter"`.

**Step result fields:**
- `rows_in`: count before filter
- `rows_out`: count after filter (may be 0 — does not fail)
- `columns_out`: same as `columns_in`

**Planner estimate:** 70% of input rows retained (configurable heuristic).

---

#### Step type: `select`

Projects a subset of columns. Drops all columns not listed.

**YAML schema:**
```yaml
- name: slim_dataset
  type: select
  input: load_sales           # Required. Previous step name.
  columns:                    # Required. List of column names to keep.
    - order_id
    - amount
    - status
```

**Validation:** All listed columns must exist in the input DataFrame. `ColumnNotFoundError`
is raised with fuzzy suggestion if a column name is misspelled.

**Pandas operation:** `df[columns_list]`

**Lineage recording:**
- Kept columns: `col::{prev}::{col} → col::{this}::{col}` with `transformation: "select"`
- Dropped columns: `col::{prev}::{col} → step::{this}` dead-end edge (column eliminated)

**Step result fields:**
- `rows_in`: same as input
- `rows_out`: same as input (row count unchanged)
- `columns_out`: only the selected columns

---

#### Step type: `rename`

Renames columns via a mapping dictionary.

**YAML schema:**
```yaml
- name: renamed_cols
  type: rename
  input: slim_dataset
  mapping:                    # Required. Dict of old_name: new_name pairs.
    order_id: id
    amount: sale_amount
```

**Validation:** All keys in mapping must exist in the input DataFrame.

**Pandas operation:** `df.rename(columns=mapping)`

**Lineage recording:** `col::{prev}::{old_name} → col::{this}::{new_name}`
with `transformation: "rename"`. Unmapped columns pass through unchanged.

**Step result fields:**
- `rows_in`: same as input
- `rows_out`: same as input
- `columns_out`: columns with new names applied

---

#### Step type: `join`

Merges two DataFrames on a common key column.

**YAML schema:**
```yaml
- name: enriched_orders
  type: join
  left: delivered_orders      # Required. Name of the left DataFrame step.
  right: load_customers        # Required. Name of the right DataFrame step.
  on: customer_id              # Required. Column name to join on.
                              # Must exist in both left and right DataFrames.
  how: left                   # Required. One of: inner, left, right, outer.
```

**All 4 join types:**

| Type | Behavior |
|---|---|
| `inner` | Only rows where key exists in both DataFrames |
| `left` | All left rows; right data filled with NaN where no match |
| `right` | All right rows; left data filled with NaN where no match |
| `outer` | All rows from both; NaN where no match on either side |

**Pandas operation:** `pd.merge(left_df, right_df, on=on_key, how=how, suffixes=("_left", "_right"))`

Column name conflicts (same column name in both DataFrames except the join key)
get suffixes `_left` and `_right` automatically.

**Lineage recording:**
- Left columns: `col::{left_step}::{col} → col::{this}::{col}` with `transformation: "join"`
- Right columns: `col::{right_step}::{col} → col::{this}::{col}` with `transformation: "join"`
- Join key edges get additional attribute `is_join_key: True`

**Planner estimates:**
- `inner`: `min(left_rows, right_rows)` (conservative)
- `left`: `left_rows` (100% retention on left side)
- `right`: `right_rows` (100% retention on right side)
- `outer`: `left_rows + right_rows` (conservative maximum)

---

#### Step type: `aggregate`

Groups rows by one or more columns and computes aggregate statistics.

**YAML schema:**
```yaml
- name: by_region
  type: aggregate
  input: delivered_orders
  group_by:                   # Required. List of columns to group by.
    - region
    - quarter
  aggregations:               # Required. List of aggregation definitions.
    - column: amount          # Column to aggregate.
      function: sum           # One of the 10 valid aggregate functions.
    - column: order_id
      function: count
    - column: amount
      function: mean
```

**All 10 aggregate functions:**

| Function | Description | Pandas |
|---|---|---|
| `sum` | Sum all values | `.sum()` |
| `mean` | Arithmetic mean | `.mean()` |
| `min` | Minimum value | `.min()` |
| `max` | Maximum value | `.max()` |
| `count` | Count non-null values | `.count()` |
| `median` | Median value | `.median()` |
| `std` | Standard deviation | `.std()` |
| `var` | Variance | `.var()` |
| `first` | First value in group | `.first()` |
| `last` | Last value in group | `.last()` |

**Output column naming convention:**
After `groupby().agg()`, Pandas produces multi-level column tuples like
`("amount", "sum")`. The executor flattens these to `{column}_{function}` strings:
- `("amount", "sum")` → `amount_sum`
- `("order_id", "count")` → `order_id_count`

**Special case:** If `column == function` (rare edge case), keeps just the column name.

**Pandas operation:**
```python
agg_dict = {col: func for col, func in aggregations}
result = df.groupby(group_by)[agg_cols].agg(agg_dict).reset_index()
# Flatten multi-level columns
result.columns = [f"{col}_{func}" if col != func else col
                  for col, func in result.columns]
```

**Lineage recording:**
- Group-by columns: direct passthrough edges
- Aggregated columns: `col::{prev}::{source_col} → col::{this}::{agg_col_name}`
  with `transformation: "aggregate"`, `function: "sum"` etc.
- This creates new column nodes (e.g., `col::by_region::amount_sum`) that
  are separate from the source column node (`col::load_sales::amount`)

**Planner estimate:** 10% of input rows (groups collapse rows significantly).

---

#### Step type: `sort`

Orders rows by a column in ascending or descending order.

**YAML schema:**
```yaml
- name: ranked
  type: sort
  input: by_region
  by: amount_sum              # Required. Column name to sort by.
                              # Must exist in input DataFrame.
  order: desc                 # Required. "asc" or "desc".
```

**Pandas operation:** `df.sort_values(by=by_col, ascending=(order == "asc"))`

**Lineage recording:** All columns pass through unchanged. Every input column
gets `col::{prev}::{col} → col::{this}::{col}` with `transformation: "sort"`.

**Step result fields:**
- `rows_in`: same as input
- `rows_out`: same as input (row count unchanged)

**Planner estimate:** 100% of input rows retained.

---

#### Step type: `validate`

Runs data quality checks on the DataFrame without stopping the pipeline.
Failed checks with `severity: error` are recorded as warnings but do not
halt execution. This is intentionally non-blocking.

**YAML schema:**
```yaml
- name: quality_checks
  type: validate
  input: loaded_data
  rules:
    - check: not_null           # Required. One of the 12 check types.
      column: customer_id       # Required for column-level checks.
      severity: error           # Required. "error" or "warning".
    - check: between
      column: amount
      value: [0, 10000]         # Required for between. [min, max].
      severity: warning
    - check: matches_pattern
      column: email
      value: "^[\\w.-]+@[\\w.-]+\\.\\w+$"  # Required for pattern. Regex string.
      severity: error
    - check: min_rows
      value: 100                # Required for row-count checks. No column needed.
      severity: error
```

**All 12 validation check types:**

| Check | Description | Required `value` | `column` needed |
|---|---|---|---|
| `not_null` | No null/NaN values in column | None | Yes |
| `not_empty` | No empty strings in column | None | Yes |
| `greater_than` | All values > threshold | Number | Yes |
| `less_than` | All values < threshold | Number | Yes |
| `between` | All values within [min, max] | [min, max] list | Yes |
| `in_values` | All values in allowed set | List of values | Yes |
| `matches_pattern` | All values match regex | Regex string | Yes |
| `no_duplicates` | No duplicate values in column | None | Yes |
| `min_rows` | DataFrame has at least N rows | Integer | No |
| `max_rows` | DataFrame has at most N rows | Integer | No |
| `positive` | All values > 0 | None | Yes |
| `date_format` | All values parseable as date | Format string | Yes |

**Each check result reports:**
- `check_type`: the check name
- `column`: column checked (if applicable)
- `passed`: boolean
- `severity`: "error" or "warning"
- `failing_count`: number of rows that failed
- `total_count`: total rows checked
- `failing_examples`: up to 3 examples of failing values

**Regex patterns** are validated with `try/re.compile()` at parse time.
Invalid regex patterns raise `PipelineConfigError` before execution begins.

**Lineage recording:** All columns pass through unchanged (validate is a passthrough).
Warnings are stored in `StepResult.warnings` JSONB field.

**Planner estimate:** 100% of input rows retained.

---

#### Step type: `save`

Marks the pipeline output for export. Writes the DataFrame to disk and
records output metadata. Does not modify the DataFrame.

**YAML schema:**
```yaml
- name: save_report
  type: save
  input: sorted
  filename: quarterly_report  # Required. Output filename (without extension).
                              # File will be: {UPLOAD_DIR}/{filename}_{run_id}.csv
```

**Pandas operation:**
```python
output_path = f"{settings.UPLOAD_DIR}/{filename}_{run_id}.csv"
df.to_csv(output_path, index=False)
```

**Lineage recording:**
- Every input column gets: `col::{prev}::{col} → step::{this}` edge
- Output file node created: `output::{step_name}::{filename}`
- Edges: `step::{this} → output::{this}::{filename}`

**ARCHITECTURAL NOTE:** The `save` step writes directly to disk from within
`StepExecutor`. This bypasses the database layer for actual data. Only metadata
(file path, row count, column info) is stored in `StepResult`. This is a known,
intentional architectural leak documented in AUDIT_REPORT.md.

**Planner estimate:** 100% of input rows written.

---

### Parser — complete validation rules

**Entry point:** `parse_pipeline_config(yaml_string: str, registered_file_ids: set[str]) -> PipelineConfig`

**Parser behaviour:** Collect ALL errors before returning — not fail-fast. This lets
users fix all YAML problems in one iteration rather than discovering them one by one.

**All 13 validation rules:**

1. **Valid YAML syntax** — `yaml.safe_load()` must succeed without exception
2. **Required `pipeline.name` key** — non-empty string required
3. **At least one step** — `pipeline.steps` must contain at least 1 step
4. **Step count limit** — number of steps ≤ `MAX_PIPELINE_STEPS` (default 50)
5. **Unique step names** — no two steps may share the same `name`
6. **Valid step name format** — step names match `[a-zA-Z0-9_]` pattern only
7. **Valid step types** — `type` must be one of the 9 valid step types
   (`load`, `filter`, `select`, `rename`, `join`, `aggregate`, `sort`, `validate`, `save`)
8. **Valid input references** — `input` (and `left`/`right` for joins) must reference
   either a `load` step's `alias` or the `name` of an earlier step in the list
9. **No forward references** — a step can only reference steps that appear before
   it in the YAML (no cycles possible with forward-reference prohibition)
10. **Registered file IDs** — `file_id` in `load` steps must exist in
    `registered_file_ids` set (passed from the database at validation time)
11. **Valid operators** — `operator` in `filter` steps must be one of the 12
    valid operators
12. **Valid join types** — `how` in `join` steps must be one of: `inner`, `left`,
    `right`, `outer`
13. **Valid aggregate functions** — `function` in aggregate `aggregations` must be
    one of: `sum`, `mean`, `min`, `max`, `count`, `median`, `std`, `var`, `first`, `last`

**Fuzzy suggestions:** When rule 7, 8, 10, 11, 12, or 13 fails with a likely typo,
the parser calls `difflib.get_close_matches(target, valid_options, n=1, cutoff=0.6)`.
If a match is found, the error message includes `"Did you mean: '{suggestion}'?"`.

**Dataclass hierarchy returned by parser:**

```python
@dataclass
class PipelineConfig:
    name: str
    description: Optional[str]
    steps: List[StepConfig]

@dataclass
class LoadStepConfig(StepConfig):
    type: Literal["load"] = "load"
    file_id: str
    alias: Optional[str] = None

@dataclass
class FilterStepConfig(StepConfig):
    type: Literal["filter"] = "filter"
    input: str
    column: str
    operator: str      # Validated against 12 operators
    value: Any         # Type coerced at execution time

@dataclass
class SelectStepConfig(StepConfig):
    type: Literal["select"] = "select"
    input: str
    columns: List[str]

@dataclass
class RenameStepConfig(StepConfig):
    type: Literal["rename"] = "rename"
    input: str
    mapping: Dict[str, str]

@dataclass
class JoinStepConfig(StepConfig):
    type: Literal["join"] = "join"
    left: str
    right: str
    on: str
    how: str    # Validated: inner/left/right/outer

@dataclass
class AggregateStepConfig(StepConfig):
    type: Literal["aggregate"] = "aggregate"
    input: str
    group_by: List[str]
    aggregations: List[AggregationSpec]

@dataclass
class SortStepConfig(StepConfig):
    type: Literal["sort"] = "sort"
    input: str
    by: str
    order: str    # "asc" or "desc"

@dataclass
class ValidateStepConfig(StepConfig):
    type: Literal["validate"] = "validate"
    input: str
    rules: List[ValidationRule]

@dataclass
class SaveStepConfig(StepConfig):
    type: Literal["save"] = "save"
    input: str
    filename: str
```

---

### StepExecutor — dispatch dict pattern

`backend/pipeline/steps.py` (606 lines)

```python
class StepExecutor:
    def __init__(self):
        self._dispatch: Dict[str, Callable] = {
            "load":      self._execute_load,
            "filter":    self._execute_filter,
            "select":    self._execute_select,
            "rename":    self._execute_rename,
            "join":      self._execute_join,
            "aggregate": self._execute_aggregate,
            "sort":      self._execute_sort,
            "validate":  self._execute_validate,
            "save":      self._execute_save,
        }

    def execute(self, step: StepConfig, df_registry: Dict[str, pd.DataFrame],
                run_id: str, lineage_recorder: LineageRecorder) -> StepExecutionResult:
        handler = self._dispatch.get(step.type)
        if handler is None:
            raise InvalidStepTypeError(step.type, list(self._dispatch.keys()))
        return handler(step, df_registry, run_id, lineage_recorder)
```

**Why dispatch dict and not if/elif:** Adding a new step type requires only:
1. Adding one entry to `_dispatch`
2. Implementing the `_execute_{type}` method
3. There is no growing conditional chain to maintain

**Every execution method signature:**
```python
def _execute_{type}(
    self,
    step: {Type}StepConfig,
    df_registry: Dict[str, pd.DataFrame],
    run_id: str,
    lineage_recorder: LineageRecorder
) -> StepExecutionResult:
```

Returns `StepExecutionResult(rows_in, rows_out, columns_in, columns_out, duration_ms, warnings)`.

---

### PipelineRunner — dependency inversion

`backend/pipeline/runner.py`

```python
ProgressCallback = Callable[[StepProgressEvent], None]

class PipelineRunner:
    def __init__(self, config: PipelineConfig, progress_callback: ProgressCallback):
        self.config = config
        self.progress_callback = progress_callback
        self.df_registry: Dict[str, pd.DataFrame] = {}
        self.lineage_recorder = LineageRecorder()
        self.step_executor = StepExecutor()

    def execute(self) -> PipelineExecutionResult:
        for i, step in enumerate(self.config.steps):
            self.progress_callback(StepProgressEvent(
                step_name=step.name, step_index=i,
                total_steps=len(self.config.steps), status="running"
            ))
            try:
                result = self.step_executor.execute(
                    step, self.df_registry, run_id=..., lineage_recorder=self.lineage_recorder
                )
                self.df_registry[step.name] = result.output_df
                self.progress_callback(StepProgressEvent(
                    step_name=step.name, status="completed",
                    rows_in=result.rows_in, rows_out=result.rows_out,
                    duration_ms=result.duration_ms
                ))
            except StepExecutionError as e:
                self.progress_callback(StepProgressEvent(
                    step_name=step.name, status="failed", error=str(e)
                ))
                raise
```

**The callback abstraction:**
- In Celery task: `lambda event: redis.publish(f"pipeline_progress:{run_id}", event.json())`
- In tests: `lambda event: recorded_events.append(event)`
- In dry-run mode: `lambda event: None` (no-op)

**Zero infrastructure imports** in `runner.py`, `steps.py`, or `lineage.py`.
These three files can be unit-tested with pure in-memory DataFrames and a
no-op callback.

---

### LineageRecorder — per-step recording methods

`backend/pipeline/lineage.py` (634 lines)

```python
class LineageRecorder:
    def __init__(self):
        self.graph = nx.DiGraph()

    # Called by StepExecutor for each step type:
    def record_load_step(self, step_name: str, file_id: str,
                          columns: List[str]) -> None: ...

    def record_filter_step(self, step_name: str, input_step: str,
                           columns: List[str], condition: dict) -> None: ...

    def record_select_step(self, step_name: str, input_step: str,
                           kept_columns: List[str],
                           dropped_columns: List[str]) -> None: ...

    def record_rename_step(self, step_name: str, input_step: str,
                           mapping: Dict[str, str],
                           unchanged_columns: List[str]) -> None: ...

    def record_join_step(self, step_name: str, left_step: str, right_step: str,
                         join_key: str, how: str,
                         output_columns: List[str]) -> None: ...

    def record_aggregate_step(self, step_name: str, input_step: str,
                               group_by: List[str],
                               agg_mapping: Dict[str, str]) -> None: ...

    def record_sort_step(self, step_name: str, input_step: str,
                          columns: List[str]) -> None: ...

    def record_validate_step(self, step_name: str, input_step: str,
                              columns: List[str]) -> None: ...

    def record_save_step(self, step_name: str, input_step: str,
                          filename: str, columns: List[str]) -> None: ...

    def to_react_flow(self) -> Dict:
        """Compute Sugiyama-inspired layout and return React Flow nodes + edges."""
        ...

    def to_networkx_json(self) -> Dict:
        """Serialize graph as NetworkX node-link format."""
        return nx.node_link_data(self.graph)

    def get_column_ancestry(self, step_name: str, column_name: str) -> Dict:
        """nx.ancestors() backward traversal."""
        ...

    def get_impact_analysis(self, step_name: str, column_name: str) -> Dict:
        """nx.descendants() forward traversal."""
        ...
```

**React Flow layout algorithm (Sugiyama-inspired):**
1. Topological sort all nodes using `nx.topological_sort(self.graph)`
2. Assign each node to a layer: `layer[node] = max(layer[pred] for pred in predecessors) + 1`
3. Group nodes by layer
4. Position within each layer by index
5. `x_pos = layer_index * 300`  (300px horizontal spacing between layers)
6. `y_pos = index_in_layer * 80`  (80px vertical spacing within a layer)

---

### Planner — dry-run estimation

`backend/pipeline/planner.py` (212 lines)

**Entry point:** `plan_execution(config: PipelineConfig, registered_files: Dict) -> ExecutionPlan`

**Heuristics by step type:**

| Step type | Row estimate | Duration estimate |
|---|---|---|
| `load` | `UploadedFile.row_count` from DB | `max(50, rows // 10000)` ms |
| `filter` | `int(input_rows * 0.70)` | `max(30, rows // 20000)` ms |
| `select` | `input_rows` (unchanged) | `max(10, rows // 50000)` ms |
| `rename` | `input_rows` (unchanged) | `max(10, rows // 50000)` ms |
| `join` (inner) | `min(left_rows, right_rows)` | `max(100, rows // 5000)` ms |
| `join` (left) | `left_rows` | `max(100, rows // 5000)` ms |
| `join` (outer) | `left_rows + right_rows` | `max(100, rows // 5000)` ms |
| `aggregate` | `int(input_rows * 0.10)` | `max(40, rows // 10000)` ms |
| `sort` | `input_rows` (unchanged) | `max(20, rows // 15000)` ms |
| `validate` | `input_rows` (unchanged) | `max(30, rows // 20000)` ms |
| `save` | `input_rows` (unchanged) | `max(20, rows // 10000)` ms |

**Failure detection:** The planner checks if any `load` step's `file_id` is
not in `registered_files`. If missing, `will_fail: true` is set for that step.

---

### Schema drift detection

`backend/pipeline/schema_drift.py`

**Entry point:** `detect_schema_drift(current_columns: Dict, previous_columns: Dict) -> DriftResult`

**Algorithm:**
```python
current_set = set(current_columns.keys())
previous_set = set(previous_columns.keys())

removed = previous_set - current_set    # Breaking: pipeline will fail at these columns
added = current_set - previous_set      # Info: no existing pipelines affected
common = current_set & previous_set

type_changed = {
    col for col in common
    if current_columns[col] != previous_columns[col]
}                                       # Warning: pipeline may produce wrong types
```

**Severity levels:**

| Change | Severity | Impact |
|---|---|---|
| Column removed | `breaking` | Pipeline referencing this column will fail |
| Data type changed | `warning` | Pipeline may produce incorrect results |
| Column added | `info` | No impact on existing pipelines |

---

### Pipeline versioning

`backend/pipeline/versioning.py`

**Auto-save on every run:** Each pipeline execution creates a `PipelineVersion` record.
`version_number` = `MAX(version_number) + 1` for this `pipeline_name`, or 1 if first.

**Diff computation** (on-demand, not stored):
```python
import difflib

def compute_diff(yaml_a: str, yaml_b: str, version_a: int, version_b: int) -> DiffResult:
    # Parse both into PipelineConfig dataclasses
    config_a = parse_pipeline_config(yaml_a)
    config_b = parse_pipeline_config(yaml_b)

    steps_a = {s.name: s for s in config_a.steps}
    steps_b = {s.name: s for s in config_b.steps}

    added_steps = [s for s in config_b.steps if s.name not in steps_a]
    removed_steps = [s for s in config_a.steps if s.name not in steps_b]
    modified_steps = [...]  # steps in both but with different config

    # Unified text diff
    unified_diff = "\n".join(difflib.unified_diff(
        yaml_a.splitlines(), yaml_b.splitlines(),
        fromfile=f"version_{version_a}", tofile=f"version_{version_b}"
    ))
    return DiffResult(added_steps, removed_steps, modified_steps, unified_diff)
```

---

## 11. Exception Hierarchy

All 14 exception classes in `backend/pipeline/exceptions.py` (443 lines).

All inherit from `PipelineIQError` which provides:
```python
class PipelineIQError(Exception):
    error_code: str      # Machine-readable code for frontend handling
    message: str         # Human-readable message
    details: dict        # Structured context (varies by subclass)
    suggestion: Optional[str]  # Fuzzy-matched suggestion if applicable

    def to_dict(self) -> dict:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
            "suggestion": self.suggestion
        }
```

The global exception handler in `main.py` catches `PipelineIQError` and
returns it as a `4xx` JSON response with the `error_code` field, enabling
the frontend to handle specific errors specifically.

### Configuration errors (raised during YAML parsing)

These are raised before pipeline execution begins. The `request_id` in the
response helps correlate with Sentry.

---

**`InvalidYAMLError`**
- `error_code`: `"INVALID_YAML"`
- When: YAML syntax is malformed (misindentation, invalid characters, etc.)
- Context: `yaml_error` — the raw PyYAML exception message
- HTTP status: `422`

---

**`MissingRequiredFieldError`**
- `error_code`: `"MISSING_REQUIRED_FIELD"`
- When: A required field is absent from a step (e.g., missing `file_id` on load step)
- Context: `step_name`, `field_name`
- HTTP status: `422`

---

**`DuplicateStepNameError`**
- `error_code`: `"DUPLICATE_STEP_NAME"`
- When: Two or more steps share the same `name`
- Context: `step_name`, `first_occurrence_index`, `duplicate_index`
- HTTP status: `422`

---

**`InvalidStepTypeError`**
- `error_code`: `"INVALID_STEP_TYPE"`
- When: `type` field contains an unrecognised step type
- Context: `step_name`, `provided_type`, `valid_types` list
- Suggestion: `difflib.get_close_matches(provided_type, valid_types, n=1, cutoff=0.6)`
- HTTP status: `422`

---

**`InvalidStepReferenceError`**
- `error_code`: `"INVALID_STEP_REFERENCE"`
- When: `input`, `left`, or `right` references a step that doesn't exist or appears later
- Context: `step_name`, `referenced_step`, `available_steps` list
- Suggestion: fuzzy match against available step names
- HTTP status: `422`

---

**`FileNotRegisteredError`**
- `error_code`: `"FILE_NOT_REGISTERED"`
- When: `file_id` in a `load` step does not exist in the database
- Context: `step_name`, `file_id`, `available_file_ids` list
- HTTP status: `422`

---

**`InvalidOperatorError`**
- `error_code`: `"INVALID_OPERATOR"`
- When: `operator` in a filter step is not one of the 12 valid operators
- Context: `step_name`, `provided_operator`, `valid_operators` list
- Suggestion: fuzzy match
- HTTP status: `422`

---

**`InvalidJoinTypeError`**
- `error_code`: `"INVALID_JOIN_TYPE"`
- When: `how` in a join step is not one of: `inner`, `left`, `right`, `outer`
- Context: `step_name`, `provided_how`, `valid_how_values`
- HTTP status: `422`

---

**`StepCountExceededError`**
- `error_code`: `"STEP_COUNT_EXCEEDED"`
- When: Pipeline has more steps than `MAX_PIPELINE_STEPS` (default 50)
- Context: `step_count`, `max_steps`
- HTTP status: `422`

---

### Runtime errors (raised during Celery task execution)

These are raised after execution begins. They update `PipelineRun.status = FAILED`
and `PipelineRun.error_message`.

---

**`ColumnNotFoundError`**
- `error_code`: `"COLUMN_NOT_FOUND"`
- When: A step references a column that does not exist in the DataFrame
- Context: `step_name`, `column_name`, `available_columns` list
- Suggestion: `difflib.get_close_matches(column_name, available_columns, n=1, cutoff=0.6)`
- Example: "Column 'statuss' not found. Available: ['status', 'amount']. Did you mean: 'status'?"

---

**`JoinKeyMissingError`**
- `error_code`: `"JOIN_KEY_MISSING"`
- When: The `on` column doesn't exist in the left or right DataFrame for a join step
- Context: `step_name`, `join_key`, `left_columns`, `right_columns`

---

**`AggregationError`**
- `error_code`: `"AGGREGATION_ERROR"`
- When: Aggregation fails (e.g., summing a non-numeric column)
- Context: `step_name`, `column`, `function`, `error_detail`

---

**`FileReadError`**
- `error_code`: `"FILE_READ_ERROR"`
- When: Pandas cannot read the file at `stored_path` (file deleted, corrupted)
- Context: `step_name`, `file_id`, `stored_path`, `error_detail`

---

**`UnsupportedFileFormatError`**
- `error_code`: `"UNSUPPORTED_FILE_FORMAT"`
- When: File extension is not `.csv` or `.json`
- Context: `file_id`, `extension`, `supported_formats`

---

**`StepTimeoutError`**
- `error_code`: `"STEP_TIMEOUT"`
- When: A step exceeds `STEP_TIMEOUT_SECONDS` (default 300 = 5 minutes)
- Context: `step_name`, `timeout_seconds`, `elapsed_seconds`

---

## 12. Business Logic — Every Rule

### Rule 1: Column ancestry completeness

Every column that appears in a `save` step output must be traceable back
to a `load` step source column via the lineage DAG.

**Location:** `backend/pipeline/lineage.py` → `get_column_ancestry()`
**Enforcement:** Application layer (LineageRecorder). Verified post-execution.
**What breaks this rule:** A step type that is added without a lineage recording
method creates orphan output column nodes with no ancestors. The graph is silently
incomplete. Ancestry queries return empty results. This is why Contract 1 (the
three-part rule) exists.

---

### Rule 2: Schema drift severity classification

When a file is re-uploaded with the same original filename:
- Removed columns → `breaking` (any pipeline referencing that column will fail)
- Changed dtypes → `warning` (results may be incorrect, pipeline still runs)
- Added columns → `info` (no impact on existing pipelines)

**Location:** `backend/pipeline/schema_drift.py` → `detect_schema_drift()`
**Layer:** Application

---

### Rule 3: Audit log immutability

Audit records are permanently immutable at the database level.
No application code can update or delete them.

**Location:** Database trigger in migration `f6a7b8c9d0e1`
**Layer:** Database — enforced independently of application code
**Consequence:** Even a buggy router that tries to `db.delete(audit_record)`
will receive a database exception.

---

### Rule 4: Pipeline YAML validation is fail-all, not fail-fast

The parser collects ALL validation errors before returning. A YAML with 5
different errors returns all 5 at once, not just the first one.

**Location:** `backend/pipeline/parser.py`
**Layer:** Application

---

### Rule 5: YAML size limits enforced at parse time

`MAX_PIPELINE_STEPS` (default 50) is enforced by the parser before any
execution begins. This prevents DoS via large YAML payloads that would
cause the Celery worker to OOM.

**Location:** `backend/pipeline/parser.py` → rule 4 of the 13 validation rules
**Layer:** Application config + parser

---

### Rule 6: File storage paths are system-generated, never user-supplied

Uploaded files are stored at `{UPLOAD_DIR}/{uuid4()}.{extension}`.
`original_filename` is stored only for display. It is never used to
construct a filesystem path.

**Location:** `backend/api/files.py` (upload handler)
**Layer:** Application
**Why:** Path traversal prevention. A malicious filename like
`../../etc/passwd` or `../auth.py` would allow overwriting system files.

---

### Rule 7: Webhook secrets never appear in API responses

`Webhook.secret` is stored in the database but `WebhookResponse` schema
returns only `has_secret: bool`. The raw secret is never returned.

**Location:** `backend/schemas.py` → `WebhookResponse`
**Layer:** Application schema (enforced by Pydantic)

---

### Rule 8: First registered user becomes admin

When the `users` table has zero records, the first user to register is
automatically assigned `role = "admin"` regardless of what role they
requested in the registration body.

**Location:** `backend/api/auth.py` (register handler)
**Layer:** Application

---

### Rule 9: Production startup blocked with default SECRET_KEY

`config.py` contains a Pydantic model validator that compares `SECRET_KEY`
against a set of known placeholder values (e.g., `"change-me-in-production"`).
If they match AND `ENVIRONMENT == "production"`, the application raises
`ValueError` and refuses to start.

**Location:** `backend/config.py`
**Layer:** Application startup validation

---

### Rule 10: PipelineRunner, StepExecutor, LineageRecorder have zero infrastructure dependencies

These three classes cannot directly import Redis, SQLAlchemy, httpx, or any
external service. All side effects must be injected through the `ProgressCallback`
protocol.

**Enforced by:** Architecture convention (architectural fitness function —
not enforced by automated tooling, but checked in code review)
**Why:** Makes the entire pipeline execution testable with pure in-memory
DataFrames and a no-op callback, with no mocking of infrastructure.

---

### Rule 11: YAML is only parsed via parse_pipeline_config()

`yaml.safe_load()` is never called directly on user-provided YAML. All user YAML
must pass through `parse_pipeline_config()` which validates and converts to typed
dataclasses.

**Layer:** Application — enforced by convention, verified in security tests

---

### Rule 12: All Prometheus metrics defined in metrics.py only

Defining a Counter, Histogram, or Gauge anywhere except `backend/metrics.py`
causes a circular import when `main.py` initialises the Prometheus instrumentator.

**Layer:** Application — enforced by Python import system (circular import error)

---

### Rule 13: Rate limiting on every public endpoint

Every new public endpoint receives a rate limiter dependency. The four tiers
are defined in `backend/utils/rate_limiter.py` and applied as FastAPI dependencies.

**Layer:** Application — enforced by code review

---

### Rule 14: Pagination on every list endpoint

No endpoint returns an unbounded list of results. Every collection endpoint
has `page` and `limit` parameters with `limit` capped at 100-200.

**Layer:** Application — enforced by code review

---

### Rule 15: Webhook delivery uses separate Celery task

HTTP calls to external webhook endpoints run in `webhook_tasks.py` as a
separate Celery task, dispatched after the pipeline task completes.
Never inside `execute_pipeline_task`.

**Layer:** Architecture — enforced by task separation

---

## 13. Configuration Reference — All 55+ Variables

All defined in `backend/config.py` using Pydantic `BaseSettings`.
Loaded from `.env` in development, environment variables in production.

### Core application

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `APP_NAME` | No | `"PipelineIQ"` | str | Application display name |
| `APP_VERSION` | No | `"2.1.4"` | str | Application version string |
| `DEBUG` | No | `False` | bool | Enable debug mode (verbose logging, SQL echo) |
| `LOG_LEVEL` | No | `"INFO"` | str | Python logging level |
| `ENVIRONMENT` | No | `"development"` | str | Environment name (used for feature flags) |

**`ENVIRONMENT` effects:**
- `"production"`: `/docs` and `/redoc` disabled, startup validation enforced
- `"development"`: all debug endpoints active, docs available

---

### Database

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `DATABASE_URL` | **Yes** | — | str | PostgreSQL connection string |
| `POSTGRES_USER` | No | `"pipelineiq"` | str | For docker-compose only |
| `POSTGRES_PASSWORD` | No | `""` | str | For docker-compose only |
| `POSTGRES_DB` | No | `"pipelineiq"` | str | For docker-compose only |

**Connection pool** (automatically configured based on URL scheme):
- PostgreSQL: `pool_size=20, max_overflow=10, pool_pre_ping=True, pool_recycle=3600`
- SQLite: `check_same_thread=False`

**`DATABASE_URL` format examples:**
```
postgresql://user:password@localhost:5432/pipelineiq
postgresql://user:password@neon.host.com/pipelineiq?sslmode=require
sqlite:///./pipelineiq.db
```

---

### Authentication

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `SECRET_KEY` | **Yes (prod)** | `"change-me-in-production"` | str | JWT signing key, min 32 chars |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `1440` | int | Token lifetime (24 hours = 1440 minutes) |

**Production startup check:** If `SECRET_KEY` matches the default value AND
`ENVIRONMENT == "production"`, application raises `ValueError` and refuses to start.

---

### Redis and Celery

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `REDIS_URL` | **Yes** | `"redis://localhost:6379/0"` | str | Redis connection string |
| `CELERY_BROKER_URL` | No | Derived from REDIS_URL | str | Override Celery broker |
| `CELERY_RESULT_BACKEND` | No | Derived from REDIS_URL | str | Override Celery backend |

**`REDIS_URL` format examples:**
```
redis://localhost:6379/0
rediss://user:password@upstash.io:6379  (Upstash with TLS)
```

**TLS handling:** When URL starts with `rediss://`, `celery_app.py` adds:
```python
broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
```

---

### File storage

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `UPLOAD_DIR` | No | `"./uploads"` | Path | Directory for uploaded files |
| `MAX_UPLOAD_SIZE` | No | `52428800` | int | Max upload bytes (50 MB) |
| `ALLOWED_EXTENSIONS` | No | `{".csv", ".json"}` | frozenset | Allowed file types |

**`UPLOAD_DIR` auto-creation:** Pydantic validator creates the directory if it
doesn't exist: `Path(v).mkdir(parents=True, exist_ok=True)`.

---

### Pipeline execution limits

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `MAX_PIPELINE_STEPS` | No | `50` | int | Max steps per pipeline YAML |
| `MAX_ROWS_PER_FILE` | No | `1000000` | int | Max rows per uploaded file |
| `STEP_TIMEOUT_SECONDS` | No | `300` | int | Max execution time per step (5 min) |
| `PREVIEW_ROWS` | No | `20` | int | Rows returned by file preview endpoint |

---

### Rate limiting

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `RATE_LIMIT_PIPELINE_RUN` | No | `"10/minute"` | str | Pipeline execution limit |
| `RATE_LIMIT_FILE_UPLOAD` | No | `"30/minute"` | str | File upload limit |
| `RATE_LIMIT_VALIDATION` | No | `"60/minute"` | str | Validation/planning limit |
| `RATE_LIMIT_READ` | No | `"120/minute"` | str | Read operation limit |

All limits are per-IP via SlowAPI backed by Redis. Format: `"{count}/{period}"`.

---

### API configuration

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `API_PREFIX` | No | `"/api/v1"` | str | URL prefix for all API routes |
| `CORS_ORIGINS` | No | See below | list | Allowed CORS origins |

**Default CORS_ORIGINS:**
```python
["http://localhost:3000", "https://pipeline-iq0.vercel.app",
 "https://pipelineiq-api.onrender.com"]
```

---

### Caching

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `CACHE_TTL_LINEAGE` | No | `3600` | int | Lineage graph cache TTL (1 hour) |
| `CACHE_TTL_STATS` | No | `30` | int | Statistics cache TTL (30 seconds) |

---

### Observability

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `SENTRY_DSN` | No | `""` | str | Sentry DSN (empty = Sentry disabled) |
| `FLOWER_USER` | No | `"admin"` | str | Celery Flower username |
| `FLOWER_PASSWORD` | No | `"change-me-in-production"` | str | Celery Flower password |
| `GRAFANA_USER` | No | `"admin"` | str | Grafana username |
| `GRAFANA_PASSWORD` | No | `"change-me-in-production"` | str | Grafana password |

---

### Schema drift

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `DRIFT_DETECTION_ENABLED` | No | `True` | bool | Enable/disable drift detection |
| `MAX_VERSIONS_PER_PIPELINE` | No | `50` | int | Max version records per pipeline name |

---

### Email / SMTP notifications

| Variable | Required | Default | Type | Description |
|---|---|---|---|---|
| `SMTP_HOST` | No | `""` | str | SMTP server hostname |
| `SMTP_PORT` | No | `587` | int | SMTP server port |
| `SMTP_USER` | No | `""` | str | SMTP authentication username |
| `SMTP_PASSWORD` | No | `""` | str | SMTP authentication password |
| `SMTP_FROM` | No | `""` | str | Sender address for emails |
| `SMTP_USE_TLS` | No | `True` | bool | Enable STARTTLS |
| `SMTP_USE_SSL` | No | `False` | bool | Use SSL (alternative to STARTTLS) |
| `SMTP_TIMEOUT` | No | `10` | int | SMTP connection timeout in seconds |

---

## 14. Testing — All 299 Tests

### Backend — 206 tests across 14 files

**Test database:** SQLite in-memory (local), PostgreSQL 15 (CI).
**Test isolation:** Each test function receives a fresh database session from the `test_db` fixture.
**Auth:** The default `client` fixture overrides `get_current_user` with a mock admin user.
The `auth_client` fixture does NOT override auth — used for testing real auth flows.

#### `conftest.py` — fixtures

| Fixture | Scope | Description |
|---|---|---|
| `test_engine` | session | Creates SQLite in-memory engine, creates all tables |
| `test_db` | function | Yields a fresh session, rolls back after each test |
| `client` | function | FastAPI TestClient with auth dependency overridden (admin mock) |
| `auth_client` | function | FastAPI TestClient without auth override (real auth) |
| `sample_sales_df` | function | 20-row DataFrame: order_id, customer_id, amount, status |
| `sample_customers_df` | function | 10-row DataFrame: customer_id, name, region |
| `sample_products_df` | function | 5-row DataFrame: product_id, name, price |
| `lineage_recorder` | function | Fresh LineageRecorder instance |
| `sales_csv_bytes` | function | CSV bytes from sample_sales_df (for upload tests) |
| `uploaded_sales_file` | function | Pre-created UploadedFile record + temp file on disk |
| `basic_pipeline_yaml` | function | Minimal valid pipeline YAML string |
| `full_pipeline_yaml` | function | Multi-step pipeline YAML exercising all step types |

**Celery mocking:** `execute_pipeline_task.delay()` is mocked in the test client to
return a mock result immediately. Tests verify database state, not Celery execution.

#### Test files and what they cover

| File | Tests | Key scenarios |
|---|---|---|
| `test_api.py` | 34 | Health check, file upload, file list, pipeline validate, plan, run, stats, get, cancel, lineage endpoints, version endpoints, error shapes (404/422/401/403) |
| `test_steps.py` | 25 | All 9 step types with valid input, filter with all 12 operators, aggregate with all 10 functions, join with all 4 types, empty DataFrame inputs, missing column errors |
| `test_validators.py` | 22 | All 12 check types, severity=error vs severity=warning behavior, check passing, check failing, check with None values |
| `test_parser.py` | 18 | Valid YAML parses correctly, each of the 13 validation rules catches its error, fuzzy suggestions accuracy, multi-error collection |
| `test_lineage.py` | 18 | Load recording, filter passthrough, join edge creation with is_join_key, aggregate new column creation, save output node, ancestry query correctness, impact analysis correctness, React Flow export shape |
| `test_auth.py` | 17 | Register valid user, register duplicate email, register weak password, login valid, login wrong password, login disabled account, JWT expiry, admin-only endpoint enforcement, role change |
| `test_planner.py` | 15 | Row estimates for each step type, total duration sum, files_read tracking, files_written tracking, will_fail detection when file missing |
| `test_versioning.py` | 12 | Version creation, version_number increment, diff empty (same YAML), diff with added step, diff with removed step, diff with modified step, restore creates new version |
| `test_schema_drift.py` | 10 | No changes → no drift, column removed → breaking, type changed → warning, column added → info, multiple changes classified independently |
| `test_webhooks.py` | 9 | Create webhook, list own webhooks, delete own webhook, reject deleting other's webhook, HMAC signature verification, delivery record creation, test delivery endpoint |
| `test_caching.py` | 8 | Cache set, cache get, cache miss (None return), cache delete, cache delete_pattern (SCAN), TTL expiry (mocked), RedisError graceful degradation |
| `test_security.py` | 7 | Path traversal in filename, CSV injection characters in column names, SQL injection in query params (ORM prevents it), auth header missing, invalid UUID in path |
| `test_rate_limiting.py` | 6 | Pipeline run limit (10/min), file upload limit (30/min), validation limit (60/min), read limit (120/min), limit reset |
| `test_performance.py` | 5 | Health check < 200ms, file list < 500ms, lineage query < 1s, concurrent uploads (5 simultaneous), concurrent runs (3 simultaneous) |

**Running backend tests:**
```bash
cd backend
pytest tests/ -v                              # All 206 tests
pytest tests/test_steps.py -v                 # Specific file
pytest tests/test_parser.py::TestParserParse -v  # Specific class
pytest tests/ --cov=backend --cov-report=html  # With coverage
pytest tests/ -v --tb=short                   # Short tracebacks for CI
```

---

### Frontend — 93 tests across 8 files

**Test framework:** Vitest 4.0.18
**Test utilities:** React Testing Library 16.3.2 + @testing-library/user-event 14.6.1
**Environment:** jsdom 28.1.0
**Setup file:** `frontend/__tests__/setup.ts`

**What setup.ts provides:**
- `@testing-library/jest-dom` matchers (toBeInTheDocument, etc.)
- Global cleanup after each test
- `next/navigation` mock (useRouter, useSearchParams)
- `next/link` mock
- `EventSource` mock (for SSE tests)
- `ResizeObserver` mock (required for CodeMirror)
- `motion/react` mock (avoids animation timing issues)
- `window.matchMedia` mock (for responsive breakpoints)

| File | Tests | What is tested |
|---|---|---|
| `api.test.ts` | 26 | `fetchWithAuth` auth header injection, 401 redirect, network error, all 25+ API endpoint functions call correct URL/method, `ApiError` parsing from error responses |
| `stores.test.ts` | 26 | `pipelineStore`: set/get active run, YAML content; `widgetStore`: split panel, merge panel, switch workspace, 5 workspace independence; `themeStore`: switch theme, custom theme; `keybindingStore`: register, unregister, conflict detection |
| `pages.test.tsx` | 12 | Login: renders form, submits, shows error on 401, clears error on retry, demo login button; Register: renders form, validates password complexity, shows error on duplicate email |
| `widgets.test.tsx` | 11 | QuickStats: renders stat values, loading state; FileUpload: drag-and-drop indicator, file type rejection, size limit message; RunHistory: renders runs list, status badges; FileRegistry: renders file names, row counts |
| `utils.test.ts` | 7 | `cn()`: merges classes, Tailwind deduplication, falsy values ignored; constants: API base URL correct, widget IDs match expected list |
| `middleware.test.ts` | 4 | Missing cookie → redirect to /login; valid cookie → pass through; login page accessible without cookie; register page accessible without cookie |
| `auth-context.test.tsx` | 4 | Login sets token in localStorage; logout clears token and redirects; demo login uses demo credentials; useAuth returns user info |
| `hooks.test.ts` | 3 | `useWidgetLayout`: toggle widget visibility; `usePipelineRun`: connects EventSource on mount, disconnects on unmount; workspace switching updates active workspace |

**Running frontend tests:**
```bash
cd frontend
npm run test           # Run all 93 tests once
npm run test:watch     # Watch mode for development
npx tsc --noEmit       # Type check
npm run lint           # ESLint
```

---

### CI/CD pipeline (`.github/workflows/ci.yml`) — 3 jobs

**Triggers:** Push to `main` or `develop`, PR to `main`

**Job 1: Backend Tests**
```yaml
runs-on: ubuntu-latest
services:
  postgres:
    image: postgres:15-alpine
    env: POSTGRES_DB=pipelineiq_test
    options: --health-cmd pg_isready --health-interval 5s
  redis:
    image: redis:7-alpine
    options: --health-cmd "redis-cli ping" --health-interval 10s
steps:
  - setup Python 3.11
  - pip install -r backend/requirements.txt
  - alembic upgrade head
  - pytest tests/ -v --tb=short
  - upload test-results.xml artifact
```

**Job 2: Frontend Check**
```yaml
runs-on: ubuntu-latest
steps:
  - setup Node.js 20
  - npm ci
  - npx tsc --noEmit      (TypeScript type check)
  - npm run test           (Vitest)
  - npm run build          (Next.js production build)
```

**Job 3: Docker Compose Smoke Test** (depends on Jobs 1 + 2)
```yaml
steps:
  - Create .env with CI-only test credentials
  - docker compose build
  - docker compose up -d
  - sleep 30 (wait for services)
  - Run Python smoke script:
      GET /health → expect 200 {"status": "ok"}
      POST /auth/login → extract token
      POST /api/v1/files/upload (test CSV) → extract file_id
      POST /api/v1/pipelines/run → extract run_id
      Poll GET /api/v1/pipelines/{run_id} every 2s for 30s
          until status == "COMPLETED" or "FAILED"
  - On failure: docker compose logs (last 50 lines)
  - Always: docker compose down --volumes
```

---

## 15. Frontend Architecture

### Widget system — all 8 widgets

Each widget is wrapped in `WidgetShell.tsx` which provides:
- Title bar with icon and widget name
- Minimize/maximize/close controls
- Resize handles
- Error boundary (per-widget)

| Widget | ID | Primary purpose |
|---|---|---|
| `FileUploadWidget` | `file-upload` | Drag-and-drop CSV/JSON upload with progress |
| `FileRegistryWidget` | `file-registry` | Browse uploaded files, preview, schema info, drift badge |
| `PipelineEditorWidget` | `pipeline-editor` | CodeMirror YAML editor (318 lines) |
| `RunMonitorWidget` | `run-monitor` | Real-time SSE step-by-step progress display |
| `LineageGraphWidget` | `lineage-graph` | Interactive React Flow DAG with sidebar |
| `RunHistoryWidget` | `run-history` | Historical run list with status filters and export |
| `QuickStatsWidget` | `quick-stats` | Platform statistics (totals, success rate, recent activity) |
| `VersionHistoryWidget` | `version-history` | Pipeline version list + diff viewer (265 lines) |

Plus `StepDAG.tsx` — embedded in `PipelineEditorWidget`, shows horizontal
step dependency flow diagram.

Plus `manage-connections` widget entry in the widget registry — present in
`constants.ts` but may be a placeholder for future functionality.

---

### Layout system — binary tree

`widgetStore.ts` (225 lines) manages a binary tree data structure for panels.
Each tree node is either:
- A **leaf node**: contains a single widget
- A **split node**: contains two children (left/right or top/bottom) with a split ratio

**5 independent workspaces:** Each workspace has its own layout tree.
Switching workspace (`Alt+1` through `Alt+5`) loads a different tree.

**Operations supported:**
- `splitWidget(widgetId, direction)`: splits a leaf node into two panels
- `mergeWidget(widgetId)`: merges a panel back into its parent
- `swapWidgets(widgetIdA, widgetIdB)`: swaps two widgets' positions
- `setActiveWidget(widgetId)`: marks a widget as focused
- `setActiveWorkspace(n)`: switches to workspace n (1-5)
- `moveWidgetToWorkspace(widgetId, workspaceN)`: moves widget to another workspace

All layout state is persisted to localStorage via Zustand `persist` middleware.

---

### Zustand stores — all 4

All stores use `create<State>()(persist(..., { name: "pipelineiq-{name}" }))` pattern.

**`widgetStore.ts`** — binary tree layout for 5 workspaces
- State: `workspaces: WorkspaceLayout[]`, `activeWorkspace: number`, `activeWidgetId: string`
- persisted: layout tree, active workspace

**`pipelineStore.ts`** — active pipeline execution state
- State: `activeRunId: string | null`, `yamlContent: string`, `runData: PipelineRun | null`
- `yamlContent` persisted (survives page reload), `runData` not persisted (transient)

**`themeStore.ts`** — theme configuration
- State: `activeTheme: string`, `customThemes: Record<string, ThemeDefinition>`
- All persisted

**`keybindingStore.ts`** — 18 keyboard shortcut definitions
- State: `bindings: KeyBinding[]` — action → key combo mapping
- All persisted (user customizable)

---

### Theme system — all 7 themes

**The 7 built-in themes:**

| Theme name | Display name |
|---|---|
| `pipelineiq-dark` | PipelineIQ Dark (default) |
| `pipelineiq-light` | PipelineIQ Light (added v2.1.4) |
| `catppuccin-mocha` | Catppuccin Mocha |
| `tokyo-night` | Tokyo Night |
| `gruvbox-dark` | Gruvbox Dark |
| `nord` | Nord |
| `rose-pine` | Rosé Pine |

**IMPORTANT:** `BUILT_IN_THEMES` array in `ThemeSelector.tsx` and the `themes`
array in `CommandPalette.tsx` BOTH maintain this list independently. When adding
a new theme, both files must be updated. This duplication is known technical debt.

**Theme system implementation:**
- Each theme defines 28 CSS variables (background, foreground, accent, border, etc.)
- `ThemeBuilder` allows creating custom themes by setting values for each variable
- Custom themes are serialised to JSON and persisted in `themeStore`
- `useTheme.ts` hook applies the active theme's CSS variables to `:root`

---

### Authentication flow (frontend)

```
1. User submits login form
2. POST /auth/login → receives {access_token, user}
3. Token stored: localStorage.setItem("pipelineiq_token", access_token)
4. Cookie set: document.cookie = "piq_auth=1; SameSite=Strict; path=/"
5. Next.js middleware reads piq_auth cookie:
   - If missing → redirect to /login
   - If present → allow through
6. All API calls: fetchWithAuth() prepends Authorization: Bearer {token}
7. On 401 response: token cleared, redirect to /login
8. On logout: token cleared, cookie deleted, redirect to /login
```

**Known vulnerability:** Token in `localStorage` is accessible to JavaScript,
making it vulnerable to XSS attacks. The `SameSite=Strict` cookie mitigates CSRF
but not XSS. `httpOnly` cookie would be more secure but requires CSRF protection.

**Demo login:** Credentials `demo@pipelineiq.app / Demo1234!` are hardcoded in
`auth-context.tsx` and seeded by `seed_demo.py`. These appear in the login page
as a "Demo Login" button.

---

### Next.js configuration

**`next.config.ts` proxy rewrites:**
```typescript
rewrites: [
  { source: "/api/:path*", destination: "http://localhost:8000/api/:path*" },
  { source: "/auth/:path*", destination: "http://localhost:8000/auth/:path*" },
  { source: "/health", destination: "http://localhost:8000/health" },
]
```

In production (Vercel + Render), these rewrites are not needed because Nginx
handles proxying. In development, they allow the frontend dev server to proxy
API calls to the backend without CORS issues.

**`middleware.ts`:** Checks for `piq_auth` cookie. If missing and not on
`/login` or `/register`, redirects to `/login`. This is the auth gate for
all frontend pages.

---

## 16. Infrastructure and Deployment

### Docker Compose — 9 services in detail

```yaml
services:
  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: pipelineiq
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes: [db_data:/var/lib/postgresql/data]
    healthcheck: {test: pg_isready, interval: 5s, retries: 5}

  redis:
    image: redis:7-alpine
    healthcheck: {test: redis-cli ping, interval: 10s, retries: 5}

  api:
    build: {context: ./backend, dockerfile: Dockerfile}
    depends_on: {db: {condition: healthy}, redis: {condition: healthy}}
    environment: [DATABASE_URL, REDIS_URL, SECRET_KEY, UPLOAD_DIR, ...]
    volumes: [uploads:/app/uploads]
    ports: [8000:8000]
    # CMD: alembic upgrade head && seed_demo && celery worker & uvicorn

  worker:
    build: {context: ./backend, dockerfile: Dockerfile}
    depends_on: {db: {condition: healthy}, redis: {condition: healthy}}
    volumes: [uploads:/app/uploads]  # Shared volume with api for file access
    command: celery -A celery_app worker --loglevel=info --concurrency=2

  frontend:
    build: {context: ./frontend, dockerfile: Dockerfile}
    depends_on: [api]
    environment: [NEXT_PUBLIC_API_URL=http://api:8000]

  flower:
    build: {context: ./backend}
    command: celery -A celery_app flower --port=5555
    depends_on: {redis: {condition: healthy}, db: {condition: healthy}}
    ports: [5555:5555]

  nginx:
    build: {context: ./nginx}
    depends_on: [api, frontend, flower, grafana]
    ports: [80:80]

  prometheus:
    image: prom/prometheus:v2.48.0
    volumes: [./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml]
    ports: [9090:9090]

  grafana:
    image: grafana/grafana:10.2.0
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning
      - grafana_data:/var/lib/grafana
    environment: [GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}]
    ports: [3001:3000]

volumes: [db_data, uploads, prometheus_data, grafana_data]
network: pipelineiq-network (bridge)
```

**Key design decision — shared uploads volume:** Both `api` and `worker` services
mount the `uploads` named volume at `/app/uploads`. This is required because:
- The `api` service handles file uploads (writes to uploads/)
- The `worker` service reads files during pipeline execution (reads from uploads/)
- Without the shared volume, the worker cannot find the files

---

### Nginx configuration — routing table

| Request path | Upstream | Special config |
|---|---|---|
| `/api/v1/pipelines/*/stream` | api:8000 | `proxy_buffering off`, 3600s timeout |
| `/api/` | api:8000 | 50MB body limit, 300s timeout |
| `/auth/` | api:8000 | Standard proxy |
| `/webhooks/`, `/audit/`, `/health`, `/metrics` | api:8000 | Standard |
| `/docs`, `/redoc` | api:8000 | Available in dev only |
| `/flower/` | flower:5555 | Standard proxy |
| `/grafana/` | grafana:3000 | Standard proxy |
| `/` (default) | frontend:3000 | Fallback for Next.js SSR |

**Security headers on every response:**
```nginx
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
```

**Missing headers (known open issues):**
- `Content-Security-Policy` — not set, open for XSS injection
- `Strict-Transport-Security` — not set, TLS handled by Render/Vercel

**SSE passthrough configuration:**
```nginx
location ~* /stream {
    proxy_pass http://api:8000;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    chunked_transfer_encoding on;
    proxy_set_header Connection '';
    proxy_http_version 1.1;
}
```

---

### Production deployment

| Component | Platform | Plan | Region | Notes |
|---|---|---|---|---|
| Backend API + Worker | Render.com | Free | Singapore | Sleeps after 15 min inactivity |
| Frontend | Vercel | Free | Global CDN | Auto-deploy on push to main |
| Database | Neon.tech PostgreSQL | Free | us-east-1 | Connection pooler endpoint |
| Redis + Queue | Upstash | Free | — | TLS: `rediss://` URL |

**Cross-region latency:** Render (Singapore) ↔ Neon (us-east-1) adds observable
database query latency. This is a known accepted trade-off for free tier hosting.

**Render cold start:** After 15 minutes of inactivity, Render spins down the
container. First request after cold start takes 30-60 seconds. Users see a
loading delay. This is documented in the README.

**Deployment trigger:** Push to `main` automatically triggers both Render and
Vercel deployments via GitHub integration. No secrets needed.

**`render.yaml` configuration:**
```yaml
services:
  - type: web
    name: pipelineiq-api
    runtime: docker
    region: singapore
    plan: free
    dockerfilePath: ./backend/Dockerfile
    dockerContext: ./backend
    envVars:
      - key: DATABASE_URL
        sync: false
      - key: REDIS_URL
        sync: false
      - key: SECRET_KEY
        generateValue: true
      - key: ENVIRONMENT
        value: production
```

---

### Celery configuration details

`backend/celery_app.py` configures Celery with:

```python
celery_app = Celery("pipelineiq")
celery_app.config_from_object({
    "broker_url": settings.CELERY_BROKER_URL,
    "result_backend": settings.CELERY_RESULT_BACKEND,
    "task_serializer": "json",
    "result_serializer": "json",
    "accept_content": ["json"],
    "timezone": "UTC",
    "enable_utc": True,
    "task_track_started": True,
    "worker_prefetch_multiplier": 1,  # One task per worker at a time
    "task_acks_late": True,           # Ack after completion, not pickup
})
```

**`worker_prefetch_multiplier=1`:** Each Celery worker processes one task at
a time. This prevents memory exhaustion when multiple large file pipelines
are queued — if a worker prefetched 4 tasks and each task loads a 2GB file,
the worker would OOM with 8GB of DataFrames.

**`task_acks_late=True`:** The task is acknowledged from the queue only after
it completes (not when it starts). If the worker crashes mid-execution, the
task re-enters the queue and another worker picks it up.

---

## 17. Observability

### Prometheus metrics — all 5 custom metrics

All defined in `backend/metrics.py`. Never define metrics elsewhere — circular import.

| Metric name | Type | Labels | Description |
|---|---|---|---|
| `pipelineiq_pipeline_runs_total` | Counter | `status` (success/failed/cancelled) | Total pipeline executions by outcome |
| `pipelineiq_pipeline_duration_seconds` | Histogram | — | Pipeline execution wall time |
| `pipelineiq_files_uploaded_total` | Counter | — | Total file upload events |
| `pipelineiq_active_users_total` | Gauge | — | Registered active users count |
| `pipelineiq_celery_queue_depth` | Gauge | — | Tasks waiting in Celery queue |

Plus auto-generated HTTP metrics from `prometheus-fastapi-instrumentator`:
- `http_requests_total` — request count by method, path, status
- `http_request_duration_seconds` — latency histogram by method, path
- `http_requests_inprogress` — in-flight request gauge

**Scrape configuration:**
```yaml
# prometheus/prometheus.yml
scrape_configs:
  - job_name: "pipelineiq-api"
    static_configs:
      - targets: ["api:8000"]
    metrics_path: /metrics
    scrape_interval: 10s
```

---

### Grafana dashboard — all 10 panels

All panels defined in `grafana/provisioning/dashboards/pipelineiq.json`.
Provisioned automatically on startup.

| Panel # | Title | Type | Query |
|---|---|---|---|
| 1 | Pipeline Runs / minute | Time series | `rate(pipelineiq_pipeline_runs_total[1m])` by status |
| 2 | API Latency p95 | Time series | `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))` |
| 3 | API Request Rate | Time series | `rate(http_requests_total[1m])` |
| 4 | Pipeline Success Rate | Gauge | success / total × 100; thresholds: <80% red, <95% yellow |
| 5 | Pipeline Duration p95 | Time series | `histogram_quantile(0.95, rate(pipelineiq_pipeline_duration_seconds_bucket[5m]))` |
| 6 | Files Uploaded | Stat | `pipelineiq_files_uploaded_total` |
| 7 | Celery Queue Depth | Stat | `pipelineiq_celery_queue_depth`; thresholds: <5 green, <20 yellow, 20+ red |
| 8 | Active HTTP Connections | Time series | `http_requests_inprogress` |
| 9 | HTTP Error Rate | Time series | `rate(http_requests_total{status=~"5.."}[5m])` |
| 10 | Registered Users | Stat | `pipelineiq_active_users_total` |

---

### Logging configuration

**Format:** `%(asctime)s | %(levelname)-8s | %(name)s | %(message)s`

**Log levels and what triggers each:**
- `DEBUG` — detailed diagnostic (SQL queries, cache hits/misses) — not enabled in production
- `INFO` — pipeline start/complete, file upload, schedule trigger, auth events
- `WARNING` — filter produced 0 rows, SSE reconnect, retry attempt, schema drift
- `ERROR` — step execution failure, webhook delivery failure, Redis error
- `CRITICAL` — database connection failure, startup validation failure

**Request ID injection:** Every request gets a UUID4 `request_id` from middleware.
This ID appears in:
- Structured log entries: `logger.info("event", request_id=request_id, ...)`
- `X-Request-ID` response header
- Sentry error reports (correlated by request_id)
- Structured error responses: `{"error_code": "...", "request_id": "..."}`

**Never log:**
- JWT tokens or partial tokens
- `SECRET_KEY` or any environment variable value that could be a secret
- `yaml_config` content (may contain sensitive data values)
- File contents
- Database connection strings
- Passwords (even hashed)

---

### Sentry integration

Configured in `main.py`:
```python
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[
            FastApiIntegration(),
            CeleryIntegration(),
            SqlalchemyIntegration()
        ]
    )
```

**What Sentry captures:**
- Unhandled exceptions in FastAPI routes (including `500` errors)
- Unhandled exceptions in Celery tasks (pipeline execution failures)
- SQLAlchemy query errors
- Each error includes: `request_id`, `user_id`, request URL, stack trace

**What Sentry does NOT capture:**
- Handled `PipelineIQError` subclasses that return 4xx (these are expected)
- Rate limit violations (expected)
- Auth failures (expected)

---

## 18. Security Posture

### What is secured

| Control | Implementation | Location |
|---|---|---|
| Password hashing | bcrypt via passlib | `backend/auth.py` |
| Password complexity | Registration validator | `backend/api/auth.py` |
| JWT signing | HS256 with SECRET_KEY | `backend/auth.py` |
| JWT expiry | 24h, enforced at decode | `backend/auth.py` |
| Production key validation | Pydantic startup validator | `backend/config.py` |
| Rate limiting | slowapi, 4 tiers | `backend/utils/rate_limiter.py` |
| YAML parsing safety | parse_pipeline_config() | `backend/pipeline/parser.py` |
| File path safety | UUID-based paths | `backend/api/files.py` |
| Name sanitization | sanitize_pipeline_name() | `backend/utils/string_utils.py` |
| SQL injection prevention | SQLAlchemy ORM (parameterized) | All DB queries |
| Webhook signing | HMAC-SHA256 | `backend/services/webhook_service.py` |
| Webhook secret hiding | has_secret: bool only | `backend/schemas.py` |
| Metrics restriction | Nginx allow/deny | `nginx/conf.d/pipelineiq.conf` |
| Docs disabled in prod | ENVIRONMENT=production check | `backend/main.py` |
| SameSite=Strict cookie | piq_auth cookie | `backend/api/auth.py` |
| Audit immutability | PostgreSQL trigger | Migration `f6a7b8c9d0e1` |
| CORS restriction | CORS_ORIGINS config | `backend/main.py` |

### Security headers set by Nginx

| Header | Value | Protects against |
|---|---|---|
| `X-Frame-Options` | `SAMEORIGIN` | Clickjacking |
| `X-Content-Type-Options` | `nosniff` | MIME type sniffing |
| `X-XSS-Protection` | `1; mode=block` | Reflected XSS (legacy browsers) |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Referrer leakage |

### Known open vulnerabilities (documented, accepted)

| # | Issue | Risk | Status |
|---|---|---|---|
| 1 | JWT in localStorage | XSS can steal token | Open — mitigated by SameSite=Strict |
| 2 | Public read endpoints | Data leakage multi-tenant | Open — acceptable single-tenant |
| 3 | No Content-Security-Policy | XSS injection vector | Open — no mitigation |
| 4 | No HTTPS redirect in Nginx | Mixed content | Open — TLS at Render/Vercel |
| 5 | API + Worker same container | Worker starves API | Open — free tier limitation |

---

## 19. Performance Profile

### Measured bottlenecks

| Bottleneck | Cause | Current mitigation |
|---|---|---|
| Render cold start (30-60s) | Free tier container sleep | Documented for users |
| Cross-region latency | Render (Singapore) ↔ Neon (us-east-1) | Accepted free tier trade-off |
| Large JOIN (1M rows) | Pandas full in-memory merge | MAX_ROWS_PER_FILE=1M limit |
| Lineage graph for complex pipelines | Graph grows with (columns × steps) | Pre-computed layout, Redis cached 1h |
| Redis pub/sub under high concurrency | Single Redis instance | Acceptable at current scale |

### Query optimizations applied

| Optimization | Before | After |
|---|---|---|
| validate_pipeline file query | `db.query(UploadedFile).all()` loads full objects | `db.query(UploadedFile.id).all()` loads IDs only |
| Pipeline task file loading | Loads all uploaded files | Loads only files referenced in YAML |
| File preview | `pd.read_csv()` then `head(N)` | `pd.read_csv(nrows=N)` reads only N rows |
| Performance indexes | Full table scans on pipeline_runs | Indexes on status, created_at, user_id |
| Redis SCAN vs KEYS | `KEYS *pattern*` blocks Redis | `SCAN` cursor iteration via cache_delete_pattern |
| Redis connection pool | New connection per health check | Module-level ConnectionPool |

### Caching TTLs

| Data | TTL | Invalidation trigger |
|---|---|---|
| Lineage graph (React Flow) | 3600s (1 hour) | New pipeline run completion |
| Dashboard statistics | 30s | Automatic TTL expiry |
| Column ancestry query | 3600s (1 hour) | New pipeline run |
| Impact analysis query | 3600s (1 hour) | New pipeline run |

### Database connection pool

PostgreSQL only:
- `pool_size=20` — baseline connections kept alive
- `max_overflow=10` — burst connections (up to 30 total)
- `pool_pre_ping=True` — validates connection before use (detects dead connections)
- `pool_recycle=3600` — connections recycled every hour (prevents stale connections)

---

## 20. Known Issues and Technical Debt

### Active TODOs (tracked in codebase comments)

| Issue | Location | Priority | What it means |
|---|---|---|---|
| Chunked file processing for >2GB files | `backend/pipeline/steps.py` → `_execute_load` | High | Files >2GB OOM the Celery worker |
| Step-level lineage caching | `backend/pipeline/lineage.py` | Medium | Lineage recomputation expensive if step retry |
| Frontend E2E tests | `frontend/` | Medium | 93 unit tests but no Playwright/Cypress |
| Public read endpoint scoping | `api/files.py`, `api/lineage.py` | High | All users see all data in multi-tenant |
| JWT → httpOnly cookie | `frontend/lib/auth-context.tsx` | Medium | XSS token theft vulnerability |
| Content-Security-Policy header | `nginx/conf.d/pipelineiq.conf` | Medium | Missing XSS defense-in-depth |
| Duplicate alembic.ini | Root and `backend/` | Low | Cosmetic, could drift |
| auth.py / api/auth.py overlap | Both files | Low | Duplicated auth concern |

### SQLite / PostgreSQL incompatibility workaround

`backend/auth.py` uses manual UUID generation (via Python's `uuid.uuid4()`) in
some places instead of `gen_random_uuid()` (PostgreSQL-only). This workaround
exists so that tests can run on SQLite. If you add new UUID generation in models,
verify it works on both databases or the test suite will fail.

### Duplicate files

- `alembic.ini` at repo root AND `backend/alembic.ini` — both present, both contain config
- `pipelineiq.db` at repo root AND `backend/pipelineiq.db` — SQLite dev databases committed to git

### Removed dependencies — do not re-add

| Package | Why removed | When |
|---|---|---|
| `@google/genai` | Unused, 500KB+ bundle overhead | v2.1.3 |
| `firebase-tools` | Unused devDependency | v2.1.3 |
| `aioredis` | Deprecated, replaced by `redis.asyncio` | v2.1.3 |
| `aiofiles` | Not imported anywhere | v2.1.3 |

---

## 21. Development Setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker and Docker Compose (recommended)
- PostgreSQL 15 (if not using Docker)
- Redis 7 (if not using Docker)
- Git

### Option A: Docker Compose (recommended — full stack)

```bash
git clone https://github.com/Siddharthk17/pipelineiq
cd pipelineiq

# Copy env template and configure
cp .env.example .env
# Edit .env — minimum required:
# POSTGRES_PASSWORD=any-password
# SECRET_KEY=minimum-32-character-string-here

# Start all 9 services
docker compose up --build -d

# Wait ~30 seconds for services to be ready
# Access:
# App:        http://localhost
# API docs:   http://localhost/docs
# Grafana:    http://localhost/grafana
# Flower:     http://localhost:5555
# Prometheus: http://localhost:9090

# Demo account is seeded automatically:
# demo@pipelineiq.app / Demo1234!
```

### Option B: Manual (backend + frontend separately)

```bash
git clone https://github.com/Siddharthk17/pipelineiq
cd pipelineiq

# Backend

cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate      # Linux/Mac
# venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp ../.env.example ../.env
# Edit .env with at minimum:
# DATABASE_URL=postgresql://postgres:password@localhost:5432/pipelineiq
# REDIS_URL=redis://localhost:6379/0
# SECRET_KEY=any-string-at-least-32-characters-long
# ENVIRONMENT=development
# UPLOAD_DIR=./uploads

# Run database migrations
cd ..
alembic upgrade head

# Seed demo data
python -m backend.scripts.seed_demo

# Start the API server
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Celery Worker (separate terminal)

cd backend
source venv/bin/activate
celery -A celery_app worker --loglevel=info --concurrency=2

# Celery Beat for schedules (separate terminal)

cd backend
source venv/bin/activate
celery -A celery_app beat --loglevel=info

# Frontend (separate terminal)

cd frontend
npm install
npm run dev
# Access at http://localhost:3000
```

### Minimum `.env` for development

```bash
# Required
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/pipelineiq
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=local-development-key-minimum-32-characters

# Defaults that work for development
ENVIRONMENT=development
UPLOAD_DIR=./uploads
MAX_UPLOAD_SIZE=52428800
MAX_PIPELINE_STEPS=50
MAX_ROWS_PER_FILE=1000000
STEP_TIMEOUT_SECONDS=300
```

### Port reference

| Service | Port | Direct access |
|---|---|---|
| API (Uvicorn) | 8000 | `http://localhost:8000` |
| Frontend (Next.js) | 3000 | `http://localhost:3000` |
| Nginx proxy | 80 | `http://localhost` |
| PostgreSQL | 5432 | `psql -h localhost -U postgres pipelineiq` |
| Redis | 6379 | `redis-cli -h localhost` |
| Flower (Celery UI) | 5555 | `http://localhost:5555` |
| Prometheus | 9090 | `http://localhost:9090` |
| Grafana | 3001 | `http://localhost:3001` |

---

## 22. Agent Instructions

### The single most important rule

Read this section before touching any file.

The `AGENTS.md` you are reading right now is the ground truth for this codebase.
If you understand everything in this document, you can safely make any change.
If you do not understand a section, read it again before proceeding.

---

### The four non-negotiable contracts

**Contract 1: Three-part rule for every new pipeline step type.**
Adding a new step type (e.g., `pivot`, `deduplicate`, `transpose`) is incomplete
unless all three of these exist simultaneously:
1. Execution method in `backend/pipeline/steps.py` → `StepExecutor._dispatch` dict
2. Lineage recording method in `backend/pipeline/lineage.py` → `LineageRecorder`
3. Test covering the step in `backend/tests/test_steps.py`

Missing any one silently breaks the product's core guarantee.

**Contract 2: schemas.py is updated first.**
When any API endpoint changes its request or response shape, `backend/schemas.py`
is updated before the router, before the tests. The schema is the contract.

**Contract 3: models.py changes require migrations.**
Every change to `backend/models.py` requires an Alembic migration with a working
`downgrade()` function. A migration without `downgrade()` is a one-way door.

**Contract 4: Pipeline engine has zero infrastructure dependencies.**
`PipelineRunner`, `StepExecutor`, `LineageRecorder` never directly import Redis,
SQLAlchemy, httpx, or any external system. All side effects are injected.

---

### Source of truth — one file per concern

| Concern | Authoritative file | Never duplicate to |
|---|---|---|
| ORM models | `backend/models.py` | Any other file |
| API schemas | `backend/schemas.py` | Router files |
| Prometheus metrics | `backend/metrics.py` | Service or router files |
| App configuration | `backend/config.py` | Inline os.environ calls |
| JWT utilities | `backend/auth.py` | Router files |
| Database sessions | `backend/database.py` → `get_db` | Inline SessionLocal() |
| Frontend API types | `frontend/lib/types.ts` | Component files |
| Theme list | `ThemeSelector.tsx` AND `CommandPalette.tsx` | (both must match) |

---

### Before making any change

1. Identify the source of truth files affected
2. Run the full test suite to establish a passing baseline:
   ```bash
   cd backend && pytest tests/ -v
   cd frontend && npm run test
   ```
3. Read the relevant section of AGENTS.md for the area being changed
4. Check `models.py` for constraints that might be violated
5. Check `exceptions.py` for existing error types before creating new ones

---

### Patterns to use

**Dispatch dict for step types (never if/elif):**
```python
# Add to dict
self._dispatch["new_step"] = self._execute_new_step

# Never:
if step.type == "load": ...
elif step.type == "new_step": ...  # Growing chain
```

**ProgressCallback for infrastructure independence:**
```python
# Inject the side effect
runner = PipelineRunner(config=config, progress_callback=lambda e: redis.publish(...))

# Never:
class PipelineRunner:
    def _emit(self, event):
        redis_client.publish(...)  # Direct dependency kills testability
```

**UUID conversion before every DB query:**
```python
from backend.utils.uuid_utils import as_uuid
run = db.get(PipelineRun, as_uuid(run_id))  # Handles case, format
```

**Raise typed exceptions, never return errors:**
```python
raise PipelineNotFoundError(run_id=run_id)  # Caught by global handler
# Never:
return JSONResponse(status_code=404, content={"detail": "not found"})
```

**Audit every state change:**
```python
await log_action(db=db, user_id=user.id, action="resource.verb", ...)
```

**Define new metrics in metrics.py:**
```python
# In metrics.py:
NEW_METRIC = Counter("pipelineiq_new_metric_total", "Description", ["label"])

# In code:
from backend.metrics import NEW_METRIC
NEW_METRIC.labels(label="value").inc()
```

---

### What to always do

- [ ] Update `backend/schemas.py` before changing any API contract
- [ ] Create an Alembic migration before deploying any `backend/models.py` change
- [ ] Add working `downgrade()` to every migration — test it with `alembic downgrade -1`
- [ ] Add lineage recording method in `LineageRecorder` for every new step type
- [ ] Add new step to `StepExecutor._dispatch`
- [ ] Add tests for new step in `test_steps.py`
- [ ] Call `log_action()` in every state-changing API handler
- [ ] Define new Prometheus metrics in `backend/metrics.py` only
- [ ] Apply rate limiter dependency to every new public endpoint
- [ ] Add `Field(description="...")` on every new Pydantic schema field
- [ ] Register new global keyboard shortcuts in `keybindingStore.ts`
- [ ] Update `frontend/lib/types.ts` when backend schemas change
- [ ] Use `as_uuid()` on all UUID path parameters before DB queries
- [ ] Sanitize pipeline and step names through `backend/utils/string_utils.py`
- [ ] Use `cache_delete_pattern()` not raw `KEYS` for Redis pattern deletion
- [ ] Use `datetime.now(timezone.utc)` — never `datetime.utcnow()` (deprecated)
- [ ] Use `PgJSONB` for JSON columns in models (not `JSONB` or `JSON` directly)
- [ ] Use `get_current_user` dependency for auth (never inline JWT decoding)
- [ ] Update `ThemeSelector.tsx` AND `CommandPalette.tsx` when adding a theme

---

### What to never do

- **Never** define an ORM model outside `backend/models.py`
- **Never** define a Pydantic schema outside `backend/schemas.py`
- **Never** define Prometheus metrics outside `backend/metrics.py` — circular import
- **Never** use `print()` for logging — use the `logger` instance
- **Never** import `backend/main.py` into any other module — circular import
- **Never** call `yaml.safe_load()` directly on user YAML — use `parse_pipeline_config()`
- **Never** use the user's `original_filename` as a filesystem path — path traversal
- **Never** add a step type without its `LineageRecorder` recording method — silent corruption
- **Never** add a step type without tests — untested transformation ships to production
- **Never** call Redis, the database, or external services from `PipelineRunner`,
  `StepExecutor`, or `LineageRecorder` — breaks testability
- **Never** query the database inside a loop — N+1 performance bug
- **Never** use `datetime.utcnow()` — deprecated in Python 3.12
- **Never** use `KEYS *pattern*` in Redis — use `SCAN` via `cache_delete_pattern()`
- **Never** return a raw webhook secret in an API response — return `has_secret: bool`
- **Never** set `verify_exp: False` in JWT decode — expired tokens become permanent
- **Never** add a NOT NULL column to a populated table in a single migration step
- **Never** write a migration without a tested `downgrade()` function
- **Never** use `any` in TypeScript without immediate type narrowing
- **Never** create raw `EventSource` in components — use `usePipelineRun` hook
- **Never** call the backend API with raw `fetch` — use `apiClient` from `lib/api.ts`
- **Never** put large data arrays in Zustand state — browser OOM
- **Never** add a third `alembic.ini` — the duplication at root and backend/ is known debt
- **Never** re-add `@google/genai`, `firebase-tools`, `aioredis`, or `aiofiles`
- **Never** update theme list in only one file — both `ThemeSelector.tsx` and
  `CommandPalette.tsx` must be updated simultaneously
- **Never** call `db.execute("raw SQL")` — use SQLAlchemy ORM for all queries
- **Never** store sensitive values (tokens, keys, passwords) in Zustand stores
- **Never** expose `/docs` or `/redoc` in production (disabled automatically when
  `ENVIRONMENT=production`)

---

### Before declaring done — complete checklist

```bash
# Backend tests — must all pass, zero new failures
cd backend && pytest tests/ -v
# Expected: 206 passed (or more if new tests added)

# Frontend type check — zero errors
cd frontend && npx tsc --noEmit

# Frontend lint — zero warnings or errors
cd frontend && npm run lint

# Frontend tests — must all pass
cd frontend && npm run test
# Expected: 93 passed (or more if new tests added)

# Integration — all 9 services start cleanly
docker compose up --build -d
curl http://localhost/health
# Expected: {"status": "ok", "db": "ok", "redis": "ok"}
```

**Per-feature checks:**

| If you added | Check |
|---|---|
| New step type | `StepExecutor._dispatch`, `LineageRecorder` method, `test_steps.py` test all exist |
| `models.py` change | Migration created, `downgrade()` tested with `alembic downgrade -1` then `alembic upgrade head` |
| API contract change | `schemas.py` updated first, `frontend/lib/types.ts` updated to match |
| New endpoint | Rate limiter applied, `log_action` called, all schema fields have descriptions |
| New Prometheus metric | Defined in `backend/metrics.py`, uses `pipelineiq_` prefix |
| New keyboard shortcut | Registered in `keybindingStore.ts`, no conflicts, documented in help modal |
| New theme | `ThemeSelector.tsx` BUILT_IN_THEMES array updated, `CommandPalette.tsx` themes array updated |
| New NOT NULL column | Three-migration pattern used (add nullable → backfill → add constraint) |
| File upload path change | Verified UUID-based path, never user-supplied filename |
| New webhook delivery | Goes through `deliver_webhook()` service, not raw httpx |
| Memory-intensive operation | Celery worker does not OOM on 100k-row test dataset |

---

*End of AGENTS.md — PipelineIQ holy grail reference document.*
*If any information in this document is inaccurate or missing, update this file immediately.*
*This document is the first file any agent or developer should read.*