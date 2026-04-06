# AGENTS.md — PipelineIQ
### The Holy Grail Reference Document & System Contract

> **THE AXIOM:**
> Every statement in this document is strictly derived from the actual codebase—source files, database migrations, test suites, configuration files, and audit reports. **Nothing is inferred. Nothing is hallucinated.** If a statement cannot be verified directly against the code, it does not exist here.
>
> **THE PROMISE:**
> Any human developer or autonomous AI agent reading this document from start to finish will acquire a perfect, zero-hallucination mental model of the entire PipelineIQ architecture. You will be able to navigate, debug, and extend this platform with absolute confidence, without reading a single source file line by line.
>
> **SYSTEM METRICS & STATE:**
> • **Authoritative Version:** `2.2.0` (Week 2 release — Data Profiler + 6 new step types)
> • **Known Anomaly:** `render.yaml` currently declares `APP_VERSION: "3.6.2"`. This is a documented configuration bug. Ignore it. `2.2.0` is the absolute truth.
> • **Scale:** `~25,000` lines of code │ `200+` tracked files │ `195` text files

---
## Table of Contents

1. [What This System Is](#1-what-this-system-is)
2. [Architecture](#2-architecture)
3. [Technology Stack — Every Package and Version](#3-technology-stack--every-package-and-version)
4. [Repository Structure — Every File Explained](#4-repository-structure--every-file-explained)
5. [Data Model — All 15 Entities](#5-data-model--all-15-entities)
6. [Database Schema — Complete Table Reference](#6-database-schema--complete-table-reference)
7. [Alembic Migration History — All 8 Revisions](#7-alembic-migration-history--all-8-revisions)
8. [API Reference — All Endpoints, All Routers](#8-api-reference--all-endpoints-all-routers)
9. [Authentication and Authorisation](#9-authentication-and-authorisation)
10. [Pipeline Engine — Complete Deep Reference](#10-pipeline-engine--complete-deep-reference)
11. [Business Logic — Every Rule Documented](#11-business-logic--every-rule-documented)
12. [Configuration Reference — All 55+ Variables](#12-configuration-reference--all-55-variables)
13. [Testing — All 352 Tests](#13-testing--all-352-tests)
14. [Frontend Architecture — Complete Reference](#14-frontend-architecture--complete-reference)
15. [Infrastructure and Deployment](#15-infrastructure-and-deployment)
16. [Observability — Metrics, Logs, Dashboards](#16-observability--metrics-logs-dashboards)
17. [Security Posture](#17-security-posture)
18. [Performance Profile](#18-performance-profile)
19. [Known Issues and Technical Debt](#19-known-issues-and-technical-debt)
20. [Development Setup — Step by Step](#20-development-setup--step-by-step)
21. [Agent Instructions](#21-agent-instructions)

---

## 1. What This System Is

PipelineIQ is a data pipeline orchestration engine and observability platform.
It solves the "black box" problem in data engineering by providing complete,
column-level traceability for every transformation applied to a dataset.

Users define complex ETL (Extract, Transform, Load) workflows using a declarative,
version-controlled YAML configuration. They upload CSV or JSON source datasets,
execute those workflows asynchronously, and observe every step through real-time
streaming, interactive lineage graphs, and automated schema drift detection.

### The Core Value Proposition

The core product guarantee is **column-level lineage**. Every pipeline run
automatically builds a Directed Acyclic Graph (DAG) using NetworkX where:
- Every **node** represents a column at a specific pipeline step, named as
  `"{step_name}.{column_name}"`
- Every **edge** represents a transformation between columns, labeled with the
  step type and transformation parameters

This DAG enables two primary analytical operations that no spreadsheet or
simple ETL tool provides:

**Backward ancestry tracing** — Given any output column (e.g., `amount_sum` in
a `save` step), trace backward through the graph to find exactly which source
file and source column it originated from, through every filter, join, and
aggregation in between. The answer might be: `amount_sum` ← `aggregate(by_region)`
← `amount` ← `filter(delivered_only)` ← `amount` ← `load(sales.csv)`.

**Forward impact analysis** — Given any source column (e.g., `amount` in
`sales.csv`), trace forward to find every downstream step and output file that
would be broken or changed if that column's format changed. An engineer can
answer "If I change `amount` from float64 to string, what reports break?" before
making the change.

### Who Uses It

Data analysts and engineers who need to build reproducible ETL processes without
writing custom Python scripts for every transformation. They interact through a
keyboard-driven, widget-based React workspace with real-time monitoring.

### What It Explicitly Does Not Do

These are hard boundaries enforced in code, not aspirational limitations:

- **No unstructured data** — CSV and JSON only. No images, PDFs, binary formats,
  Parquet, or Avro. No pipeline step executor handles non-tabular data.
- **No real-time streaming** — pipeline execution is batch-oriented and
  queue-based. Progress is streamed in real-time; the data processing is not.
  PipelineIQ is not Apache Flink. Not Kafka Streams.
- **No distributed compute** — the Pandas engine loads complete datasets into
  worker RAM. Not Apache Spark. Not Dask. One Celery worker processes one
  pipeline run at a time. Individual files are limited by worker RAM
  (~2GB–4GB per job effectively). `MAX_ROWS_PER_FILE` is enforced at 1,000,000.
- **No multi-worker parallelism within a single run** — steps execute
  sequentially in a single Celery worker. There is no DAG-based step
  parallelism within a pipeline run.

### Codebase Metrics

| Area | Lines | Files |
|------|-------|-------|
| Backend source (Python) | 9,186 | 48 |
| Backend tests (Python) | 5,098 | 16 |
| Frontend (TypeScript/TSX/CSS) | 6,317 | 45 |
| Infrastructure (Docker, Nginx, YAML) | 761 | 10 |
| Database migrations (Python) | 789 | 6 |
| **Total** | **~22,151** | **125** |

### Live Production System

| Component | Platform | URL |
|---|---|---|
| Backend API | Render.com (free tier) | https://pipelineiq-api.onrender.com |
| Frontend | Vercel (free tier) | https://pipeline-iq0.vercel.app |
| Database | Neon.tech PostgreSQL | us-east-1 connection pooler |
| Cache + Queue | Upstash Redis | TLS (rediss://) endpoint |

Demo credentials: `demo@pipelineiq.app` / `Demo1234!`

---

## 2. Architecture

### Architectural Pattern

PipelineIQ is a **Modular Layered Monolith** with a **Distributed Task Execution**
worker pattern. It is explicitly not microservices — the decision was made to keep
all backend code in one deployable unit while still allowing the worker tier to
scale independently of the API tier.

The layers are:
- **API Layer** — FastAPI routers handle HTTP, authentication, validation, SSE
- **Domain Layer** — `pipeline/` and `services/` contain all business logic
- **Worker Layer** — Celery processes handle long-running transformations
- **Persistence Layer** — SQLAlchemy ORM + Alembic migrations
- **Observability Layer** — Prometheus + Grafana + Sentry

### System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Browser                                   │
│  Next.js 15 · React 19 · Zustand · ReactFlow · CodeMirror · SSE     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP / SSE
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Nginx (Port 80)                                 │
│  Reverse proxy · Security headers · SSE buffering disabled          │
│  /metrics → internal networks only                                  │
│  /api/*/stream → 3600s timeout                                      │
└──────┬──────────────────┬──────────────────┬────────────────────────┘
       │                  │                  │
       ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌────────────────┐
│ FastAPI API  │  │ Next.js      │  │ Grafana        │
│ Port 8000    │  │ Port 3000    │  │ Port 3001      │
└──────┬───────┘  └──────────────┘  └───────┬────────┘
       │                                    │
       ├──── Celery (Redis broker) ───┐     │
       │                              │     │
       ▼                              ▼     ▼
┌──────────────┐  ┌──────────────┐  ┌────────────────┐
│  PostgreSQL  │  │    Redis 7   │  │  Prometheus    │
│  Port 5432   │  │  Port 6379   │  │  Port 9090     │
└──────────────┘  └──────────────┘  └────────────────┘
                       ↑
              Celery broker + result backend
              SSE pub/sub channels
              Application cache
```

### Component Map

**Backend API (`backend/api/`)**
- Responsibility: HTTP routing, request validation, JWT authentication, rate
  limiting, SSE stream management, response formatting
- All 13 routers registered under `/api/v1/` via `backend/api/router.py`
- Auth routes registered at `/auth/` directly in `main.py`
- Never contains business logic — delegates immediately to pipeline engine or services
- Communicates with: PostgreSQL (via get_db), Redis (SSE pub/sub), Celery (task dispatch)

**Pipeline Engine (`backend/pipeline/`)**
- Responsibility: YAML parsing, static validation, dry-run planning, step execution,
  column lineage DAG construction, schema drift detection, pipeline versioning
- Contains 8 modules: `parser.py`, `runner.py`, `steps.py`, `lineage.py`,
  `exceptions.py`, `validators.py`, `schema_drift.py`, `planner.py`, `versioning.py`
- Has zero infrastructure dependencies — no Redis, no database, no HTTP
- Communicates through dependency injection only (ProgressCallback protocol)

**Distributed Workers (`backend/tasks/`)**
- Responsibility: Celery task entrypoints for all long-running operations
- Contains: `pipeline_tasks.py`, `webhook_tasks.py`, `schedule_tasks.py`,
  `notification_tasks.py`
- Never called directly from HTTP — always via Celery broker (Redis)
- Owns state transitions: `PENDING → RUNNING → COMPLETED/FAILED/CANCELLED`

**Services (`backend/services/`)**
- Responsibility: Outbound communication to external systems
- Contains: `audit_service.py`, `webhook_service.py`, `notification_service.py`
- Called directly from routers and tasks (known architectural coupling, documented)
- External HTTP calls via `httpx`

**Frontend Workspace (`frontend/`)**
- Responsibility: Keyboard-driven, widget-based UI
- 5 independent workspaces, binary tree panel layout
- 8 widgets, 7 themes, command palette, Vim-style keybindings
- Real-time data via SSE; all state in Zustand stores

### Primary Data Flow — Pipeline Execution

This is the complete trace of a pipeline execution from HTTP request to browser update:

```
Step 1: Client sends POST /api/v1/pipelines/run with YAML body

Step 2: api/pipelines.py router receives request
        → Validates JWT (get_current_user dependency)
        → Rate limits (10/minute per user)
        → Calls parse_pipeline_config(yaml_string) — validates 13 rules
        → Checks permission: user must have RUNNER or OWNER for this pipeline name
        → Queries UploadedFile.id for all file_ids in YAML to verify existence

Step 3: Creates PipelineRun record in PostgreSQL
        status=PENDING, yaml_config=yaml_string, name=requested_name, user_id=user.id

Step 4: Dispatches execute_pipeline_task.delay(str(run.id))
        → Task serialized as JSON, pushed to Redis Celery broker
        → Returns 202 {"run_id": "uuid", "status": "PENDING"} immediately

Step 5: Celery worker picks up the task from Redis
        → Loads PipelineRun from PostgreSQL by run_id
        → Updates status to RUNNING, sets started_at = now()

Step 6: Worker creates ProgressCallback that publishes to Redis:
        channel = f"pipeline_progress:{run_id}"
        callback = lambda event: redis.publish(channel, event.model_dump_json())

Step 7: Worker creates PipelineRunner(config=parsed_config, progress_callback=callback)

Step 8: For each step in config.steps:
        8a. ProgressCallback publishes StepProgressEvent(status="STARTED", step_name=...)
        8b. StepExecutor._dispatch[step.type](step, current_dataframe) runs Pandas op
        8c. LineageRecorder.record_{type}_step(...) updates NetworkX DiGraph
        8d. df_registry[step.name] = result_dataframe
        8e. ProgressCallback publishes StepProgressEvent(status="COMPLETED", rows_in=N, rows_out=M, duration_ms=X)

Step 9: Simultaneously, SSE endpoint GET /api/v1/pipelines/{run_id}/stream:
        → Subscribed to Redis channel pipeline_progress:{run_id}
        → Forwards each message to browser EventSource as text/event-stream
        → Sends keepalive comment every 500ms to prevent proxy timeout

Step 10: Browser EventSource receives events and updates RunMonitorWidget in real-time

Step 11: After all steps complete:
         → LineageGraph record created (NetworkX serialized + React Flow layout pre-computed)
         → StepResult records created for each step
         → PipelineRun updated: status=COMPLETED, completed_at=now(), total_rows_in/out
         → ProgressCallback publishes pipeline_completed event
         → SSE stream closes on terminal event

Step 12: Async side effects dispatched as separate Celery tasks:
         → deliver_webhook_task.delay(...) for each active webhook
         → notification_tasks.py for Slack/email if configured
         → audit_service.log_action(...) for immutable audit record
```

### Architectural Boundaries

**Respected boundaries (the correct pattern):**
- Raw YAML strings never cross the parse_pipeline_config() boundary as dicts —
  only typed PipelineConfig dataclasses enter the engine
- PipelineRunner, StepExecutor, and LineageRecorder have zero imports of Redis,
  SQLAlchemy, or any infrastructure library
- Output data files (CSV/JSON results) are never stored in the database —
  only on disk in UPLOAD_DIR
- All Prometheus metric definitions live in metrics.py exclusively

**Known leaks (documented, intentional or acknowledged debt):**
- StepExecutor writes output files directly to disk during the `save` step,
  bypassing the database layer for data content (intentional — performance)
- audit_service.log_action() is called directly from routers, creating API-to-
  persistence coupling (acknowledged debt)
- Schema drift detection logic is split between api/files.py (reporting) and
  pipeline/schema_drift.py (logic) — the router has some logic that should be
  in the module (known debt)
- Some read endpoints (files, lineage, versions) have no per-user scoping —
  all users can see all data (acceptable for single-tenant, open issue for multi)

---

## 3. Technology Stack — Every Package and Version

### Backend Python Packages (`backend/requirements.txt`)

| Package | Version | Purpose in this codebase |
|---|---|---|
| `fastapi` | 0.109.0 | API framework. All 13 routers. Dependency injection. |
| `uvicorn[standard]` | 0.27.0 | ASGI server. Runs the FastAPI app. |
| `pydantic` | 2.5.3 | All request/response schemas in schemas.py. Config validation. |
| `pydantic-settings` | 2.1.0 | BaseSettings in config.py. Reads from .env and environment. |
| `sqlalchemy` | 2.0.25 | ORM. All 14 models in models.py. Modern Mapped syntax. |
| `alembic` | 1.13.1 | Database migrations. 8 revisions. |
| `psycopg2-binary` | 2.9.9 | PostgreSQL driver. Required for production. |
| `pandas` | 2.1.4 | Primary data transformation engine. All 9 step executors. |
| `numpy` | 1.26.3 | Numerical operations. Used by Pandas internally. |
| `pyyaml` | 6.0.1 | YAML parsing. Used inside parse_pipeline_config() only. |
| `networkx` | 3.2.1 | Column lineage DAG construction. DiGraph. Ancestry/impact queries. |
| `celery` | 5.3.6 | Task queue. Pipeline execution, webhooks, schedules, notifications. |
| `croniter` | 2.0.1 | Cron expression parsing and validation for schedules. |
| `redis` | 5.0.1 | Redis client. Celery broker, SSE pub/sub, cache, connection pool. |
| `slowapi` | 0.1.9 | Rate limiting. 4 tiers. Per-IP via Limiter backed by Redis. |
| `python-multipart` | 0.0.6 | Multipart form data. Required for file upload endpoints. |
| `python-dotenv` | 1.0.0 | .env file loading in development. |
| `python-jose[cryptography]` | 3.3.0 | JWT creation, signing (HS256), and verification. |
| `passlib[bcrypt]` | 1.7.4 | Password hashing. bcrypt algorithm. |
| `bcrypt` | 4.0.1 | bcrypt backend for passlib. |
| `httpx` | 0.26.0 | HTTP client. Used in webhook_service.py for outbound delivery. |
| `flower` | 2.0.1 | Celery monitoring web UI. Port 5555. |
| `prometheus-fastapi-instrumentator` | 6.1.0 | Auto-instruments FastAPI with HTTP metrics. |
| `sentry-sdk[fastapi]` | 1.39.1 | Error tracking. FastAPI + Celery + SQLAlchemy integrations. |
| `pytest` | 7.4.4 | Backend test runner. 259 tests. |
| `pytest-asyncio` | 0.23.3 | Async test support. |
| `factory-boy` | 3.3.0 | Test factory pattern for model creation. |

**Removed dependencies (do not re-add, removal was intentional):**
- `aioredis` — deprecated; replaced by `redis.asyncio` (removed v2.1.3)
- `aiofiles` — was imported nowhere (removed v2.1.3)

### Frontend npm Packages (`frontend/package.json`)

**Production dependencies:**

| Package | Version | Purpose in this codebase |
|---|---|---|
| `next` | ^15.4.9 | React framework. App Router. SSR. API proxy rewrites. |
| `react` | ^19.2.1 | UI library. All components. |
| `react-dom` | ^19.2.1 | React DOM renderer. |
| `zustand` | ^5.0.11 | State management. 4 stores, all persisted to localStorage. |
| `@xyflow/react` | ^12.10.1 | ReactFlow. Lineage graph visualization. 4 custom node types. |
| `@uiw/react-codemirror` | ^4.25.5 | CodeMirror React wrapper. YAML editor in PipelineEditorWidget. |
| `@codemirror/lang-yaml` | ^6.1.2 | YAML syntax highlighting for CodeMirror. |
| `@codemirror/state` | ^6.5.4 | CodeMirror state management. |
| `@codemirror/view` | ^6.39.16 | CodeMirror view layer. |
| `@dnd-kit/core` | ^6.3.1 | Drag and drop core. Widget repositioning. |
| `@dnd-kit/sortable` | ^10.0.0 | Sortable drag and drop. Widget ordering. |
| `@dnd-kit/utilities` | ^3.2.2 | DnD kit utilities. |
| `@hookform/resolvers` | ^5.2.1 | Form validation resolvers. Used in login/register. |
| `@tanstack/react-query` | ^5.90.21 | Server state management. API data fetching with caching. |
| `lucide-react` | ^0.553.0 | Icon set. Used throughout the UI. |
| `motion` | ^12.23.24 | Animation library (Motion/Framer). Modal transitions, counters. |
| `date-fns` | ^4.1.0 | Date formatting utilities. |
| `clsx` | ^2.1.1 | Class name utility. |
| `tailwind-merge` | ^3.3.1 | Tailwind class merging. Used in cn() utility function. |
| `class-variance-authority` | ^0.7.1 | CVA for component variant styling. |
| `postcss` | ^8.5.6 | CSS processing. Required by Tailwind. |
| `autoprefixer` | ^10.4.21 | CSS vendor prefixing. |

**Development dependencies:**

| Package | Version | Purpose |
|---|---|---|
| `typescript` | 5.9.3 | TypeScript compiler. strict mode enabled. |
| `tailwindcss` | 4.1.11 | CSS framework. v4 syntax. |
| `@tailwindcss/postcss` | 4.1.11 | PostCSS plugin for Tailwind v4. |
| `@tailwindcss/typography` | ^0.5.19 | Typography plugin for prose content. |
| `vitest` | ^4.0.18 | Frontend test runner. 93 tests. |
| `@testing-library/react` | ^16.3.2 | React Testing Library. Component testing. |
| `@testing-library/user-event` | ^14.6.1 | User event simulation for tests. |
| `@testing-library/jest-dom` | ^6.9.1 | DOM assertion matchers. |
| `jsdom` | ^28.1.0 | Browser simulation for tests. |
| `@vitejs/plugin-react` | ^5.1.4 | Vite React plugin for Vitest. |
| `eslint` | 9.39.1 | JavaScript/TypeScript linter. |
| `eslint-config-next` | 16.0.8 | Next.js ESLint configuration. |
| `tw-animate-css` | ^1.4.0 | Tailwind animation utilities. |
| `@types/node` | ^20 | Node.js TypeScript definitions. |
| `@types/react` | ^19 | React TypeScript definitions. |
| `@types/react-dom` | ^19 | React DOM TypeScript definitions. |

**Removed frontend dependencies (do not re-add):**
- `@google/genai` — was unused, 500KB+ bundle size cost (removed v2.1.3)
- `firebase-tools` — was unused devDependency (removed v2.1.3)

### Infrastructure

| Component | Technology | Version | Purpose |
|---|---|---|---|
| Container runtime | Docker + Docker Compose | — | 9-service local stack |
| Reverse proxy | Nginx | 1.25-alpine | SSL, SSE, security headers, routing |
| Metrics collection | Prometheus | 2.48.0 | Scrapes api:8000/metrics every 10s |
| Metrics visualization | Grafana | 10.2.0 | 10-panel operational dashboard |
| Container metrics | cAdvisor | — | Per-container CPU/memory |
| System metrics | node-exporter | — | Host system metrics for Prometheus |

### Data Stores

**PostgreSQL 15 (primary)**
Stores: users, uploaded file metadata, pipeline run metadata, step results,
serialized lineage graphs, audit logs, webhook configs, webhook deliveries,
pipeline schedules, pipeline permissions, pipeline versions, notification configs,
schema snapshots, file version chains.

Pool configuration: `pool_size=20`, `max_overflow=10`, `pool_pre_ping=True`,
`pool_recycle=3600`. Defined in `backend/database.py`.

**Redis 7 (multi-purpose)**
Four distinct use cases in this codebase:
1. **Celery broker** — task queue channel for pipeline execution and side effects
2. **Celery result backend** — stores Celery task completion state
3. **SSE pub/sub bridge** — `pipeline_progress:{run_id}` channels carry step events
4. **Application cache** — lineage graphs (1h TTL), dashboard stats (30s TTL)

Redis connection pooling: `redis.ConnectionPool` instantiated at module level in
`backend/utils/cache.py`. Never create a new `redis.Redis` client per request.

**Local Disk (`UPLOAD_DIR`)**
Stores: uploaded source CSV/JSON files, exported pipeline output files.
Default path: `/app/uploads`. In Docker Compose this is a named volume shared
between `api` and `worker` containers. File paths are always UUID-generated, never
user-supplied filenames.

---

## 4. Repository Structure — Every File Explained

**186 tracked files total. 30 top-level directories.**

```
pipelineiq/                              ← Project root
├── backend/                             ← All Python backend code
│   ├── api/                             ← 13 FastAPI router modules
│   │   ├── __init__.py                  ← Package init (empty)
│   │   ├── router.py                    ← Aggregates all sub-routers under /api/v1/
│   │   ├── files.py                     ← File upload, preview, schema, drift (458 lines)
│   │   ├── pipelines.py                 ← Validate, plan, run, stream, cancel, export (421 lines)
│   │   ├── lineage.py                   ← Graph retrieval, ancestry, impact (cached)
│   │   ├── versions.py                  ← Pipeline version list, diff, restore
│   │   ├── webhooks.py                  ← Webhook CRUD, test endpoint, delivery log
│   │   ├── audit.py                     ← Audit log viewer (admin: all, user: own)
│   │   ├── schedules.py                 ← Cron-based pipeline scheduling CRUD
│   │   ├── templates.py                 ← 5 pre-built pipeline templates (public)
│   │   ├── notifications.py             ← Slack/email notification config + test
│   │   ├── dashboard.py                 ← Per-user analytics (cached 30s)
│   │   ├── permissions.py               ← Per-pipeline RBAC management
│   │   └── debug.py                     ← Dev utilities, Sentry test (non-production only)
│   ├── pipeline/                        ← Core transformation engine (zero infra deps)
│   │   ├── __init__.py                  ← Package init (empty)
│   │   ├── parser.py                    ← YAML → PipelineConfig dataclasses (703 lines)
│   │   ├── runner.py                    ← Step orchestration + ProgressCallback (257 lines)
│   │   ├── steps.py                     ← 9 step executors via dispatch dict (606 lines)
│   │   ├── lineage.py                   ← NetworkX DAG + React Flow layout (634 lines)
│   │   ├── exceptions.py                ← 14-class hierarchy + fuzzy suggestions (443 lines)
│   │   ├── validators.py                ← 12 data quality check implementations
│   │   ├── schema_drift.py              ← Breaking/warning/info drift classification
│   │   ├── planner.py                   ← Dry-run execution planner (8 heuristics, 212 lines)
│   │   └── versioning.py                ← Pipeline version save + diff generation
│   ├── services/                        ← External communication (called from routers/tasks)
│   │   ├── __init__.py                  ← Package init (empty)
│   │   ├── audit_service.py             ← log_action() — append-only audit persistence
│   │   ├── webhook_service.py           ← HMAC-signed HTTP delivery, retry, delivery records
│   │   └── notification_service.py      ← Slack webhook + SMTP email delivery
│   ├── tasks/                           ← Celery task definitions (never called directly via HTTP)
│   │   ├── __init__.py                  ← Package init (empty)
│   │   ├── pipeline_tasks.py            ← execute_pipeline_task (primary long-running job)
│   │   ├── webhook_tasks.py             ← deliver_webhook_task (separate async HTTP delivery)
│   │   ├── schedule_tasks.py            ← Celery Beat schedule checker
│   │   └── notification_tasks.py        ← Async Slack/email notification delivery
│   ├── utils/                           ← Shared utility modules
│   │   ├── __init__.py                  ← Package init (empty)
│   │   ├── cache.py                     ← Redis cache helpers, SCAN-based pattern delete
│   │   ├── rate_limiter.py              ← slowapi Limiter + 4 rate limit tiers
│   │   ├── uuid_utils.py                ← as_uuid(), validate_uuid_format() — shared, not duplicated
│   │   ├── string_utils.py              ← sanitize_pipeline_name(), sanitize_step_name()
│   │   └── time_utils.py                ← utcnow() wrapper (replaces deprecated datetime.utcnow)
│   ├── alembic/                         ← Database migration system
│   │   ├── env.py                       ← Alembic environment configuration
│   │   ├── script.py.mako               ← Migration file template
│   │   └── versions/                    ← 8 migration scripts
│   │       ├── 97385cb62e0a_initial_schema.py
│   │       ├── 14a9b359a361_schema_snapshots.py
│   │       ├── c3f5e7a8b901_native_uuid.py
│   │       ├── d4e6f8a1b2c3_users_rbac.py
│   │       ├── e5f6a7b8c9d0_webhooks.py
│   │       ├── f6a7b8c9d0e1_audit_logs.py
│   │       ├── a1b2c3d4e5f6_performance_indexes.py
│   │       └── b2c3d4e5f6a7_feature_expansion.py
│   ├── tests/                           ← 259 backend tests across 20 executable test files
│   │   ├── conftest.py                  ← All fixtures: test_db, clients, sample data
│   │   ├── integration/
│   │   │   └── test_infrastructure.py   ← 6 infra integration checks (gated by RUN_INTEGRATION_TESTS=1)
│   │   ├── test_steps.py                ← 25 StepExecutor unit tests
│   │   ├── test_validators.py           ← 22 validation check tests
│   │   ├── test_parser.py               ← 18 YAML parsing tests
│   │   ├── test_lineage.py              ← 18 lineage graph tests
│   │   ├── test_auth.py                 ← 17 authentication tests
│   │   ├── test_planner.py              ← 15 dry-run planner tests
│   │   ├── test_versioning.py           ← 12 versioning and diff tests
│   │   ├── test_schema_drift.py         ← 10 drift detection tests
│   │   ├── test_webhooks.py             ← 9 webhook tests
│   │   ├── test_caching.py              ← 8 Redis cache tests
│   │   ├── test_security.py             ← 7 security penetration tests
│   │   ├── test_sse.py                  ← 9 SSE endpoint tests
│   │   ├── test_api.py                  ← 37 API integration tests
│   │   ├── test_rate_limiting.py        ← 6 rate limit enforcement tests
│   │   ├── test_performance.py          ← 5 performance benchmark tests
│   │   └── unit/infrastructure/
│   │       ├── test_celery_queues.py    ← 12 queue/routing invariants
│   │       ├── test_redis_connections.py← 11 Redis role/pool invariants
│   │       ├── test_sse_lifecycle.py    ← 8 SSE protocol lifecycle invariants
│   │       └── test_file_upload.py      ← 4 upload-path/ORJSON safety checks
│   ├── scripts/
│   │   ├── __init__.py
│   │   └── seed_demo.py                 ← Creates demo user + sample files for development
│   ├── sample_data/                     ← 4 CSV files + 3 YAML pipeline examples
│   ├── vendor/                          ← Was 86 .whl files — removed in v2.1.3, now empty
│   ├── main.py                          ← FastAPI app factory, middleware, exception handlers
│   ├── config.py                        ← Pydantic BaseSettings with 55+ env vars
│   ├── models.py                        ← ALL 14 SQLAlchemy ORM models (348 lines)
│   ├── schemas.py                       ← ALL 20+ Pydantic schemas (334 lines)
│   ├── metrics.py                       ← ALL Prometheus metric definitions (centralized)
│   ├── auth.py                          ← JWT utilities: get_current_user, get_current_admin
│   ├── celery_app.py                    ← Celery config, Redis TLS handling, Beat schedules
│   ├── database.py                      ← SQLAlchemy engine, connection pool, get_db dependency
│   ├── dependencies.py                  ← Shared FastAPI dependency functions
│   ├── alembic.ini                      ← Alembic config (ALSO exists at root — known duplicate)
│   ├── pipelineiq.db                    ← SQLite dev DB (should be gitignored — known issue)
│   ├── requirements.txt                 ← 28 Python packages with pinned versions
│   └── Dockerfile                       ← Python 3.11-slim, appuser (uid 1000)
├── frontend/                            ← Next.js 15 + React 19 application
│   ├── app/                             ← Next.js App Router pages
│   │   ├── globals.css                  ← Global styles + CSS variable theme definitions
│   │   ├── layout.tsx                   ← Root layout wrapping all pages with providers
│   │   ├── providers.tsx                ← React providers wrapper (QueryClient, AuthProvider)
│   │   ├── page.tsx                     ← Main dashboard / home page
│   │   ├── login/page.tsx               ← Login form with demo login button (275 lines)
│   │   └── register/page.tsx            ← Registration form with password validation (280 lines)
│   ├── components/
│   │   ├── ErrorBoundary.tsx            ← App-level React error boundary
│   │   ├── layout/                      ← Application shell components
│   │   │   ├── TopBar.tsx               ← Navigation, user info, theme switcher, presence
│   │   │   ├── WidgetGrid.tsx           ← Binary tree panel layout engine
│   │   │   ├── CommandPalette.tsx       ← Ctrl+K command palette (lists all 7 themes)
│   │   │   ├── TerminalLauncher.tsx     ← Alt+Enter widget launcher
│   │   │   ├── PresenceIndicator.tsx    ← Online user indicator (WebSocket-ready stub)
│   │   │   └── KeybindingsModal.tsx     ← Keyboard shortcut reference (Alt+K)
│   │   ├── widgets/                     ← 8 workspace widgets + shell + DAG
│   │   │   ├── WidgetShell.tsx          ← Common wrapper: title bar, resize, close controls
│   │   │   ├── FileUploadWidget.tsx     ← Drag-and-drop file upload
│   │   │   ├── FileRegistryWidget.tsx   ← File list with preview, schema, drift badge
│   │   │   ├── PipelineEditorWidget.tsx ← CodeMirror YAML editor, validate, plan (318 lines)
│   │   │   ├── RunMonitorWidget.tsx     ← Real-time SSE step progress monitor
│   │   │   ├── LineageGraphWidget.tsx   ← React Flow lineage DAG visualizer
│   │   │   ├── RunHistoryWidget.tsx     ← Historical run list with status filters
│   │   │   ├── QuickStatsWidget.tsx     ← Platform statistics overview
│   │   │   ├── VersionHistoryWidget.tsx ← Version list and YAML diff viewer (265 lines)
│   │   │   └── StepDAG.tsx              ← Horizontal step execution order diagram
│   │   ├── lineage/                     ← React Flow lineage visualization (251 lines total)
│   │   │   ├── LineageGraph.tsx         ← Main React Flow graph component
│   │   │   ├── LineageSidebar.tsx       ← Column detail panel on node click
│   │   │   └── nodes/                   ← 4 custom ReactFlow node type components
│   │   │       ├── SourceFileNode.tsx   ← Source file nodes (blue, left edge)
│   │   │       ├── StepNode.tsx         ← Transformation step nodes (purple)
│   │   │       ├── ColumnNode.tsx       ← Column-level nodes (grey)
│   │   │       └── OutputFileNode.tsx   ← Output file nodes (green, right edge)
│   │   ├── ui/                          ← shadcn/ui base components — NEVER MODIFY THESE
│   │   └── theme/
│   │       ├── ThemeSelector.tsx        ← BUILT_IN_THEMES array (7 entries, must be kept in sync)
│   │       └── ThemeBuilder.tsx         ← Custom theme creation with CSS variable editor
│   ├── hooks/                           ← Custom React hooks
│   │   ├── usePipelineRun.ts            ← SSE connection + exponential backoff reconnect
│   │   ├── useKeybindings.ts            ← Global keyboard shortcut registration
│   │   ├── useLineage.ts                ← Lineage graph data fetching and caching
│   │   ├── useTheme.ts                  ← Theme application to CSS variables
│   │   ├── useWidgetLayout.ts           ← Widget layout toggle and workspace management
│   │   └── use-mobile.ts                ← Responsive layout detection (useIsMobile)
│   ├── store/                           ← Zustand persisted stores
│   │   ├── widgetStore.ts               ← Binary tree layout, 5 workspaces (225 lines)
│   │   ├── pipelineStore.ts             ← Active run ID, YAML content, run data
│   │   ├── themeStore.ts                ← Active theme, custom theme definitions
│   │   └── keybindingStore.ts           ← 18 keyboard shortcut definitions
│   ├── lib/                             ← Shared client utilities
│   │   ├── api.ts                       ← Single fetchWithAuth + all API functions (252 lines)
│   │   ├── auth-context.tsx             ← AuthProvider, useAuth — JWT in localStorage
│   │   ├── types.ts                     ← TypeScript interfaces for all API response shapes
│   │   ├── constants.ts                 ← Widget IDs, default configs, keybinding definitions
│   │   └── utils.ts                     ← cn() classname merging utility
│   ├── public/                          ← Static assets
│   ├── __tests__/                       ← 93 frontend tests across 8 files
│   │   ├── setup.ts                     ← Test environment: jest-dom matchers, mocks
│   │   ├── api.test.ts                  ← 26 API client tests
│   │   ├── stores.test.ts               ← 26 Zustand store tests
│   │   ├── pages.test.tsx               ← 12 page component tests
│   │   ├── widgets.test.tsx             ← 11 widget component tests
│   │   ├── utils.test.ts                ← 7 utility function tests
│   │   ├── middleware.test.ts           ← 4 auth redirect tests
│   │   ├── auth-context.test.tsx        ← 4 AuthProvider tests
│   │   └── hooks.test.ts                ← 3 custom hook tests
│   ├── middleware.ts                    ← Next.js auth redirect middleware
│   ├── next.config.ts                   ← API proxy rewrites to backend
│   ├── tsconfig.json                    ← TypeScript strict mode, @ alias to root
│   ├── vitest.config.ts                 ← Vitest + jsdom + @ path alias
│   ├── eslint.config.mjs                ← ESLint configuration
│   ├── postcss.config.mjs               ← PostCSS config for Tailwind
│   ├── metadata.json                    ← Application metadata
│   ├── package.json                     ← 33 npm packages
│   └── Dockerfile                       ← Multi-stage Node.js 20 build
├── nginx/
│   ├── nginx.conf                       ← Worker processes 1024, keepalive 65s
│   ├── conf.d/pipelineiq.conf           ← Server block, routing rules, SSE config
│   └── Dockerfile                       ← FROM nginx:1.25-alpine
├── prometheus/
│   └── prometheus.yml                   ← Scrape: api:8000/metrics every 10s, localhost:9090
├── grafana/
│   └── provisioning/
│       ├── dashboards/
│       │   ├── dashboard.yml            ← Dashboard provider, updates every 10s
│       │   └── pipelineiq.json          ← 10-panel dashboard definition
│       └── datasources/
│           └── prometheus.yml           ← Prometheus at http://prometheus:9090
├── .github/
│   ├── SECRETS_REQUIRED.md             ← Documents which GitHub secrets are needed
│   └── workflows/
│       └── ci.yml                       ← 3-job CI: backend tests, frontend check, smoke test
├── postman/
│   ├── PipelineIQ.postman_collection.json    ← 23 requests in 6 folders
│   └── PipelineIQ.postman_environment.json  ← base_url, token, run_id, file_id, webhook_id
├── .env                                 ← Local environment variables (gitignored)
├── .env.example                         ← Template with all required/optional variables
├── .gitignore                           ← Git exclusions
├── alembic.ini                          ← Root-level Alembic config (DUPLICATE of backend/alembic.ini)
├── docker-compose.yml                   ← 9-service orchestration with health checks
├── render.yaml                          ← Render.com deployment blueprint
├── AGENTS.md                            ← This file
├── AUDIT_REPORT.md                      ← Full technical audit (February 2026)
├── CHANGELOG.md                         ← Version history from 0.1.2 to 2.1.4
├── README.md                            ← Public documentation
├── LICENSE                              ← Apache 2.0
└── pipelineiq.db                        ← Root-level SQLite (SHOULD be gitignored — known issue)
```

---

## 5. Data Model — All 15 Entities

All entities are defined in `backend/models.py`. No model is defined
anywhere else. This is a hard rule.

### Entity 1: `User`

Represents a registered platform account with role-based access.

**All fields:**
```
id                  UUID        PK, generated by _generate_uuid()
email               VARCHAR(255) UNIQUE, NOT NULL, indexed
username            VARCHAR(100) UNIQUE, NOT NULL, indexed
hashed_password     VARCHAR(255) NOT NULL — bcrypt via passlib
role                ENUM        NOT NULL, DEFAULT 'viewer' — CHECK IN ('admin', 'viewer')
is_active           BOOLEAN     NOT NULL, DEFAULT true
created_at          DATETIME    NOT NULL, server_default=func.now(), timezone=True
updated_at          DATETIME    NOT NULL, server_default=func.now(), timezone=True
```

**Relationships:**
- Has many `PipelineRun` (via user_id FK, SET NULL on delete)
- Has many `Webhook` (cascade delete)
- Has many `PipelineSchedule` (cascade delete)
- Has many `NotificationConfig` (cascade delete)
- Has many `PipelinePermission` (cascade delete)
- Has many `AuditLog` (via user_id FK, SET NULL on delete)

**Lifecycle:**
1. Created via POST /auth/register
2. First registered user automatically becomes admin (checked by counting rows)
3. Never deleted — is_active set to False for deactivation
4. role changed via PATCH /auth/users/{user_id}/role (admin only)

**Password requirements (enforced at registration):**
- Minimum 8 characters
- At least one uppercase letter
- At least one digit
- At least one special character (e.g., !@#$%^&*)

**Sensitive fields:** `hashed_password` (bcrypt), `email` (PII)

---

### Entity 2: `PipelineRun`

Represents a single execution of a user-defined pipeline configuration.

**All fields:**
```
id              UUID        PK, generated by _generate_uuid()
name            VARCHAR(255) NOT NULL — display name for this run
status          ENUM        NOT NULL, DEFAULT 'PENDING'
                            VALUES: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
yaml_config     TEXT        NOT NULL — the full YAML submitted for this run
created_at      DATETIME    NOT NULL, server_default=func.now(), indexed
started_at      DATETIME    NULLABLE — set when Celery worker picks up task
completed_at    DATETIME    NULLABLE — set on COMPLETED, FAILED, or CANCELLED
total_rows_in   INTEGER     NULLABLE — sum of rows_in across all steps
total_rows_out  INTEGER     NULLABLE — rows_out of the final save step
error_message   TEXT        NULLABLE — populated on FAILED status
user_id         UUID        FK(users.id), NULLABLE, SET NULL on user delete, indexed
celery_task_id  VARCHAR(255) NULLABLE — stored for task revocation on cancellation
```

**Computed property (not stored):**
```
duration_ms     = (completed_at - started_at).total_seconds() * 1000
```

**Relationships:**
- Has many `StepResult` (cascade delete-orphan)
- Has one `LineageGraph` (cascade delete-orphan, unique constraint on pipeline_run_id)
- Belongs to `User` (nullable)

**Status state machine:**
```
[Created] → PENDING
PENDING → RUNNING    (when Celery worker picks up the task)
RUNNING → COMPLETED  (all steps succeeded)
RUNNING → FAILED     (any step raised StepExecutionError)
RUNNING → CANCELLED  (user called POST /pipelines/{run_id}/cancel)
```

**CANCELLED behaviour:**
- `celery_app.control.revoke(celery_task_id, terminate=True)` is called
- Status is immediately set to CANCELLED regardless of whether revoke succeeds
- Partially executed steps are orphaned (no cleanup of written files)

---

### Entity 3: `StepResult`

Represents the outcome metrics of a single transformation step within a run.

**All fields:**
```
id                UUID        PK, generated by _generate_uuid()
pipeline_run_id   UUID        FK(pipeline_runs.id) NOT NULL, indexed, CASCADE delete
step_name         VARCHAR(255) NOT NULL — matches the step name in YAML
step_type         VARCHAR(50) NOT NULL — one of the 9 step types
step_index        INTEGER     NOT NULL — 0-based position in the steps list
status            ENUM        NOT NULL, DEFAULT 'PENDING'
                              VALUES: PENDING, RUNNING, COMPLETED, FAILED, SKIPPED
rows_in           INTEGER     NULLABLE — rows in the input DataFrame
rows_out          INTEGER     NULLABLE — rows in the output DataFrame
columns_in        JSONB       NULLABLE — list of column names entering this step
columns_out       JSONB       NULLABLE — list of column names leaving this step
duration_ms       INTEGER     NULLABLE — step execution time in milliseconds
warnings          JSONB       NULLABLE — validation warnings from validate steps
error_message     TEXT        NULLABLE — error details on FAILED status
created_at        DATETIME    NOT NULL, server_default=func.now()
```

**Lifecycle:** Created and persisted by the Celery worker after each step
completes or fails. Written inside execute_pipeline_task, not via the API.

---

### Entity 4: `LineageGraph`

The serialized column-level lineage trace for a specific pipeline run.

**All fields:**
```
id               UUID    PK, generated by _generate_uuid()
pipeline_run_id  UUID    FK(pipeline_runs.id) UNIQUE NOT NULL — one graph per run
graph_data       JSONB   NOT NULL — NetworkX DiGraph in node-link JSON format
react_flow_data  JSONB   NOT NULL — pre-computed React Flow {nodes: [], edges: []}
created_at       DATETIME NOT NULL, server_default=func.now()
```

**Critical design decision:** The React Flow layout is computed once during
execution by the LineageRecorder and stored here. It is NEVER recomputed on
subsequent API calls. This means the `GET /api/v1/lineage/{run_id}` endpoint
simply reads and returns this record — it never calls NetworkX again.

**Layout algorithm (stored in react_flow_data):**
1. Topological sort of the NetworkX DiGraph
2. Layer assignment: each node's layer = max(predecessor layers) + 1
3. Node position: x = layer_index × 300px, y = position_within_layer × 80px

**Node ID naming convention (used in graph_data):**
- Source file: `"file::{file_id}"`
- Step: `"step::{step_name}"`
- Column at step: `"col::{step_name}::{column_name}"`
- Output file: `"output::{step_name}::{filename}"`

---

### Entity 5: `UploadedFile`

A source dataset (CSV or JSON) available for pipeline input.

**All fields:**
```
id                   UUID        PK, generated by _generate_uuid()
original_filename    VARCHAR(255) NOT NULL — user-supplied name, display only
stored_path          VARCHAR(512) NOT NULL — UUID-based filesystem path, never user-supplied
file_size_bytes      INTEGER     NOT NULL — file size in bytes
row_count            INTEGER     NOT NULL — rows detected after reading
column_count         INTEGER     NOT NULL — columns detected after reading
columns              JSONB       NOT NULL — list of column names: ["col_a", "col_b", ...]
dtypes               JSONB       NOT NULL — column dtype map: {"col_a": "int64", ...}
version              INTEGER     NOT NULL, DEFAULT 1 — increments on re-upload of same name
previous_version_id  UUID        FK(uploaded_files.id) NULLABLE, SET NULL — version chain
created_at           DATETIME    NOT NULL, server_default=func.now()
```

**File storage path:**
```
stored_path = f"{UPLOAD_DIR}/{uuid4()}.{detected_extension}"
```
The extension is detected from content_type or the original filename.
The user-supplied `original_filename` is NEVER used as a filesystem path.

**Versioning:** When a file with the same `original_filename` is uploaded again,
`version` increments and `previous_version_id` points to the previous record.
Old files remain on disk and in the database.

**Relationships:**
- Has many `SchemaSnapshot` (cascade delete)

---

### Entity 6: `SchemaSnapshot`

A point-in-time snapshot of a file's schema used for drift detection.

**All fields:**
```
id          UUID     PK, generated by _generate_uuid()
file_id     UUID     FK(uploaded_files.id) NOT NULL, CASCADE delete, indexed
run_id      UUID     FK(pipeline_runs.id) NULLABLE — which run triggered this snapshot
columns     JSONB    NOT NULL — list of column names at snapshot time
dtypes      JSONB    NOT NULL — dtype map at snapshot time
row_count   INTEGER  NOT NULL — row count at snapshot time
captured_at DATETIME NOT NULL, server_default=func.now()
```

**When snapshots are created:** On every file upload (via POST /api/v1/files/upload).
The new file's schema is compared against the most recent snapshot for the same
`original_filename` to detect drift.

---

### Entity 7: `PipelineVersion`

A versioned snapshot of a pipeline's YAML configuration over time.

**All fields:**
```
id              UUID        PK, generated by _generate_uuid()
pipeline_name   VARCHAR(255) NOT NULL, indexed
version_number  INTEGER     NOT NULL
yaml_config     TEXT        NOT NULL — the full YAML at this version
created_at      DATETIME    NOT NULL, server_default=func.now()
run_id          UUID        FK(pipeline_runs.id) NULLABLE — which run created this version
change_summary  TEXT        NULLABLE — human-readable diff summary
UNIQUE          (pipeline_name, version_number)
```

**Version increment logic:** Implemented in `pipeline/versioning.py`. Each pipeline
run saves a new version. The version number is `MAX(version_number) + 1` for the
given pipeline name.

**Diff format (computed on demand, not stored):**
```json
{
  "steps_added": ["new_step_name"],
  "steps_removed": ["old_step_name"],
  "steps_modified": [{"name": "step_name", "changes": {...}}],
  "unified_diff": "--- v1\n+++ v2\n@@ -1,5 +1,6 @@\n..."
}
```

---

### Entity 8: `Webhook`

An outbound webhook endpoint registration.

**All fields:**
```
id          UUID        PK, generated by _generate_uuid()
user_id     UUID        FK(users.id) NOT NULL, CASCADE delete
url         VARCHAR(2048) NOT NULL — must start with http:// or https://
secret      VARCHAR(255) NULLABLE — raw HMAC signing secret (never exposed in API)
events      JSONB       NOT NULL, DEFAULT ["pipeline_completed", "pipeline_failed"]
is_active   BOOLEAN     NOT NULL, DEFAULT true
created_at  DATETIME    NOT NULL, server_default=func.now()
```

**CRITICAL:** The `secret` field is stored but `WebhookResponse` schema (Pydantic)
returns `has_secret: bool` — the raw secret is NEVER returned via any API response.
This was a security fix in v2.1.3. The secret is used only during HMAC signing.

**Per-user limit:** Maximum 10 webhooks per user (enforced at creation time).

---

### Entity 9: `WebhookDelivery`

An individual outbound webhook delivery attempt record.

**All fields:**
```
id              UUID        PK, generated by _generate_uuid()
webhook_id      UUID        FK(webhooks.id) NOT NULL, CASCADE delete, indexed
run_id          UUID        NULLABLE — which pipeline run triggered this delivery
event_type      VARCHAR(50) NOT NULL — e.g., "pipeline_completed", "pipeline_failed"
payload         JSONB       NOT NULL — the full JSON body sent to the webhook URL
response_status INTEGER     NULLABLE — HTTP status code received from remote
response_body   TEXT        NULLABLE — first N bytes of response body
attempt_number  INTEGER     NOT NULL, DEFAULT 1 — 1, 2, or 3
delivered_at    DATETIME    NULLABLE — timestamp of successful delivery
failed_at       DATETIME    NULLABLE — timestamp of final failure
error_message   TEXT        NULLABLE — network error or timeout description
created_at      DATETIME    NOT NULL, server_default=func.now()
```

**Retry logic:** Maximum 3 attempts. Implemented in `tasks/webhook_tasks.py`.
The task is retried with increasing delays. All attempts create a new
`WebhookDelivery` record with `attempt_number` incrementing.

---

### Entity 10: `AuditLog`

An immutable record of every significant user action in the system.

**All fields:**
```
id              UUID        PK, generated by _generate_uuid()
user_id         UUID        FK(users.id) NULLABLE, SET NULL, indexed
action          VARCHAR(100) NOT NULL, indexed — format: "resource.action"
resource_type   VARCHAR(50) NULLABLE — e.g., "pipeline_run", "uploaded_file"
resource_id     UUID        NULLABLE — the ID of the affected resource
details         JSONB       NULLABLE, DEFAULT {} — additional context
ip_address      VARCHAR(45) NULLABLE — IPv4 or IPv6 address of the request
user_agent      TEXT        NULLABLE — User-Agent header string
created_at      DATETIME    NOT NULL, server_default=func.now(), indexed
```

**IMMUTABILITY ENFORCED AT DATABASE LEVEL.**
Migration `f6a7b8c9d0e1` installs a PostgreSQL trigger that raises an exception
on any `UPDATE` or `DELETE` operation against `audit_logs`. Records are permanently
append-only. Application code cannot override this.

**Action naming convention:** `resource.action` e.g.:
- `"file.upload"`, `"file.delete"`
- `"pipeline.run"`, `"pipeline.cancel"`
- `"user.register"`, `"user.login"`, `"user.logout"`
- `"webhook.create"`, `"webhook.delete"`, `"webhook.test"`
- `"schedule.create"`, `"schedule.delete"`, `"schedule.toggle"`
- `"permission.grant"`, `"permission.revoke"`
- `"role.change"`

---

### Entity 11: `PipelineSchedule`

A cron-based recurring pipeline execution configuration.

**All fields:**
```
id              UUID        PK, generated by _generate_uuid()
user_id         UUID        FK(users.id) NOT NULL, CASCADE delete
pipeline_name   VARCHAR(255) NOT NULL
yaml_config     TEXT        NOT NULL — the YAML to execute on schedule
cron_expression VARCHAR(100) NOT NULL — validated by croniter at creation
is_active       BOOLEAN     NOT NULL, DEFAULT true
last_run_at     DATETIME    NULLABLE, timezone=True — when last execution started
next_run_at     DATETIME    NULLABLE, timezone=True — computed by croniter
created_at      DATETIME    NOT NULL, server_default=func.now()
```

**Cron validation:** `croniter(cron_expression)` is called at creation. If the
expression is invalid, a validation error is raised before the record is saved.

**Execution:** `schedule_tasks.py` Celery Beat task polls active schedules and
dispatches `execute_pipeline_task.delay(...)` when `next_run_at` has passed.

---

### Entity 12: `NotificationConfig`

A user notification channel configuration (Slack or email).

**All fields:**
```
id          UUID    PK, generated by _generate_uuid()
user_id     UUID    FK(users.id) NOT NULL, CASCADE delete
type        ENUM    NOT NULL — VALUES: slack, email
config      JSONB   NOT NULL, DEFAULT {}
            For slack: {"slack_webhook_url": "https://hooks.slack.com/..."}
            For email: {"email_to": "user@example.com"}
events      JSONB   NOT NULL, DEFAULT ["pipeline_completed", "pipeline_failed"]
is_active   BOOLEAN NOT NULL, DEFAULT true
created_at  DATETIME NOT NULL, server_default=func.now()
```

---

### Entity 13: `PipelinePermission`

Per-pipeline access control entry for fine-grained RBAC.

**All fields:**
```
id               UUID        PK, generated by _generate_uuid()
pipeline_name    VARCHAR(255) NOT NULL, indexed
user_id          UUID        FK(users.id) NOT NULL, CASCADE delete
permission_level ENUM        NOT NULL — VALUES: owner, runner, viewer
created_at       DATETIME    NOT NULL, server_default=func.now()
UNIQUE           (pipeline_name, user_id) — one permission level per user per pipeline
```

**Permission semantics:**
- `owner` — can run, view, delete, and manage permissions for this pipeline name
- `runner` — can run and view runs for this pipeline name
- `viewer` — can view run history and lineage for this pipeline name only

**Admin override:** Users with `role=admin` bypass all permission checks.
The `get_current_admin` dependency handles admin access.

**Known gap:** Per-pipeline permissions are defined but enforcement is incomplete.
The pipeline execution endpoint does not fully enforce `runner` vs `viewer`
distinctions. This is documented in AUDIT_REPORT.md as an open issue.

---

### Entity 14: `PipelineTemplate` (special case)

Pre-seeded, read-only template entries. Unlike other entities, templates are not
stored in a database table — they are defined as static data structures in
`backend/api/templates.py` and returned via API.

**5 available templates:**
1. **Basic ETL** — load → filter → aggregate → save
2. **Data Cleaning** — load → select → rename → validate → save
3. **Data Validation** — load → validate (multiple rules) → save
4. **Aggregation Report** — load → filter → aggregate → sort → save
5. **Merge/Join** — load (×2) → join → select → save

Each template has: `id`, `name`, `description`, `category`, `yaml_config`

### Entity 15: `FileProfile` (added v2.2.0)

Automatic data profile generated for each uploaded file. Captures statistical
metadata including completeness, uniqueness, semantic type inference, and
distribution characteristics.

**All fields:**
```
id                   UUID        PK, generated by _generate_uuid()
file_id             UUID        FK(uploaded_files.id) NOT NULL, CASCADE delete, indexed
column_name         VARCHAR(255) NOT NULL — name of the profiled column
semantic_type        VARCHAR(50) NOT NULL — inferred type: integer, float, categorical,
                             datetime, text, boolean, unknown
completeness         FLOAT       NOT NULL — percentage of non-null values (0.0-1.0)
uniqueness           FLOAT       NOT NULL — percentage of unique values (0.0-1.0)
min_value            VARCHAR(255) NULLABLE — minimum value (for numeric/datetime)
max_value            VARCHAR(255) NULLABLE — maximum value (for numeric/datetime)
mean_value           FLOAT       NULLABLE — mean (for numeric only)
median_value         FLOAT       NULLABLE — median (for numeric only)
std_dev              FLOAT       NULLABLE — standard deviation (for numeric only)
top_values           JSONB       NULLABLE — top 5 most frequent values with counts
histogram            JSONB       NULLABLE — histogram bin data for numeric columns
created_at           DATETIME    NOT NULL, server_default=func.now(), timezone=True
```

**Semantic type inference rules:**
- `integer` — int64 dtype with >90% unique values and no decimals
- `float` — float64 dtype
- `categorical` — any dtype with <10% unique values (cardinality threshold)
- `datetime` — matches common date patterns (ISO, US, EU formats)
- `boolean` — only true/false/0/1 values
- `text` — object dtype with high cardinality (>50 unique values)
- `unknown` — fallback when no pattern detected

**Triggered on:** Every file upload via POST /api/v1/files/upload
**Processing:** Runs asynchronously via Celery task on 'bulk' queue

---

## 6. Database Schema — Complete Table Reference

### Complete Column-Level Schema

**Table: `users`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | PostgreSQL gen_random_uuid() in prod, manual in tests |
| `email` | VARCHAR(255) | UNIQUE, NOT NULL, INDEX | RFC 5322 validated at registration |
| `username` | VARCHAR(100) | UNIQUE, NOT NULL, INDEX | 3–50 chars, alphanumeric + underscore |
| `hashed_password` | VARCHAR(255) | NOT NULL | bcrypt hash, never plain text |
| `role` | VARCHAR(20) | NOT NULL, DEFAULT 'viewer' | CHECK IN ('admin', 'viewer') |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT true | False = deactivated, not deleted |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | Timezone-aware |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | Timezone-aware |

**Table: `pipeline_runs`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `name` | VARCHAR(255) | NOT NULL | Display name, sanitized |
| `status` | ENUM | NOT NULL, DEFAULT 'PENDING' | PENDING/RUNNING/COMPLETED/FAILED/CANCELLED |
| `yaml_config` | TEXT | NOT NULL | Full YAML submitted for this run |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now(), INDEX | Primary sort key |
| `started_at` | TIMESTAMPTZ | NULLABLE | Set by Celery worker on pickup |
| `completed_at` | TIMESTAMPTZ | NULLABLE | Set on terminal state |
| `total_rows_in` | INTEGER | NULLABLE | Sum rows_in across steps |
| `total_rows_out` | INTEGER | NULLABLE | Rows from final save step |
| `error_message` | TEXT | NULLABLE | Populated on FAILED |
| `user_id` | UUID | FK(users.id), NULLABLE, SET NULL, INDEX | |
| `celery_task_id` | VARCHAR(255) | NULLABLE | For revocation on cancel |

**Table: `step_results`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `pipeline_run_id` | UUID | FK(pipeline_runs.id), NOT NULL, INDEX, CASCADE | |
| `step_name` | VARCHAR(255) | NOT NULL | From YAML step name field |
| `step_type` | VARCHAR(50) | NOT NULL | One of 9 step types |
| `step_index` | INTEGER | NOT NULL | 0-based position, INDEX |
| `status` | ENUM | NOT NULL, DEFAULT 'PENDING' | PENDING/RUNNING/COMPLETED/FAILED/SKIPPED |
| `rows_in` | INTEGER | NULLABLE | |
| `rows_out` | INTEGER | NULLABLE | |
| `columns_in` | JSONB | NULLABLE | `["col_a", "col_b"]` |
| `columns_out` | JSONB | NULLABLE | `["col_a", "col_c"]` |
| `duration_ms` | INTEGER | NULLABLE | |
| `warnings` | JSONB | NULLABLE | Validation step warnings |
| `error_message` | TEXT | NULLABLE | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Table: `lineage_graphs`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `pipeline_run_id` | UUID | FK(pipeline_runs.id), UNIQUE, NOT NULL | One per run |
| `graph_data` | JSONB | NOT NULL | NetworkX node-link serialization |
| `react_flow_data` | JSONB | NOT NULL | Pre-computed {nodes, edges} |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Table: `uploaded_files`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `original_filename` | VARCHAR(255) | NOT NULL | Display only — NEVER used as filesystem path |
| `stored_path` | VARCHAR(512) | NOT NULL | UUID-based path in UPLOAD_DIR |
| `file_size_bytes` | INTEGER | NOT NULL | Bytes |
| `row_count` | INTEGER | NOT NULL | Rows detected after read |
| `column_count` | INTEGER | NOT NULL | Columns detected |
| `columns` | JSONB | NOT NULL | `["col_a", "col_b"]` |
| `dtypes` | JSONB | NOT NULL | `{"col_a": "int64"}` |
| `version` | INTEGER | NOT NULL, DEFAULT 1 | Increments on re-upload |
| `previous_version_id` | UUID | FK(uploaded_files.id), NULLABLE, SET NULL | Version chain |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Table: `schema_snapshots`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `file_id` | UUID | FK(uploaded_files.id), NOT NULL, INDEX, CASCADE | |
| `run_id` | UUID | FK(pipeline_runs.id), NULLABLE | Which run triggered snapshot |
| `columns` | JSONB | NOT NULL | Column names at snapshot time |
| `dtypes` | JSONB | NOT NULL | Dtypes at snapshot time |
| `row_count` | INTEGER | NOT NULL | |
| `captured_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Table: `pipeline_versions`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `pipeline_name` | VARCHAR(255) | NOT NULL, INDEX | |
| `version_number` | INTEGER | NOT NULL | |
| `yaml_config` | TEXT | NOT NULL | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |
| `run_id` | UUID | FK(pipeline_runs.id), NULLABLE | |
| `change_summary` | TEXT | NULLABLE | Human-readable diff |
| — | UNIQUE | (pipeline_name, version_number) | |

**Table: `webhooks`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `user_id` | UUID | FK(users.id), NOT NULL, CASCADE | |
| `url` | VARCHAR(2048) | NOT NULL | Must start with http:// or https:// |
| `secret` | VARCHAR(255) | NULLABLE | Never exposed in API responses |
| `events` | JSONB | NOT NULL | `["pipeline_completed", "pipeline_failed"]` |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT true | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Table: `webhook_deliveries`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `webhook_id` | UUID | FK(webhooks.id), NOT NULL, INDEX, CASCADE | |
| `run_id` | UUID | NULLABLE | |
| `event_type` | VARCHAR(50) | NOT NULL | |
| `payload` | JSONB | NOT NULL | Full body sent |
| `response_status` | INTEGER | NULLABLE | HTTP status received |
| `response_body` | TEXT | NULLABLE | Truncated response |
| `attempt_number` | INTEGER | NOT NULL, DEFAULT 1 | |
| `delivered_at` | TIMESTAMPTZ | NULLABLE | |
| `failed_at` | TIMESTAMPTZ | NULLABLE | |
| `error_message` | TEXT | NULLABLE | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Table: `audit_logs`** ← IMMUTABLE VIA DATABASE TRIGGER

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `user_id` | UUID | FK(users.id), NULLABLE, SET NULL, INDEX | |
| `action` | VARCHAR(100) | NOT NULL, INDEX | Format: "resource.action" |
| `resource_type` | VARCHAR(50) | NULLABLE | |
| `resource_id` | UUID | NULLABLE | |
| `details` | JSONB | NULLABLE, DEFAULT {} | |
| `ip_address` | VARCHAR(45) | NULLABLE | Supports IPv6 |
| `user_agent` | TEXT | NULLABLE | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now(), INDEX | |

**Table: `pipeline_schedules`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `user_id` | UUID | FK(users.id), NOT NULL, CASCADE | |
| `pipeline_name` | VARCHAR(255) | NOT NULL | |
| `yaml_config` | TEXT | NOT NULL | |
| `cron_expression` | VARCHAR(100) | NOT NULL | croniter-validated |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT true | |
| `last_run_at` | TIMESTAMPTZ | NULLABLE | |
| `next_run_at` | TIMESTAMPTZ | NULLABLE | Computed by croniter |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Table: `notification_configs`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `user_id` | UUID | FK(users.id), NOT NULL, CASCADE | |
| `type` | ENUM | NOT NULL | VALUES: slack, email |
| `config` | JSONB | NOT NULL, DEFAULT {} | Channel-specific config |
| `events` | JSONB | NOT NULL | Event subscriptions |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT true | |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Table: `pipeline_permissions`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK, NOT NULL | |
| `pipeline_name` | VARCHAR(255) | NOT NULL, INDEX | |
| `user_id` | UUID | FK(users.id), NOT NULL, CASCADE | |
| `permission_level` | ENUM | NOT NULL | VALUES: owner, runner, viewer |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |
| — | UNIQUE | (pipeline_name, user_id) | One record per user per pipeline |

### Performance Indexes Added in Migration 7

Migration `a1b2c3d4e5f6` added these composite and single-column indexes:
```sql
CREATE INDEX ix_pipeline_runs_status ON pipeline_runs(status);
CREATE INDEX ix_pipeline_runs_created_at ON pipeline_runs(created_at);
CREATE INDEX ix_step_results_pipeline_run_id ON step_results(pipeline_run_id);
CREATE INDEX ix_webhook_deliveries_webhook_id ON webhook_deliveries(webhook_id);
CREATE INDEX ix_audit_logs_user_created ON audit_logs(user_id, created_at);
```

Plus implicit indexes from UNIQUE constraints and foreign keys.

### JSON Column Type — PgJSONB

`PgJSONB` is a custom SQLAlchemy type defined in `backend/models.py` that maps to:
- `JSONB` on PostgreSQL (binary JSON, indexed, queryable)
- `JSON` on SQLite (text — used in tests only)

This is why tests work against SQLite: the custom type degrades gracefully.
Never use raw `JSONB` type directly — always use `PgJSONB`.

---

## 7. Alembic Migration History — All 8 Revisions

All migrations are in `backend/alembic/versions/`. Run with `alembic upgrade head`.

### Migration 1: `97385cb62e0a` — Initial Schema

**What it created:**
- `pipeline_runs` table — core execution records
- `uploaded_files` table — file metadata
- `lineage_graphs` table — serialized lineage storage
- `step_results` table — per-step metrics
- `pipeline_versions` table — YAML version history
- `schema_snapshots` table — schema drift baseline records

**Limitations of this revision:**
- All IDs were `VARCHAR(36)` string UUIDs (not native PostgreSQL UUID type)
- JSON columns used `JSON` type (not `JSONB`)
- Some FK constraints were missing
- No user authentication

---

### Migration 2: `14a9b359a361` — Schema Snapshots and Pipeline Versions

**What it changed:** Minor refinements to schema snapshot and pipeline version
table structures. Primarily a placeholder revision during development.

---

### Migration 3: `c3f5e7a8b901` — CRITICAL: PostgreSQL Native Types

**What it changed:**
- Migrated ALL `VARCHAR(36)` ID columns to native PostgreSQL `UUID` type
- Migrated ALL `JSON` columns to `JSONB` type
- Dropped and recreated all FK constraints to use native UUID types
- Dropped and recreated all UNIQUE constraints

**WARNING:** This migration contains destructive operations. It cannot be run
against a non-empty SQLite database. It is the reason why tests use SQLite
with VARCHAR(36) style UUIDs through the `_generate_uuid()` Python function
rather than PostgreSQL's `gen_random_uuid()` SQL function.

**Downgrade complexity:** The `downgrade()` function reverses the UUID→VARCHAR
migration. It is complex and has not been tested against production data.

---

### Migration 4: `d4e6f8a1b2c3` — Users Table and RBAC

**What it created:**
- `users` table with `email`, `username`, `hashed_password`, `role`, `is_active`
- Unique indexes on `email` and `username`
- `user_id` FK column on `pipeline_runs`
- CHECK constraint on `role` column (admin/viewer values only)

---

### Migration 5: `e5f6a7b8c9d0` — Webhooks

**What it created:**
- `webhooks` table with URL, secret, events, and is_active
- `webhook_deliveries` table with delivery tracking
- CASCADE delete from webhooks to webhook_deliveries

---

### Migration 6: `f6a7b8c9d0e1` — Audit Logs with Immutability

**What it created:**
- `audit_logs` table with all fields documented in section 5
- Composite index on `(user_id, created_at)`
- Index on `action` column

**The immutability trigger (PostgreSQL-only):**
```sql
CREATE OR REPLACE FUNCTION audit_logs_immutable()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'audit_logs records are immutable and cannot be modified or deleted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_logs_no_update
BEFORE UPDATE OR DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();
```

This trigger exists at the database level. Application code cannot bypass it.
The trigger is SQLite-incompatible — tests that audit log records do not hit
this trigger because tests run on SQLite.

---

### Migration 7: `a1b2c3d4e5f6` — Performance Indexes

**What it added:**
```sql
CREATE INDEX ix_pipeline_runs_status ON pipeline_runs(status);
CREATE INDEX ix_pipeline_runs_created_at ON pipeline_runs(created_at DESC);
CREATE INDEX ix_step_results_pipeline_run_id ON step_results(pipeline_run_id);
CREATE INDEX ix_webhook_deliveries_webhook_id ON webhook_deliveries(webhook_id);
CREATE INDEX ix_audit_logs_user_created ON audit_logs(user_id, created_at DESC);
```

No schema changes — purely performance optimization.

---

### Migration 8: `b2c3d4e5f6a7` — Feature Expansion

**What it created and modified:**
- Added `CANCELLED` to the `pipeline_runs.status` enum
- Created `pipeline_schedules` table (cron scheduling)
- Created `notification_configs` table (Slack/email)
- Created `pipeline_permissions` table (per-pipeline RBAC)
- Added `version` and `previous_version_id` columns to `uploaded_files`
- Added `celery_task_id` column to `pipeline_runs` (for task revocation)

**This migration added the largest number of new tables of any revision.**

---

### Migration Commands Reference

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration (ALWAYS review the generated file)
alembic revision --autogenerate -m "describe_what_changed_precisely"

# Roll back one migration
alembic downgrade -1

# Roll back to a specific revision
alembic downgrade f6a7b8c9d0e1

# Show current revision
alembic current

# Show all revisions
alembic history --verbose

# Show SQL without executing (for review)
alembic upgrade head --sql
```

**What autogenerate misses** (always review manually):
- `server_default` changes
- Index renames (shows as drop+create)
- PostgreSQL-specific types (JSONB, UUID)
- Trigger creation and removal
- CHECK constraint modifications
- ENUM value additions (PostgreSQL requires special handling)

---

## 8. API Reference — All Endpoints, All Routers

All routes return JSON. Error responses follow:
```json
{
  "error_type": "PipelineNotFoundError",
  "message": "Pipeline run abc-123 not found",
  "details": {"run_id": "abc-123"},
  "request_id": "req-uuid-here"
}
```

Every response includes headers:
- `X-Request-ID` — unique UUID for this request, correlates with Sentry
- `X-Process-Time` — response time in seconds
- `X-API-Version` — backend API version string
- `X-App-Version` — application version string

### Router: Auth (prefix: `/auth/`)

These endpoints are registered directly in `main.py`, not in `router.py`.

---

#### POST `/auth/register`

Create a new user account.

**Authentication:** None
**Rate limit:** None (known gap — should be rate-limited)

**Request body:**
```json
{
  "email": "user@example.com",
  "username": "myusername",
  "password": "Str0ngP@ss!"
}
```

**Validation rules:**
- email: must match RFC 5322 pattern, unique in users table
- username: 3–50 characters, alphanumeric + underscores only, unique
- password: minimum 8 characters, must contain uppercase, digit, and special character

**Response (201 Created):**
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "username": "myusername",
  "role": "viewer",
  "is_active": true,
  "created_at": "2026-02-01T10:00:00Z"
}
```

**Special behaviour:** If `users` table is empty, the new user is automatically
assigned `role="admin"` regardless of what they requested.

**Error responses:**
- `400` — email already registered
- `400` — username already taken
- `422` — password complexity requirements not met
- `422` — email format invalid

**Side effects:** Audit log entry (`user.register`)

---

#### POST `/auth/login`

Authenticate a user and return a JWT access token.

**Authentication:** None
**Rate limit:** None (known gap)

**Request body:**
```json
{
  "email": "user@example.com",
  "password": "Str0ngP@ss!"
}
```

**Response (200 OK):**
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
- `401` — invalid credentials
- `401` — account is deactivated (is_active=false)

**Side effects:** Updates `last_login`, audit log entry (`user.login`)

---

#### GET `/auth/me`

Get the current authenticated user's profile.

**Authentication:** Required (Bearer JWT)
**Response:** Same shape as user object in login response.

---

#### POST `/auth/logout`

Logout (server-side is stateless — just an audit record).

**Authentication:** Required (Bearer JWT)
**Response:** `{"message": "Logged out successfully"}`
**Side effects:** Audit log entry (`user.logout`)

---

#### GET `/auth/users`

List all registered users.

**Authentication:** Required (Bearer JWT, **admin only**)
**Error responses:** `403` — non-admin user

**Response:**
```json
[
  {
    "id": "uuid",
    "email": "...",
    "username": "...",
    "role": "admin",
    "is_active": true,
    "created_at": "..."
  }
]
```

---

#### PATCH `/auth/users/{user_id}/role`

Update a user's global role.

**Authentication:** Required (Bearer JWT, **admin only**)

**Request body:**
```json
{"role": "admin"}
```
or
```json
{"role": "viewer"}
```

**Side effects:** Audit log entry (`role.change`)

---

### Router: Files (prefix: `/api/v1/files/`, defined in `api/files.py`)

---

#### POST `/api/v1/files/upload`

Upload a CSV or JSON data file.

**Authentication:** Required (Bearer JWT)
**Rate limit:** 30/minute per user
**Content-Type:** `multipart/form-data`
**Max size:** `MAX_UPLOAD_SIZE` (default 50MB = 52,428,800 bytes)
**Max rows:** `MAX_ROWS_PER_FILE` (default 1,000,000)
**Allowed extensions:** `.csv`, `.json`

**Processing steps (in order):**
1. Validate extension and content type
2. Check file size against MAX_UPLOAD_SIZE
3. Generate UUID-based stored filename: `{uuid4()}.{ext}`
4. Save to `{UPLOAD_DIR}/{stored_filename}`
5. Read file with Pandas (CSV: `pd.read_csv()`, JSON: `pd.read_json()`)
6. Enforce MAX_ROWS_PER_FILE
7. Extract columns and dtypes
8. Create `UploadedFile` record in database
9. Create `SchemaSnapshot` record
10. If previous upload with same filename exists, run drift detection
11. Increment Prometheus `pipelineiq_files_uploaded_total` counter
12. Log audit entry (`file.upload`)

**Response (201 Created):**
```json
{
  "id": "uuid",
  "original_filename": "sales_data.csv",
  "stored_path": "/app/uploads/uuid4-here.csv",
  "row_count": 50000,
  "column_count": 8,
  "columns": ["order_id", "customer_id", "amount", "status", "region", "date", "product", "quantity"],
  "dtypes": {
    "order_id": "int64",
    "customer_id": "int64",
    "amount": "float64",
    "status": "object",
    "region": "object",
    "date": "object",
    "product": "object",
    "quantity": "int64"
  },
  "file_size_bytes": 4194304,
  "version": 1,
  "schema_drift": null
}
```

When drift detected, `schema_drift` is non-null:
```json
{
  "schema_drift": {
    "has_drift": true,
    "breaking_changes": 1,
    "warnings": 1,
    "drift_items": [
      {
        "column": "customer_id",
        "drift_type": "removed",
        "severity": "breaking"
      },
      {
        "column": "discount",
        "drift_type": "added",
        "severity": "info"
      }
    ]
  }
}
```

**Error responses:**
- `400` — unsupported file type
- `400` — file exceeds MAX_UPLOAD_SIZE
- `400` — file exceeds MAX_ROWS_PER_FILE
- `422` — file cannot be parsed as valid CSV or JSON

---

#### GET `/api/v1/files/`

List all uploaded files.

**Authentication:** None (publicly accessible — known security gap)
**Rate limit:** 120/minute

**Response:**
```json
{
  "files": [
    {
      "id": "uuid",
      "original_filename": "sales.csv",
      "row_count": 50000,
      "column_count": 8,
      "file_size_bytes": 4194304,
      "version": 2,
      "created_at": "2026-02-01T10:00:00Z"
    }
  ],
  "total": 1
}
```

---

#### GET `/api/v1/files/{file_id}`

Get full metadata for a specific file.

**Authentication:** None (publicly accessible)
**Error responses:** `404`, `422` invalid UUID

---

#### DELETE `/api/v1/files/{file_id}`

Delete a file record and remove it from disk.

**Authentication:** Required (Bearer JWT)
**Processing:** Removes database record, then `os.remove(stored_path)`
**Error responses:** `404`
**Side effects:** Audit log (`file.delete`)

---

#### GET `/api/v1/files/{file_id}/preview`

Preview the first N rows of an uploaded file.

**Authentication:** None (publicly accessible)
**Query params:** `rows` (default 20, max 100)
**Implementation:** Uses `pd.read_csv(nrows=rows)` — never loads full file

**Response:**
```json
{
  "file_id": "uuid",
  "original_filename": "sales.csv",
  "columns": ["order_id", "amount", "status"],
  "rows": [
    [1001, 250.00, "delivered"],
    [1002, 99.99, "pending"]
  ],
  "total_rows_in_file": 50000,
  "rows_shown": 20
}
```

---

#### GET `/api/v1/files/{file_id}/schema/history`

Get all schema snapshots for a file, ordered newest first.

**Authentication:** None (publicly accessible)

**Response:**
```json
{
  "file_id": "uuid",
  "original_filename": "sales.csv",
  "snapshots": [
    {
      "id": "uuid",
      "columns": ["order_id", "amount", "status"],
      "dtypes": {"order_id": "int64"},
      "row_count": 50000,
      "captured_at": "2026-02-01T10:00:00Z"
    }
  ]
}
```

---

#### GET `/api/v1/files/{file_id}/schema/diff`

Compare the two most recent schema snapshots for drift detection.

**Authentication:** None (publicly accessible)

**Response:**
```json
{
  "file_id": "uuid",
  "has_drift": true,
  "columns_added": ["discount"],
  "columns_removed": ["customer_id"],
  "type_changes": [
    {"column": "amount", "from": "float64", "to": "object"}
  ],
  "severity": "breaking"
}
```

---

### Router: Pipelines (prefix: `/api/v1/pipelines/`, defined in `api/pipelines.py`)

---

#### POST `/api/v1/pipelines/validate`

Validate a YAML pipeline configuration without executing it.

**Authentication:** Required (Bearer JWT)
**Rate limit:** 60/minute

**Request body:**
```json
{
  "yaml_config": "pipeline:\n  name: test\n  steps:\n    - name: load_data\n      type: load\n      file_id: 'abc-123'"
}
```

**Response (200 OK — valid):**
```json
{
  "is_valid": true,
  "errors": [],
  "warnings": [
    {
      "field": "steps",
      "message": "No save step found. Pipeline will not produce output files.",
      "suggestion": "Add a step with type: save"
    }
  ]
}
```

**Response (200 OK — invalid):**
```json
{
  "is_valid": false,
  "errors": [
    {
      "step_name": "filter_data",
      "field": "column",
      "message": "Column 'ammount' not found in step 'load_data' output",
      "suggestion": "Did you mean 'amount'? Available columns: amount, status, region"
    }
  ],
  "warnings": []
}
```

**Side effects:** None — read-only validation

---

#### POST `/api/v1/pipelines/plan`

Generate a dry-run execution plan with row estimates.

**Authentication:** Required (Bearer JWT)
**Rate limit:** 60/minute
**Request body:** Same as validate

**Response (200 OK):**
```json
{
  "pipeline_name": "quarterly_report",
  "total_steps": 5,
  "estimated_total_duration_ms": 1450,
  "files_read": ["uuid-of-sales-file"],
  "files_written": ["quarterly_report.csv"],
  "estimated_rows_processed": 38500,
  "will_succeed": true,
  "warnings": [],
  "steps": [
    {
      "step_index": 0,
      "step_name": "load_sales",
      "step_type": "load",
      "estimated_rows_in": 55000,
      "estimated_rows_out": 55000,
      "estimated_columns": ["order_id", "amount", "status", "region"],
      "estimated_duration_ms": 200,
      "will_fail": false,
      "warnings": []
    },
    {
      "step_index": 1,
      "step_name": "delivered_only",
      "step_type": "filter",
      "estimated_rows_in": 55000,
      "estimated_rows_out": 38500,
      "estimated_columns": ["order_id", "amount", "status", "region"],
      "estimated_duration_ms": 150,
      "will_fail": false,
      "warnings": []
    },
    {
      "step_index": 2,
      "step_name": "by_region",
      "step_type": "aggregate",
      "estimated_rows_in": 38500,
      "estimated_rows_out": 3850,
      "estimated_columns": ["region", "amount_sum", "order_id_count"],
      "estimated_duration_ms": 800,
      "will_fail": false,
      "warnings": []
    }
  ]
}
```

---

#### POST `/api/v1/pipelines/run`

Queue and start asynchronous pipeline execution.

**Authentication:** Required (Bearer JWT)
**Rate limit:** 10/minute per user
**Authorisation:** User must have `runner` or `owner` permission for the pipeline name

**Request body:**
```json
{
  "yaml_config": "pipeline:\n  name: quarterly_report\n  steps:\n    ...",
  "name": "Q4 2025 Regional Analysis"
}
```

**Processing order:**
1. Validate JWT → get user
2. Apply rate limit (10/min)
3. Parse YAML → validate 13 rules → raise if invalid
4. Check file IDs exist in database
5. Check pipeline permission for user
6. Create PipelineRun record (status=PENDING)
7. Dispatch `execute_pipeline_task.delay(str(run.id))`
8. Log audit entry (`pipeline.run`)
9. Return immediately

**Response (202 Accepted):**
```json
{
  "run_id": "uuid-of-new-run",
  "status": "PENDING"
}
```

**Error responses:**
- `400` — YAML validation failed (returns all errors)
- `403` — user lacks runner/owner permission
- `404` — referenced file_id not found
- `429` — rate limit exceeded

---

#### GET `/api/v1/pipelines/`

List pipeline runs with pagination and optional status filtering.

**Authentication:** Required (Bearer JWT)
**Rate limit:** 120/minute
**Query params:**
- `page` — integer, default 1, minimum 1
- `limit` — integer, default 20, minimum 1, maximum 100
- `status_filter` — optional, one of: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED

**Response:**
```json
{
  "runs": [
    {
      "id": "uuid",
      "name": "Q4 Analysis",
      "status": "COMPLETED",
      "total_rows_in": 55000,
      "total_rows_out": 3850,
      "duration_ms": 1450,
      "created_at": "2026-02-01T10:00:00Z",
      "completed_at": "2026-02-01T10:00:01Z"
    }
  ],
  "total": 42,
  "page": 1,
  "limit": 20
}
```

---

#### GET `/api/v1/pipelines/stats`

Aggregate pipeline statistics.

**Authentication:** None (publicly accessible — known gap)
**Rate limit:** 120/minute

**Response:**
```json
{
  "total_runs": 42,
  "completed": 35,
  "failed": 5,
  "pending": 1,
  "running": 1,
  "cancelled": 0,
  "success_rate": 83.3,
  "total_files": 10
}
```

---

#### GET `/api/v1/pipelines/{run_id}`

Get full details of a specific pipeline run including all step results.

**Authentication:** None (publicly accessible — known gap)
**Rate limit:** 120/minute

**Response:**
```json
{
  "id": "uuid",
  "name": "Q4 Analysis",
  "status": "COMPLETED",
  "yaml_config": "pipeline:\n  name: ...",
  "created_at": "...",
  "started_at": "...",
  "completed_at": "...",
  "total_rows_in": 55000,
  "total_rows_out": 3850,
  "duration_ms": 1450,
  "error_message": null,
  "step_results": [
    {
      "step_name": "load_sales",
      "step_type": "load",
      "step_index": 0,
      "status": "COMPLETED",
      "rows_in": 55000,
      "rows_out": 55000,
      "columns_in": [],
      "columns_out": ["order_id", "amount", "status", "region"],
      "duration_ms": 200,
      "warnings": null,
      "error_message": null
    }
  ]
}
```

**Error responses:** `404`, `422` invalid UUID

---

#### GET `/api/v1/pipelines/{run_id}/stream`

Server-Sent Events endpoint for real-time execution progress.

**Authentication:** None (supports anonymous viewing)
**Content-Type:** `text/event-stream`

**SSE event types:**

`step_started`:
```
event: step_started
data: {"step_name": "load_sales", "step_index": 0, "total_steps": 5, "timestamp": "..."}
```

`step_completed`:
```
event: step_completed
data: {"step_name": "load_sales", "step_index": 0, "rows_in": 55000, "rows_out": 55000, "duration_ms": 200, "columns_out": ["order_id", "amount"]}
```

`step_failed`:
```
event: step_failed
data: {"step_name": "filter_data", "step_index": 1, "error": "Column 'amount' not found", "duration_ms": 50}
```

`pipeline_completed`:
```
event: pipeline_completed
data: {"run_id": "uuid", "status": "COMPLETED", "total_duration_ms": 1450, "total_rows_out": 3850}
```

`pipeline_failed`:
```
event: pipeline_failed
data: {"run_id": "uuid", "status": "FAILED", "error": "Step 'filter_data' failed: Column not found"}
```

**Headers set by Nginx for SSE:**
```
X-Accel-Buffering: no
Cache-Control: no-cache
Connection: keep-alive
Content-Type: text/event-stream
```

**Keepalive:** Comment lines sent every 500ms: `: keepalive`

**Nginx timeout:** 3600 seconds (1 hour) for the stream endpoint

**Client reconnect:** `usePipelineRun.ts` implements exponential backoff:
1s → 2s → 4s → 8s → 16s, max 5 retries

---

#### POST `/api/v1/pipelines/{run_id}/cancel`

Cancel a running or pending pipeline.

**Authentication:** Required (Bearer JWT)

**Processing:**
1. Load PipelineRun, verify status is RUNNING or PENDING
2. Call `celery_app.control.revoke(celery_task_id, terminate=True)`
3. Update status to CANCELLED, set completed_at = now()
4. Log audit entry (`pipeline.cancel`)

**Response:**
```json
{"run_id": "uuid", "status": "CANCELLED"}
```

**Error responses:**
- `404` — run not found
- `409` — run already in terminal state (COMPLETED/FAILED/CANCELLED)

---

#### POST `/api/v1/pipelines/preview`

Preview sample data at a specific pipeline step.

**Authentication:** Required (Bearer JWT)
**Rate limit:** 60/minute
**Query params:** `step_index` (default 0)

**Response:** Step preview with estimated columns and sample data.

---

#### GET `/api/v1/pipelines/{run_id}/export`

Download the output file from a completed pipeline run.

**Authentication:** Required (Bearer JWT)
**Response:** File download (Content-Disposition: attachment)
**Error responses:**
- `400` — run not in COMPLETED status
- `404` — no output file found for this run

---

### Router: Lineage (prefix: `/api/v1/lineage/`, defined in `api/lineage.py`)

All lineage endpoints are cached in Redis with 1-hour TTL.

---

#### GET `/api/v1/lineage/{run_id}`

Get the pre-computed React Flow lineage graph.

**Authentication:** None (publicly accessible)
**Cache:** Redis, 1h TTL, key: `lineage:{run_id}:graph`

**Response:**
```json
{
  "run_id": "uuid",
  "nodes": [
    {
      "id": "file::abc-123",
      "type": "sourceFile",
      "data": {"label": "sales.csv", "file_id": "abc-123"},
      "position": {"x": 0, "y": 0}
    },
    {
      "id": "col::load_sales::amount",
      "type": "columnNode",
      "data": {"label": "amount", "step": "load_sales", "dtype": "float64"},
      "position": {"x": 300, "y": 80}
    }
  ],
  "edges": [
    {
      "id": "file::abc-123-col::load_sales::amount",
      "source": "file::abc-123",
      "target": "col::load_sales::amount",
      "type": "default",
      "animated": false
    }
  ]
}
```

---

#### GET `/api/v1/lineage/{run_id}/column`

Trace a specific column backward to its source file and column.

**Authentication:** None (publicly accessible)
**Cache:** Redis, 1h TTL
**Query params:** `step` (required), `column` (required)

**Response:**
```json
{
  "run_id": "uuid",
  "target_step": "save_report",
  "target_column": "amount_sum",
  "source_file": "sales.csv",
  "source_column": "amount",
  "transformation_chain": [
    {"step": "load_sales", "type": "load", "column": "amount"},
    {"step": "delivered_only", "type": "filter", "column": "amount"},
    {"step": "by_region", "type": "aggregate", "column": "amount_sum"},
    {"step": "save_report", "type": "save", "column": "amount_sum"}
  ]
}
```

---

#### GET `/api/v1/lineage/{run_id}/impact`

Forward impact analysis — what does changing a column break?

**Authentication:** None (publicly accessible)
**Cache:** Redis, 1h TTL
**Query params:** `step` (required), `column` (required)

**Response:**
```json
{
  "run_id": "uuid",
  "source_step": "load_sales",
  "source_column": "amount",
  "affected_steps": ["delivered_only", "by_region", "save_report"],
  "affected_output_columns": ["amount_sum"],
  "affected_output_files": ["quarterly_report.csv"]
}
```

---

### Router: Versions (prefix: `/api/v1/versions/`, defined in `api/versions.py`)

---

#### GET `/api/v1/versions/{pipeline_name}`

List all versions of a pipeline, newest first.

**Authentication:** None (publicly accessible)

**Response:**
```json
{
  "pipeline_name": "quarterly_report",
  "versions": [
    {
      "id": "uuid",
      "version_number": 5,
      "created_at": "2026-02-01T10:00:00Z",
      "change_summary": "Added aggregate step"
    }
  ],
  "total": 5
}
```

---

#### GET `/api/v1/versions/{pipeline_name}/{version_number}`

Get a specific version with full YAML config.

**Authentication:** None

**Response:** Version object including full `yaml_config` field.

---

#### GET `/api/v1/versions/{pipeline_name}/diff/{version_a}/{version_b}`

Compute a diff between two versions.

**Authentication:** None

**Response:**
```json
{
  "pipeline_name": "quarterly_report",
  "version_a": 3,
  "version_b": 5,
  "steps_added": ["validate_data"],
  "steps_removed": [],
  "steps_modified": [
    {
      "name": "delivered_only",
      "changes": {"operator": {"from": "equals", "to": "not_equals"}}
    }
  ],
  "unified_diff": "--- version_3\n+++ version_5\n@@ -8,6 +8,8 @@\n..."
}
```

---

#### POST `/api/v1/versions/{pipeline_name}/restore/{version_number}`

Restore a pipeline to a previous version (creates a new version with old YAML).

**Authentication:** None (no auth required — known gap)

**Response:** New version object with restored YAML. Does not overwrite existing versions.

---

### Router: Webhooks (prefix: `/api/v1/webhooks/` or `/webhooks/`)

---

#### POST `/webhooks/`

Register a new webhook endpoint.

**Authentication:** Required (Bearer JWT)
**Per-user limit:** 10 webhooks maximum

**Request body:**
```json
{
  "url": "https://example.com/pipeline-events",
  "secret": "optional-signing-secret",
  "events": ["pipeline_completed", "pipeline_failed"]
}
```

**Validation:** URL must start with `http://` or `https://`

**Response (201 Created):**
```json
{
  "id": "uuid",
  "url": "https://example.com/pipeline-events",
  "has_secret": true,
  "events": ["pipeline_completed", "pipeline_failed"],
  "is_active": true,
  "created_at": "..."
}
```

Note: `has_secret: bool` — the raw secret is NEVER returned.

**Side effects:** Audit log (`webhook.create`)

---

#### GET `/webhooks/`

List the current user's webhooks.

**Authentication:** Required (Bearer JWT)
**Response:** Array of webhook objects (own only, secret hidden).

---

#### DELETE `/webhooks/{webhook_id}`

Delete a webhook and all its delivery records.

**Authentication:** Required (Bearer JWT)
**Authorisation:** Must be the webhook owner or admin
**Error responses:** `403`, `404`
**Side effects:** Audit log (`webhook.delete`)

---

#### GET `/webhooks/{webhook_id}/deliveries`

List delivery attempt history for a webhook.

**Authentication:** Required (Bearer JWT)
**Authorisation:** Must be the webhook owner
**Response limit:** 50 most recent deliveries

---

#### POST `/webhooks/{webhook_id}/test`

Send a test payload to verify the webhook endpoint.

**Authentication:** Required (Bearer JWT)
**Processing:** Signs payload with HMAC-SHA256 if secret configured, sends via httpx

**Response:**
```json
{
  "delivered": true,
  "response_status": 200,
  "response_body": "OK"
}
```

**Error response (502):** if remote endpoint unreachable or returns error.

---

### Router: Audit (prefix: `/audit/`)

---

#### GET `/audit/logs`

Get all audit logs with pagination and filtering.

**Authentication:** Required (Bearer JWT, **admin only**)
**Query params:** `page`, `limit` (default 50, max 100), `action` (filter), `user_id` (filter)

---

#### GET `/audit/logs/mine`

Get the current user's audit log entries.

**Authentication:** Required (Bearer JWT)
**Query params:** `page`, `limit`

---

### Router: Schedules (prefix: `/schedules/`)

---

#### POST `/schedules/`

Create a cron-based recurring pipeline schedule.

**Authentication:** Required (Bearer JWT)

**Request body:**
```json
{
  "pipeline_name": "daily_etl",
  "yaml_config": "pipeline:\n  name: daily_etl\n  steps:\n    ...",
  "cron_expression": "0 2 * * *"
}
```

**Cron validation:** `croniter(cron_expression)` called at creation time. Invalid
expression raises `422` before any database write.

**Response (201 Created):**
```json
{
  "id": "uuid",
  "pipeline_name": "daily_etl",
  "cron_expression": "0 2 * * *",
  "is_active": true,
  "next_run_at": "2026-02-02T02:00:00Z",
  "last_run_at": null,
  "created_at": "..."
}
```

**Side effects:** Audit log (`schedule.create`)

---

#### GET `/schedules/`

List user's pipeline schedules.

**Authentication:** Required (Bearer JWT)
**Response:** Array of schedule objects (own only).

---

#### PATCH `/schedules/{schedule_id}/toggle`

Enable or disable a pipeline schedule.

**Authentication:** Required (Bearer JWT)
**Processing:** Flips `is_active`, recomputes `next_run_at` if enabling.
**Side effects:** Audit log (`schedule.toggle`)

---

#### DELETE `/schedules/{schedule_id}`

Delete a pipeline schedule.

**Authentication:** Required (Bearer JWT)
**Side effects:** Audit log (`schedule.delete`)

---

### Router: Templates (prefix: `/api/v1/templates/`)

---

#### GET `/api/v1/templates/`

List all 5 pre-built pipeline templates.

**Authentication:** None (public)

**Response:**
```json
{
  "templates": [
    {
      "id": "basic-etl",
      "name": "Basic ETL",
      "description": "Load, filter, aggregate, and save",
      "category": "etl"
    }
  ]
}
```

---

#### GET `/api/v1/templates/{template_id}`

Get a specific template including its full YAML configuration.

**Authentication:** None (public)

Template IDs: `basic-etl`, `data-cleaning`, `data-validation`, `aggregation`, `merge-join`

---

### Router: Notifications (prefix: `/api/v1/notifications/`)

---

#### POST `/api/v1/notifications/`

Create a notification channel configuration.

**Authentication:** Required (Bearer JWT)

**Request body (Slack):**
```json
{
  "type": "slack",
  "config": {"slack_webhook_url": "https://hooks.slack.com/services/..."},
  "events": ["pipeline_completed", "pipeline_failed"]
}
```

**Request body (Email):**
```json
{
  "type": "email",
  "config": {"email_to": "alerts@company.com"},
  "events": ["pipeline_failed"]
}
```

---

#### POST `/api/v1/notifications/{config_id}/test`

Send a test notification to verify the channel works.

**Authentication:** Required (Bearer JWT)
**Error response:** `502` if delivery fails

---

### Router: Dashboard (prefix: `/api/v1/dashboard/`)

---

#### GET `/api/v1/dashboard/stats`

Get per-user analytics and activity summary.

**Authentication:** Required (Bearer JWT)
**Rate limit:** 120/minute
**Cache:** Redis, 30s TTL

**Response:**
```json
{
  "total_runs": 42,
  "completed": 35,
  "failed": 5,
  "pending": 1,
  "running": 1,
  "cancelled": 0,
  "success_rate": 83.3,
  "total_files": 10,
  "pipelines_by_status": {"COMPLETED": 35, "FAILED": 5, "PENDING": 1, "RUNNING": 1},
  "most_used_pipelines": [
    {"pipeline_name": "daily_etl", "run_count": 15},
    {"pipeline_name": "quarterly_report", "run_count": 8}
  ],
  "recent_activity": [
    {
      "action": "pipeline.run",
      "resource_type": "pipeline_run",
      "details": {"pipeline_name": "daily_etl"},
      "created_at": "2026-02-01T10:00:00Z"
    }
  ]
}
```

---

### Router: Permissions (prefix: `/api/v1/pipelines/{pipeline_name}/permissions/`)

---

#### POST `/api/v1/pipelines/{pipeline_name}/permissions`

Grant a user a permission level on a specific pipeline.

**Authentication:** Required (Bearer JWT, pipeline owner or admin)

**Request body:**
```json
{
  "user_id": "uuid-of-target-user",
  "permission_level": "runner"
}
```

**Behaviour:** If a permission record already exists for this user+pipeline,
it is updated (not duplicated). UNIQUE constraint enforces one record per pair.

**Side effects:** Audit log (`permission.grant`)

---

#### GET `/api/v1/pipelines/{pipeline_name}/permissions`

List all permission entries for a pipeline.

**Authentication:** Required (Bearer JWT)

---

#### DELETE `/api/v1/pipelines/{pipeline_name}/permissions/{user_id}`

Revoke a user's permission.

**Authentication:** Required (Bearer JWT, pipeline owner or admin)
**Side effects:** Audit log (`permission.revoke`)

---

### System Endpoints (registered directly in `main.py`)

---

#### GET `/health`

Verify system health.

**Authentication:** None (public, used by Docker health checks)

**Checks performed:**
1. PostgreSQL: `SELECT 1` query
2. Redis: `PING` command

**Response (200 — healthy):**
```json
{
  "status": "ok",
  "version": "3.6.2",
  "db": "ok",
  "redis": "ok"
}
```

**Response (200 — degraded):**
```json
{
  "status": "degraded",
  "version": "3.6.2",
  "db": "error",
  "redis": "ok"
}
```

---

#### GET `/metrics`

Prometheus metrics in text format.

**Authentication:** None (but restricted to internal networks by Nginx)
**Nginx restriction:** Only accessible from `10.x`, `172.16.x`, `192.168.x`, `127.0.0.1`

---

#### GET `/debug/sentry-test`

Intentionally raise a ValueError to test Sentry error capture.

**Available only when:** `ENVIRONMENT != "production"`
**Authentication:** None

---

## 9. Authentication and Authorisation

### JWT Implementation

**Library:** `python-jose[cryptography]`
**Algorithm:** HS256 (HMAC-SHA256)
**Signing key:** `settings.SECRET_KEY` — minimum 32 characters
**Token lifetime:** `settings.ACCESS_TOKEN_EXPIRE_MINUTES` (default 1440 = 24 hours)
**Refresh tokens:** NOT implemented. Users must re-authenticate after 24 hours.

**Token payload structure:**
```json
{
  "sub": "user-uuid-string",
  "role": "admin",
  "exp": 1706880000
}
```

**Password hashing:**
- Library: `passlib[bcrypt]`
- Algorithm: bcrypt with automatic salt
- Never store or log plain text passwords

**Token storage (frontend):**
- Stored in `localStorage` as key `pipelineiq_token`
- Cookie `piq_auth=1` set for Next.js middleware (SameSite=Strict as of v2.1.3)
- Known XSS vulnerability — httpOnly cookie would be more secure
- SameSite=Strict mitigates CSRF while staying as cookie

### FastAPI Dependency Chain

```python
# In backend/auth.py

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    # 1. Decode JWT with SECRET_KEY, algorithm HS256
    # 2. Extract user_id from payload["sub"]
    # 3. Query database for user by ID
    # 4. Verify is_active=True
    # 5. Verify token not expired (jose handles this automatically)
    # Returns User object or raises HTTPException(401)

def get_current_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    # Raises HTTPException(403) if current_user.role != "admin"
    return current_user

def get_optional_user(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db)
) -> Optional[User]:
    # Returns None if no token (for public endpoints that optionally identify user)
    # Returns User if valid token
```

### Complete Role Matrix

| Operation | No Auth | viewer | runner | owner | admin |
|---|---|---|---|---|---|
| Register / Login | ✓ | ✓ | ✓ | ✓ | ✓ |
| View pipeline runs | ✓* | ✓* | ✓* | ✓* | ✓ |
| View files | ✓* | ✓* | ✓* | ✓* | ✓ |
| View lineage graphs | ✓* | ✓* | ✓* | ✓* | ✓ |
| Upload files | ✗ | ✓ | ✓ | ✓ | ✓ |
| Delete files | ✗ | ✓ | ✓ | ✓ | ✓ |
| Run pipeline | ✗ | ✗ | ✓** | ✓** | ✓ |
| Cancel pipeline | ✗ | ✗ | ✓ | ✓ | ✓ |
| Export pipeline output | ✗ | ✗ | ✓ | ✓ | ✓ |
| Create webhook | ✗ | ✓ | ✓ | ✓ | ✓ |
| Delete own webhook | ✗ | ✓ | ✓ | ✓ | ✓ |
| View own audit log | ✗ | ✓ | ✓ | ✓ | ✓ |
| View all audit logs | ✗ | ✗ | ✗ | ✗ | ✓ |
| List all users | ✗ | ✗ | ✗ | ✗ | ✓ |
| Change user roles | ✗ | ✗ | ✗ | ✗ | ✓ |
| Grant pipeline permissions | ✗ | ✗ | ✗ | ✓** | ✓ |

*\* Publicly accessible — known security gap, acceptable for single-tenant*
*\*\* For that specific pipeline name only*

### Known Authentication Gaps

1. **No rate limiting on auth endpoints** — login and register are not rate-limited.
   Brute force attempts are not blocked. This is documented in AUDIT_REPORT.md.
2. **JWT in localStorage** — XSS-vulnerable. Mitigation: SameSite=Strict cookie.
3. **Per-pipeline permission enforcement incomplete** — pipeline execution does
   not fully enforce runner vs viewer distinction.
4. **Several read endpoints are public** — files, pipeline runs, lineage, and
   versions have no auth requirement. All users can see all data.

---

## 10. Pipeline Engine — Complete Deep Reference

The pipeline engine lives in `backend/pipeline/` and is approximately 3,000 lines
of Python across 9 files. It has **zero infrastructure dependencies** — no Redis
imports, no SQLAlchemy imports, no HTTP clients. It operates purely on Python
objects, DataFrames, and the NetworkX graph.

### YAML Contract — Complete Reference

Every pipeline is a YAML document with this top-level structure:

```yaml
pipeline:
  name: string          # Required. Display name. Sanitized via string_utils.py
  description: string   # Optional. Not persisted separately.
  steps:                # Required. List of step configs. At least 1 step.
    - name: string      # Required per step. Unique within pipeline. [a-zA-Z0-9_] only
      type: string      # Required. Must be one of the 9 valid types.
      # ... step-specific fields
```

**Example of a complete, valid pipeline:**
```yaml
pipeline:
  name: quarterly_sales_report
  steps:
    - name: load_sales
      type: load
      file_id: "abc-123-uuid"

    - name: load_customers
      type: load
      file_id: "def-456-uuid"

    - name: delivered_only
      type: filter
      input: load_sales
      column: status
      operator: equals
      value: delivered

    - name: with_customers
      type: join
      left: delivered_only
      right: load_customers
      on: customer_id
      how: left

    - name: quality_check
      type: validate
      input: with_customers
      rules:
        - check: not_null
          column: customer_id
          severity: error
        - check: between
          column: amount
          value: [0, 100000]
          severity: warning

    - name: by_region
      type: aggregate
      input: quality_check
      group_by: [region]
      aggregations:
        - column: amount
          function: sum
        - column: order_id
          function: count

    - name: renamed
      type: rename
      input: by_region
      mapping:
        amount_sum: total_revenue
        order_id_count: order_count

    - name: top_first
      type: sort
      input: renamed
      by: total_revenue
      order: desc

    - name: slim
      type: select
      input: top_first
      columns: [region, total_revenue, order_count]

    - name: save_report
      type: save
      input: slim
      filename: quarterly_report
```

---

### Step Type 1: `load`

Read a CSV or JSON file from disk into a Pandas DataFrame.

**Parameters:**
```yaml
- name: step_name        # Unique, alphanumeric + underscore
  type: load
  file_id: "uuid"        # Required. Must be registered in uploaded_files table.
```

**Pandas operation:**
- CSV: `pd.read_csv(file_path, dtype=detected_dtypes)`
- JSON: `pd.read_json(file_path)`

**Memory note:** The full file is loaded into worker RAM. Files up to
`MAX_ROWS_PER_FILE` (1M rows) are enforced at upload time.

**Lineage recording:**
- Source file node added: `file::{file_id}`
- Step node added: `step::{step_name}`
- Column nodes added: `col::{step_name}::{col}` for each column
- Edges: `file::{file_id}` → `step::{step_name}` → `col::{step_name}::{col}`

**Error conditions:**
- `FileReadError` — file not found at `stored_path` (disk and DB out of sync)
- `UnsupportedFileFormatError` — file extension not csv or json

---

### Step Type 2: `filter`

Keep rows matching a condition. Row count is reduced.

**Parameters:**
```yaml
- name: step_name
  type: filter
  input: previous_step_name     # Required. Name of step providing input DataFrame.
  column: column_name           # Required. Must exist in input DataFrame.
  operator: operator_name       # Required. Must be one of 12 valid operators.
  value: any                    # Required for most operators. Not for is_null/is_not_null.
```

**All 12 filter operators and their Pandas implementation:**

| Operator | Pandas equivalent | Notes |
|---|---|---|
| `equals` | `df[col] == value` | Strict equality |
| `not_equals` | `df[col] != value` | |
| `greater_than` | `df[col] > value` | Value coerced to numeric |
| `less_than` | `df[col] < value` | Value coerced to numeric |
| `gte` | `df[col] >= value` | greater_than_or_equal |
| `lte` | `df[col] <= value` | less_than_or_equal |
| `contains` | `df[col].str.contains(value, na=False)` | String columns only |
| `not_contains` | `~df[col].str.contains(value, na=False)` | |
| `starts_with` | `df[col].str.startswith(value, na=False)` | |
| `ends_with` | `df[col].str.endswith(value, na=False)` | |
| `is_null` | `df[col].isna()` | No value parameter needed |
| `is_not_null` | `df[col].notna()` | No value parameter needed |

**Dry-run estimate:** ~70% row retention heuristic.

**Lineage recording:**
- Step node: `step::{step_name}`
- Column nodes: `col::{step_name}::{col}` for each column (passthrough — all columns preserved)
- Edges: `col::{prev_step}::{col}` → `step::{step_name}` → `col::{step_name}::{col}`
- Transformation type on edge: `"filter"`

**Error conditions:**
- `ColumnNotFoundError` — column not in input DataFrame (includes fuzzy suggestion)
- `InvalidOperatorError` — operator not in valid set (includes suggestion)

---

### Step Type 3: `select`

Keep only specified columns, dropping all others.

**Parameters:**
```yaml
- name: step_name
  type: select
  input: previous_step_name
  columns: [col_a, col_b, col_c]    # Required. List of column names to keep.
```

**Pandas operation:** `df[columns]`

**Lineage recording:**
- Kept columns get full passthrough edges
- Dropped columns get edges only to the step node (dead end in graph)
- This is a projection — it can create "orphan" column nodes

---

### Step Type 4: `rename`

Rename columns via a mapping dictionary.

**Parameters:**
```yaml
- name: step_name
  type: rename
  input: previous_step_name
  mapping:
    old_column_name: new_column_name
    another_old: another_new
```

**Pandas operation:** `df.rename(columns=mapping)`

**Lineage recording:**
- Input column nodes → step → new output column nodes (renamed names)
- Edge labeled with `transformation: "rename"`

**Error conditions:**
- `ColumnNotFoundError` — key in mapping not in input DataFrame

---

### Step Type 5: `join`

Merge two DataFrames on a common key column.

**Parameters:**
```yaml
- name: step_name
  type: join
  left: left_step_name      # Required. Name of left input step.
  right: right_step_name    # Required. Name of right input step.
  on: join_key_column       # Required. Column name present in BOTH DataFrames.
  how: inner                # Required. One of: inner, left, right, outer
```

**Join types:**

| How | Pandas | Result |
|---|---|---|
| `inner` | `how='inner'` | Only rows where key exists in BOTH |
| `left` | `how='left'` | All left rows, matching right rows |
| `right` | `how='right'` | All right rows, matching left rows |
| `outer` | `how='outer'` | All rows from both |

**Column conflict resolution:** `suffixes=("_left", "_right")` on duplicate non-key columns.

**Dry-run estimates:**
- inner: `min(left_rows, right_rows)`
- left: `left_rows`
- right: `right_rows`
- outer: `left_rows + right_rows`

**Lineage recording:**
- Left columns → step → merged output columns
- Right columns → step → merged output columns
- Join key edges marked with `is_join_key=True`
- In React Flow: join key edges rendered as `animated=True` (dotted animated style)

**Error conditions:**
- `JoinKeyMissingError` — `on` column not in left DataFrame
- `JoinKeyMissingError` — `on` column not in right DataFrame
- `InvalidJoinTypeError` — `how` not in valid set

---

### Step Type 6: `aggregate`

Group rows by one or more columns and compute statistics.

**Parameters:**
```yaml
- name: step_name
  type: aggregate
  input: previous_step_name
  group_by: [region, product]     # Required. List of grouping columns.
  aggregations:                    # Required. List of aggregation definitions.
    - column: amount               # Required. Column to aggregate.
      function: sum                # Required. One of 10 aggregate functions.
    - column: order_id
      function: count
```

**All 10 aggregate functions:**

| Function | Pandas agg | Output column name |
|---|---|---|
| `sum` | `"sum"` | `{column}_sum` |
| `mean` | `"mean"` | `{column}_mean` |
| `min` | `"min"` | `{column}_min` |
| `max` | `"max"` | `{column}_max` |
| `count` | `"count"` | `{column}_count` |
| `median` | `"median"` | `{column}_median` |
| `std` | `"std"` | `{column}_std` |
| `var` | `"var"` | `{column}_var` |
| `first` | `"first"` | `{column}_first` |
| `last` | `"last"` | `{column}_last` |

**Output column naming:** After `groupby().agg()`, Pandas produces multi-level
column index tuples `(column, function)`. The executor flattens to `{column}_{function}`.
Special case: if the column name equals the function result (rare), the column name is kept.

**Example:** aggregating `amount` with `sum` produces column `amount_sum`.

**Dry-run estimate:** ~10% row retention (groups collapse many rows to few).

**Lineage recording:**
- group_by columns → step (passthrough)
- aggregated columns → step → new output columns (`amount_sum`, etc.)

**Error conditions:**
- `ColumnNotFoundError` — group_by column not in input
- `ColumnNotFoundError` — aggregation column not in input
- `AggregationError` — aggregation operation fails (e.g., sum on string column)

---

### Step Type 7: `sort`

Order rows by a column in ascending or descending order.

**Parameters:**
```yaml
- name: step_name
  type: sort
  input: previous_step_name
  by: column_name           # Required. Column to sort by.
  order: desc               # Required. One of: asc, desc
```

**Pandas operation:** `df.sort_values(by=by, ascending=(order == 'asc'))`

**Row count change:** None. All rows preserved.

**Lineage recording:** Passthrough — all columns get straight-through edges.

---

### Step Type 8: `validate`

Run data quality checks. Non-blocking — warnings do not stop execution.

**Parameters:**
```yaml
- name: step_name
  type: validate
  input: previous_step_name
  rules:
    - check: not_null          # Required. One of 12 check types.
      column: column_name      # Required for most checks.
      severity: error          # Required. "error" or "warning".
      value: any               # Optional. Check-specific parameter.
      value: [min, max]        # For "between" check.
      value: ["a", "b"]        # For "in_values" check.
```

**All 12 validation check types:**

| Check | What it verifies | Parameters |
|---|---|---|
| `not_null` | Column has no null/NaN values | `column` |
| `not_empty` | Column has no empty strings | `column` |
| `greater_than` | All values > threshold | `column`, `value` (numeric) |
| `less_than` | All values < threshold | `column`, `value` (numeric) |
| `between` | All values in [min, max] | `column`, `value: [min, max]` |
| `in_values` | All values in allowed set | `column`, `value: [list]` |
| `matches_pattern` | All values match regex | `column`, `value: "regex"` |
| `no_duplicates` | No duplicate values in column | `column` |
| `min_rows` | DataFrame has at least N rows | `value: N` |
| `max_rows` | DataFrame has at most N rows | `value: N` |
| `positive` | All values > 0 | `column` |
| `date_format` | All values match date format | `column`, `value: "format"` |

**Severity behaviour:**
- `"error"` severity — check is FAILING but execution CONTINUES (non-blocking)
  The step status becomes "COMPLETED_WITH_WARNINGS" and warnings are persisted
  in `step_results.warnings` JSONB column.
- `"warning"` severity — same behaviour, different label in output

**Each check result includes:**
```json
{
  "check": "not_null",
  "column": "customer_id",
  "severity": "error",
  "passed": false,
  "failing_count": 142,
  "total_count": 55000,
  "examples": [
    {"row_index": 5, "value": null},
    {"row_index": 23, "value": null},
    {"row_index": 89, "value": null}
  ]
}
```

**Row count change:** None. All rows pass through regardless of validation results.

**Lineage recording:** Passthrough — all columns get straight-through edges.

---

### Step Type 9: `save`

Mark a DataFrame as output and write it to disk.

**Parameters:**
```yaml
- name: step_name
  type: save
  input: previous_step_name
  filename: output_filename    # Required. Without extension. Output will be CSV.
```

**What happens:**
1. DataFrame written to `{UPLOAD_DIR}/{filename}_{uuid}.csv`
2. Output file metadata recorded in `StepResult`
3. Output file node added to lineage graph
4. `PipelineRun.total_rows_out` updated

**Note on multiple saves:** As of v2.1.3, multiple save steps are supported in a
single pipeline, writing to different output files.

**Lineage recording:**
- Input columns → step → output file node (`output::{step_name}::{filename}`)

---

### Step Type 10: `pivot`

Reshape data from long to wide format by pivoting on an index column.

**Parameters:**
```yaml
- name: step_name
  type: pivot
  input: previous_step_name
  index: column_to_use_as_index      # Required. Column to use as the index.
  columns: column_to_pivot           # Required. Column whose unique values become new columns.
  values: column_to_fill             # Required. Column to aggregate for the values.
  aggfunc: sum                       # Optional. Aggregation function: sum, mean, count, min, max. Default: sum
```

**Pandas operation:** `df.pivot_table(index=index, columns=columns, values=values, aggfunc=aggfunc)`

**Example:** Converting transaction records with one row per item to a matrix with columns for each product category.

---

### Step Type 11: `unpivot`

Reshape data from wide to long format by melting columns into rows.

**Parameters:**
```yaml
- name: step_name
  type: unpivot
  input: previous_step_name
  id_vars: [col_a, col_b]            # Required. Columns to keep as identifiers.
  value_vars: [col_c, col_d, col_e]   # Required. Columns to unpivot into rows.
  var_name: variable                  # Optional. Name for the resulting variable column. Default: "variable"
  value_name: value                   # Optional. Name for the resulting value column. Default: "value"
```

**Pandas operation:** `df.melt(id_vars=id_vars, value_vars=value_vars, var_name=var_name, value_name=value_name)`

**Example:** Converting a matrix with product columns back to normalized transaction rows.

---

### Step Type 12: `deduplicate`

Remove duplicate rows based on specified columns.

**Parameters:**
```yaml
- name: step_name
  type: deduplicate
  input: previous_step_name
  subset: [column_a, column_b]       # Optional. Columns to consider for duplicates. Default: all columns.
  keep: first                        # Optional. Which duplicate to keep: first, last, False (drop all). Default: first
```

**Pandas operation:** `df.drop_duplicates(subset=subset, keep=keep)`

---

### Step Type 13: `fill_nulls`

Fill null/NaN values using forward fill, backward fill, or a constant value.

**Parameters:**
```yaml
- name: step_name
  type: fill_nulls
  input: previous_step_name
  method: ffill                      # Required. fill method: ffill, bfill, or constant
  value: 0                           # Optional. Constant value when method=constant. Default: 0
  columns: [col_a, col_b]            # Optional. Specific columns to fill. Default: all columns
```

**Pandas operation:** `df.fillna(method=method, value=value)` or `df[columns].fillna(method=method)`

**Fill methods:**
- `ffill` (forward fill) — propagate last valid observation forward
- `bfill` (backward fill) — use next valid observation to fill
- `constant` — fill with specified value

---

### Step Type 14: `sample`

Randomly sample rows from the DataFrame.

**Parameters:**
```yaml
- name: step_name
  type: sample
  input: previous_step_name
  n: 100                            # Optional. Number of rows to sample. Either n or frac required.
  frac: 0.1                         # Optional. Fraction of rows to sample (0.0-1.0).
  random_state: 42                  # Optional. Seed for reproducibility.
  replace: false                    # Optional. Sample with replacement. Default: false
```

**Pandas operation:** `df.sample(n=n, frac=frac, random_state=random_state, replace=replace)`

**Note:** Either `n` or `frac` must be specified, but not both.

---

### Parser — `backend/pipeline/parser.py` (703 lines)

**Entry point:**
```python
def parse_pipeline_config(
    yaml_string: str,
    registered_file_ids: Optional[set[str]] = None
) -> PipelineConfig:
    ...
```

**Dataclass hierarchy produced:**

```
PipelineConfig
├── name: str
├── description: Optional[str]
└── steps: list[StepConfig]
      ├── LoadStepConfig
      │   └── file_id: str
      ├── FilterStepConfig
      │   ├── input: str
      │   ├── column: str
      │   ├── operator: str
      │   └── value: Any
      ├── SelectStepConfig
      │   ├── input: str
      │   └── columns: list[str]
      ├── RenameStepConfig
      │   ├── input: str
      │   └── mapping: dict[str, str]
      ├── JoinStepConfig
      │   ├── left: str
      │   ├── right: str
      │   ├── on: str
      │   └── how: str
      ├── AggregateStepConfig
      │   ├── input: str
      │   ├── group_by: list[str]
      │   └── aggregations: list[AggregationConfig]
      ├── SortStepConfig
      │   ├── input: str
      │   ├── by: str
      │   └── order: str
      ├── ValidateStepConfig
      │   ├── input: str
      │   └── rules: list[ValidationRule]
      └── SaveStepConfig
          ├── input: str
          └── filename: str
```

**All 13 validation rules (checked in order, all errors collected before returning):**

1. **Pipeline name non-empty** — `pipeline.name` must exist and not be blank
2. **At least one step** — `pipeline.steps` must have minimum 1 entry
3. **Step count limit** — `len(steps) <= MAX_PIPELINE_STEPS` (default 50)
4. **No duplicate step names** — all step names must be unique within pipeline
5. **Valid step name format** — matches `[a-zA-Z0-9_]+` (no spaces, no hyphens)
6. **Valid step type** — must be one of: load, filter, select, rename, join, aggregate,
   sort, validate, save, pivot, unpivot, deduplicate, fill_nulls, sample
7. **Valid input references** — `input` (or `left`/`right` for join) must reference
   a step name defined earlier in the list (forward references not allowed)
8. **No circular dependencies** — detected via topological sort of step references
9. **Registered file IDs** — `file_id` in load steps must exist in `registered_file_ids`
   set (passed in from API layer after DB query)
10. **Valid filter operators** — `operator` must be one of the 12 valid operators
11. **Valid join types** — `how` must be one of: inner, left, right, outer
12. **Valid aggregate functions** — each `function` must be one of the 10 valid functions
13. **Save step recommended** — if no save step exists, a `WARNING` is added (not an error)

**Fuzzy suggestion system:**
Uses `difflib.get_close_matches(target, candidates, n=1, cutoff=0.6)`.
If a column name is `ammount`, the error includes `suggestion: "amount"`.
If a step type is `filtr`, the error includes `suggestion: "filter"`.
This applies to: step types, column names, operator names, function names.

**The parser does NOT stop on first error.** It collects all errors across all
steps and returns them together. This is intentional — users see all problems at once.

---

### Exception Hierarchy — `backend/pipeline/exceptions.py` (443 lines)

All exceptions inherit from `PipelineIQError` which provides:
- `error_code: str` — machine-readable code used by frontend to handle specific errors
- `message: str` — human-readable message for display
- `details: dict` — context fields (column names, suggestions, available options)
- `to_dict() -> dict` — serialization for API response body

**Configuration errors** (raised during YAML parsing, before any execution):

| Class | error_code | When raised |
|---|---|---|
| `PipelineConfigError` | `PIPELINE_CONFIG_ERROR` | Generic config error base class |
| `InvalidYAMLError` | `INVALID_YAML` | YAML syntax is malformed |
| `MissingRequiredFieldError` | `MISSING_REQUIRED_FIELD` | Required field absent |
| `DuplicateStepNameError` | `DUPLICATE_STEP_NAME` | Two steps have same name |
| `InvalidStepTypeError` | `INVALID_STEP_TYPE` | Unknown step type (+ suggestion) |
| `InvalidStepReferenceError` | `INVALID_STEP_REFERENCE` | Input references unknown step |
| `FileNotRegisteredError` | `FILE_NOT_REGISTERED` | file_id not in database |
| `CircularDependencyError` | `CIRCULAR_DEPENDENCY` | Step references form a cycle |
| `StepCountExceededError` | `STEP_COUNT_EXCEEDED` | > MAX_PIPELINE_STEPS steps |

**Runtime errors** (raised during step execution, after pipeline has started):

| Class | error_code | When raised |
|---|---|---|
| `PipelineExecutionError` | `PIPELINE_EXECUTION_ERROR` | Generic execution base class |
| `ColumnNotFoundError` | `COLUMN_NOT_FOUND` | Column missing from DataFrame (+ suggestion) |
| `InvalidOperatorError` | `INVALID_OPERATOR` | Filter operator not in valid set |
| `JoinKeyMissingError` | `JOIN_KEY_MISSING` | Join `on` column not in one side |
| `AggregationError` | `AGGREGATION_ERROR` | Agg operation fails (e.g., sum on strings) |
| `FileReadError` | `FILE_READ_ERROR` | Stored file not found on disk |
| `UnsupportedFileFormatError` | `UNSUPPORTED_FILE_FORMAT` | Not csv or json |
| `StepTimeoutError` | `STEP_TIMEOUT` | Step exceeded STEP_TIMEOUT_SECONDS |

**Not-found errors** (raised from API layer):

| Class | error_code | When raised |
|---|---|---|
| `PipelineNotFoundError` | `PIPELINE_NOT_FOUND` | PipelineRun ID doesn't exist |

**The global exception handler in `main.py`:**
```python
@app.exception_handler(PipelineIQError)
async def pipelineiq_error_handler(request, exc: PipelineIQError):
    return JSONResponse(
        status_code=400,
        content={
            "error_type": exc.__class__.__name__,
            "message": exc.message,
            "details": exc.details,
            "request_id": request.state.request_id
        }
    )
```

---

### PipelineRunner — `backend/pipeline/runner.py` (257 lines)

The runner orchestrates step execution. It has zero infrastructure dependencies.

```python
ProgressCallback = Callable[[StepProgressEvent], None]

class PipelineRunner:
    def __init__(
        self,
        config: PipelineConfig,
        executor: StepExecutor,
        lineage_recorder: LineageRecorder,
        progress_callback: ProgressCallback
    ):
        self.config = config
        self.executor = executor
        self.lineage_recorder = lineage_recorder
        self.progress_callback = progress_callback
        self.df_registry: dict[str, pd.DataFrame] = {}  # step_name → DataFrame

    def execute(self) -> RunResult:
        for i, step in enumerate(self.config.steps):
            self.progress_callback(StepProgressEvent(
                status="STARTED", step_name=step.name, step_index=i,
                total_steps=len(self.config.steps)
            ))
            try:
                result_df = self.executor.execute_step(step, self.df_registry)
                self.df_registry[step.name] = result_df
                self.progress_callback(StepProgressEvent(
                    status="COMPLETED", step_name=step.name,
                    rows_in=result.rows_in, rows_out=result.rows_out,
                    duration_ms=result.duration_ms
                ))
            except StepExecutionError as e:
                self.progress_callback(StepProgressEvent(
                    status="FAILED", step_name=step.name, error=str(e)
                ))
                raise  # Propagates to Celery task, marks run as FAILED
```

**The `df_registry`** is a dict mapping step name to the output DataFrame of that step.
The `save` step reads from `df_registry[step.input]`. The `join` step reads from
`df_registry[step.left]` and `df_registry[step.right]`.

---

### LineageRecorder — `backend/pipeline/lineage.py` (634 lines)

Builds the `networkx.DiGraph` during execution.

```python
class LineageRecorder:
    def __init__(self):
        self.graph = nx.DiGraph()

    def record_load_step(self, step_name: str, file_id: str, columns: list[str]) -> None:
        # Adds: file node, step node, column nodes
        # Adds edges: file → step → each column node

    def record_filter_step(self, step_name: str, input_step: str, columns: list[str]) -> None:
        # Adds: step node, column nodes (same names as input)
        # Adds edges: each input column → step → same output column
        # transformation attribute on edges: "filter"

    def record_join_step(self, step_name: str, left_step: str, right_step: str,
                         join_key: str, output_columns: list[str]) -> None:
        # Adds edges from both left and right columns
        # Join key edges marked with is_join_key=True

    def record_aggregate_step(self, step_name: str, input_step: str,
                               group_by: list[str], agg_columns: dict) -> None:
        # group_by columns get passthrough edges
        # aggregated columns → step → new columns (e.g., amount → amount_sum)

    def record_save_step(self, step_name: str, input_step: str,
                          columns: list[str], filename: str) -> None:
        # Adds output file node
        # All input columns → step → output file node

    def get_react_flow_data(self) -> dict:
        # Returns pre-computed {nodes: [...], edges: [...]} for React Flow
        # Layout: topological sort → layer assignment → x/y position
        # x = layer_index * 300, y = position_in_layer * 80

    def get_column_ancestry(self, step: str, column: str) -> AncestryResult:
        # Uses nx.ancestors() to walk backward
        # Returns: source_file, source_column, transformation_chain

    def get_impact_analysis(self, step: str, column: str) -> ImpactResult:
        # Uses nx.descendants() to walk forward
        # Returns: affected_steps, affected_output_columns, affected_files

    def serialize(self) -> dict:
        # NetworkX node-link format via nx.node_link_data(self.graph)
        # Stored in lineage_graphs.graph_data
```

---

### Dry-Run Planner — `backend/pipeline/planner.py` (212 lines)

8 heuristics for estimating step outputs without running them:

| Step type | Row retention estimate |
|---|---|
| `load` | Exact: reads row_count from UploadedFile record |
| `filter` | 70% of input rows |
| `select` | 100% (no row changes) |
| `rename` | 100% (no row changes) |
| `sort` | 100% (no row changes) |
| `validate` | 100% (non-blocking, all rows pass through) |
| `aggregate` | 10% of input rows (groups collapse rows) |
| `join` (inner) | min(left, right) rows |
| `join` (left) | left rows |
| `join` (right) | right rows |
| `join` (outer) | left + right rows |
| `save` | 100% (write to disk, all rows) |

Duration estimates: `max(base_duration_ms, row_count // step_divisor)` where
divisors are step-type-specific (load: 5000, filter: 10000, aggregate: 2000, etc.)

Failure detection: checks if referenced file_ids exist in the database.
If any file is missing, `will_fail: true` for that step.

---

### Schema Drift Detector — `backend/pipeline/schema_drift.py`

Called on every file upload. Compares the new schema against the most recent
`SchemaSnapshot` for the same `original_filename`.

**Severity classification:**

| Change | Severity | Impact |
|---|---|---|
| Column removed from new file | `breaking` | Pipeline referencing this column WILL fail |
| Column dtype changed | `warning` | Pipeline may produce wrong results |
| Column added to new file | `info` | No impact on existing pipelines |

**Output format:**
```json
{
  "has_drift": true,
  "changes": [
    {"column": "customer_id", "change_type": "removed", "severity": "breaking"},
    {"column": "amount", "change_type": "type_changed", "from_dtype": "float64", "to_dtype": "object", "severity": "warning"},
    {"column": "discount", "change_type": "added", "severity": "info"}
  ]
}
```

---

### Pipeline Versioning — `backend/pipeline/versioning.py`

On every successful pipeline run, a `PipelineVersion` record is created with the
YAML configuration used for that run.

**Version number:** `SELECT MAX(version_number) FROM pipeline_versions WHERE pipeline_name = ?`
then `version_number = max + 1`. Starts at 1.

**Diff computation (on demand, not stored):** Uses Python's `difflib` for unified
diff plus structured step-level comparison:
```python
def compute_diff(yaml_v1: str, yaml_v2: str) -> VersionDiff:
    config_v1 = parse_pipeline_config(yaml_v1)
    config_v2 = parse_pipeline_config(yaml_v2)

    steps_v1 = {s.name: s for s in config_v1.steps}
    steps_v2 = {s.name: s for s in config_v2.steps}

    added = [name for name in steps_v2 if name not in steps_v1]
    removed = [name for name in steps_v1 if name not in steps_v2]
    modified = [name for name in steps_v1 if name in steps_v2 and steps_v1[name] != steps_v2[name]]

    unified_diff = "\n".join(difflib.unified_diff(
        yaml_v1.splitlines(), yaml_v2.splitlines(),
        fromfile=f"version_{v1}", tofile=f"version_{v2}"
    ))

    return VersionDiff(added_steps=added, removed_steps=removed,
                       modified_steps=modified, unified_diff=unified_diff)
```

---

## 11. Business Logic — Every Rule Documented

### Rule 1: Column Ancestry Completeness

Every column in a `save` step must be traceable backward through the lineage graph
to at least one `load` step's source column.

A column node with no incoming edges from a source file indicates a bug in step
lineage recording. There is no automatic detection — the graph is just silently
incomplete, producing wrong answers for ancestry queries.

**Location:** `backend/pipeline/lineage.py` → `get_column_ancestry()`
**Enforcement layer:** Application (via LineageRecorder)
**Tests:** `test_lineage.py`

---

### Rule 2: Schema Drift Classification

When a file is re-uploaded with the same `original_filename`:
- Missing columns compared to last snapshot → `BREAKING` (any pipeline using this column WILL fail)
- Changed dtypes compared to last snapshot → `WARNING` (results may be incorrect)
- New columns not in last snapshot → `INFO` (no impact on existing pipelines)

**Location:** `backend/pipeline/schema_drift.py`
**Enforcement layer:** Application
**Triggered by:** Every POST /api/v1/files/upload

---

### Rule 3: Audit Log Immutability

Audit records created by `log_action()` can never be modified or deleted.
A PostgreSQL trigger (revision `f6a7b8c9d0e1`) raises a database exception on
any `UPDATE` or `DELETE` against `audit_logs`. Application code cannot override this.

**Location:** Database trigger (revision `f6a7b8c9d0e1`)
**Enforcement layer:** Database — independent of application code
**Tests:** `test_audit.py`

---

### Rule 4: First User Admin Promotion

When the `users` table contains zero records, the first user to register is
automatically assigned `role="admin"` regardless of what role they requested or
what the default is.

**Implementation:**
```python
user_count = db.query(func.count(User.id)).scalar()
if user_count == 0:
    new_user.role = UserRole.ADMIN
```

**Location:** `backend/api/auth.py` (register endpoint)
**Enforcement layer:** Application

---

### Rule 5: Password Complexity

Passwords must meet all four requirements at registration time:
1. Minimum 8 characters
2. At least one uppercase letter (A-Z)
3. At least one digit (0-9)
4. At least one special character (not alphanumeric)

**Location:** `backend/schemas.py` — `@field_validator` on `RegisterRequest.password`
**Enforcement layer:** Application (Pydantic validation)

---

### Rule 6: Production Secret Key Validation

`config.py` includes a Pydantic model validator that compares `SECRET_KEY` against
a known placeholder/default value. If they match AND `ENVIRONMENT=production`, the
application refuses to start with a `ValueError`.

**Location:** `backend/config.py`
**Enforcement layer:** Application startup

---

### Rule 7: File Path Security

Uploaded files are always stored at UUID-generated paths. The user-supplied
`original_filename` is stored for display only and NEVER used as a filesystem path.

**Implementation:**
```python
stored_filename = f"{uuid.uuid4()}.{detected_extension}"
stored_path = settings.UPLOAD_DIR / stored_filename
```

**Location:** `backend/api/files.py` (upload handler)
**Enforcement layer:** Application

---

### Rule 8: Pipeline Step Reference Order

Steps can only reference steps defined earlier in the `steps` list.
Forward references are not allowed.

```yaml
# INVALID — filter references a step defined AFTER it
steps:
  - name: filter_data
    type: filter
    input: load_data    # load_data is defined BELOW this step
  - name: load_data
    type: load
    file_id: "..."

# VALID — load_data is defined BEFORE filter_data
steps:
  - name: load_data
    type: load
    file_id: "..."
  - name: filter_data
    type: filter
    input: load_data    # load_data already defined above
```

**Location:** `backend/pipeline/parser.py` — validation rule 7
**Enforcement layer:** Application

---

### Rule 9: No Circular Dependencies

The parser detects circular step references using topological sort.
A step cannot directly or transitively reference itself.

**Location:** `backend/pipeline/parser.py` — validation rule 8
**Error raised:** `CircularDependencyError`

---

### Rule 10: File ID Verification Before Execution

Before dispatching a pipeline to Celery, the API layer queries the database
to verify that all `file_id` values referenced in `load` steps actually exist.

**Performance:** Uses ID-only query: `db.query(UploadedFile.id).all()` (not full object load).

**Location:** `backend/api/pipelines.py` (run endpoint)
**Enforcement layer:** Application

---

### Rule 11: Pipeline Step Count Limit

No pipeline may have more than `MAX_PIPELINE_STEPS` steps (default 50).
This prevents DoS via memory exhaustion in the worker.

**Location:** `backend/pipeline/parser.py` — validation rule 3
**Config variable:** `MAX_PIPELINE_STEPS` (backend/config.py)
**Error raised:** `StepCountExceededError`

---

### Rule 12: Validate Step Non-Blocking

The `validate` step NEVER stops pipeline execution regardless of check results.
Even if all checks fail with severity `"error"`, the pipeline continues to the
next step. Failures are recorded in `StepResult.warnings` and visible in the UI.

**Location:** `backend/pipeline/validators.py`
**Enforcement layer:** Application

---

### Rule 13: Webhook Secret Hidden in API

The raw webhook secret is NEVER returned in any API response.
`WebhookResponse` schema returns `has_secret: bool` only.

**Location:** `backend/schemas.py` — `WebhookResponse.has_secret`
**Enforcement layer:** Application (Pydantic schema)

---

### Rule 14: Webhook URL Validation

Webhook URLs must start with `http://` or `https://`. No other protocols allowed.

**Location:** `backend/api/webhooks.py` — creation validation
**Enforcement layer:** Application

---

### Rule 15: Per-User Webhook Limit

Maximum 10 webhooks per user. Enforced at creation time.

**Location:** `backend/api/webhooks.py`
**Enforcement layer:** Application

---

### Rule 16: Redis SCAN Not KEYS

All Redis pattern-based operations must use cursor-based `SCAN` iteration.
`KEYS *pattern*` blocks the Redis event loop and is forbidden.

**Implementation:** `cache_delete_pattern()` in `backend/utils/cache.py` wraps SCAN.

**Location:** `backend/utils/cache.py`
**Enforcement layer:** Convention (no automatic detection)

---

### Rule 17: Redis Cache Graceful Degradation

All Redis cache operations must catch `RedisError` and fall through to the
database or direct computation. A Redis outage must not crash the API.

**Location:** `backend/utils/cache.py`
**Enforcement layer:** Application (try/except pattern)

---

### Rule 18: Celery JSON Serialization Only

Celery tasks are configured with `task_serializer="json"`. Pickle is never used.
This prevents arbitrary code execution via malicious task payloads.

**Location:** `backend/celery_app.py`
**Enforcement layer:** Celery configuration

---

### Rule 19: Celery Prefetch Multiplier = 1

`worker_prefetch_multiplier=1` is set in `celery_app.py`. One task per worker
at a time. This prevents memory exhaustion when multiple pipeline runs are
queued — each pipeline can load gigabytes of DataFrames.

**Never change to a higher value without profiling.**

**Location:** `backend/celery_app.py`
**Enforcement layer:** Celery configuration

---

### Rule 20: Webhook Delivery Isolation

HTTP calls to external webhook endpoints must run in `webhook_tasks.py` as a
separate Celery task. They must never be added to `execute_pipeline_task`.
The pipeline task must complete without waiting for external HTTP responses.

**Location:** `backend/tasks/webhook_tasks.py`
**Enforcement layer:** Architectural convention

---

### Rule 21: Step Name Sanitization

All pipeline names and step names are sanitized through `string_utils.py`
before storage or use in filesystem paths, Redis keys, or database queries.

**Location:** `backend/utils/string_utils.py`
**Functions:** `sanitize_pipeline_name()`, `sanitize_step_name()`

---

### Rule 22: UTC Timestamps Only

All datetime operations use `datetime.now(timezone.utc)`.
`datetime.utcnow()` is deprecated (since Python 3.12) and was replaced
throughout the codebase in v2.1.3.

**Location:** Everywhere datetime is created — `backend/auth.py`, `backend/tasks/`
**Enforcement layer:** Convention (linting would catch violations)

---

### Rule 23: Docs Disabled in Production

FastAPI's `/docs` (Swagger UI) and `/redoc` endpoints are disabled when
`ENVIRONMENT=production`. The OpenAPI spec is still accessible programmatically.

**Location:** `backend/main.py`
```python
app = FastAPI(
    docs_url=None if settings.ENVIRONMENT == "production" else "/docs",
    redoc_url=None if settings.ENVIRONMENT == "production" else "/redoc"
)
```

---

### Rule 24: Metrics Internal Networks Only

The `/metrics` Prometheus endpoint is restricted at the Nginx level to
internal network ranges only: `10.x`, `172.16.x`, `192.168.x`, `127.0.0.1`.

**Location:** `nginx/conf.d/pipelineiq.conf`
**Added:** v2.1.3

---

### Rule 25: Pipeline Engine Infrastructure Independence

`PipelineRunner`, `StepExecutor`, and `LineageRecorder` must have zero direct
imports of Redis, SQLAlchemy, httpx, or any infrastructure library.
All side effects are injected through the `ProgressCallback` protocol.

This rule exists to preserve testability and architectural cleanliness.
Violating it couples the engine to infrastructure and makes unit testing
require mocking those systems.

---

## 12. Configuration Reference — All 55+ Variables

All variables are defined in `backend/config.py` as a Pydantic `BaseSettings` class.
Values are read from environment variables or `.env` file. The `settings` singleton
is imported everywhere configuration is needed.

### Core Application

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `APP_NAME` | No | `"PipelineIQ"` | String | Application display name |
| `APP_VERSION` | No | `"3.6.2"` | String | Application version string |
| `DEBUG` | No | `False` | Boolean | Verbose logging, SQL echo |
| `LOG_LEVEL` | No | `"INFO"` | String | Python logging level |
| `ENVIRONMENT` | No | `"development"` | String | `"development"` or `"production"` |

### Database

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `DATABASE_URL` | **Yes** | — | PostgreSQL URL | Full connection string |
| `POSTGRES_USER` | No | `"pipelineiq"` | String | For docker-compose only |
| `POSTGRES_PASSWORD` | No | `""` | String | For docker-compose only |
| `POSTGRES_DB` | No | `"pipelineiq"` | String | For docker-compose only |

**Pool config (PostgreSQL only, set in database.py):**
`pool_size=20`, `max_overflow=10`, `pool_pre_ping=True`, `pool_recycle=3600`

### Authentication

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `SECRET_KEY` | **Yes** | `"change-me-in-production"` | String (32+ chars) | JWT signing key |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `1440` | Integer | Token lifetime (24 hours) |

**Startup validation:** If `SECRET_KEY == "change-me-in-production"` AND
`ENVIRONMENT == "production"`, application refuses to start.

### Redis and Celery

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `REDIS_URL` | **Yes** | `"redis://localhost:6379/0"` | Redis URL | Main Redis connection |
| `CELERY_BROKER_URL` | No | Derived from `REDIS_URL` | Redis URL | Defaults to REDIS_URL |
| `CELERY_RESULT_BACKEND` | No | Derived from `REDIS_URL` | Redis URL | Defaults to REDIS_URL |

**Upstash TLS handling (in celery_app.py):**
When `REDIS_URL` starts with `rediss://` (TLS):
```python
broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
```

### File Storage

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `UPLOAD_DIR` | No | `"./uploads"` | Path string | Directory for uploaded files |
| `MAX_UPLOAD_SIZE` | No | `52428800` | Integer (bytes) | Max upload = 50MB |
| `ALLOWED_EXTENSIONS` | No | `{".csv", ".json"}` | frozenset | Allowed file types |

**UPLOAD_DIR** is auto-created on startup if it doesn't exist.

### Pipeline Execution

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `MAX_PIPELINE_STEPS` | No | `50` | Integer | Steps per pipeline (DoS prevention) |
| `MAX_ROWS_PER_FILE` | No | `1000000` | Integer | Max rows per uploaded file |
| `STEP_TIMEOUT_SECONDS` | No | `300` | Integer | Max execution per step (5 min) |

### API

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `API_PREFIX` | No | `"/api/v1"` | String | API URL prefix |
| `CORS_ORIGINS` | No | `["http://localhost:3000", "https://pipeline-iq0.vercel.app", "https://pipelineiq-api.onrender.com"]` | JSON list | Allowed CORS origins |

### Caching

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `CACHE_TTL_STATS` | No | `30` | Integer (seconds) | Dashboard stats cache TTL |

Lineage graph cache TTL is hardcoded to 3600 seconds (1 hour) in `cache.py`.

### Rate Limiting

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `RATE_LIMIT_PIPELINE_RUN` | No | `"10/minute"` | slowapi string | Pipeline execution limit |
| `RATE_LIMIT_FILE_UPLOAD` | No | `"30/minute"` | slowapi string | File upload limit |
| `RATE_LIMIT_VALIDATION` | No | `"60/minute"` | slowapi string | Validate/plan limit |
| `RATE_LIMIT_READ` | No | `"120/minute"` | slowapi string | Read endpoint limit |

### Schema Drift

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `DRIFT_DETECTION_ENABLED` | No | `True` | Boolean | Enable/disable drift detection |
| `MAX_VERSIONS_PER_PIPELINE` | No | `50` | Integer | Max stored versions per pipeline |

### Monitoring

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `FLOWER_USER` | No | `"admin"` | String | Celery Flower web UI username |
| `FLOWER_PASSWORD` | No | `"change-me-in-production"` | String | Celery Flower password |
| `GRAFANA_USER` | No | `"admin"` | String | Grafana dashboard username |
| `GRAFANA_PASSWORD` | No | `"change-me-in-production"` | String | Grafana dashboard password |

### Error Tracking

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `SENTRY_DSN` | No | `""` (disabled) | URL string | Sentry DSN (empty = Sentry disabled) |

### Email / SMTP

| Variable | Required | Default | Format | Description |
|---|---|---|---|---|
| `SMTP_HOST` | No | `""` | String | SMTP server hostname |
| `SMTP_PORT` | No | `587` | Integer | SMTP server port |
| `SMTP_USER` | No | `""` | String | SMTP authentication username |
| `SMTP_PASSWORD` | No | `""` | String | SMTP authentication password |
| `SMTP_FROM` | No | `""` | String | Sender address for all emails |
| `SMTP_USE_TLS` | No | `True` | Boolean | Enable STARTTLS (port 587) |
| `SMTP_USE_SSL` | No | `False` | Boolean | Use SSL directly (port 465) |
| `SMTP_TIMEOUT` | No | `10` | Integer (seconds) | Connection timeout |

### Docker Compose Environment

These variables are used by `docker-compose.yml` for service configuration:

| Variable | Used by | Description |
|---|---|---|
| `POSTGRES_USER` | db service | PostgreSQL superuser |
| `POSTGRES_PASSWORD` | db service | PostgreSQL password |
| `POSTGRES_DB` | db service | Database name |
| `FLOWER_USER` | flower service | Flower auth |
| `FLOWER_PASSWORD` | flower service | Flower auth |
| `GRAFANA_USER` | grafana service | Grafana admin |
| `GRAFANA_PASSWORD` | grafana service | Grafana admin |

---

## 13. Testing — All 352 Tests

### Backend — 259 Tests Across 20 Executable Files

**Test infrastructure:**
- Framework: `pytest` 7.4.4
- Async support: `pytest-asyncio` 0.23.3
- Test factories: `factory-boy` 3.3.0
- Test database: SQLite in-memory (local), PostgreSQL 15 (CI)
- Rate limiter: reset before each test via fixture
- Celery: task dispatch mocked in test client

**`conftest.py` — Shared Fixtures:**

```python
test_engine     # SQLite in-memory SQLAlchemy engine
test_db         # Fresh database session per test (auto-rollback)
client          # TestClient with auth dependencies overridden → injects mock admin user
auth_client     # TestClient WITHOUT auth override → tests real auth flow
sample_sales_df # 20-row deterministic Pandas DataFrame (order_id, amount, status, region)
sample_customers_df # 10-row deterministic Pandas DataFrame (customer_id, name, region)
products_df     # 5-row Pandas DataFrame (product_id, name, price, category)
sales_csv_bytes # Bytes representation of sales DataFrame as CSV
uploaded_sales_file # Pre-created UploadedFile record for sales.csv
lineage_recorder    # Fresh LineageRecorder instance
```

**Test breakdown by file:**

| File | Tests | What is covered |
|---|---|---|
| `test_api.py` | 37 | All REST endpoints, error codes, response shapes, auth enforcement |
| `test_steps.py` | 25 | All 9 step types, edge cases (empty DF, null columns, type errors) |
| `test_validators.py` | 22 | All 12 check types, error severity, edge cases (empty DF, null values) |
| `test_parser.py` | 18 | Valid YAML, invalid YAML, all 13 validation rules, fuzzy suggestions |
| `test_lineage.py` | 18 | Graph construction for all step types, ancestry tracing, impact analysis |
| `test_auth.py` | 17 | Registration, login, JWT expiry, role enforcement, admin-only endpoints |
| `test_planner.py` | 15 | Row estimates for all step types, failure detection, 8 heuristics |
| `test_versioning.py` | 12 | Version creation, list, diff between versions, restore |
| `test_schema_drift.py` | 10 | Column added/removed/type-changed detection, severity classification |
| `test_webhooks.py` | 9 | CRUD, HMAC signature verification, delivery simulation, retry |
| `test_sse.py` | 9 | SSE stream endpoint behavior and lifecycle |
| `test_caching.py` | 8 | Redis get/set/delete/pattern operations, TTL, error handling |
| `test_security.py` | 7 | Auth bypass attempts, path traversal, SQL injection, header injection |
| `test_rate_limiting.py` | 6 | Enforcement of each of the 4 rate limit tiers |
| `test_performance.py` | 5 | Response time thresholds, concurrent upload stability |
| `integration/test_infrastructure.py` | 6 | Gated integration checks for compose/SSE/queue wiring |
| `unit/infrastructure/test_celery_queues.py` | 12 | Queue names/routes, delivery semantics, route coverage |
| `unit/infrastructure/test_redis_connections.py` | 11 | Redis role URL and shared pool invariants |
| `unit/infrastructure/test_sse_lifecycle.py` | 8 | SSE event/terminal lifecycle and headers |
| `unit/infrastructure/test_file_upload.py` | 4 | Upload constants, ORJSON default response, bounded-read guard |

**Test naming conventions:**

```python
# Describes exact expected behaviour
def test_filter_step_removes_rows_where_operator_equals_value():
def test_pipeline_run_status_is_pending_immediately_after_creation():
def test_validate_step_does_not_stop_execution_on_check_failure():

# Regression tests — names the bug
def test_aggregate_step_does_not_raise_on_empty_dataframe():
    # Regression: StepExecutor raised KeyError when aggregate received empty input
def test_schema_drift_detector_handles_file_with_no_columns():
    # Regression: IndexError on zero-column CSV
def test_lineage_recorder_handles_join_step_with_identical_column_names():
    # Regression: NetworkX raised error when both sides of join had same non-key columns
```

**SQLite UUID workaround:**
`auth.py` uses `_generate_uuid()` Python function instead of PostgreSQL's
`gen_random_uuid()` SQL function for UUID generation. This allows tests to run
on SQLite which has no `gen_random_uuid()` function. The workaround is intentional
and must not be "fixed" — it would break the test suite.

**Running backend tests:**
```bash
# All tests
cd backend && pytest tests/ -v

# Specific file
cd backend && pytest tests/test_parser.py -v

# Specific test class
cd backend && pytest tests/test_parser.py::TestParserValidation -v

# Specific test
cd backend && pytest tests/test_parser.py::TestParserValidation::test_duplicate_step_names_raises_error -v

# With coverage
cd backend && pytest tests/ --cov=backend --cov-report=html

# CI-equivalent (fail fast, short traceback)
cd backend && pytest tests/ -v --tb=short -x
```

---

### Frontend — 93 Tests Across 8 Files

**Test infrastructure:**
- Framework: Vitest 4.0.18
- Component testing: React Testing Library 16.3.2
- User event simulation: @testing-library/user-event 14.6.1
- DOM assertions: @testing-library/jest-dom 6.9.1
- Test environment: jsdom 28.1.0
- Setup file: `frontend/__tests__/setup.ts`

**Setup file mocks:**
- `next/navigation` — `useRouter`, `usePathname`, `useSearchParams`
- `next/link` — renders as `<a>` tag
- `EventSource` — global mock for SSE testing
- `ResizeObserver` — stub for layout components
- `motion/react` — animations disabled (renders children directly)

**Test breakdown by file:**

| File | Tests | What is covered |
|---|---|---|
| `api.test.ts` | 26 | Token management (store/retrieve/clear), all 25+ API functions, 401 → redirect, error parsing to ApiError |
| `stores.test.ts` | 26 | pipelineStore state transitions, widgetStore binary tree operations, themeStore switching, keybindingStore conflict detection |
| `pages.test.tsx` | 12 | Login form validation, login error display, register form validation, password complexity UI, demo login button |
| `widgets.test.tsx` | 11 | QuickStats number display, FileUpload drag-and-drop zones, RunHistory status filters, FileRegistry preview buttons |
| `utils.test.ts` | 7 | `cn()` class merging with conflicts, API constants values |
| `middleware.test.ts` | 4 | Unauthenticated redirect to /login, authenticated passthrough, token cookie checking |
| `auth-context.test.tsx` | 4 | AuthProvider login flow, logout clears token, demo login uses demo credentials, token persists |
| `hooks.test.ts` | 3 | Widget layout toggle, workspace switching (Alt+1-5), widgetStore integration |

**Testing philosophy (enforced by codebase convention):**
- Test **behaviour** — what the user sees and can do
- Never test implementation details (state setter calls, internal props)

```typescript
// Correct — tests user-visible behaviour
it("shows error message when login credentials are invalid", async () => {
  server.use(handlers.auth.loginFailed())
  render(<LoginPage />)
  await userEvent.type(screen.getByLabelText(/email/i), "test@example.com")
  await userEvent.type(screen.getByLabelText(/password/i), "wrongpassword")
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }))
  expect(await screen.findByText(/invalid credentials/i)).toBeInTheDocument()
})

// Wrong — tests implementation internals
it("calls setError when API returns 401", () => {
  const setError = vi.fn()
  render(<LoginPage setError={setError} />)
  expect(setError).toHaveBeenCalledWith("invalid credentials")
})
```

**Running frontend tests:**
```bash
cd frontend
npm run test              # Single run
npm run test:watch        # Watch mode
npx tsc --noEmit          # TypeScript type check (no output)
npm run lint              # ESLint check
```

---

### CI/CD Pipeline — 3 Jobs

**Triggers:** Push to `main` or `develop` branch; PRs to `main`

**Job 1: Backend Tests**
```yaml
runs-on: ubuntu-latest
services:
  postgres:
    image: postgres:15-alpine
    env: POSTGRES_USER/PASSWORD/DB
    health-check: pg_isready
  redis:
    image: redis:7-alpine
    health-check: redis-cli ping
steps:
  - checkout
  - setup Python 3.11
  - pip install -r requirements.txt
  - alembic upgrade head
  - pytest tests/ -v --tb=short
  - upload test-results.xml artifact
```

**Job 2: Frontend Check**
```yaml
runs-on: ubuntu-latest
steps:
  - checkout
  - setup Node.js 20
  - npm ci
  - npx tsc --noEmit     # TypeScript check
  - vitest run            # Run 93 tests
  - next build            # Production build
```

**Job 3: Docker Compose Smoke Test** (depends on Jobs 1 and 2)
```yaml
steps:
  - checkout
  - create .env with CI-only credentials
  - docker compose build
  - docker compose up -d
  - sleep 30
  - verify: GET /health via Nginx → expect 200
  - run Python smoke script:
      POST /auth/login → extract token
      POST /api/v1/files/upload → extract file_id
      POST /api/v1/pipelines/run → extract run_id
      poll GET /api/v1/pipelines/{run_id} until status == "COMPLETED" or 30s timeout
  - on failure: docker compose logs (last 50 lines)
  - always: docker compose down --volumes
```

---

## 14. Frontend Architecture — Complete Reference

### Technology Decisions

| Choice | Why |
|---|---|
| Next.js 15 App Router | SSR for auth pages, middleware for auth guards, API proxy rewrites |
| Zustand over Redux | Less boilerplate, TypeScript-first, built-in persistence |
| ReactFlow for lineage | Purpose-built for DAG visualization with custom node types |
| CodeMirror for YAML | Professional editor with syntax highlighting and debounced validation |
| Motion for animations | Smooth modal transitions, animated counters, SSE stream indicators |
| Tailwind CSS v4 | CSS variable-based theming, utility-first, no class name conflicts |
| dnd-kit for drag | Lightweight drag-and-drop, accessible, works with Tailwind |
| Tanstack React Query | Server state management with automatic background refetching |

### Application Shell

**Route structure:**
```
/ or /dashboard   → Main workspace (auth required via middleware)
/login            → Login page
/register         → Registration page
```

**Auth flow:**
1. `middleware.ts` checks for `piq_auth` cookie on every request
2. If missing → redirect to `/login`
3. If present → allow request to proceed
4. `auth-context.tsx` reads `pipelineiq_token` from localStorage for API calls
5. On 401 response → `apiClient` clears token and redirects to `/login`

**Next.js API proxy (next.config.ts):**
```typescript
rewrites: [
  { source: "/api/:path*", destination: "http://localhost:8000/api/:path*" },
  { source: "/auth/:path*", destination: "http://localhost:8000/auth/:path*" },
  { source: "/health", destination: "http://localhost:8000/health" },
  { source: "/metrics", destination: "http://localhost:8000/metrics" },
]
```

This allows the frontend to call `/api/v1/pipelines/run` instead of the full
backend URL, avoiding CORS in development.

---

### Widget System — All 8 Widgets

Every widget is wrapped in `WidgetShell.tsx` which provides:
- Title bar with widget icon and name
- Minimize, close, and settings controls
- Resize handles (drag-based)
- Error boundary isolation (prevents one widget crash from affecting others)

**Widget 1: FileUploadWidget**
- Drag-and-drop file upload zone
- File type validation feedback
- Upload progress indicator
- On success: shows file_id, row count, columns, schema drift badge if detected
- Calls: POST /api/v1/files/upload

**Widget 2: FileRegistryWidget**
- Paginated list of all uploaded files
- For each file: original_filename, row_count, column_count, upload date
- Preview button: fetches first 20 rows, shows in modal
- Schema button: shows column names and dtypes
- Drift badge: red if breaking changes detected since last upload
- Calls: GET /api/v1/files/, GET /api/v1/files/{id}/preview, GET /api/v1/files/{id}/schema/history

**Widget 3: PipelineEditorWidget (318 lines)**
- CodeMirror YAML editor with syntax highlighting
- Debounced validation: 800ms after last keystroke calls POST /api/v1/pipelines/validate
- Inline error markers in the editor for invalid YAML
- "Plan" button: opens dry-run plan modal
- "Run" button: calls POST /api/v1/pipelines/run, triggers Ctrl+Enter keybinding
- "Preview" button: shows sample data at selected step
- Step DAG visualization (StepDAG.tsx): horizontal flow diagram of steps
- Calls: POST /api/v1/pipelines/validate, POST /api/v1/pipelines/plan, POST /api/v1/pipelines/run

**Widget 4: RunMonitorWidget**
- Connects to SSE stream for the active pipeline run
- Shows each step as a progress row: name, type, status indicator, rows in/out, duration
- Animated duration bar that fills as execution progresses
- Step status icons: hourglass (pending), spinner (running), check (completed), X (failed)
- On pipeline_completed or pipeline_failed: SSE connection closed, final state displayed
- Calls: GET /api/v1/pipelines/{run_id}/stream (SSE)

**Widget 5: LineageGraphWidget**
- React Flow interactive DAG visualizer
- 4 custom node types: SourceFileNode (blue), StepNode (purple), ColumnNode (grey), OutputFileNode (green)
- Animated edges on join key connections
- Node click: opens LineageSidebar with column ancestry details
- Controls: zoom, fit view, minimap toggle
- Calls: GET /api/v1/lineage/{run_id}, GET /api/v1/lineage/{run_id}/column, GET /api/v1/lineage/{run_id}/impact

**Widget 6: RunHistoryWidget**
- Paginated list of past pipeline runs
- Status filter: ALL, COMPLETED, FAILED, RUNNING, PENDING, CANCELLED
- For each run: name, status badge (colored), rows out, duration, timestamp
- Click run → loads it as active run in RunMonitorWidget and LineageGraphWidget
- Export button for COMPLETED runs
- Calls: GET /api/v1/pipelines/, GET /api/v1/pipelines/{run_id}/export

**Widget 7: QuickStatsWidget**
- Platform statistics overview with animated counters (Motion library)
- Shows: total runs, success rate (%), files uploaded, active schedules
- Pipeline status breakdown as mini bar chart
- Most-used pipelines list
- Calls: GET /api/v1/dashboard/stats (cached 30s), GET /api/v1/pipelines/stats

**Widget 8: VersionHistoryWidget (265 lines)**
- Pipeline version list for a selected pipeline name
- For each version: version number, created at, change summary
- Diff view: side-by-side YAML diff using unified diff format
- Restore button: sends POST /api/v1/versions/{name}/restore/{version}
- Calls: GET /api/v1/versions/{pipeline_name}, GET /api/v1/versions/{name}/diff/{v1}/{v2}

---

### Layout System — Binary Tree

`widgetStore.ts` (225 lines) manages a **binary tree** layout where:
- Each leaf node is a widget panel
- Each internal node is a split (horizontal or vertical)
- Splitting a panel replaces it with an internal node having two children
- Closing a panel removes the leaf and collapses its parent

**5 independent workspaces:**
Each workspace has its own complete binary tree layout. Switching workspaces
(Alt+1 through Alt+5) swaps which tree is rendered. Layouts persist independently.

**Default widget IDs:**
```typescript
const DEFAULT_WIDGETS = [
  { id: "quick-stats",        title: "Quick Stats",        icon: "bar-chart-2" },
  { id: "file-registry",      title: "File Registry",      icon: "database" },
  { id: "pipeline-editor",    title: "Pipeline Editor",    icon: "code" },
  { id: "lineage-graph",      title: "Lineage Graph",      icon: "git-merge" },
  { id: "file-upload",        title: "File Upload",        icon: "upload-cloud" },
  { id: "run-history",        title: "Run History",        icon: "history" },
  { id: "version-history",    title: "Pipeline Versions",  icon: "history" },
  { id: "manage-connections", title: "Manage Connections", icon: "link" },
]
```

---

### Zustand Stores — All 4

All stores use `create<State>()(persist(...))` pattern with localStorage persistence.

**widgetStore.ts (225 lines)**
- State: `workspaces` (array of 5 layout trees), `activeWorkspace` (0-4), `activeWidgetId`
- Actions: `splitWidget()`, `closeWidget()`, `swapWidgets()`, `setActiveWorkspace()`,
  `setActiveWidget()`
- Persistence: full state including all 5 workspace layouts
- Uses `partialize` to exclude transient selection state

**pipelineStore.ts**
- State: `activeRunId`, `yamlContent`, `lastRunData`
- Actions: `setActiveRunId()`, `setYamlContent()`, `setLastRunData()`, `clearActiveRun()`
- Persistence: `yamlContent` (so editor content survives page reload)
- `lastRunData` excluded from persistence (transient)

**themeStore.ts**
- State: `activeTheme` (string), `customThemes` (dict of theme name → CSS var map)
- Actions: `setTheme()`, `addCustomTheme()`, `deleteCustomTheme()`
- Persistence: full state
- On load: applies theme CSS variables to `:root` via `useTheme()` hook

**keybindingStore.ts**
- State: `bindings` (array of 18 `{action, key, description, handler}` objects)
- Actions: `updateBinding()`, `resetBindings()`
- Persistence: keybinding overrides (not handlers — handlers re-registered on mount)

---

### Theme System — All 7 Themes

Themes define CSS variables applied to `:root`. The `ThemeSelector.tsx` renders
a dropdown and `useTheme()` hook applies the variables.

**7 built-in themes (in BUILT_IN_THEMES array and CommandPalette themes array):**

| Theme ID | Style | Added |
|---|---|---|
| `catppuccin-mocha` | Dark purple, pastel accents | v0.1.2 |
| `tokyo-night` | Dark blue, neon accents | v0.1.2 |
| `gruvbox-dark` | Warm dark, earth tones | v0.1.2 |
| `nord` | Cool dark, arctic palette | v0.1.2 |
| `rose-pine` | Dark purple-rose | v0.1.2 |
| `pipelineiq-dark` | Custom dark theme | v0.1.2 |
| `pipelineiq-light` | Custom light theme | **v2.1.4** |

**IMPORTANT:** Both `ThemeSelector.tsx` (BUILT_IN_THEMES array) and `CommandPalette.tsx`
(themes array) maintain independent theme lists. When adding a new theme, BOTH files
must be updated. This is a known duplication. Do not add a third location.

**CSS variables per theme (~28 variables):**
`--background`, `--foreground`, `--card`, `--card-foreground`, `--popover`,
`--popover-foreground`, `--primary`, `--primary-foreground`, `--secondary`,
`--secondary-foreground`, `--muted`, `--muted-foreground`, `--accent`,
`--accent-foreground`, `--destructive`, `--destructive-foreground`, `--border`,
`--input`, `--ring`, `--radius`, `--shadow`, `--grid-gap`, and others.

---

### Keyboard Shortcuts — All 18

Registered in `keybindingStore.ts` and `lib/constants.ts`. All rebindable.

| Shortcut | Action |
|---|---|
| `Alt + Enter` | Open widget launcher (TerminalLauncher) |
| `Ctrl + K` | Open command palette |
| `Ctrl + Enter` | Run active pipeline from editor |
| `Alt + 1` | Switch to workspace 1 |
| `Alt + 2` | Switch to workspace 2 |
| `Alt + 3` | Switch to workspace 3 |
| `Alt + 4` | Switch to workspace 4 |
| `Alt + 5` | Switch to workspace 5 |
| `Alt + Shift + 1` | Move active widget to workspace 1 |
| `Alt + Shift + 2` | Move active widget to workspace 2 |
| `Alt + Shift + 3` | Move active widget to workspace 3 |
| `Alt + Shift + 4` | Move active widget to workspace 4 |
| `Alt + Shift + 5` | Move active widget to workspace 5 |
| `Alt + Q` | Close active widget |
| `Alt + K` | Open keybindings editor modal |
| `Alt + T` | Open theme selector |
| `Ctrl + Shift + 1` | Toggle pipeline editor widget |
| `Ctrl + Shift + 2` | Toggle lineage graph widget |

---

### SSE Streaming — usePipelineRun.ts

```typescript
function usePipelineRun(runId: string | null) {
  const [events, setEvents] = useState<SSEEvent[]>([])
  const [isConnected, setIsConnected] = useState(false)
  const [connectionError, setConnectionError] = useState<string | null>(null)
  const reconnectAttempts = useRef(0)
  const maxRetries = 5
  const baseDelay = 1000  // 1 second

  useEffect(() => {
    if (!runId) return

    const connect = () => {
      const es = new EventSource(`/api/v1/pipelines/${runId}/stream`)

      es.onopen = () => {
        setIsConnected(true)
        reconnectAttempts.current = 0
      }

      es.onmessage = (e) => {
        const event = JSON.parse(e.data)
        setEvents(prev => [...prev, event])
        if (event.status === "COMPLETED" || event.status === "FAILED") {
          es.close()  // Close on terminal event
        }
      }

      es.onerror = () => {
        es.close()
        setIsConnected(false)
        if (reconnectAttempts.current < maxRetries) {
          const delay = baseDelay * Math.pow(2, reconnectAttempts.current)
          // Backoff: 1s, 2s, 4s, 8s, 16s
          setTimeout(connect, delay)
          reconnectAttempts.current++
        } else {
          setConnectionError("Connection lost after 5 retries")
        }
      }

      return es
    }

    const es = connect()
    return () => es.close()  // Cleanup on unmount
  }, [runId])

  return { events, isConnected, connectionError }
}
```

---

## 15. Infrastructure and Deployment

### Docker Compose — All 9 Services

```yaml
services:
  db:
    image: postgres:15-alpine
    volumes: ["db_data:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pipelineiq"]
      interval: 5s, timeout: 5s, retries: 5

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s

  api:
    build: ./backend
    command: "alembic upgrade head && python scripts/seed_demo.py && uvicorn main:app --host 0.0.0.0 --port 8000"
    ports: ["8000:8000"]
    depends_on: [db (healthy), redis (healthy)]
    volumes: ["uploads:/app/uploads"]

  worker:
    build: ./backend  # SAME IMAGE as api
    command: "celery -A celery_app worker --loglevel=info --concurrency=2"
    depends_on: [db (healthy), redis (healthy)]
    volumes: ["uploads:/app/uploads"]  # SAME VOLUME — shared uploads dir

  frontend:
    build: ./frontend
    ports: ["3000:3000"]
    depends_on: [api]

  flower:
    build: ./backend
    command: "celery -A celery_app flower --port=5555"
    ports: ["5555:5555"]
    depends_on: [redis (healthy)]

  nginx:
    build: ./nginx
    ports: ["80:80"]
    depends_on: [api, frontend, flower]

  prometheus:
    image: prom/prometheus:v2.48.0
    ports: ["9090:9090"]
    volumes: ["./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml"]

  grafana:
    image: grafana/grafana:10.2.0
    ports: ["3001:3000"]  # Host port 3001, container port 3000
    volumes: ["./grafana/provisioning:/etc/grafana/provisioning"]
```

**Volumes:** `db_data`, `prometheus_data`, `grafana_data`, `uploads`
**Network:** Single bridge network `pipelineiq-network`

**Critical shared volume:** Both `api` and `worker` services mount `uploads` volume.
This allows the worker to read uploaded files and write output files, and the API
to serve downloads of those output files.

---

### Nginx Configuration (`nginx/conf.d/pipelineiq.conf`)

**Upstream targets:**
```nginx
set $backend    http://api:8000;
set $frontend   http://frontend:3000;
set $flower     http://flower:5555;
set $grafana    http://grafana:3000;
```

**Security headers on ALL responses:**
```nginx
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
```

**Not implemented (open issues):**
- `Content-Security-Policy` header
- `Strict-Transport-Security` header

**Route rules:**
| Path | Upstream | Special config |
|---|---|---|
| `/api/v1/pipelines/*/stream` | api:8000 | `proxy_buffering off; X-Accel-Buffering: no; 3600s timeout` |
| `/api/` | api:8000 | `client_max_body_size 50m; proxy_read_timeout 300s` |
| `/auth/` | api:8000 | Standard proxy |
| `/webhooks/` | api:8000 | Standard proxy |
| `/health` | api:8000 | Standard proxy |
| `/metrics` | api:8000 | **Internal networks only** (10.x, 172.16.x, 192.168.x, 127.0.0.1) |
| `/docs` | api:8000 | Disabled in production by FastAPI |
| `/flower/` | flower:5555 | Standard proxy |
| `/grafana/` | grafana:3000 | Standard proxy |
| `/` | frontend:3000 | Default fallback |

**SSE-specific Nginx configuration:**
```nginx
location /api/v1/pipelines/ {
    # For stream endpoints
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding on;
    proxy_read_timeout 3600s;
    add_header X-Accel-Buffering "no";
    add_header Cache-Control "no-cache";
    add_header Connection "keep-alive";
}
```

---

### Render.com Deployment Blueprint (`render.yaml`)

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
        sync: false  # Set manually in Render dashboard
      - key: REDIS_URL
        sync: false
      - key: SECRET_KEY
        generateValue: true  # Render generates a random value
      - key: ENVIRONMENT
        value: production
      - key: APP_VERSION
        value: "3.6.2"
      - key: UPLOAD_DIR
        value: /tmp/uploads  # Ephemeral on Render free tier
```

**Critical Render limitation:** Render free tier uses **ephemeral storage**.
Files uploaded to `/tmp/uploads` are lost on restart or deploy. For persistent
file storage on Render, an S3-compatible bucket would be needed.

**Production constraints:**
- API and worker run in the **same container** on Render free tier
- The Dockerfile `CMD` runs `uvicorn` (API) + `celery worker` (background) in one container
- Cold start: ~30–60 seconds after 15 minutes of inactivity
- Cross-region latency: Render Singapore ↔ Neon PostgreSQL us-east-1

---

### Backend Dockerfile Analysis

```dockerfile
FROM python:3.11-slim
WORKDIR /app

# Non-root user
RUN adduser --uid 1000 --disabled-password --gecos "" appuser

# Layer caching optimization
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Create required directories
RUN mkdir -p /tmp/uploads /app/data /app/uploads && \
    chown -R appuser:appuser /tmp/uploads /app/data /app/uploads

USER appuser

# Combined command: migrate + seed + celery (background) + uvicorn
CMD ["sh", "-c", "alembic upgrade head && python scripts/seed_demo.py && celery -A celery_app worker --loglevel=info -D && uvicorn main:app --host 0.0.0.0 --port 8000"]
```

---

### Frontend Dockerfile Analysis

```dockerfile
# Stage 1: Builder
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json .
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_URL
RUN npm run build   # standalone output mode

# Stage 2: Runner
FROM node:20-alpine AS runner
WORKDIR /app
RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
```

---

## 16. Observability — Metrics, Logs, Dashboards

### Prometheus Metrics (`backend/metrics.py`)

**All metric definitions in one file.** Never define metrics elsewhere — circular import.

| Metric name | Type | Labels | What it measures |
|---|---|---|---|
| `pipelineiq_pipeline_runs_total` | Counter | `status` (success/failed) | Total pipeline executions |
| `pipelineiq_pipeline_duration_seconds` | Histogram | — | End-to-end pipeline execution time |
| `pipelineiq_files_uploaded_total` | Counter | — | Total file uploads |
| `pipelineiq_active_users_total` | Gauge | — | Registered active users (is_active=True) |
| `pipelineiq_celery_queue_depth` | Gauge | — | Tasks waiting in Celery queue |

**Plus standard FastAPI metrics** from `prometheus-fastapi-instrumentator`:
- `http_requests_total` — Counter, labels: method, handler, status
- `http_request_duration_seconds` — Histogram, labels: method, handler
- `http_requests_inprogress` — Gauge, labels: method, handler

**Prometheus scrape config:**
```yaml
scrape_configs:
  - job_name: "pipelineiq-api"
    static_configs:
      - targets: ["api:8000"]
    metrics_path: /metrics
    scrape_interval: 10s
```

---

### Grafana Dashboard — All 10 Panels

Provisioned automatically from `grafana/provisioning/dashboards/pipelineiq.json`.

| # | Panel title | Type | Query |
|---|---|---|---|
| 1 | Pipeline Runs / minute | Time series | `rate(pipelineiq_pipeline_runs_total[1m])` by status |
| 2 | API Latency p95 | Time series | `histogram_quantile(0.95, http_request_duration_seconds_bucket[5m])` |
| 3 | API Request Rate | Time series | `rate(http_requests_total[1m])` by handler |
| 4 | Pipeline Success Rate | Gauge | Success / total × 100 (red <80%, yellow <95%) |
| 5 | Pipeline Duration p95 | Time series | `histogram_quantile(0.95, pipelineiq_pipeline_duration_seconds_bucket[5m])` |
| 6 | Files Uploaded | Stat | `pipelineiq_files_uploaded_total` |
| 7 | Celery Queue Depth | Stat | `pipelineiq_celery_queue_depth` (green <5, yellow <20, red 20+) |
| 8 | Active HTTP Connections | Time series | `http_requests_inprogress` |
| 9 | HTTP Error Rate | Time series | `rate(http_requests_total{status=~"5.."}[5m])` |
| 10 | Registered Users | Stat | `pipelineiq_active_users_total` |

---

### Logging

**Logger:** Python standard `logging` module
**Format:** `%(asctime)s | %(levelname)-8s | %(name)s | %(message)s`
**Levels:** DEBUG, INFO, WARNING, ERROR, CRITICAL
**Request ID injection:** `request_id_middleware` in `main.py` adds a UUID to
every request and injects it into structured log output

**What is always logged at INFO level:**
- Pipeline start (run_id, user_id, step_count)
- Pipeline completion or failure (run_id, duration_ms, final status)
- File upload (file_id, filename, row_count)
- User login (user_id, email)
- Schedule trigger (schedule_id, pipeline_name)

**What is logged at ERROR level:**
- Step execution failure (run_id, step_name, error message)
- Webhook delivery failure (webhook_id, attempt_number, error)
- Database connection failure

**What is NEVER logged:**
- `yaml_config` content (could contain sensitive values)
- File content (CSV/JSON data)
- Database connection strings
- JWT tokens
- Any `os.environ` value that could be a secret

---

### Sentry Integration

Configured in `main.py`:
```python
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[FastAPIIntegration(), CeleryIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=0.1,
    )
```

Activated only when `SENTRY_DSN` is non-empty. Never activates in test suite.

Every request includes `X-Request-ID` header. When an error is captured by Sentry,
the `request_id` appears in the error context, allowing correlation between
Sentry errors and application logs.

---

## 17. Security Posture

### What Is Protected

| Control | Implementation | Location |
|---|---|---|
| JWT signing | HS256, SECRET_KEY (min 32 chars) | auth.py |
| Password hashing | bcrypt via passlib | auth.py |
| Password complexity | uppercase + digit + special + 8 chars | schemas.py |
| Production secret validation | Startup validator blocks default key | config.py |
| Rate limiting | slowapi, 4 tiers, per-IP | rate_limiter.py |
| YAML validation | Typed parser, 13 rules, no arbitrary execution | parser.py |
| File path security | UUID-generated paths, original_filename never used | files.py |
| Input sanitization | Pipeline/step name sanitization | string_utils.py |
| Webhook HMAC signing | SHA256, X-PipelineIQ-Signature header | webhook_service.py |
| Webhook secret hiding | has_secret: bool (never raw secret) | schemas.py |
| Metrics restriction | Internal networks only | nginx conf |
| Docs disabled in prod | None when ENVIRONMENT=production | main.py |
| SameSite=Strict cookie | piq_auth cookie | auth.py |
| Audit log immutability | Database trigger | migration f6a7b8c9d0e1 |
| CORS | Configurable allowlist | config.py |
| SQL injection | SQLAlchemy ORM (parameterized queries) | models.py |

### Known Open Vulnerabilities

These are documented, not fixed:

| # | Issue | Location | Risk | Mitigation |
|---|---|---|---|---|
| 1 | JWT in localStorage | auth-context.tsx | XSS token theft | SameSite=Strict cookie |
| 2 | Public read endpoints | files.py, lineage.py | Data leakage in multi-user | Acceptable for single-tenant |
| 3 | No CSP header | nginx conf | XSS defense-in-depth | None |
| 4 | No HTTPS redirect | nginx conf | Mixed content | TLS at Render/Vercel |
| 5 | No auth rate limiting | auth.py | Brute force | None |
| 6 | Worker in same container | Dockerfile | Worker starves API | Separate on paid tier |

---

## 18. Performance Profile

### Rate Limits — Exact Values

| Tier | Limit | Endpoints |
|---|---|---|
| Auth | 5/minute per IP | `/auth/register`, `/auth/login` — **NOTE: currently NOT rate-limited (known gap)** |
| Pipeline execution | 10/minute per user | `POST /api/v1/pipelines/run` |
| File upload | 30/minute per user | `POST /api/v1/files/upload` |
| Validation/dry-run | 60/minute per user | `POST /api/v1/pipelines/validate`, `POST /api/v1/pipelines/plan` |
| Read operations | 120/minute per user | All GET endpoints |

Rate limits are per-IP via slowapi, backed by Redis for distributed rate limiting.

### Caching

| Cached data | TTL | Redis key pattern | Invalidation |
|---|---|---|---|
| Lineage graph | 3600s (1h) | `lineage:{run_id}:graph` | On new run completion |
| Column ancestry | 3600s (1h) | `lineage:{run_id}:col:{step}:{col}` | On new run completion |
| Dashboard stats | 30s | `dashboard:{user_id}:stats` | Automatic TTL |

### Query Optimizations Applied

| Optimization | Location | What it avoids |
|---|---|---|
| ID-only validation query | `validate_pipeline` in pipelines.py | Loading full UploadedFile objects |
| Referenced files only | `execute_pipeline_task` in pipeline_tasks.py | Loading all files for a run |
| Preview with nrows | `preview` in files.py | Loading entire file into memory |
| Performance indexes | Migration a1b2c3d4e5f6 | Full table scans on common queries |
| Lineage pre-computation | LineageRecorder | Recomputing layout on every API call |
| Lineage caching | cache.py | Recomputing lineage graph queries |

### Known Bottlenecks

| Bottleneck | Root cause | Current limit | Future fix |
|---|---|---|---|
| Large JOIN operations | Pandas in-memory | ~2GB per worker | Chunked processing |
| Lineage graph for complex pipelines | O(columns × steps) NetworkX | Pre-computed, not an issue | — |
| Redis pub/sub at scale | Single Redis instance | >100 concurrent runs | Redis Cluster |
| Cross-region DB queries | Render SG ↔ Neon us-east | Adds ~150ms | Same-region deployment |
| Render cold start | Free tier sleep | 30–60s after 15min idle | Paid tier |
| Worker blocks API (Render) | Same container | Under load | Separate containers |

---

## 19. Known Issues and Technical Debt

### Active TODOs (in code or AUDIT_REPORT.md)

| ID | Description | Location | Priority |
|---|---|---|---|
| T1 | Chunked file processing for files >2GB (OOM risk) | `pipeline/steps.py` → `_execute_load` | High |
| T2 | Step-level lineage caching (currently run-level only) | `pipeline/lineage.py` | Medium |
| T3 | Scope read endpoints to authenticated user | `api/files.py`, `api/lineage.py`, `api/versions.py` | High |
| T4 | Move JWT to httpOnly cookie | `frontend/lib/auth-context.tsx` | Medium |
| T5 | Add Content-Security-Policy header | `nginx/conf.d/pipelineiq.conf` | Medium |
| T6 | Add auth rate limiting to login/register | `backend/api/auth.py` | High |
| T7 | Remove duplicate alembic.ini at root | Root `alembic.ini` | Low |
| T8 | Separate auth.py and api/auth.py overlap | Both files | Low |
| T9 | No frontend E2E tests (Playwright/Cypress) | `frontend/` | Medium |
| T10 | No Celery worker health check in Docker Compose | `docker-compose.yml` | Medium |
| T11 | No token refresh strategy | `backend/auth.py`, `frontend/lib/api.ts` | Low |
| T12 | File storage on Render is ephemeral | Render free tier | Production blocker |

### HAD FIXMEs (fixed in v2.1.3)

All issues marked ✅ in `AUDIT_REPORT.md` were fixed in v2.1.3. See CHANGELOG.md for details.

### Structural Anomalies

**Duplicate alembic.ini:**
Both `./alembic.ini` and `./backend/alembic.ini` exist. Do not add a third.
Do not delete either until it's verified which one is authoritative.

**SQLite files in repository:**
`./pipelineiq.db` and `./backend/pipelineiq.db` exist in the repository.
These should be gitignored. `./pipelineiq.db` was added to `.gitignore` in v2.1.3.
`./backend/pipelineiq.db` may or may not be ignored — check `.gitignore`.

**package.json naming history:**
Frontend `package.json` was named `"ai-studio-applet"` until v2.1.3. Now correctly
named `"pipelineiq"`. Do not rename it again.

### Removed Dependencies — Do Not Re-Add

| Package | Reason removed | Version removed |
|---|---|---|
| `@google/genai` (frontend) | Unused, 500KB+ bundle cost | v2.1.3 |
| `firebase-tools` (frontend devDep) | Unused | v2.1.3 |
| `aioredis` (backend) | Deprecated, replaced by `redis.asyncio` | v2.1.3 |
| `aiofiles` (backend) | Not imported anywhere | v2.1.3 |

---

## 20. Development Setup — Step by Step

### Prerequisites

- Python 3.11 or higher
- Node.js 20 or higher
- Docker and Docker Compose v2
- Git

### Option A: Full Docker Compose Stack (Recommended)

```bash
# 1. Clone
git clone https://github.com/Siddharthk17/PipelineIQ.git
cd PipelineIQ

# 2. Create environment file
cp .env.example .env

# 3. Edit .env — minimum required:
# DATABASE_URL=postgresql://pipelineiq:yourpassword@db:5432/pipelineiq
# REDIS_URL=redis://redis:6379/0
# SECRET_KEY=any-string-with-at-least-32-characters-here
# POSTGRES_PASSWORD=yourpassword  (must match DATABASE_URL)

# 4. Build and start all 9 services
docker compose up --build -d

# 5. Wait for services to be healthy (~30 seconds)
docker compose ps

# 6. Access the application
# App:        http://localhost
# API docs:   http://localhost/docs
# Grafana:    http://localhost/grafana  (admin/change-me-in-production)
# Flower:     http://localhost:5555
# Prometheus: http://localhost:9090

# Demo credentials: demo@pipelineiq.app / Demo1234!
```

### Option B: Manual Setup (API + Worker + Frontend separately)

```bash
# 1. Clone
git clone https://github.com/Siddharthk17/PipelineIQ.git
cd PipelineIQ

# 2. Create environment file
cp .env.example .env

# 3. Edit .env with local database/Redis URLs:
# DATABASE_URL=postgresql://localhost:5432/pipelineiq_dev
# REDIS_URL=redis://localhost:6379/0
# SECRET_KEY=local-development-key-minimum-32-chars
# ENVIRONMENT=development
# UPLOAD_DIR=./uploads

# 4. Backend setup
cd backend
python -m venv venv
source venv/bin/activate          # On Windows: venv\Scripts\activate
pip install -r requirements.txt

# 5. Database setup (PostgreSQL must be running)
alembic upgrade head
python -m backend.scripts.seed_demo  # Creates demo user + sample files

# 6. Start API server (Terminal 1)
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 7. Start Celery worker (Terminal 2)
cd backend
source venv/bin/activate
celery -A celery_app worker --loglevel=info --concurrency=2

# 8. Optionally start Celery Beat for schedules (Terminal 3)
celery -A celery_app beat --loglevel=info

# 9. Frontend setup (Terminal 4)
cd frontend
npm install
echo 'NEXT_PUBLIC_API_URL=http://localhost:8000' > .env.local
npm run dev    # Starts at http://localhost:3000
```

### Minimum `.env` for Development

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/pipelineiq_dev
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=local-development-key-minimum-32-characters-here
ENVIRONMENT=development
UPLOAD_DIR=./uploads
```

### Running the Full Local Stack

| Component | Command | Port | Health check |
|---|---|---|---|
| PostgreSQL | `docker run -p 5432:5432 postgres:15-alpine` | 5432 | `pg_isready` |
| Redis | `docker run -p 6379:6379 redis:7-alpine` | 6379 | `redis-cli ping` |
| API | `uvicorn main:app --reload` | 8000 | `GET /health` |
| Worker | `celery -A celery_app worker --loglevel=info` | — | Flower UI |
| Beat | `celery -A celery_app beat --loglevel=info` | — | Flower UI |
| Flower | `celery -A celery_app flower --port=5555` | 5555 | `GET /` |
| Frontend | `npm run dev` | 3000 | `GET /` |

---

## 21. Agent Instructions

### Before Touching Any File

1. Read this entire `AGENTS.md` — you are doing this now.
2. Read `backend/models.py` — understand every entity and relationship.
3. Read `backend/schemas.py` — understand every API input/output contract.
4. Read `backend/pipeline/exceptions.py` — understand the error hierarchy.
5. Read `copilot-instructions.md` — understand every coding convention.
6. Identify which test files cover the module you will modify.
7. Run the full test suite: `cd backend && pytest tests/ -v`
8. Confirm zero failures before making any change.

### Source of Truth for Each Concern

These are the canonical locations. Never define things elsewhere.

| Concern | Single canonical location |
|---|---|
| ORM models | `backend/models.py` only |
| API schemas | `backend/schemas.py` only |
| Prometheus metrics | `backend/metrics.py` only |
| App configuration | `backend/config.py` only |
| JWT / auth utilities | `backend/auth.py` |
| Database session | `backend/database.py` → `get_db` |
| Error hierarchy | `backend/pipeline/exceptions.py` |
| Rate limiter | `backend/utils/rate_limiter.py` |
| UUID utilities | `backend/utils/uuid_utils.py` |
| String sanitization | `backend/utils/string_utils.py` |
| Cache utilities | `backend/utils/cache.py` |
| Frontend API client | `frontend/lib/api.ts` |
| Frontend types | `frontend/lib/types.ts` |
| Widget store | `frontend/store/widgetStore.ts` |
| Theme registry | `frontend/components/theme/ThemeSelector.tsx` AND `frontend/components/layout/CommandPalette.tsx` |

### The Non-Negotiable Three-Part Rule for New Step Types

A new pipeline step type is INCOMPLETE until ALL THREE of these exist:
1. Execution method in `backend/pipeline/steps.py` → `StepExecutor._dispatch`
2. Lineage recording method in `backend/pipeline/lineage.py` → `LineageRecorder`
3. Test in `backend/tests/test_steps.py`

A step with missing lineage recording silently produces a broken lineage graph.
No error. No warning. Wrong output. There is no safety net.
A step with no test ships untested code to production.

### Making Safe Changes

**Adding a new API endpoint:**
1. Update `backend/schemas.py` first — define request and response schemas
2. Implement the router function with `Depends(get_current_user)` or appropriate auth
3. Apply a rate limiter dependency
4. Call `log_action()` for all state-changing operations
5. Use `as_uuid()` on all UUID path parameters
6. Sanitize user-supplied string inputs via `string_utils.py`
7. Add to `backend/api/router.py` if needed
8. Add tests in `backend/tests/test_api.py`
9. Update `frontend/lib/types.ts` if the schema changed

**Adding a new ORM model:**
1. Add the model class to `backend/models.py` — bottom of file
2. `alembic revision --autogenerate -m "add_{table_name}_table"`
3. Review the generated migration — autogenerate makes mistakes
4. Test downgrade: `alembic downgrade -1` → `alembic upgrade head`
5. Update `backend/schemas.py` with response schemas for the new entity

**Adding a new pipeline step type:**
1. Add to `StepExecutor._dispatch` in `backend/pipeline/steps.py`
2. Implement `_execute_{type}(self, step, df) -> pd.DataFrame`
3. Add to `PipelineParser` valid step types in `backend/pipeline/parser.py`
4. Add a `StepConfig` dataclass for the new step
5. Implement `LineageRecorder.record_{type}_step(...)` in `backend/pipeline/lineage.py`
6. Add dry-run estimation in `backend/pipeline/planner.py`
7. Write tests in `backend/tests/test_steps.py`
8. Write lineage tests in `backend/tests/test_lineage.py`

**Adding a new theme:**
1. Define CSS variable map in `frontend/components/theme/ThemeSelector.tsx` → `BUILT_IN_THEMES`
2. Add theme ID to `frontend/components/layout/CommandPalette.tsx` → themes array
3. Test both dropdowns show the new theme

### What to Always Do

- [ ] Update `backend/schemas.py` before changing any API contract
- [ ] Create an Alembic migration before any `backend/models.py` change
- [ ] Write a working `downgrade()` function — test it with `alembic downgrade -1`
- [ ] Add lineage recording method for every new step type
- [ ] Add the new step to `StepExecutor._dispatch`
- [ ] Write tests for every new step in `test_steps.py`
- [ ] Call `log_action()` in every state-changing API handler
- [ ] Define new Prometheus metrics in `backend/metrics.py` only
- [ ] Apply a rate limiter dependency to every new public endpoint
- [ ] Use `Field(description=...)` on every Pydantic schema field
- [ ] Register new keyboard shortcuts in `keybindingStore.ts`
- [ ] Update `frontend/lib/types.ts` when backend schemas change
- [ ] Use `as_uuid()` on all UUID path parameters before DB queries
- [ ] Sanitize pipeline and step names through `string_utils.py`
- [ ] Use `cache_delete_pattern()` not raw `KEYS` for Redis pattern operations
- [ ] Use `datetime.now(timezone.utc)` not `datetime.utcnow()`
- [ ] Use `PgJSONB` for all JSON columns in ORM models

### What to Never Do

- **Never** define an ORM model outside `backend/models.py`
- **Never** define a Pydantic schema outside `backend/schemas.py`
- **Never** define Prometheus metrics outside `backend/metrics.py` — circular import
- **Never** use `print()` for logging — use the `logger` instance
- **Never** import `backend/main.py` into any other module — circular import
- **Never** call `yaml.safe_load()` directly on user YAML — use `parse_pipeline_config()`
- **Never** use the user's `original_filename` as a filesystem path — path traversal
- **Never** add a step type without the lineage recording method — silent graph corruption
- **Never** add a step type without tests
- **Never** call Redis, database, or external services from `PipelineRunner`, `StepExecutor`, or `LineageRecorder`
- **Never** query the database inside a loop — N+1 bug
- **Never** use `datetime.utcnow()` — deprecated
- **Never** use `KEYS *pattern*` in Redis — use `cache_delete_pattern()`
- **Never** return a raw webhook secret in an API response — return `has_secret: bool`
- **Never** set `verify_exp: False` in JWT decode
- **Never** add a NOT NULL column to a populated table in a single migration step
- **Never** write a migration without a working `downgrade()` function
- **Never** use `any` in TypeScript without immediate narrowing
- **Never** create raw `EventSource` in components — use `usePipelineRun` hook
- **Never** call the backend API with raw `fetch` — use `apiClient` from `lib/api.ts`
- **Never** put large data arrays in Zustand state — browser memory
- **Never** update themes in only one file — update both ThemeSelector.tsx AND CommandPalette.tsx
- **Never** re-add `@google/genai`, `firebase-tools`, `aioredis`, or `aiofiles`
- **Never** change `worker_prefetch_multiplier` above 1 without profiling memory
- **Never** add external HTTP calls inside `execute_pipeline_task` — use `webhook_tasks.py`

### Before Declaring Done

```bash
# Backend
cd backend
pytest tests/ -v                   # Latest baseline: 253 passed, 6 skipped
python -c "from backend.main import app"  # No circular import errors

# Frontend
cd frontend
npx tsc --noEmit                   # Zero TypeScript errors
npm run lint                        # Zero ESLint errors
npm run test                        # All 93+ tests pass

# Integration
docker compose up --build -d        # All 9 services start cleanly
curl http://localhost/health         # Returns: {"status": "ok", "db": "ok", "redis": "ok"}
```

**Feature-specific checks:**
- New step type: dispatch entry + LineageRecorder method + test_steps.py test
- models.py change: migration created, downgrade tested
- API contract change: schemas.py first, types.ts updated after
- New endpoint: rate limiter + log_action + Field(description) on all schema fields
- New Prometheus metric: defined in metrics.py, pipelineiq_ prefix
- New keyboard shortcut: registered in keybindingStore.ts, no conflicts verified
- Memory check: Celery worker does not exceed RAM on 100k-row test dataset

---

*End of AGENTS.md — PipelineIQ v2.1.4*
