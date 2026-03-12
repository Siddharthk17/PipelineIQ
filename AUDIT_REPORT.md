# PipelineIQ — Technical Audit Report

**Date:** February 2026
**Scope:** Full codebase review — backend, frontend, infrastructure, CI/CD, security, testing
**Codebase:** ~16,000 lines across Python, TypeScript, YAML, Docker, and Nginx configs

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Codebase Metrics](#2-codebase-metrics)
3. [Backend Architecture](#3-backend-architecture)
4. [Pipeline Engine](#4-pipeline-engine)
5. [Data Lineage System](#5-data-lineage-system)
6. [Frontend Architecture](#6-frontend-architecture)
7. [Database Design](#7-database-design)
8. [Security Audit](#8-security-audit)
9. [API Design](#9-api-design)
10. [Testing](#10-testing)
11. [Infrastructure](#11-infrastructure)
12. [CI/CD Pipeline](#12-cicd-pipeline)
13. [Observability](#13-observability)
14. [Performance Characteristics](#14-performance-characteristics)
15. [Code Quality Observations](#15-code-quality-observations)
16. [Identified Issues](#16-identified-issues)
17. [Recommendations](#17-recommendations)

---

## 1. Executive Summary

PipelineIQ is a data pipeline orchestration platform built from scratch. Users define transformation pipelines in YAML, upload data files, execute pipelines asynchronously, and inspect the results through column-level lineage graphs.

The system covers a wide surface area for a single-developer project: 9-step pipeline engine, column-level lineage tracking, schema drift detection, pipeline versioning with diffs, data quality validation (12 checks), real-time SSE streaming, JWT auth with RBAC, webhooks with retries, audit logging, 5 Prometheus metrics, a 10-panel Grafana dashboard, and a keyboard-driven React frontend with 5 workspaces, 8 widgets, a command palette, and 6 themes.

**What works well:**
- The pipeline engine is cleanly structured — parser, validator, executor, and lineage recorder are separate concerns with clear interfaces
- The YAML → typed dataclass parsing catches errors early with fuzzy suggestions for typos
- Column-level lineage is implemented properly using NetworkX with topological layout for visualization
- The frontend workspace system (binary tree layout with 5 independent workspaces) is more sophisticated than typical dashboards
- 206 backend tests + 93 frontend tests (299 total) cover the core engine, API, auth, stores, widgets, and edge cases
- Docker Compose brings up 9 services with a single command
- The CI pipeline does end-to-end smoke testing (login → upload → run → poll for completion)

**What needs attention:**
- Several API endpoints lack authentication checks (lineage, file listing, pipeline listing are fully public)
- No input size limits on YAML config payloads at the API level
- The Celery worker runs in the same container as the API on Render (acceptable for free tier, not for production scale)
- No database connection pooling configuration for production (relies on SQLAlchemy defaults)
- Frontend stores JWT in localStorage (vulnerable to XSS; httpOnly cookie would be more secure)
- No HTTPS redirect enforcement at the Nginx level

---

## 2. Codebase Metrics

### Line Counts

| Area | Lines | Files |
|------|-------|-------|
| Backend source (Python) | 7,186 | 48 |
| Backend tests (Python) | 3,098 | 16 |
| Frontend (TypeScript/TSX/CSS) | 5,317 | 45 |
| Infrastructure (Docker, Nginx, YAML) | 661 | 10 |
| Database migrations (Python) | 489 | 6 |
| **Total** | **~16,751** | **125** |

### Largest Files (backend)

| File | Lines | Purpose |
|------|-------|---------|
| `pipeline/parser.py` | 703 | YAML parsing + 13 validation rules |
| `pipeline/lineage.py` | 634 | NetworkX graph construction + React Flow layout |
| `pipeline/steps.py` | 606 | 9 step type executors (Pandas operations) |
| `pipeline/exceptions.py` | 443 | 14-class exception hierarchy with fuzzy suggestions |
| `api/files.py` | 458 | File upload, preview, schema history, drift |
| `api/pipelines.py` | 421 | Pipeline CRUD, execution, SSE streaming |
| `models.py` | 348 | 10 SQLAlchemy ORM models |
| `schemas.py` | 334 | 20+ Pydantic request/response models |

### Largest Files (frontend)

| File | Lines | Purpose |
|------|-------|---------|
| `widgets/PipelineEditorWidget.tsx` | 318 | CodeMirror YAML editor with validation |
| `app/register/page.tsx` | 280 | Registration form |
| `app/login/page.tsx` | 275 | Login form with demo button |
| `widgets/VersionHistoryWidget.tsx` | 265 | Pipeline version diffing |
| `lib/api.ts` | 252 | Fetch wrapper + all API endpoint functions |
| `lineage/LineageGraph.tsx` | 251 | ReactFlow graph rendering |
| `store/widgetStore.ts` | 225 | Binary tree layout + 5 workspaces |

### Dependency Count

| Area | Count | Notable |
|------|-------|---------|
| Backend (PyPI) | 28 packages | FastAPI, SQLAlchemy, Pandas, Celery, NetworkX |
| Frontend (npm) | 33 packages | Next.js 15, React 19, Zustand, ReactFlow, CodeMirror |

---

## 3. Backend Architecture

### Application Structure

```
Request → Nginx → FastAPI (main.py)
                    ├── Middleware: request_id, timing, CORS, rate limiting
                    ├── /auth/*     → auth.py (JWT login/register)
                    ├── /api/v1/*   → api/router.py
                    │                 ├── files.py      (upload, preview, schema)
                    │                 ├── pipelines.py   (validate, plan, run, stream)
                    │                 ├── lineage.py     (graph, ancestry, impact)
                    │                 ├── versions.py    (list, diff, restore)
                    │                 ├── webhooks.py    (CRUD, test, deliveries)
                    │                 └── audit.py       (logs)
                    ├── /health     → health check (DB + Redis)
                    └── /metrics    → Prometheus
```

### Configuration

`config.py` uses Pydantic BaseSettings with 55+ environment variables grouped into: core app settings, database, Redis/Celery, file handling, pipeline limits, rate limiting, auth, monitoring, and deployment. The upload directory is auto-created with a Pydantic validator. Celery broker/backend URLs default to `REDIS_URL` if not explicitly set.

### Database Layer

`database.py` builds the SQLAlchemy engine based on the URL scheme:
- **PostgreSQL**: pool_size=20, max_overflow=10, pool_pre_ping=True, pool_recycle=3600
- **SQLite**: check_same_thread=False (for testing)

Sessions are yielded through a FastAPI dependency (`get_db`) with automatic cleanup in a `finally` block.

### Task Queue

Celery is configured in `celery_app.py` with JSON serialization, UTC timezone, task tracking, and prefetch_multiplier=1 (one task per worker at a time). For Upstash Redis (TLS), the app conditionally adds `broker_use_ssl` and `redis_backend_use_ssl` with `ssl.CERT_NONE` when the URL starts with `rediss://`.

### Error Handling

`main.py` registers three exception handlers:
1. `PipelineIQError` → 400 with structured error body (type, message, details, request_id)
2. `RequestValidationError` → 422 with field-level errors
3. Generic `Exception` → 500 with error reference ID

Every request gets an `X-Request-ID` header (UUID4) and `X-Process-Time` header (seconds).

---

## 4. Pipeline Engine

The engine lives in `backend/pipeline/` (8 files, ~3,000 lines) and handles everything from YAML parsing to step execution to lineage recording.

### Parser (`parser.py`, 703 lines)

YAML is parsed into a hierarchy of typed dataclasses:

```
PipelineConfig
  ├── name: str
  ├── description: Optional[str]
  └── steps: List[StepConfig]
        ├── LoadStepConfig(file_id, alias)
        ├── FilterStepConfig(input, column, operator, value)
        ├── SelectStepConfig(input, columns)
        ├── RenameStepConfig(input, mapping)
        ├── JoinStepConfig(left, right, on, how)
        ├── AggregateStepConfig(input, group_by, aggregations)
        ├── SortStepConfig(input, by, order)
        ├── SaveStepConfig(input, filename)
        └── ValidateStepConfig(input, rules)
```

The parser runs 13 validation checks and returns all errors at once (not fail-fast):
1. Pipeline name is non-empty
2. At least 1 step exists
3. No duplicate step names
4. Step count within MAX_PIPELINE_STEPS limit
5. Step names match `[a-zA-Z0-9_]` pattern
6. Step types are valid enum values
7. Input/left/right references point to earlier steps
8. Load steps reference registered file IDs
9. Filter operators are valid
10. Join configs have valid key and method
11. Aggregation functions are in the allowed set (sum, mean, min, max, count, median, std, var, first, last)
12. At least one save step exists (warning, not error)
13. Validate check types are supported

Typo suggestions use `difflib.get_close_matches(target, candidates, n=1, cutoff=0.6)`. If you type `filtr` instead of `filter`, the error message will suggest the correct type.

### Step Executor (`steps.py`, 606 lines)

Uses a **dispatch dictionary** pattern — a `Dict[StepType, Callable]` maps each step type to its handler method. No if-elif chains.

Each handler follows the same pattern:
1. Resolve input DataFrames from `df_registry` (a `Dict[str, pd.DataFrame]` maintained by the runner)
2. Validate columns exist (with fuzzy suggestions on error)
3. Execute the Pandas operation
4. Record lineage
5. Return `StepExecutionResult` with rows_in, rows_out, columns_in, columns_out, duration_ms

**Filter** supports 12 operators mapped to lambdas:
```
equals, not_equals, greater_than, less_than, gte, lte,
contains, not_contains, starts_with, ends_with, is_null, is_not_null
```

**Aggregate** handles multi-level column flattening. After `groupby().agg()`, Pandas produces `(column, function)` tuples as column names. The executor flattens these to `column_function` (e.g., `amount_sum`), with a special case: if the column name equals the function name, it keeps just the column name.

**Join** uses `pd.merge()` with `suffixes=("_left", "_right")` for column name conflicts.

### Runner (`runner.py`, 257 lines)

The runner iterates through steps sequentially. For each step:
1. Emit `StepProgressEvent(status=RUNNING)` via the progress callback
2. Call the step executor
3. Store the output DataFrame in `df_registry[step.name]`
4. Emit `StepProgressEvent(status=COMPLETED)` with metrics
5. If any step throws `StepExecutionError`, mark the run as FAILED and stop

The progress callback is dependency-injected — the runner doesn't know it's going to Redis. In tests, it's a no-op lambda. In production, it's `make_redis_progress_callback(run_id)` which publishes JSON to a Redis pub/sub channel.

### Planner (`planner.py`, 212 lines)

Dry-run mode estimates what would happen without executing anything:
- **Filter** keeps ~70% of rows (heuristic)
- **Aggregate** reduces to ~10% of rows
- **Join** output depends on type: inner = min(left, right), left = left, outer = left + right
- **Duration** estimated as `max(base_ms, row_count // divisor)` with step-type-specific divisors
- **Failure detection**: checks if referenced file IDs exist in the database

### Exception Hierarchy (`exceptions.py`, 443 lines)

14 exception classes organized in two groups:

**Config errors** (caught during parsing): `InvalidYAMLError`, `MissingRequiredFieldError`, `DuplicateStepNameError`, `InvalidStepTypeError`, `InvalidStepReferenceError`, `FileNotRegisteredError`

**Runtime errors** (caught during execution): `ColumnNotFoundError`, `InvalidOperatorError`, `JoinKeyMissingError`, `AggregationError`, `FileReadError`, `UnsupportedFileFormatError`, `StepTimeoutError`

All inherit from `PipelineIQError` which provides `to_dict()` for API serialization.

---

## 5. Data Lineage System

### Graph Construction (`lineage.py`, 634 lines)

The `LineageRecorder` builds a `networkx.DiGraph` during pipeline execution. Every step type has a dedicated recording method that adds nodes and edges with typed metadata.

**Node naming convention:**
- Source file: `file::{file_id}`
- Column: `col::{step_name}::{column_name}`
- Step: `step::{step_name}`
- Output file: `output::{step_name}::{filename}`

**Per-step recording:**
- **load**: file → step → column nodes (one per CSV column)
- **filter/sort/validate** (passthrough): input columns → step → output columns (same names)
- **select** (projection): kept columns get edges through; dropped columns get edges to step only (dead ends)
- **rename**: input columns → step → output columns (new names from mapping)
- **join**: left columns + right columns → step → merged output columns; join key edges marked with `is_join_key=True`
- **aggregate**: group-by columns + aggregation columns → step → new output columns (e.g., `amount_sum`)
- **save**: input columns → step → output file node

### Query Methods

**Column ancestry** (`get_column_ancestry`): Uses `nx.ancestors()` to walk backward from a column node. Returns the source file, source column, and the transformation chain (list of steps traversed in topological order).

**Impact analysis** (`get_impact_analysis`): Uses `nx.descendants()` to walk forward from a column node. Returns all affected steps and output columns downstream.

### Visualization Layout

The React Flow layout uses a Sugiyama-inspired algorithm:
1. Topological sort the entire graph
2. Assign layers: each node's layer = max(predecessor layers) + 1
3. Position: X = layer × 300px, Y = index-within-layer × 80px

The computed layout is serialized alongside the raw NetworkX graph data and stored in the `lineage_graphs` table. This avoids recomputation on every API call.

---

## 6. Frontend Architecture

### Tech Choices

- **Next.js 15** with App Router — server-side rendering, middleware for auth guards
- **React 19** — latest concurrent features
- **Zustand 5** — lightweight state management with localStorage persistence
- **ReactFlow 12** — interactive graph visualization for lineage
- **CodeMirror 4** — YAML editor with syntax highlighting
- **Motion 12** — animations for modals, transitions, number counters
- **Tailwind CSS v4** — utility-first styling with CSS variable-based theming
- **dnd-kit** — drag-and-drop for widget repositioning
- **Tanstack React Query 5** — server state management with caching

### Layout System

The widget layout is a **binary tree** where each node is either a widget or a split (horizontal/vertical). This allows arbitrary nesting of panels, similar to a tiling window manager.

There are **5 independent workspaces**, each with its own layout tree. Widgets can be moved between workspaces via `Alt+Shift+1-5`. The entire layout state is persisted to localStorage.

### State Management

4 Zustand stores, all persisted:
- **themeStore** — active theme name + custom themes (CSS variable maps)
- **widgetStore** — 5 workspace layout trees + active workspace + active widget
- **pipelineStore** — active run ID + last YAML config
- **keybindingStore** — 18 keybinding definitions (action → key combo)

### Real-Time Updates

The `usePipelineRun` hook connects to the SSE endpoint for the active run. Events are: `step_started`, `step_completed`, `step_failed`, `pipeline_completed`, `pipeline_failed`. On terminal events, the hook refetches the full run data and invalidates React Query caches.

### Auth Flow

1. Login/register POSTs to `/auth/login` or `/auth/register`
2. JWT stored in localStorage (`pipelineiq_token`)
3. Cookie `piq_auth=1` set for Next.js middleware
4. Middleware redirects unauthenticated users to `/login`
5. `fetchApi()` wrapper adds `Authorization: Bearer` header to all requests
6. On 401 response, token is cleared and user redirected to `/login`

### Theme System

6 built-in themes define 28 CSS variables (background colors, accent colors, text colors, border colors, grid gap, border radius, shadow). The ThemeBuilder lets users create custom themes by picking values for each variable. Custom themes are serialized as JSON and persisted in Zustand.

---

## 7. Database Design

### Models (10 tables)

| Model | PK | Key Fields | Relationships |
|-------|----|-----------|----|
| User | UUID | email, username, hashed_password, role, is_active | → pipeline_runs, webhooks |
| PipelineRun | UUID | pipeline_name, status, yaml_config, started_at, completed_at, total_rows_in/out, user_id | → step_results, lineage_graphs |
| StepResult | UUID | step_name, step_type, status, rows_in/out, columns_in/out, duration_ms, warnings | ← pipeline_run |
| UploadedFile | UUID | filename, stored_path, row_count, column_count, columns, dtypes, file_size | → schema_snapshots |
| LineageGraph | UUID | graph_data (JSONB), react_flow_data (JSONB) | ← pipeline_run |
| SchemaSnapshot | UUID | file_id, run_id, columns, dtypes, snapshot_at | ← uploaded_file |
| PipelineVersion | UUID | pipeline_name, version_number, yaml_config, change_summary | unique(name, version) |
| Webhook | UUID | user_id, url, secret, events, is_active | → deliveries |
| WebhookDelivery | UUID | webhook_id, event_type, payload, response_status, retry_number | ← webhook |
| AuditLog | UUID | user_id, action, resource_type, resource_id, details (JSONB), ip_address, user_agent | immutable (DB trigger) |

**Design notes:**
- All primary keys are UUIDs (PostgreSQL native UUID type after migration 3)
- JSONB columns for flexible data (graph data, step warnings, schema info) on PostgreSQL; falls back to JSON on SQLite
- Cascade delete-orphan on step_results and webhook_deliveries
- `PipelineRun.duration_ms` is a computed property (`completed_at - started_at`)
- Timestamps are timezone-aware with server defaults
- The audit_logs table has a database trigger that raises an exception on UPDATE or DELETE — records are immutable

---

## 8. Security Audit

### Authentication

| Aspect | Implementation | Assessment |
|--------|---------------|------------|
| Password hashing | bcrypt via passlib | ✅ Industry standard |
| Token format | JWT HS256, 24h expiry | ✅ Reasonable for this use case |
| Token storage | localStorage | ⚠️ Vulnerable to XSS; httpOnly cookie preferred |
| Token validation | Decode → extract user_id → query DB → check is_active | ✅ Full validation chain |
| Admin enforcement | Separate `get_current_admin` dependency | ✅ Clean separation |
| First user auto-admin | On registration, if user count = 0 | ✅ Acceptable bootstrap pattern |

### Authorization Gaps

Several endpoints are publicly accessible without authentication:

| Endpoint | Current Auth | Should Be |
|----------|-------------|-----------|
| GET `/api/v1/files/` | None | User-scoped |
| GET `/api/v1/files/{id}` | None | User-scoped |
| GET `/api/v1/files/{id}/preview` | None | User-scoped |
| GET `/api/v1/pipelines/` | None | User-scoped |
| GET `/api/v1/pipelines/{id}` | None | User-scoped |
| GET `/api/v1/pipelines/stats` | None | User-scoped |
| GET `/api/v1/lineage/*` | None | User-scoped |
| GET `/api/v1/versions/*` | None | User-scoped |

Write endpoints (upload, run, delete) correctly require authentication.

### HTTP Security Headers (Nginx)

| Header | Value | Status |
|--------|-------|--------|
| X-Frame-Options | SAMEORIGIN | ✅ |
| X-Content-Type-Options | nosniff | ✅ |
| X-XSS-Protection | 1; mode=block | ✅ |
| Referrer-Policy | strict-origin-when-cross-origin | ✅ |
| Content-Security-Policy | Not set | ⚠️ Should be added |
| Strict-Transport-Security | Not set | ⚠️ Should be added for HTTPS |

### Input Validation

| Input | Validation | Status |
|-------|-----------|--------|
| File upload | Extension check (.csv, .json), MAX_UPLOAD_SIZE (50MB) | ✅ |
| Pipeline YAML | Parsed into typed dataclasses, 13 validation rules | ✅ |
| Step names | Regex: `[a-zA-Z0-9_]` only | ✅ |
| Filter values | Type coercion in filter lambdas | ✅ |
| Regex patterns (validate step) | Wrapped in try/catch for invalid regex | ✅ |
| YAML payload size | No explicit limit at API level | ⚠️ |
| SQL injection | SQLAlchemy ORM (parameterized queries) | ✅ |
| Path traversal | Files stored with UUID names, not user-supplied paths | ✅ |

### Webhook Security

- Payloads signed with HMAC-SHA256 using per-webhook secrets
- Signature sent in `X-PipelineIQ-Signature` header
- 3-attempt retry with increasing delays

### Secrets Management

- All secrets via environment variables (not hardcoded)
- `.env` in `.gitignore`
- `.env.example` contains only placeholder values
- CI uses inline test-only values (not production secrets)

---

## 9. API Design

### Consistency

- All endpoints return JSON
- Error responses follow a consistent schema: `{type, message, details, request_id}`
- UUID-based resource identifiers
- Rate limiting on write-heavy endpoints (4 tiers)

### Rate Limiting Configuration

| Tier | Limit | Endpoints |
|------|-------|-----------|
| Pipeline execution | 10/minute | POST /pipelines/run |
| File upload | 30/minute | POST /files/upload |
| Validation | 60/minute | POST /pipelines/validate, /pipelines/plan |
| Read operations | 120/minute | GET /pipelines/*, /files/* |

Rate limits are per-IP via SlowAPI backed by Redis.

### SSE Implementation

The pipeline stream endpoint (`GET /pipelines/{id}/stream`) subscribes to a Redis pub/sub channel (`pipeline_progress:{run_id}`). Nginx is configured for SSE passthrough with buffering disabled and a 3600-second timeout. Terminal events (`pipeline_completed`, `pipeline_failed`) trigger stream closure.

### Documentation

- OpenAPI spec auto-generated by FastAPI
- Swagger UI at `/docs`
- ReDoc at `/redoc`
- Postman collection with 23 requests across 6 folders

---

## 10. Testing

### Coverage

**299 total tests (206 backend + 93 frontend) across 22 files.**

#### Backend — 206 tests across 14 files

| Category | Tests | Files | What's Tested |
|----------|-------|-------|---------------|
| API endpoints | 34 | 1 | All REST routes, error codes, response shapes |
| Step executor | 25 | 1 | All 9 step types, edge cases (empty results, missing columns) |
| Validation engine | 22 | 1 | All 12 check types, severity levels |
| YAML parser | 18 | 1 | Valid configs, invalid configs, fuzzy suggestions |
| Lineage tracking | 18 | 1 | Graph construction, ancestry, impact analysis |
| Authentication | 17 | 1 | Register, login, JWT, roles, admin-only |
| Dry-run planner | 15 | 1 | Row estimates, failure detection, heuristics |
| Pipeline versioning | 12 | 1 | Save, list, diff, restore |
| Schema drift | 10 | 1 | Added/removed/type-changed detection |
| Webhooks | 9 | 1 | CRUD, signing, delivery, retries |
| Caching | 8 | 1 | Redis get/set/delete/pattern |
| Security | 7 | 1 | Auth bypass, injection attempts, headers |
| Rate limiting | 6 | 1 | Enforcement per tier |
| Performance | 5 | 1 | Response times, concurrent load |

#### Frontend — 93 tests across 8 files

| Category | Tests | Files | What's Tested |
|----------|-------|-------|---------------|
| API layer | 26 | 1 | Token management, fetchApi, all 25+ API functions, error handling, 401 redirect |
| Zustand stores | 26 | 1 | Pipeline, widget (binary tree), theme, keybinding stores |
| Page components | 12 | 1 | Login/register forms, validation, error states, demo login |
| Widget components | 11 | 1 | QuickStats, FileUpload, RunHistory, FileRegistry rendering and interaction |
| Utilities | 7 | 1 | cn() classname merging, API constants |
| Middleware | 4 | 1 | Auth redirect logic for unauthenticated users |
| Auth context | 4 | 1 | AuthProvider login, logout, demo login, token persistence |
| Hooks | 3 | 1 | Widget layout toggle, workspace switching |

### Test Infrastructure

- **Backend:** conftest.py provides SQLite in-memory engine, test DB session, authenticated TestClient (admin mock), sample DataFrames (sales 20 rows, customers 10 rows, products 5 rows), CSV/JSON byte fixtures, lineage recorder, file upload helpers. Tests run in isolation — each test gets a fresh database session. Rate limiter is reset before each test via fixture. The `client` fixture overrides auth to inject an admin user; `auth_client` tests real auth flows.
- **Frontend:** Vitest + React Testing Library + jsdom. Setup file provides jest-dom matchers, cleanup, mocks for next/navigation, next/link, EventSource, ResizeObserver. motion/react mocked to avoid animation issues. API calls mocked with vi.spyOn(globalThis, "fetch") or vi.mock().

### CI Test Execution

Backend tests run in CI against real PostgreSQL 15 + Redis 7 (not SQLite). Frontend tests run with Vitest in the CI pipeline (tsc → vitest → build). The CI also runs a Docker Compose smoke test that builds all 9 services and runs a Python script doing: health check → login → upload CSV → run pipeline → poll for completion.

### Test Gaps

| Area | Status |
|------|--------|
| Frontend unit tests | ✅ 93 tests with Vitest + React Testing Library |
| Frontend E2E tests | ❌ None — no Playwright/Cypress |
| Load testing | Minimal — 5 tests in test_performance.py |
| Celery task integration tests | ❌ Tasks tested through API, not directly |
| Webhook delivery retry tests | Partial — retry logic tested, actual HTTP delivery mocked |
| Migration rollback tests | ❌ Not tested |

---

## 11. Infrastructure

### Docker Compose (9 services)

| Service | Image | Health Check | Depends On |
|---------|-------|-------------|------------|
| db | postgres:15-alpine | `pg_isready` every 5s | — |
| redis | redis:7-alpine | `redis-cli ping` every 10s | — |
| api | Custom (Python 3.11-slim) | Via Nginx /health | db (healthy), redis (healthy) |
| worker | Same image as api | — | db (healthy), redis (healthy) |
| frontend | Custom (Node 20) | — | api |
| flower | Same image as api | — | redis (healthy), db (healthy) |
| nginx | Custom (Alpine 1.25) | — | api, frontend, flower, grafana |
| prometheus | prom/prometheus:v2.48.0 | — | — |
| grafana | grafana/grafana:10.2.0 | — | prometheus |

**Volumes:** `db_data`, `prometheus_data`, `grafana_data`, `uploads` (shared between api and worker)

**Network:** Single bridge network `pipelineiq-network`

### Nginx Configuration

Nginx routes requests based on path prefix:

| Path | Upstream | Special Config |
|------|----------|---------------|
| `/api/` | api:8000 | 50MB body limit, 300s timeout |
| `/api/v1/pipelines/*/stream` | api:8000 | Buffering OFF, 3600s timeout (SSE) |
| `/auth/`, `/webhooks/`, `/audit/` | api:8000 | Standard proxy |
| `/health`, `/metrics`, `/docs` | api:8000 | Standard proxy |
| `/flower/` | flower:5555 | — |
| `/grafana/` | grafana:3000 | — |
| `/` | frontend:3000 | Default fallback |

### Production Deployment

| Component | Platform | Plan |
|-----------|----------|------|
| Backend API + Worker | Render.com | Free (750h/month, sleeps after 15min) |
| Frontend | Vercel | Free (auto-deploy from GitHub) |
| Database | Neon.tech PostgreSQL | Free (connection pooler, us-east-1) |
| Redis | Upstash | Free (TLS, rediss://) |

The backend Dockerfile runs: `alembic upgrade head → seed_demo → celery worker (background) → uvicorn`. API and worker share a single container on Render's free tier.

### Dockerfile Analysis

**Backend** (`backend/Dockerfile`):
- Base: `python:3.11-slim`
- Non-root user: `appuser` (uid 1000)
- Layer caching: requirements.txt copied and installed before source code
- Directories created: `/tmp/uploads`, `/app/data`, `/app/uploads` (all chowned to appuser)
- CMD: shell command chain — migrations, seed, celery (backgrounded), uvicorn

**Frontend** (`frontend/Dockerfile`):
- Multi-stage build: builder stage installs deps + builds, runner stage copies standalone output
- Non-root user: `nextjs`
- Build-time arg: `NEXT_PUBLIC_API_URL` baked into the build
- Output: standalone mode (`node server.js`)

---

## 12. CI/CD Pipeline

### GitHub Actions Workflow (`.github/workflows/ci.yml`)

**Triggers:** Push to `main`/`develop`, PRs to `main`

**Job 1: Backend Tests**
- Runner: ubuntu-latest
- Services: PostgreSQL 15-alpine, Redis 7-alpine (both health-checked)
- Steps: checkout → Python 3.11 setup → pip install → alembic upgrade head → pytest (206 tests) → upload test-results.xml artifact

**Job 2: Frontend Check**
- Runner: ubuntu-latest
- Steps: checkout → Node 20 setup → npm ci → tsc --noEmit → npm run build

**Job 3: Docker Compose Smoke Test** (depends on Jobs 1 and 2)
- Creates `.env` with CI-only credentials
- `docker compose build && docker compose up -d && sleep 30`
- Verifies health endpoint through Nginx
- Runs Python smoke script: login → upload CSV → run pipeline → poll 30s for completion
- On failure: dumps last 50 lines of `docker compose logs`
- Always: `docker compose down --volumes`

### Deployment Triggers

| Platform | Trigger | Mechanism |
|----------|---------|-----------|
| Render | Push to main | Auto-deploy (GitHub integration) |
| Vercel | Push to main | Auto-deploy (GitHub integration) |

No GitHub secrets are required for deployment — both platforms are connected directly to the repository.

---

## 13. Observability

### Prometheus Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `pipelineiq_pipeline_runs_total` | Counter | Pipeline runs by status (success/failed) |
| `pipelineiq_pipeline_duration_seconds` | Histogram | Pipeline execution duration |
| `pipelineiq_files_uploaded_total` | Counter | Files uploaded |
| `pipelineiq_active_users_total` | Gauge | Registered active users |
| `pipelineiq_celery_queue_depth` | Gauge | Tasks waiting in Celery queue |

Plus standard HTTP metrics from `prometheus-fastapi-instrumentator`: request count, duration histogram, in-progress requests.

### Grafana Dashboard (10 panels)

| Panel | Type | Query |
|-------|------|-------|
| Pipeline Runs / min | Time series | `rate(pipelineiq_pipeline_runs_total[1m])` |
| API Latency p95 | Time series | `histogram_quantile(0.95, http_request_duration_seconds_bucket[5m])` |
| API Request Rate | Time series | `rate(http_requests_total[1m])` |
| Pipeline Success Rate | Gauge | Success / total × 100 (thresholds: <80% red, <95% yellow) |
| Pipeline Duration p95 | Time series | `histogram_quantile(0.95, pipelineiq_pipeline_duration_seconds_bucket[5m])` |
| Files Uploaded | Stat | `pipelineiq_files_uploaded_total` |
| Celery Queue Depth | Stat | `pipelineiq_celery_queue_depth` (thresholds: <5 green, <20 yellow, 20+ red) |
| Active HTTP Connections | Time series | `http_requests_inprogress` |
| HTTP Error Rate | Time series | `rate(http_requests_total{status=~"5.."}[5m])` |
| Registered Users | Stat | `pipelineiq_active_users_total` |

### Sentry Integration

Configured in `main.py` via `sentry_sdk.init()` with FastAPI and Celery integrations. Sends unhandled exceptions with request context. Activated only when `SENTRY_DSN` is set.

### Audit Logging

Every significant action is recorded in the `audit_logs` table:
- Action type (file.upload, pipeline.run, user.login, webhook.create, etc.)
- Resource type and ID
- User ID
- Request IP and User-Agent
- Timestamp
- Additional details as JSONB

The table has a PostgreSQL trigger that blocks UPDATE and DELETE — records are append-only.

---

## 14. Performance Characteristics

### Database

- Connection pool: 20 connections, 10 overflow, pre-ping enabled, 3600s recycle
- UUID primary keys (indexed by default)
- JSONB columns for flexible data (graph data, step metadata)
- No explicit secondary indexes beyond PKs and foreign keys

### Caching

- Redis-backed caching via `utils/cache.py`
- Lineage graphs cached permanently in the database (no recomputation)
- Pipeline stats endpoint uses configurable cache TTL (default 30s)

### Rate Limiting

4 tiers enforced per-IP via SlowAPI:
- Pipeline execution: 10/min
- File upload: 30/min
- Validation: 60/min
- Read operations: 120/min

### Known Bottlenecks

| Bottleneck | Impact | Mitigation |
|-----------|--------|-----------|
| Render free tier cold start | 30-60s after 15min inactivity | Expected; document for users |
| Cross-region latency (Render Singapore ↔ Neon us-east-1) | Added DB query latency | Acceptable for free tier |
| Single-container API + Worker | Worker execution blocks under load | Separate containers in paid tier |
| Large file processing (1M rows) | Memory-bound Pandas operations | MAX_ROWS_PER_FILE limit (1M) |
| Lineage graph for complex pipelines | Graph size grows with columns × steps | Pre-computed layout avoids repeated computation |

---

## 15. Code Quality Observations

### Strengths

1. **Clean separation of concerns** — the pipeline engine (parser → validator → runner → executor → lineage) has clear interfaces between components. The runner doesn't know about Redis; it takes a callback. The parser doesn't know about the database; it takes a set of registered file IDs.

2. **Typed throughout** — YAML doesn't stay as a dict. It's parsed into dataclass hierarchies with enums for operators, join types, sort orders, and step types. This catches errors at parse time, not runtime.

3. **Error messages are helpful** — `ColumnNotFoundError` includes the misspelled name, the available columns, and a fuzzy-matched suggestion. `InvalidStepTypeError` does the same for step types. This is better than most production systems.

4. **The exception hierarchy is well-structured** — 14 exception classes in two groups (config errors and runtime errors) each carry specific context fields. The API serializes them into structured error responses with request IDs.

5. **The frontend workspace system is non-trivial** — a binary tree layout with 5 independent workspaces, drag-and-drop, and keyboard-driven navigation is more complex than a typical grid dashboard. The Zustand store managing this is well-organized.

6. **Comprehensive test fixtures** — `conftest.py` provides deterministic sample data, authenticated clients, upload helpers, and pipeline YAML builders. Tests are self-contained.

### Weaknesses

1. **Some read endpoints lack auth** — file listings, pipeline listings, lineage graphs, and version history are publicly accessible. In a multi-user system, this means any user can see any other user's data.

2. **No pagination on some list endpoints** — `GET /files/` and some other list endpoints return all records. This will become a problem as data grows.

3. **Frontend has no tests** — 0 unit tests, 0 integration tests, 0 E2E tests. The entire frontend relies on manual testing.

4. **Token in localStorage** — XSS vulnerability. An httpOnly secure cookie would prevent JavaScript from reading the token.

5. **Duplicate code between `auth.py` and `api/auth.py`** — auth utilities are split across two files with slightly overlapping concerns.

---

## 16. Identified Issues

### High Priority

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 1 | Public read endpoints expose all users' data | `api/files.py`, `api/pipelines.py`, `api/lineage.py`, `api/versions.py` | Data leakage in multi-user deployment |
| 2 | No YAML payload size limit | `api/pipelines.py` | Potential DoS via large YAML payloads |
| 3 | JWT stored in localStorage | `frontend/lib/auth-context.tsx` | XSS can steal tokens |
| 4 | No Content-Security-Policy header | `nginx/conf.d/pipelineiq.conf` | Missing defense-in-depth against XSS |

### Medium Priority

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 5 | No pagination on file/pipeline list endpoints | `api/files.py`, `api/pipelines.py` | Performance degradation over time |
| 6 | No HTTPS redirect in Nginx | `nginx/conf.d/pipelineiq.conf` | Mixed content possible in production |
| 7 | Celery worker in same container as API | `backend/Dockerfile` CMD | Worker tasks can starve API under load |
| 8 | Missing `GRAFANA_PASSWORD` in CI `.env` | `.github/workflows/ci.yml` | Warning in CI logs (non-breaking) |
| 9 | `vendor/` directory (86 .whl files) tracked in repo | `backend/vendor/` | Bloats repository; use pip install in CI |

### Low Priority

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 10 | `alembic.ini` duplicated at root and `backend/` | `alembic.ini`, `backend/alembic.ini` | Confusing; could drift |
| 11 | `@google/genai` in frontend deps but unused | `frontend/package.json` | Dead dependency |
| 12 | `firebase-tools` in frontend devDeps but unused | `frontend/package.json` | Dead dependency |
| 13 | Frontend package name is `ai-studio-applet`, not `pipelineiq` | `frontend/package.json` | Misleading |
| 14 | `pipelineiq.db` (SQLite file) in repo root | `pipelineiq.db` | Should be gitignored |

---

## 17. Recommendations

### Security (do first)

1. **Add auth to read endpoints** — scope file/pipeline/lineage queries to the authenticated user's data using `user_id` filters
2. **Add YAML payload size limit** — enforce a max body size (e.g., 100KB) on pipeline validate/plan/run endpoints
3. **Move JWT to httpOnly cookie** — prevents XSS-based token theft; requires CSRF protection in exchange
4. **Add Content-Security-Policy header** — restrict script sources to prevent XSS

### Reliability

5. **Add pagination** to `GET /files/` and `GET /pipelines/` with `page` + `limit` parameters
6. **Add database indexes** on `pipeline_runs.user_id`, `pipeline_runs.created_at`, `uploaded_files.user_id` for query performance
7. **Add health checks** for the Celery worker in Docker Compose
8. **Separate API and worker** containers when moving beyond free tier

### Code Quality

9. **Add frontend tests** — at minimum, snapshot tests for key components and integration tests for the auth flow
10. **Remove dead dependencies** — `@google/genai` and `firebase-tools` from `frontend/package.json`
11. **Fix package name** — rename `ai-studio-applet` to `pipelineiq` in `frontend/package.json`
12. **Add `.db` to `.gitignore`** — prevent SQLite files from being committed
13. **Remove duplicate `alembic.ini`** — keep only the root copy

### Infrastructure

14. **Add Strict-Transport-Security header** in Nginx for HTTPS enforcement
15. **Configure Render health check path** — ensure Render pings `/health` to prevent unnecessary cold starts
16. **Add Redis connection pooling** in the Celery config for production workloads

---

*End of report.*
