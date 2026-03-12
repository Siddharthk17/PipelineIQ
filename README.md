# PipelineIQ

A data pipeline orchestration engine. Define pipelines in YAML, upload CSV/JSON files, execute transformations with real-time monitoring, and trace every column back to its source through automatic lineage graphs.

**Live Demo** → [pipeline-iq0.vercel.app](https://pipeline-iq0.vercel.app)
Login: `demo@pipelineiq.app` / `Demo1234!`

---

## What It Does

You write a YAML file that describes a data pipeline:

```yaml
pipeline:
  name: quarterly_sales_report
  steps:
    - name: load_sales
      type: load
      file_id: "abc-123"

    - name: delivered_only
      type: filter
      input: load_sales
      column: status
      operator: equals
      value: delivered

    - name: by_region
      type: aggregate
      input: delivered_only
      group_by: [region]
      aggregations:
        - column: amount
          function: sum
        - column: order_id
          function: count

    - name: sorted
      type: sort
      input: by_region
      by: amount_sum
      order: desc

    - name: save_report
      type: save
      input: sorted
      filename: quarterly_report
```

PipelineIQ takes that YAML, validates it, queues it for async execution, runs each step with Pandas, streams progress to the browser in real time via SSE, builds a column-level lineage graph with NetworkX, detects schema drift when files change, and versions every pipeline config with git-style diffs.

The frontend is a keyboard-driven workspace with draggable widgets, 7 built-in themes (including light mode), mobile responsive layout, and a command palette.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Browser                                   │
│  Next.js 15 · React 19 · Zustand · ReactFlow · CodeMirror · SSE     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP / SSE
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          Nginx                                      │
│  Reverse proxy · Security headers · SSE passthrough · Rate limits   │
└──────┬─────────────────┬────────────────────┬───────────────────────┘
       │                 │                    │
       ▼                 ▼                    ▼
┌──────────────┐  ┌──────────────┐  ┌────────────────┐
│   FastAPI    │  │   Frontend   │  │    Grafana     │
│   Uvicorn    │  │  Next.js SSR │  │   Dashboards   │
│   Port 8000  │  │   Port 3000  │  │   Port 3000    │
└──────┬───────┘  └──────────────┘  └───────┬────────┘
       │                                    │
       ├──── Celery Task Queue ──────┐      │
       │                             │      │
       ▼                             ▼      ▼
┌──────────────┐  ┌──────────────┐  ┌────────────────┐
│  PostgreSQL  │  │    Redis     │  │  Prometheus    │
│   Primary DB │  │ Cache/Broker │  │   Metrics      │
│   Port 5432  │  │  Port 6379   │  │   Port 9090    │
└──────────────┘  └──────────────┘  └────────────────┘
```

**Request flow inside the backend:**

```
YAML Config → Parser → Validator → Runner → Step Executor → Lineage Recorder
                                      │                           │
                                      └── Progress Callback ──→ Redis Pub/Sub ──→ SSE Stream
```

**Key design decisions:**

- **Typed all the way down** — YAML is parsed into dataclass hierarchies, not dicts
- **Dispatch dict** — step execution uses `Dict[StepType, Callable]`, not if-elif chains
- **Dependency inversion** — the runner takes a progress callback; it doesn't know about Redis
- **Fuzzy suggestions** — `ColumnNotFoundError` uses `difflib.get_close_matches` to suggest typo fixes
- **Pre-computed layouts** — the React Flow lineage graph is computed once and stored in the database, not recalculated on every API call

---

## Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| API | FastAPI + Uvicorn | 0.109.0 / 0.27.0 |
| ORM | SQLAlchemy + Alembic | 2.0.25 / 1.13.1 |
| Database | PostgreSQL (prod) / SQLite (test) | 15 |
| Data Processing | Pandas + NumPy | 2.1.4 / 1.26.3 |
| Lineage Graphs | NetworkX | 3.2.1 |
| Task Queue | Celery + Redis | 5.3.6 / 5.0.1 |
| Auth | JWT (python-jose) + bcrypt | HS256 |
| Rate Limiting | SlowAPI | 0.1.9 |
| Metrics | Prometheus + Grafana | 2.48.0 / 10.2.0 |
| Error Tracking | Sentry | 1.39.1 |
| Frontend | Next.js + React + TypeScript | 15.4 / 19.2 / 5.9 |
| State | Zustand (persisted to localStorage) | 5.0.11 |
| Graph Visualization | ReactFlow | 12.10 |
| Code Editor | CodeMirror (YAML mode) | 4.25 |
| Animations | Motion | 12.23 |
| CSS | Tailwind CSS v4 | 4.1.11 |
| Drag & Drop | dnd-kit | 6.3 |
| Reverse Proxy | Nginx | 1.25 |

---

## Features

### Pipeline Engine — 9 Step Types

| Step | What it does | Pandas operation |
|------|-------------|-----------------|
| `load` | Read a CSV or JSON file into memory | `pd.read_csv()` / `pd.read_json()` |
| `filter` | Keep rows matching a condition | 12 operators: `equals`, `greater_than`, `contains`, `is_null`, etc. |
| `select` | Pick specific columns, drop the rest | `df[columns]` |
| `rename` | Rename columns via a mapping | `df.rename(columns={...})` |
| `join` | Merge two datasets on a key | `pd.merge(left, right, on=key, how=inner\|left\|right\|outer)` |
| `aggregate` | Group by columns and compute stats | 10 functions: `sum`, `mean`, `min`, `max`, `count`, `median`, `std`, `var`, `first`, `last` |
| `sort` | Order rows by a column | `df.sort_values()` |
| `validate` | Run data quality checks (non-blocking) | 12 checks: `not_null`, `between`, `matches_pattern`, `no_duplicates`, etc. |
| `save` | Mark output for export | Records metadata and lineage |

### Column-Level Data Lineage

Every pipeline run produces a directed graph (NetworkX DiGraph) that traces each output column back to its source file and source column, through every transformation.

- **Ancestry queries** — "Where did `amount_sum` come from?" → traces backward through aggregate ← filter ← load ← sales.csv
- **Impact analysis** — "If I change the `amount` column in sales.csv, what breaks?" → traces forward to every downstream step and output
- **Visual graph** — rendered as an interactive ReactFlow diagram with typed nodes (source files, steps, columns, output files) and animated join-key edges
- **Layout algorithm** — Sugiyama-inspired layered layout: topological sort, layer assignment by longest path, 300px horizontal / 80px vertical spacing

### Schema Drift Detection

When you re-upload a file, PipelineIQ compares the new schema against the previous snapshot:

| Drift Type | Severity | Example |
|-----------|----------|---------|
| Column removed | **Breaking** | `customer_id` was in v1, gone in v2 |
| Type changed | **Warning** | `amount` was `float64`, now `object` |
| Column added | **Info** | New column `discount` appeared |

Schema snapshots are stored per-file per-pipeline-run for point-in-time diffing.

### Pipeline Versioning

Every pipeline run saves a versioned copy of the YAML config. You can list all versions by name, diff any two versions (steps added/removed/modified + unified diff), or restore an old version.

### Validation Engine — 12 Checks

The `validate` step runs data quality rules without stopping the pipeline:

`not_null` · `not_empty` · `greater_than` · `less_than` · `between` · `in_values` · `matches_pattern` (regex) · `no_duplicates` · `min_rows` · `max_rows` · `positive` · `date_format`

Each rule reports: pass/fail, severity (error/warning), failing count, total count, and up to 3 failing examples.

### Dry-Run Planning

Before executing, you can generate an execution plan that estimates row counts per step (filter keeps ~70%, aggregate reduces to ~10%), duration per step, whether the pipeline will fail, and which files will be read/written.

### Real-Time Streaming

Pipeline progress flows through: Celery worker → Redis pub/sub → FastAPI SSE endpoint → browser EventSource. The UI updates step-by-step with rows in/out, duration, status, and error messages.

### Auth, Webhooks, and Audit

- **JWT tokens** — HS256, 24-hour expiry, bcrypt password hashing, password complexity enforcement (uppercase, number, special character required)
- **Two roles** — `admin` (full access) and `viewer` (read-only); first registered user is auto-admin
- **Per-pipeline RBAC** — owner, runner, viewer permissions per pipeline; admins override
- **Webhooks** — HMAC-SHA256 signed payloads, async delivery via dedicated Celery task, delivery tracking
- **Audit log** — every action recorded with user, IP, user agent, timestamp; immutable via database trigger
- **Pipeline scheduling** — cron-based recurring execution via Celery Beat
- **Notifications** — Slack integration with configurable event subscriptions
- **Pipeline templates** — 5 pre-built templates (ETL, cleaning, validation, aggregation, merge/join)
- **Pipeline cancellation** — cancel running pipelines with Celery task revocation
- **Export** — download pipeline output files directly from the API

### Observability

- **5 custom Prometheus metrics** — `pipeline_runs_total`, `pipeline_duration_seconds`, `files_uploaded_total`, `active_users_total`, `celery_queue_depth` (centralized in `metrics.py` to avoid circular imports)
- **10-panel Grafana dashboard** — pipeline rate, API latency p95, success rate gauge, queue depth, error rate, active connections
- **Sentry integration** — error tracking for FastAPI, Celery, and SQLAlchemy
- **Request headers** — every response gets `X-Request-ID`, `X-Process-Time`, `X-API-Version`, and `X-App-Version`

### Frontend

- **5 workspaces** (Alt+1 through Alt+5) with independent widget layouts
- **9 widgets** — Quick Stats, Pipeline Editor, Run Monitor, File Registry, File Upload, Lineage Graph, Run History, Version History, Manage Connections
- **Binary tree layout** — widgets split horizontally/vertically, drag-and-drop to swap positions
- **Mobile responsive** — stacked single-column layout on mobile devices
- **Command palette** (Ctrl+K) — fuzzy search for commands
- **Terminal launcher** (Alt+Enter) — quick-add widgets to current workspace
- **7 built-in themes** — Catppuccin Mocha, Tokyo Night, Gruvbox Dark, Nord, Rosé Pine, PipelineIQ Dark, PipelineIQ Light
- **Custom theme builder** — 25+ CSS variables, export as JSON
- **18 rebindable keyboard shortcuts**
- **YAML editor** — CodeMirror with syntax highlighting, debounced validation (800ms), inline plan preview
- **Step DAG visualization** — horizontal flow diagram showing step dependencies in the pipeline editor
- **Data preview** — preview sample data at each pipeline step from the editor
- **Live run monitor** — SSE-powered step progress with animated duration bars and exponential backoff reconnection
- **Error boundaries** — graceful per-widget error handling prevents full-app crashes
- **Presence indicators** — shows current user online status (WebSocket-ready)

---

## Quick Start

### Run Everything with Docker

```bash
git clone https://github.com/Siddharthk17/PipelineIQ.git
cd PipelineIQ
cp .env.example .env    # set POSTGRES_PASSWORD and SECRET_KEY at minimum
docker compose up --build -d
```

Wait ~30 seconds, then open http://localhost. A demo account is seeded automatically.

| Service | URL |
|---------|-----|
| App (via Nginx) | http://localhost |
| Swagger API Docs | http://localhost/docs |
| ReDoc API Docs | http://localhost/redoc |
| Flower (Celery) | http://localhost:5555 |
| Grafana | http://localhost/grafana |
| Prometheus | http://localhost:9090 |

### Backend Only

```bash
cd backend
pip install -r requirements.txt
export DATABASE_URL="sqlite:///./pipelineiq.db"
export REDIS_URL="redis://localhost:6379/0"
export SECRET_KEY="dev-secret-change-in-production"

alembic upgrade head
python -m backend.scripts.seed_demo
uvicorn backend.main:app --reload --port 8000

# In another terminal:
celery -A backend.celery_app:celery_app worker --loglevel=info
```

### Frontend Only

```bash
cd frontend
npm ci
echo 'NEXT_PUBLIC_API_URL=http://localhost:8000' > .env.local
npm run dev    # → http://localhost:3000
```

---

## API Reference

Base path: `/api/v1` — proxied through Nginx (Docker) or Next.js rewrites (Vercel).

### Auth

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/auth/register` | Create account (first user = admin) | No |
| POST | `/auth/login` | Get JWT token | No |
| GET | `/auth/me` | Current user profile | Yes |
| POST | `/auth/logout` | Logout | Yes |
| GET | `/auth/users` | List all users | Admin |
| PATCH | `/auth/users/{id}/role` | Change user role | Admin |

### Files

| Method | Path | Description | Auth | Rate Limit |
|--------|------|-------------|------|------------|
| POST | `/api/v1/files/upload` | Upload CSV/JSON (50MB max) | Yes | 30/min |
| GET | `/api/v1/files/` | List files | No | — |
| GET | `/api/v1/files/{id}` | File metadata | No | — |
| DELETE | `/api/v1/files/{id}` | Delete file | Yes | — |
| GET | `/api/v1/files/{id}/preview` | Preview first N rows | No | — |
| GET | `/api/v1/files/{id}/schema/history` | Schema snapshots | No | — |
| GET | `/api/v1/files/{id}/schema/diff` | Detect schema drift | No | — |

### Pipelines

| Method | Path | Description | Auth | Rate Limit |
|--------|------|-------------|------|------------|
| POST | `/api/v1/pipelines/validate` | Validate YAML config | Yes | 60/min |
| POST | `/api/v1/pipelines/plan` | Dry-run execution plan | Yes | 60/min |
| POST | `/api/v1/pipelines/preview` | Preview sample data at step | Yes | 60/min |
| POST | `/api/v1/pipelines/run` | Execute pipeline (async) | Yes | 10/min |
| GET | `/api/v1/pipelines/` | List all runs (paginated) | No | 120/min |
| GET | `/api/v1/pipelines/stats` | Aggregate stats | No | 120/min |
| GET | `/api/v1/pipelines/{id}` | Run details + step results | No | 120/min |
| GET | `/api/v1/pipelines/{id}/stream` | SSE progress stream | No | — |
| POST | `/api/v1/pipelines/{id}/cancel` | Cancel running pipeline | Yes | — |
| GET | `/api/v1/pipelines/{id}/export` | Download output file | Yes | — |

### Lineage

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/lineage/{run_id}` | Full ReactFlow graph |
| GET | `/api/v1/lineage/{run_id}/column?step=X&column=Y` | Column ancestry trace |
| GET | `/api/v1/lineage/{run_id}/impact?step=X&column=Y` | Forward impact analysis |

### Versions

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/versions/{name}` | All versions of a pipeline |
| GET | `/api/v1/versions/{name}/{version}` | Specific version config |
| GET | `/api/v1/versions/{name}/diff/{a}/{b}` | Diff two versions |
| POST | `/api/v1/versions/{name}/restore/{v}` | Restore old version |

### Webhooks

| Method | Path | Auth |
|--------|------|------|
| POST | `/api/v1/webhooks/` | Yes |
| GET | `/api/v1/webhooks/` | Yes |
| DELETE | `/api/v1/webhooks/{id}` | Yes |
| GET | `/api/v1/webhooks/{id}/deliveries` | Yes |
| POST | `/api/v1/webhooks/{id}/test` | Yes |

### Audit & Health

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/audit/logs` | All audit logs (paginated) | Admin |
| GET | `/api/v1/audit/logs/mine` | Current user's logs | Yes |
| GET | `/health` | DB + Redis status | No |
| GET | `/metrics` | Prometheus metrics | Internal only |

### Schedules

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/schedules/` | Create cron-based pipeline schedule | Yes |
| GET | `/api/v1/schedules/` | List user's schedules | Yes |
| PATCH | `/api/v1/schedules/{id}/toggle` | Enable/disable schedule | Yes |
| DELETE | `/api/v1/schedules/{id}` | Delete schedule | Yes |

### Templates

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/templates/` | List pipeline templates | No |
| GET | `/api/v1/templates/{id}` | Get template with YAML | No |

### Notifications

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/notifications/` | Create Slack/email notification config | Yes |
| GET | `/api/v1/notifications/` | List notification configs | Yes |
| DELETE | `/api/v1/notifications/{id}` | Delete notification config | Yes |
| POST | `/api/v1/notifications/{id}/test` | Send test notification | Yes |

### Dashboard

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/dashboard/stats` | Personal analytics & activity | Yes |

### Per-Pipeline Permissions

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/pipelines/{name}/permissions` | Grant permission | Owner/Admin |
| GET | `/api/v1/pipelines/{name}/permissions` | List permissions | Yes |
| DELETE | `/api/v1/pipelines/{name}/permissions/{user_id}` | Revoke permission | Owner/Admin |

---

## Pipeline YAML Reference

### load
```yaml
- name: my_data
  type: load
  file_id: "uuid-of-uploaded-file"
```

### filter
Operators: `equals` · `not_equals` · `greater_than` · `less_than` · `gte` · `lte` · `contains` · `not_contains` · `starts_with` · `ends_with` · `is_null` · `is_not_null`
```yaml
- name: expensive
  type: filter
  input: my_data
  column: amount
  operator: greater_than
  value: 100
```

### select
```yaml
- name: slim
  type: select
  input: my_data
  columns: [order_id, amount, status]
```

### rename
```yaml
- name: renamed
  type: rename
  input: my_data
  mapping:
    old_name: new_name
```

### join
Types: `inner` · `left` · `right` · `outer`
```yaml
- name: enriched
  type: join
  left: orders
  right: customers
  on: customer_id
  how: left
```

### aggregate
Functions: `sum` · `mean` · `min` · `max` · `count` · `median` · `std` · `var` · `first` · `last`
```yaml
- name: summary
  type: aggregate
  input: my_data
  group_by: [region]
  aggregations:
    - column: amount
      function: sum
    - column: order_id
      function: count
```

### sort
```yaml
- name: ranked
  type: sort
  input: summary
  by: amount_sum
  order: desc
```

### validate
Checks: `not_null` · `not_empty` · `greater_than` · `less_than` · `between` · `in_values` · `matches_pattern` · `no_duplicates` · `min_rows` · `max_rows` · `positive` · `date_format`
```yaml
- name: checks
  type: validate
  input: my_data
  rules:
    - check: not_null
      column: customer_id
      severity: error
    - check: between
      column: amount
      value: [0, 10000]
      severity: warning
```

### save
```yaml
- name: output
  type: save
  input: ranked
  filename: final_report
```

### Full Example

```yaml
pipeline:
  name: sales_analytics
  steps:
    - name: load_sales
      type: load
      file_id: "sales-uuid"
    - name: load_customers
      type: load
      file_id: "customers-uuid"
    - name: delivered
      type: filter
      input: load_sales
      column: status
      operator: equals
      value: delivered
    - name: with_customers
      type: join
      left: delivered
      right: load_customers
      on: customer_id
      how: left
    - name: by_region
      type: aggregate
      input: with_customers
      group_by: [region]
      aggregations:
        - column: amount
          function: sum
        - column: order_id
          function: count
    - name: top_first
      type: sort
      input: by_region
      by: amount_sum
      order: desc
    - name: export
      type: save
      input: top_first
      filename: regional_summary
```

---

## Keyboard Shortcuts

All shortcuts are rebindable through the Keybindings modal (`Alt+K`).

| Shortcut | Action |
|----------|--------|
| Alt + Enter | Open widget launcher |
| Ctrl + K | Command palette |
| Ctrl + Enter | Run pipeline (from editor) |
| Alt + 1–5 | Switch workspace |
| Alt + Shift + 1–5 | Move active widget to workspace |
| Alt + Q | Close active widget |
| Alt + K | Keybindings editor |
| Alt + T | Theme selector |
| Ctrl + Shift + 1–6 | Toggle individual widgets |

---

## Testing

### Backend (206 tests)

```bash
cd backend
python -m pytest tests/ -v --tb=short
```

**206 tests across 14 files:**

| File | Tests | Coverage |
|------|-------|----------|
| test_api.py | 34 | REST endpoints, uploads, pipeline runs, error cases |
| test_steps.py | 25 | All 9 step types, edge cases, error conditions |
| test_validators.py | 22 | All 12 validation checks and severity levels |
| test_parser.py | 18 | YAML parsing, validation rules, fuzzy suggestions |
| test_lineage.py | 18 | Graph construction, ancestry, impact analysis |
| test_auth.py | 17 | Registration, login, JWT, roles, admin endpoints |
| test_planner.py | 15 | Dry-run estimates, failure detection |
| test_versioning.py | 12 | Version save/restore, diff generation |
| test_schema_drift.py | 10 | Drift detection, severity classification |
| test_webhooks.py | 9 | CRUD, HMAC signing, delivery, retries |
| test_caching.py | 8 | Redis cache operations |
| test_security.py | 7 | Auth bypass attempts, injection, headers |
| test_rate_limiting.py | 6 | Per-tier rate enforcement |
| test_performance.py | 5 | Response time, concurrent load |

Backend tests use SQLite in-memory for speed. CI runs against PostgreSQL 15 + Redis 7.

### Frontend (93 tests)

```bash
cd frontend
npm run test
```

**93 tests across 8 files:**

| File | Tests | Coverage |
|------|-------|----------|
| api.test.ts | 26 | Token management, fetchApi, all API functions, error handling |
| stores.test.ts | 26 | Pipeline, widget, theme, keybinding stores |
| pages.test.tsx | 12 | Login/register forms, validation, error states |
| widgets.test.tsx | 11 | QuickStats, FileUpload, RunHistory, FileRegistry widgets |
| utils.test.ts | 7 | cn() utility, constants values |
| middleware.test.ts | 4 | Auth redirect logic |
| auth-context.test.tsx | 4 | AuthProvider login, logout, demo login |
| hooks.test.ts | 3 | Widget layout toggle, workspace switching |

Frontend tests use Vitest + React Testing Library + jsdom.

---

## Database Migrations

```bash
alembic upgrade head           # Run pending migrations
alembic revision --autogenerate -m "description"  # Create new migration
alembic downgrade -1           # Rollback one step
```

**Migration history:**

| # | ID | What it does |
|---|----|-------------|
| 1 | `97385cb62e0a` | Initial schema — pipeline_runs, uploaded_files, lineage_graphs, step_results |
| 2 | `14a9b359a361` | Schema snapshots + pipeline versions |
| 3 | `c3f5e7a8b901` | Upgrade String(36) IDs to PostgreSQL native UUID |
| 4 | `d4e6f8a1b2c3` | Users table + user_id FK on pipeline_runs |
| 5 | `e5f6a7b8c9d0` | Webhooks + webhook_deliveries |
| 6 | `f6a7b8c9d0e1` | Audit logs with immutable trigger (blocks UPDATE/DELETE) |
| 7 | `a1b2c3d4e5f6` | Performance indexes on pipeline_runs, step_results, webhook_deliveries, audit_logs |
| 8 | `b2c3d4e5f6a7` | CANCELLED pipeline status, pipeline_schedules, notification_configs, pipeline_permissions, file versioning |

---

## Deployment

### Production (current)

| Service | Platform | URL |
|---------|----------|-----|
| Backend API | Render.com (free) | https://pipelineiq-api.onrender.com |
| Frontend | Vercel (free) | https://pipeline-iq0.vercel.app |
| Database | Neon.tech PostgreSQL (free) | us-east-1 connection pooler |
| Cache + Queue | Upstash Redis (free, TLS) | rediss:// endpoint |

Both Render and Vercel auto-deploy on push to `main`. No GitHub secrets needed.

> Render free tier sleeps after 15 minutes of inactivity. First request after cold start takes ~30–60 seconds.

### Self-Hosted (Docker Compose)

```bash
cp .env.example .env
docker compose up --build -d
```

Starts all 9 services behind Nginx on port 80.

---

## CI/CD

GitHub Actions runs on every push to `main`/`develop` and every PR to `main`:

1. **Backend Tests** — Python 3.11 against PostgreSQL 15 + Redis 7. Runs Alembic migrations, then 206 pytest tests.
2. **Frontend Check** — Node 20. TypeScript check (`tsc --noEmit`) + production build.
3. **Docker Smoke Test** — builds all services, starts them, waits 30s, then runs: health check → login → upload CSV → run pipeline → poll for completion.

---

## Project Structure

```
PipelineIQ/
├── backend/
│   ├── api/                    # FastAPI routers (13 modules)
│   ├── pipeline/               # Core engine (parser, steps, runner, lineage, validators, drift, versioning, planner)
│   ├── services/               # Webhook delivery, audit logging, notification service
│   ├── tasks/                  # Celery tasks: pipeline execution, webhook delivery, schedule checker
│   ├── utils/                  # Cache, rate limiter, UUID utils, string/time helpers
│   ├── tests/                  # 206 tests, 14 files
│   ├── alembic/                # 8 database migrations
│   ├── scripts/                # seed_demo.py
│   ├── sample_data/            # 4 CSVs + 3 pipeline YAMLs
│   ├── main.py                 # App factory, middleware, health
│   ├── config.py               # 55+ env vars via Pydantic (production secret key validation)
│   ├── models.py               # 14 SQLAlchemy models
│   ├── schemas.py              # 20+ Pydantic schemas
│   ├── metrics.py              # Centralized Prometheus metric definitions
│   ├── auth.py                 # JWT + bcrypt
│   ├── celery_app.py           # Celery config + Redis SSL + Beat schedule
│   ├── database.py             # Engine + session factory
│   └── Dockerfile
├── frontend/
│   ├── app/                    # Next.js pages (dashboard, login, register)
│   ├── components/
│   │   ├── layout/             # TopBar, WidgetGrid, CommandPalette, TerminalLauncher, PresenceIndicator, KeybindingsModal
│   │   ├── widgets/            # 8 widgets + WidgetShell wrapper + StepDAG
│   │   ├── lineage/            # LineageGraph, sidebar, 4 custom ReactFlow nodes
│   │   └── theme/              # ThemeSelector, ThemeBuilder
│   ├── hooks/                  # useKeybindings, usePipelineRun (SSE + reconnect), useLineage, useTheme, useIsMobile
│   ├── store/                  # Zustand (theme, widgets, pipeline, keybindings)
│   ├── lib/                    # API client, auth context, types, constants
│   └── Dockerfile
├── nginx/                      # Reverse proxy (SSE-safe, security headers, /metrics restricted)
├── grafana/                    # 10-panel dashboard
├── prometheus/                 # Scrape config
├── postman/                    # 23-request API collection
├── .github/workflows/ci.yml   # 3-job CI pipeline
├── docker-compose.yml          # 9 services
└── render.yaml                 # Render.com blueprint
```

~7,200 lines backend · ~3,100 lines tests · ~5,000 lines frontend · ~660 lines infra config.

---

## Environment Variables

Full list in `.env.example`. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | PostgreSQL connection string |
| `REDIS_URL` | — | Redis connection string |
| `SECRET_KEY` | — | JWT signing key (32+ chars) |
| `UPLOAD_DIR` | `/app/uploads` | File storage path |
| `MAX_UPLOAD_SIZE` | `52428800` | 50 MB |
| `MAX_PIPELINE_STEPS` | `50` | Steps per pipeline |
| `MAX_ROWS_PER_FILE` | `1000000` | 1M rows |
| `STEP_TIMEOUT_SECONDS` | `300` | 5 min per step |
| `RATE_LIMIT_PIPELINE_RUN` | `10/minute` | Execution rate limit |
| `RATE_LIMIT_FILE_UPLOAD` | `30/minute` | Upload rate limit |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | 24h token lifetime |
| `SENTRY_DSN` | — | Optional error tracking |
| `SMTP_HOST` | — | SMTP server for email notifications |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | — | SMTP username |
| `SMTP_PASSWORD` | — | SMTP password |
| `SMTP_FROM` | — | From address for notifications |
| `SMTP_USE_TLS` | `true` | Enable STARTTLS |
| `SMTP_USE_SSL` | `false` | Use SSL instead of STARTTLS |

---

## Postman Collection

Import `postman/PipelineIQ.postman_collection.json` — 23 requests in 6 folders (Auth, Files, Pipelines, Lineage, Versioning, Webhooks) with auto-extracted variables for `token`, `file_id`, and `run_id`.

---

## License

[Apache 2.0](LICENSE)
