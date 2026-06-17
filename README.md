# PipelineIQ

> **Data pipeline orchestration engine** — Define pipelines in YAML, execute at scale, trace every column back to its source, and let AI heal failures autonomously.

[![CI](https://github.com/Siddharthk17/PipelineIQ/actions/workflows/ci.yml/badge.svg)](https://github.com/Siddharthk17/PipelineIQ/actions)
[![Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-15-000000?logo=next.js)](https://nextjs.org)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python)](https://python.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6?logo=typescript)](https://typescriptlang.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?logo=postgresql)](https://postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7.4-FF4438?logo=redis)](https://redis.io)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)](https://docker.com)

**Live Demo** → [pipeline-iq0.vercel.app](https://pipeline-iq0.vercel.app)  
Login: `demo@pipelineiq.app` / `Demo1234!`

---

## Table of Contents

- [Why PipelineIQ?](#why-pipelineiq)
- [Quick Start](#quick-start)
- [The Pipeline YAML](#the-pipeline-yaml)
- [How It Works](#how-it-works)
- [Step Types — 19 Operations](#step-types--19-operations)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [API Reference](#api-reference)
- [Frontend Dashboard](#frontend-dashboard)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Observability](#observability)
- [Testing](#testing)
- [Database](#database)
- [Deployment](#deployment)
- [CI/CD](#cicd)
- [Project Structure](#project-structure)
- [Environment Variables](#environment-variables)
- [Contributing](#contributing)
- [License](#license)

---

## Why PipelineIQ?

Data teams waste time stitching together ad-hoc scripts, manual exports, and fragile spreadsheet workflows. PipelineIQ replaces that chaos with a single platform to:

- **Define** data pipelines in a simple YAML format — no Python or SQL required
- **Execute** them asynchronously with real-time streaming progress to the browser
- **Trace** every output column back to its origin through automatic column-level lineage
- **Heal** autonomously — when pipelines break, Gemini AI diagnoses and patches them
- **Detect** schema drift when source files change across pipeline runs
- **Govern** with RBAC, immutable audit logs, webhooks, data contracts, and column security policies
- **Stream** data through Redpanda/Kafka topics with circuit-breaker resilience
- **Extend** with WebAssembly UDFs for compute-intensive row-level transformations
- **Scale** from a single Docker Compose laptop to a distributed Kubernetes cluster

---

## Quick Start

### Run Everything with Docker Compose (21 Services)

```bash
git clone https://github.com/Siddharthk17/PipelineIQ.git
cd PipelineIQ
cp .env.example .env
# Edit .env — set POSTGRES_PASSWORD and SECRET_KEY at minimum
docker compose up --build -d
```

Wait ~45 seconds for all services to initialize. A demo account is seeded automatically.

| Service | URL | Purpose |
|---------|-----|---------|
| Application (via Nginx) | http://localhost | Full app behind reverse proxy |
| Swagger API Docs | http://localhost/docs | Interactive API playground |
| ReDoc API Docs | http://localhost/redoc | Alternative API browser |
| Celery Flower | http://localhost:5555 | Worker monitoring dashboard |
| Grafana | http://localhost/grafana | 10-panel observability dashboard |
| Prometheus | http://localhost:9090 | Metrics scraping |
| Jaeger Tracing | http://localhost:16686 | Distributed tracing UI |
| Redpanda Console | http://localhost:8090 | Streaming topic management |
| MinIO Console | http://localhost:9001 | S3-compatible storage admin |

### Backend Only (Development)

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

### Frontend Only (Development)

```bash
cd frontend
npm ci
echo 'NEXT_PUBLIC_API_URL=http://localhost:8000' > .env.local
npm run dev    # → http://localhost:3000
```

### Kubernetes

```bash
kubectl create namespace pipelineiq
kubectl apply -f k8s/secrets.yaml   # fill in secrets first
kubectl apply -f k8s/
```

Includes: backend + 6 Celery worker types, PostgreSQL, Redis (4 instances), MinIO, Nginx ingress with TLS, horizontal pod autoscaling (CPU-based).

---

## The Pipeline YAML

PipelineIQ pipelines are defined in a straightforward YAML format. Each pipeline has a name, optional description, and a list of ordered steps. Steps reference previous steps via their `name` fields to form a directed acyclic graph (DAG).

```yaml
pipeline:
  name: quarterly_sales_report
  description: Aggregate quarterly sales data by region
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

---

## How It Works

### Execution Flow

```
YAML Config → Parser → Validator → Planner → Runner → Step Executor → Lineage Recorder
                                     │                                         │
                                     └── Dry-run estimate ─┐                   │
                                                           ▼                   │
                                                ┌──────────────────┐           │
                                                │  Execution Engine│           │
                                                │  Pandas / DuckDB │           │
                                                └──────────────────┘           │
                                                                               │
                      ┌── Schema Drift Detection ←── File Upload               │
                      │         │                                              │
                      │         └── Autonomous Healing (Gemini AI)             │
                      │                                                        │
                      └─────────────── Progress Callback ──→ Redis Pub/Sub ──→ SSE Stream
```

1. **Parse** — YAML is parsed into strongly-typed Python dataclasses (not dicts) with full validation
2. **Validate** — 13 semantic checks: step references, file IDs, operator validity, SQL safety, etc.
3. **Plan** — Dry-run execution estimates row counts, duration, and potential failure points
4. **Execute** — Pipeline runs asynchronously via Celery, each step dispatched to the optimal engine
5. **Stream** — Progress events flow from Celery worker → Redis Pub/Sub → FastAPI SSE → Browser EventSource
6. **Record** — Column-level lineage is built as a NetworkX DAG, serialized with pre-computed React Flow layout
7. **Detect** — Schema drift is checked against previous snapshots for breaking changes
8. **Heal** — On failure, Gemini AI diagnoses and suggests patches, verified in a sandbox before applying

### Key Design Decisions

- **Typed all the way down** — YAML is parsed into dataclass hierarchies, never raw dicts
- **Dispatch dict** — Step execution uses `Dict[StepType, Callable]`, not if-elif chains
- **Dependency inversion** — The runner takes a progress callback; it doesn't know about Redis or SSE
- **Fuzzy suggestions** — `ColumnNotFoundError` uses Jaro-Winkler similarity (`difflib`) to suggest typo fixes
- **Pre-computed layouts** — The React Flow lineage graph is computed once and stored in the database
- **Write/read replica split** — Separate database URLs for writes and reads with PgBouncer pooling
- **Dedicated SSE service** — A separate FastAPI process on port 8001 handles streaming to avoid blocking the main API
- **Multiple Redis instances** — 4 separate instances for broker, pub/sub, cache, and Yjs collaboration prevent interference
- **Sandboxed healing** — AI-generated pipeline patches are verified in isolation before applying

---

## Step Types — 19 Operations

### Core Steps (I/O)

| Step | Description | Key Fields |
|------|-------------|------------|
| `load` | Read a CSV, JSON, Parquet, or Excel file | `file_id` |
| `save` | Write output to a downloadable file | `input`, `filename` |

### Transform Steps

| Step | Description | Key Fields |
|------|-------------|------------|
| `filter` | Keep rows matching a condition (12 operators) | `input`, `column`, `operator`, `value` |
| `select` | Project only specified columns | `input`, `columns` |
| `rename` | Rename columns via a mapping dict | `input`, `mapping` |
| `join` | Merge two datasets (inner/left/right/outer) | `left`, `right`, `on`, `how` |
| `aggregate` | Group-by with 10 aggregation functions | `input`, `group_by`, `aggregations` |
| `sort` | Order rows ascending or descending | `input`, `by`, `order` |
| `transform` | Custom expression-based row transform | `input`, `expression` |

### Reshape Steps

| Step | Description | Key Fields |
|------|-------------|------------|
| `pivot` | Long-to-wide reshape | `input`, `index`, `columns`, `values` |
| `unpivot` | Wide-to-long reshape (melt) | `input`, `id_vars`, `value_vars` |

### Quality Steps

| Step | Description | Key Fields |
|------|-------------|------------|
| `validate` | Run 12 data quality checks (non-blocking) | `input`, `rules` |
| `deduplicate` | Remove duplicate rows | `input`, `subset`, `keep` |
| `fill_nulls` | Fill missing values with a strategy | `input`, `columns`, `strategy` |
| `sample` | Random row sampling (n or fraction) | `input`, `n`, `fraction`, `stratify_by` |

### Advanced Steps

| Step | Description | Key Fields |
|------|-------------|------------|
| `sql` | Execute sandboxed DuckDB SQL with CTEs | `input`, `query` |
| `wasm_compute` | WebAssembly UDF execution per row | `input`, `wasm_file_id`, `function`, `input_columns`, `output_column` |

### Streaming Steps (Redpanda/Kafka)

| Step | Description | Key Fields |
|------|-------------|------------|
| `stream_consume` | Read micro-batches from a topic | `topic`, `consumer_group`, `batch_size` |
| `stream_publish` | Write pipeline output to a topic | `input`, `topic`, `serialize` |

### Filter Operators (12)

`equals` · `not_equals` · `greater_than` · `less_than` · `gte` · `lte` · `contains` · `not_contains` · `starts_with` · `ends_with` · `is_null` · `is_not_null`

### Aggregation Functions (10)

`sum` · `mean` · `min` · `max` · `count` · `median` · `std` · `var` · `first` · `last`

### Validation Checks (12)

`not_null` · `not_empty` · `greater_than` · `less_than` · `between` · `in_values` · `matches_pattern` (regex) · `no_duplicates` · `min_rows` · `max_rows` · `positive` · `date_format`

### Full Pipeline Example

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

    - name: validate_output
      type: validate
      input: top_first
      rules:
        - check: not_null
          column: region
          severity: error
        - check: positive
          column: amount_sum
          severity: warning

    - name: export
      type: save
      input: top_first
      filename: regional_summary
```

---

## Features

### Hybrid Execution Engine — Pandas + DuckDB

PipelineIQ automatically routes each step to the optimal execution engine based on data volume:

- **Pandas** (< 50K rows) — Low overhead, fast for small datasets
- **DuckDB** (>= 50K rows) — Vectorized execution, memory-efficient for large datasets with automatic spill-to-disk

DuckDB also powers the SQL step type (`sql`), supporting window functions, CTEs, and custom queries via the `{{input}}` placeholder.

### Tiered Arrow Data Bus

Pipeline steps communicate through a three-tier Arrow IPC data bus for zero-copy data transfer:

| Tier | Threshold | Storage | TTL |
|------|-----------|---------|-----|
| One (fast) | < 10 MB | Redis (Arrow IPC bytes) | 1 hour |
| Two (shared memory) | 10–500 MB | `/dev/shm` (Arrow IPC file) | Run lifetime |
| Three (spill) | >= 500 MB | MinIO/S3 (Parquet + zstd) | 48 hours |

Auto-eviction from Redis when memory exceeds 90% — data is demoted to `/dev/shm` transparently.

### Autonomous Healing with Gemini AI

When a pipeline execution fails, PipelineIQ attempts autonomous recovery:

1. **Detect** — The failure reason and schema drift are analyzed
2. **Generate** — Gemini (`gemini-2.5-flash`) proposes a JSON patch to the YAML configuration
3. **Sandbox** — The patched pipeline runs in isolation to verify correctness
4. **Apply** — If verified, the patch is applied and the pipeline retries
5. **Learn** — Full healing audit trail stored in `HealingAttempt` model

Healing attempts are stored with complete traceability — schema diffs, AI-generated patches, sandbox validation results, and final outcomes. The system supports up to 3 healing retries per pipeline run with fallback models (`gemini-2.0-flash`, `gemini-1.5-flash`).

### AI-Powered Pipeline Generation

Generate complete, valid YAML pipelines from natural language:

> *"Load the sales CSV, filter for delivered orders in Q1 2026, join with customer data, aggregate revenue by region, and save the result"*

The AI uses actual column names from your uploaded files, generates valid YAML with correct step ordering, and produces a pipeline ready to run immediately.

### Column-Level Data Lineage

Every pipeline run produces a **directed acyclic graph** (NetworkX DiGraph) tracing each output column back to its source:

- **Node naming convention**: `file::{file_id}`, `col::{step_name}::{column}`, `step::{step_name}`, `output::{step_name}::{filename}`
- **Ancestry queries** — "Where did `amount_sum` come from?" → traces backward through aggregate → filter → load → `sales.csv`
- **Impact analysis** — "If I rename `customer_id`, what breaks?" → traces forward to every downstream step and output
- **Blast radius analysis** — Integrated with the data catalog for organization-wide impact assessment
- **Visual graph** — Interactive React Flow diagram with typed nodes (source files, columns, steps, output files) and animated join-key edges
- **Layout algorithm** — Sugiyama-inspired layered layout: topological sort → layer assignment by longest path → 300px horizontal / 80px vertical spacing

### Data Catalog

A global catalog indexes every file, column, pipeline, and streaming topic:

- **Asset discovery** — Search and browse all data assets across the organization
- **Relationship mapping** — Directed edges capture `reads_from`, `writes_to`, `transforms`, `joins` relationships
- **Blast radius analysis** — "What breaks if I rename this column?" shows every downstream consumer
- **Lineage export** — Export as OpenLineage-compatible JSON for integration with external tools

### Schema Drift Detection

When a file is re-uploaded, the new schema is compared against the previous snapshot:

| Drift Type | Severity | Example |
|-----------|----------|---------|
| Column removed | **Breaking** | `customer_id` was in v1, gone in v2 |
| Type changed | **Warning** | `amount` was `float64`, now `object` |
| Column added | **Info** | New column `discount` appeared |

Schema snapshots are stored per-file, per-pipeline-run for point-in-time diffing. Diffs are computed on every pipeline execution and reported in the run results.

### Streaming Pipelines (Redpanda / Kafka)

Real-time streaming pipelines powered by Redpanda (Kafka-compatible):

- **Streaming load** — Consume from Redpanda topics into pipeline steps via `stream_consume`
- **Streaming save** — Publish pipeline output to Redpanda topics via `stream_publish`
- **Consumer groups** — Manage offsets, consumer lag, and throughput
- **Circuit breaker** — Resilient broker connection handling with automatic Redis fallback queue
- **Dedicated workers** — Streaming and bulk worker queues for workload isolation
- **Monitoring** — Per-batch stats, consumer lag, throughput tracking

### WebAssembly UDF Execution

Extend the pipeline engine with custom WebAssembly functions:

- **Write in any language** — Rust, C, Go, or any language targeting WASM
- **Sandboxed execution** — Wasmtime runtime with memory safety and fuel-bounded CPU budgets (10M fuel per step)
- **Security** — No WASI imports, no filesystem, no network, no environment variables
- **Module cache** — SHA256 hash-based cache (max 100 modules) for fast execution
- **CPU budget** — 10M fuel per step, 1K per row, 30-second timeout
- **Validation** — On upload: checks exports, fuel budget, and disallowed imports

### Data Contracts

Define formal guarantees between pipeline stages:

- **Schema contracts** — Expected column names, types, and nullability
- **Row contracts** — Min/max row counts, uniqueness constraints
- **Quality contracts** — Acceptable ranges for null rates, data freshness
- **Two severity levels** — `warn` (run stays COMPLETED) vs `block` (run → CONTRACT_VIOLATION status)
- **Enforcement** — Contracts are checked post-execution with integration into the healing system

### Column-Level Security Policies

Fine-grained column-level access control:

- **Two policy types** — `redacted` (column dropped entirely) and `masked` (partial value obfuscation)
- **Evaluated at** — File preview and pipeline load step
- **Role-based exemption** — `allowed_roles` see full values
- **Policies cached** — Redis with 60-second TTL
- **PII detection** — Automatic scanning for email, phone, SSN, credit card, IP, person name, and address patterns

### Pipeline Versioning

Every pipeline run saves a versioned copy of the YAML config (max 50 versions):

- **List versions** — All versions of a pipeline by name
- **Diff view** — Any two versions with step-level add/remove/modify markers plus unified YAML diff
- **Restore** — Roll back to any previous version with one click

### Authentication, RBAC & Audit

- **JWT tokens** — HS256, 24-hour expiry, bcrypt (rounds=12) password hashing with ProcessPoolExecutor to avoid blocking the async event loop
- **Two roles** — `admin` (full access) and `viewer` (read-only); first registered user is auto-admin
- **Per-pipeline RBAC** — Owner, runner, viewer permissions per pipeline; admins override
- **Webhooks** — HMAC-SHA256 signed payloads with `X-PipelineIQ-Signature` header; async delivery via Celery with up to 3 retries (0s, 30s, 120s backoff); events: `pipeline_completed`, `pipeline_failed`
- **Audit log** — Every action recorded: user, IP, user agent, timestamp; immutable via database trigger (blocks UPDATE/DELETE)
- **Scheduling** — Cron-based recurring execution via Celery Beat with `croniter`; dynamic beat schedule rebuilt from database on each tick (no restart needed)
- **Notifications** — Slack and email integration with configurable event subscriptions
- **Pipeline cancellation** — Cancel running pipelines with Celery task revocation
- **Export** — Download pipeline output files directly from the API

### Dry-Run Planning

Before executing, generate an **execution plan** that estimates:
- Row counts per step (filter keeps ~70%, aggregate reduces to ~10%)
- Duration per step
- Whether the pipeline will fail (and where)
- Which files will be read/written

### Real-Time Streaming

Pipeline progress flows through a dedicated event pipeline:

```
Celery worker → Redis Pub/Sub → FastAPI SSE endpoint (port 8001) → Browser EventSource
```

The UI updates **step-by-step** with rows in/out, duration, status, error messages, and animated progress bars with exponential backoff reconnection.

### Collaborative Editing (Yjs)

Multiple users can edit the same pipeline YAML simultaneously:

- **Yjs** — Conflict-free replicated data types (CRDTs) for real-time YAML collaboration
- **y-websocket** — Dedicated WebSocket server with Redis persistence (`y-redis`) for cross-server sync
- **Remote cursors** — See where other users are editing in real time via `y-codemirror.next`
- **Collaborator presence** — Shows who is currently in the workspace
- **JWT authentication** — Cookie-based (`pipelineiq_token`) with rejection logging for failed auth attempts

### Column Autocomplete & Fuzzy Suggestions

- **Column autocomplete** — Jaro-Winkler similarity (threshold 0.85) suggests closest column names for typos in filter, select, rename, and join steps
- **Invalid step type suggestions** — Closest valid step type is suggested when an invalid type is entered
- **Step reference suggestions** — Missing step references suggest the closest matching step name

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                 Browser                                      │
│        Next.js 15 · React 19 · Zustand · ReactFlow · CodeMirror · SSE        │
│        Yjs Client · Tailwind CSS v4 · Motion · dnd-kit                       │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │ HTTP / SSE / WebSocket
                                    ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                               Nginx (Port 80)                                 │
│        Reverse proxy · Security headers · SSE passthrough · Rate limits       │
│        Gzip compression · CSP headers · Private-IP /metrics restriction       │
│        X-Correlation-ID propagation                                           │
└──────┬─────────────────┬──────────────────┬─────────────────┬─────────────────┘
       │                 │                  │                 │
       ▼                 ▼                  ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   FastAPI    │  │   Frontend   │  │   Grafana    │  │    Flower    │
│   Port 8000  │  │  Next.js SSR │  │  Port 3000   │  │  Port 5555   │
│  + SSE Port  │  │  Port 3000   │  │  /grafana/   │  │  /flower/    │
│    8001      │  └──────────────┘  └──────────────┘  └──────────────┘
└──────┬───────┘
       │
       ├──────────────────────────────────────────────────┐
       │                                                  │
       ▼                                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             Celery Workers                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │Critical  │  │ Default  │  │  Bulk    │  │ Gemini   │  │  Streaming   │   │
│  │(2 conc)  │  │(4 conc)  │  │(2 conc)  │  │(1 conc)  │  │ (4 conc)     │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────────┘   │
│  Celery Beat (Scheduler) · Flower (Monitoring)                              │
└─────────────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Data Layer                                        │
│  ┌──────────┐  ┌──────────────────────────────────────────────┐  ┌────────┐ │
│  │PostgreSQL│  │           Redis (4 instances)                │  │ MinIO  │ │
│  │   15     │  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐         │  │  S3-   │ │
│  │  Primary │  │  │Broker│ │PubSub│ │Cache │ │ Yjs  │         │  │compat  │ │
│  │  Replica │  │  │6379  │ │6380  │ │6381  │ │6382  │         │  │Storage │ │
│  └──────────┘  │  └──────┘ └──────┘ └──────┘ └──────┘         │  └────────┘ │
│  PgBouncer     └──────────────────────────────────────────────┘             │
│  Pooling                                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Streaming & Observability                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Redpanda   │  │   Jaeger     │  │  Prometheus  │  │ Yjs WebSocket│     │
│  │ Kafka-compat │  │ Distributed  │  │   Scraping   │  │  Collab      │     │
│  │   Port 9092  │  │   Tracing    │  │  Port 9090   │  │  Port 1234   │     │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Celery Worker Topology (6 Dedicated Queues)

| Queue | Pods | Concurrency | Purpose |
|-------|------|-------------|---------|
| `critical` | 1 | 2 | Urgent user-facing tasks (pipeline execution) |
| `default` | 2 | 4 | Standard pipeline execution |
| `bulk` | 2 | 2 | Large batch operations |
| `gemini` | 1 | 1 | Gemini AI calls (rate-limited: 50/min, 900K tokens/min) |
| `streaming` | 1 | 4 | Streaming pipeline processing |
| `beat` | 1 | 1 | Celery Beat scheduler |

### 4 Redis Instances

| Instance | Port | Purpose | Max Memory |
|----------|------|---------|------------|
| `redis-broker` | 6379 | Celery broker + result backend | 512 MB |
| `redis-pubsub` | 6380 | SSE real-time event pub/sub | 512 MB |
| `redis-cache` | 6381 | LRU cache (stats, policies, AI responses) | 1 GB |
| `redis-yjs` | 6382 | Yjs CRDT persistence (append-only) | 128 MB |

### MinIO Buckets & Lifecycle Policies

| Bucket | Purpose | Auto-Delete |
|--------|---------|-------------|
| `pipelineiq-uploads` | User source data | No |
| `pipelineiq-outputs` | Pipeline output files | 7 days |
| `pipelineiq-spills` | Arrow IPC spills | 2 days |
| `pipelineiq-wasm` | WASM modules | No |

---

## Tech Stack

### Backend

| Category | Technology | Version |
|----------|-----------|---------|
| API Framework | FastAPI + Uvicorn | 0.109.0 / 0.27.0 |
| ASGI Server | Gunicorn + Uvicorn workers (4 workers) | 22.0.0 |
| ORM | SQLAlchemy + Alembic | 2.0.25 / 1.13.1 |
| Database | PostgreSQL 15 (primary + streaming replica) | 15 |
| Connection Pooling | PgBouncer (transaction mode, 10K clients) | — |
| Data Processing | Pandas + NumPy + DuckDB | 2.1.4 / 1.26.3 / 1.5.2 |
| Columnar Format | Apache Arrow (PyArrow) | 24.0.0 |
| Lineage Graphs | NetworkX | 3.2.1 |
| Task Queue | Celery + Redis (4 instances) | 5.3.6 |
| Auth | JWT (PyJWT) + bcrypt (rounds=12, ProcessPoolExecutor) | HS256 |
| Rate Limiting | SlowAPI (per-endpoint tiers) | 0.1.9 |
| Serialization | Orjson | 3.11.6 |
| Validation | Pydantic v2 + Pydantic-Settings | 2.5.3 / 2.1.0 |
| Object Storage | MinIO (S3-compatible) + boto3 | 7.2.11 / 1.34.162 |
| Streaming | Redpanda (Kafka-compatible) + confluent-kafka | ≥2.4.0 |
| WASM Runtime | Wasmtime (sandboxed, fuel-bounded) | ≥18.0.0 |
| AI / LLM | Google Gemini API (`gemini-2.5-flash`) | 1.2.0 |
| Distributed Tracing | OpenTelemetry SDK + auto-instrumentation | 1.42.0 |
| Error Tracking | Sentry SDK (FastAPI + Celery + SQLAlchemy) | 1.45.1 |
| Monitoring | Prometheus + prometheus-fastapi-instrumentator | 6.1.0 |
| Fuzzy Matching | Jellyfish (Jaro-Winkler) | 1.2.1 |
| Scheduling | croniter + Celery Beat | 2.0.1 |
| Logging | Structlog (structured JSON, correlation IDs) | 24.1.0 |
| System | psutil | 7.2.2 |

### Frontend

| Category | Technology | Version |
|----------|-----------|---------|
| Framework | Next.js | 15.4 |
| UI Library | React | 19.2 |
| Language | TypeScript | 5.9 |
| State Management | Zustand (persisted to localStorage) | 5.0.11 |
| Graph Visualization | ReactFlow (@xyflow/react) | 12.10 |
| Code Editor | CodeMirror 6 (YAML mode, Yjs collaboration) | 4.25 |
| Animations | Motion | 12.23 |
| CSS Framework | Tailwind CSS v4 | 4.11 |
| Drag & Drop | dnd-kit | 6.3 / 10.0 |
| Collaboration | Yjs + y-websocket + y-codemirror.next | 13.6.30 |
| Icons | Lucide React | 0.553 |
| Forms | React Hook Form + @hookform/resolvers | 5.2 |
| Server State | TanStack React Query | 5.100 |
| Utilities | clsx, tailwind-merge, class-variance-authority, date-fns, use-debounce | — |
| Testing | Vitest + React Testing Library + jsdom | 4.0 / 16.3 |
| E2E Testing | Playwright | 1.56 |

### Infrastructure

| Category | Technology |
|----------|-----------|
| Container Orchestration | Docker Compose (21 services) |
| Reverse Proxy | Nginx (security headers, SSE passthrough, rate limiting) |
| Monitoring | Prometheus + Grafana (10-panel auto-provisioned dashboard) |
| Distributed Tracing | Jaeger (OTLP collector, in-memory storage) |
| Orchestration (alt) | Kubernetes (k8s/ manifests, HPA, Civo k3s) |
| Deployment (API) | Render.com |
| Deployment (Frontend) | Vercel |
| Production Database | Neon.tech PostgreSQL |
| Production Cache | Upstash Redis (TLS) |
| CI/CD | GitHub Actions (5 jobs) |
| Load Testing | k6 |
| Chaos Engineering | 7 fault-injection scenarios |

---

## API Reference

Base path: `/api/v1` — proxied through Nginx (Docker) or Next.js rewrites (Vercel).

### Auth

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/auth/register` | Create account (first user = admin) | No |
| POST | `/auth/login` | Get JWT token (24h expiry) | No |
| GET | `/auth/me` | Current user profile | Yes |
| POST | `/auth/logout` | Logout | Yes |
| GET | `/auth/users` | List all users | Admin |
| PATCH | `/auth/users/{id}/role` | Change user role | Admin |

### Files

| Method | Path | Description | Auth | Rate Limit |
|--------|------|-------------|------|------------|
| POST | `/api/v1/files/upload` | Upload CSV/JSON/Parquet/Excel (500MB max) | Yes | 30/min |
| GET | `/api/v1/files/` | List files | Yes | — |
| GET | `/api/v1/files/{id}` | File metadata | Yes | — |
| DELETE | `/api/v1/files/{id}` | Delete file | Yes | — |
| GET | `/api/v1/files/{id}/preview` | Preview first N rows | Yes | — |
| GET | `/api/v1/files/{id}/schema/history` | Schema snapshots | Yes | — |
| GET | `/api/v1/files/{id}/schema/diff` | Detect schema drift | Yes | — |

### Pipelines

| Method | Path | Description | Auth | Rate Limit |
|--------|------|-------------|------|------------|
| POST | `/api/v1/pipelines/validate` | Validate YAML configuration | Yes | 60/min |
| POST | `/api/v1/pipelines/plan` | Dry-run execution plan | Yes | 60/min |
| POST | `/api/v1/pipelines/preview` | Preview sample data at a step | Yes | 60/min |
| POST | `/api/v1/pipelines/run` | Execute pipeline (async, via Celery) | Yes | 10/min |
| GET | `/api/v1/pipelines/` | List all runs (paginated, filterable) | Yes | 120/min |
| GET | `/api/v1/pipelines/stats` | Aggregate pipeline statistics | Yes | 120/min |
| GET | `/api/v1/pipelines/{id}` | Run details + step-by-step results | Yes | 120/min |
| GET | `/api/v1/pipelines/{id}/stream` | SSE progress stream | Yes | — |
| POST | `/api/v1/pipelines/{id}/cancel` | Cancel a running pipeline | Yes | — |
| GET | `/api/v1/pipelines/{id}/export` | Download output file | Yes | — |

### Lineage

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/lineage/{run_id}` | Full React Flow graph (pre-computed layout) |
| GET | `/api/v1/lineage/{run_id}/column?step=X&column=Y` | Column ancestry trace |
| GET | `/api/v1/lineage/{run_id}/impact?step=X&column=Y` | Forward impact analysis |

### Versions

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/versions/{name}` | All versions of a pipeline |
| GET | `/api/v1/versions/{name}/{version}` | Specific version YAML |
| GET | `/api/v1/versions/{name}/diff/{a}/{b}` | Unified diff between two versions |
| POST | `/api/v1/versions/{name}/restore/{v}` | Restore a previous version |

### Webhooks

| Method | Path | Auth |
|--------|------|------|
| POST | `/api/v1/webhooks/` | Yes |
| GET | `/api/v1/webhooks/` | Yes |
| DELETE | `/api/v1/webhooks/{id}` | Yes |
| GET | `/api/v1/webhooks/{id}/deliveries` | Yes |
| POST | `/api/v1/webhooks/{id}/test` | Yes |

### Schedules

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/schedules/` | Create cron-based pipeline schedule | Yes |
| GET | `/api/v1/schedules/` | List user's schedules | Yes |
| PATCH | `/api/v1/schedules/{id}/toggle` | Enable/disable schedule | Yes |
| DELETE | `/api/v1/schedules/{id}` | Delete schedule | Yes |

### Contracts

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/contracts/` | Create data contract | Yes |
| GET | `/api/v1/contracts/` | List contracts | Yes |
| GET | `/api/v1/contracts/{id}` | Contract details | Yes |
| DELETE | `/api/v1/contracts/{id}` | Delete contract | Yes |

### WASM Modules

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/wasm/upload` | Upload WASM module (validated) | Yes |
| GET | `/api/v1/wasm/` | List modules | Yes |
| DELETE | `/api/v1/wasm/{id}` | Delete module | Yes |

### Column Policies

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/v1/column-policies/` | Create column security policy | Yes |
| GET | `/api/v1/column-policies/` | List policies | Yes |
| DELETE | `/api/v1/column-policies/{id}` | Delete policy | Yes |

### Data Catalog

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/catalog/assets` | List catalog assets | Yes |
| GET | `/api/v1/catalog/assets/{id}` | Asset details with relationships | Yes |
| GET | `/api/v1/catalog/assets/{id}/blast-radius` | Impact analysis for asset changes | Yes |

### Templates, Notifications, Dashboard, Permissions

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/templates/` | List 5 pre-built pipeline templates | No |
| GET | `/api/v1/templates/{id}` | Get template with YAML | No |
| POST | `/api/v1/notifications/` | Create Slack/email notification config | Yes |
| GET | `/api/v1/notifications/` | List notification configs | Yes |
| DELETE | `/api/v1/notifications/{id}` | Delete notification config | Yes |
| POST | `/api/v1/notifications/{id}/test` | Send test notification | Yes |
| GET | `/api/v1/dashboard/stats` | Personal analytics & activity | Yes |
| POST | `/api/v1/pipelines/{name}/permissions` | Grant pipeline permission | Owner/Admin |
| GET | `/api/v1/pipelines/{name}/permissions` | List pipeline permissions | Yes |
| DELETE | `/api/v1/pipelines/{name}/permissions/{user_id}` | Revoke pipeline permission | Owner/Admin |

### Audit & Health

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/audit/logs` | All audit logs (paginated) | Admin |
| GET | `/api/v1/audit/logs/mine` | Current user's logs | Yes |
| GET | `/health` | DB + Redis + Storage readiness checks | No |
| GET | `/livez` | Liveness probe (no deps) | No |
| GET | `/healthz` | Health check alias | No |
| GET | `/readyz` | Readiness probe (all deps) | No |
| GET | `/metrics` | Prometheus metrics (5 custom + instrumentator) | Internal Only |

Every response includes `X-Request-ID`, `X-Process-Time`, `X-API-Version`, and `X-App-Version` headers.

---

## Frontend Dashboard

### Workspaces & Widgets

The dashboard is organized into 5 workspaces (persisted via Zustand + localStorage). Each workspace holds a grid of widgets in a recursive binary-tree split layout (row/col). Widgets can be rearranged via drag-and-drop.

| Widget | Description |
|--------|-------------|
| **Quick Stats** | Aggregate pipeline metrics (runs, files, users, success rate) |
| **File Registry** | Browse and manage uploaded files with metadata |
| **File Upload** | Upload CSV/JSON/Parquet/XLSX (drag-and-drop, 500MB limit) |
| **Pipeline Editor** | CodeMirror 6 YAML editor with Yjs real-time collaboration |
| **Step DAG** | Visual dependency graph of pipeline steps |
| **Run Monitor** | Real-time step-by-step pipeline execution status via SSE |
| **Run History** | Past pipeline runs with status, duration, row counts |
| **Execution Timeline** | Gantt chart showing step overlap and duration |
| **Lineage Graph** | Interactive column-level lineage visualization (React Flow) |
| **Pipeline Versions** | Version browser with diff viewer and restore |
| **Templates** | 5 pre-built pipeline templates for common patterns |
| **Schedules** | Cron schedule management with next-run display |
| **WASM Modules** | Upload, manage, and catalog WebAssembly UDF modules |
| **Manage Connections** | Data source and API connection configuration |
| **Column Policies** | Column-level security policy management |
| **Data Contracts** | Contract management with violation badges |
| **AI Generation** | Natural language → YAML pipeline generation modals |
| **Healing Agent** | AI healing attempt history and status |
| **PII Detection** | Banner showing detected PII in uploaded files |
| **Data Preview** | Preview sample data at each pipeline step |

### Layout System

- Recursive split layout (binary tree `LayoutNode` with row/col splits)
- Drag-and-drop widget rearrangement
- 5 workspaces with independent layouts
- Persisted to localStorage
- Error boundaries on every widget — graceful per-widget error handling prevents full-app crashes

### Theme System

7 built-in themes:

| Theme | Description |
|-------|-------------|
| **PipelineIQ Dark** | Default dark theme |
| **PipelineIQ Light** | Light theme variant |
| **Catppuccin Mocha** | Warm dark with pastel accents |
| **Tokyo Night** | Deep blue-dark with neon accents |
| **Gruvbox Dark** | Retro warm dark |
| **Nord** | Frosty blue-gray palette |
| **Rosé Pine** | Soft pine-inspired tones |

- **Custom theme builder** — 25+ CSS variables with live preview, export as JSON
- CSS variable injection via `buildThemeCss()`, `sanitizeThemeName()` prevents CSS injection
- Local font loading: Inter (sans-serif) and JetBrains Mono (monospace) via `next/font/local`

---

## Keyboard Shortcuts

All shortcuts are rebindable through the Keybindings modal (`Alt+K`). There are 18 configurable shortcuts.

| Shortcut | Action |
|----------|--------|
| `Ctrl + K` | Command palette (fuzzy search, 30+ commands) |
| `Alt + Enter` | Widget terminal launcher |
| `Alt + Q` | Close active widget |
| `Alt + T` | Theme selector / builder |
| `Alt + K` | Keybindings editor |
| `Ctrl + Enter` | Run pipeline (from editor) |
| `Alt + 1–5` | Switch to workspace 1–5 |
| `Alt + Shift + 1–5` | Move active widget to workspace |
| `Ctrl + Shift + 1–9` | Toggle specific widgets |

---

## Observability

### Prometheus Metrics

| Metric | Type | Labels |
|--------|------|--------|
| `pipelineiq_pipeline_runs_total` | Counter | `status` (completed, failed, cancelled, running) |
| `pipelineiq_pipeline_duration_seconds` | Histogram | — |
| `pipelineiq_files_uploaded_total` | Counter | — |
| `pipelineiq_active_users_total` | Gauge | — |
| `pipelineiq_celery_queue_depth` | Gauge | `queue` (critical, default, bulk) |

Plus auto-instrumentation from `prometheus_fastapi_instrumentator` (request rate, latency, error rate per endpoint).

### Grafana Dashboard

A 10-panel dashboard auto-provisioned at `/grafana/`:

- Pipeline runs per minute (rate chart by status)
- API response latency (p50, p95, p99)
- Pipeline success rate (gauge)
- Celery queue depth per queue
- Error rate over time
- Active connections / SSE streams
- File uploads over time
- Worker pool utilization
- AI healing latency
- Storage usage (MinIO bucket sizes)

### Distributed Tracing (Jaeger / OpenTelemetry)

End-to-end tracing across the entire request lifecycle:

- HTTP requests → FastAPI middleware → Celery task execution → SQLAlchemy queries → Redis operations → S3/MinIO calls
- Trace context propagated via W3C Trace Context headers
- Auto-instrumentation for FastAPI, Celery, SQLAlchemy, Redis, and HTTPX
- Jaeger UI at `http://localhost:16686` (in-memory storage, 100K max traces)

### Structured Logging (Structlog)

All services emit structured JSON logs with:

- `request_id` — Correlating across services
- `user_id` — Per-user debugging
- `correlation_id` — End-to-end tracing via `x-correlation-id` header
- Access logs with method, path, status, duration
- Health check noise filtered out (`/health`, `/livez`, `/readyz`, `/metrics`)
- Production: JSON output for log aggregators (Grafana Loki, Datadog)
- Development: Colorized console output

### Error Tracking (Sentry)

- FastAPI + Celery + SQLAlchemy integrations
- 0.1 `traces_sample_rate`, 0.1 `profiles_sample_rate`
- Environment and release tags attached
- Only enabled when `SENTRY_DSN` is configured

---

## Testing

### Backend (206+ tests)

```bash
cd backend
python -m pytest tests/ -v --tb=short
```

| Suite | Tests | Coverage |
|-------|-------|----------|
| `tests/unit/api/` | ~40 | All REST endpoint tests: auth, rate limiting, files, pipelines |
| `tests/unit/pipeline/` | ~35 | Parser, all 19 step types, validators, versioning, planner |
| `tests/unit/ai/` | ~20 | Pipeline generation, autocomplete, rate limiting, YAML diff/cache |
| `tests/unit/healing/` | ~15 | Prompts, classifier, patch applier, sandbox, schema drift/diff |
| `tests/unit/storage/` | ~10 | Arrow bus tiered storage, S3 operations |
| `tests/unit/infrastructure/` | ~15 | Redis connections, SSE lifecycle, caching, Celery queues |
| `tests/unit/scheduling/` | ~8 | Cron utilities, schedule API, Celery Beat registration |
| `tests/unit/contracts/` | ~8 | Data contract validation, enforcement, type mapping |
| `tests/unit/webhooks/` | ~8 | Delivery, HMAC signing, retry logic |
| `tests/unit/security/` | ~10 | Security leak detection, comprehensive auth bypass tests |
| `tests/unit/catalog/` | ~8 | OpenLineage export, lineage cache, blast radius CTE |
| `tests/unit/streaming/` | ~5 | Streaming unit tests, SSE |
| `tests/unit/profiling/` | ~5 | Data profiling, profiling tasks |
| `tests/e2e/` | ~10 | Week 3 API, Week 8 WASM, Week 9 streaming, Week 10 full cycle |
| `tests/integration/` | ~8 | Stress, performance, profiling, healing E2E, AI pipeline |
| `tests/security/` | ~3 | WASM sandbox security isolation |

Backend tests use SQLite in-memory for speed. CI runs against PostgreSQL 15 + Redis 7.

### Frontend (93+ tests)

```bash
cd frontend
npm run test
```

| Suite | Tests | Coverage |
|-------|-------|----------|
| `api.test.ts` | 26 | Token management, fetchApi, all API functions, error handling |
| `stores.test.ts` | 26 | Pipeline, widget, theme, keybinding Zustand stores |
| `pages.test.tsx` | 12 | Login/register forms, validation, error states |
| `widgets.test.tsx` | 11 | QuickStats, FileUpload, RunHistory, FileRegistry widgets |
| `utils.test.ts` | 7 | cn() utility, constants values |
| `middleware.test.ts` | 4 | Auth redirect logic |
| `auth-context.test.tsx` | 4 | AuthProvider login, logout, demo login |
| `hooks.test.ts` | 3 | Widget layout toggle, workspace switching |

Frontend tests use Vitest + React Testing Library + jsdom.

### E2E Tests (Playwright, 14+ suites)

```bash
cd frontend
E2E_BASE_URL=http://localhost:3000 npm run test:e2e
```

- Authentication flow (login, register, logout)
- File upload (CSV, JSON, Parquet, XLSX)
- Pipeline builder (visual DAG editor)
- Pipeline run (validate, plan, execute, monitor)
- AI pipeline generation (natural language → YAML)
- Schedules (CRUD, toggle, execution)
- Data contracts (create, enforce, violations)
- WASM modules (upload, validate, execute)
- Data catalog (browse, search, lineage)
- Templates (list, load, customize)
- Healing agent (failure, repair, verify)
- Collaboration (Yjs cursors, presence)
- Streaming (Redpanda publish/consume)
- Scheduling (cron, Celery Beat)

### Load Testing (k6)

```bash
k6 run k6/load-auth.js
k6 run k6/load-file-upload.js
k6 run k6/load-pipeline-api.js
```

### Chaos Engineering

```bash
bash chaos/chaos-scenarios.sh
```

7 scenarios: random service crashes, network latency injection, Redis failover, PostgreSQL connection pool exhaustion, disk space simulation, CPU throttling, memory pressure.

---

## Database

### Migration Commands

```bash
alembic upgrade head                        # Run pending migrations
alembic revision --autogenerate -m "desc"   # Create new migration
alembic downgrade -1                        # Rollback one step
alembic current                             # Show current migration
alembic history                             # Show all migrations
```

### Migration History

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

### Database Models (23 Tables)

| Model | Table | Description |
|-------|-------|-------------|
| `User` | `users` | Registered users (email, username, hashed_password, role, active) |
| `UploadedFile` | `uploaded_files` | File metadata with versioning (filename, size, columns, dtypes) |
| `FileProfile` | `file_profiles` | Auto-computed data profiles |
| `SchemaSnapshot` | `schema_snapshots` | Point-in-time schema for drift detection |
| `PipelineRun` | `pipeline_runs` | Full run lifecycle (status, timing, row counts, user, trigger) |
| `StepResult` | `step_results` | Per-step execution details (rows in/out, columns, timing, warnings) |
| `LineageGraph` | `lineage_graphs` | Serialized NetworkX graph + pre-computed React Flow layout |
| `HealingAttempt` | `healing_attempts` | Autonomous healing metadata (schema diffs, Gemini patches, sandbox results) |
| `PipelineVersion` | `pipeline_versions` | Versioned YAML configs with change summaries (max 50) |
| `PipelineSchedule` | `pipeline_schedules` | Cron-based recurring schedules with stats |
| `ScheduleRun` | `schedule_runs` | Schedule-triggered run history |
| `PipelineContract` | `pipeline_contracts` | Data contract definitions |
| `ContractViolationRecord` | `contract_violations` | Breach records |
| `ColumnPolicy` | `column_policies` | Column-level access policies (redact/mask) |
| `NotificationConfig` | `notification_configs` | Slack/email notification channels |
| `Webhook` | `webhooks` | Webhook registrations (URL, secret, events, active) |
| `WebhookDelivery` | `webhook_deliveries` | Delivery attempt records (response, timing, errors) |
| `AuditLog` | `audit_logs` | Immutable action log (blocked from UPDATE/DELETE via trigger) |
| `WasmModule` | `wasm_modules` | WASM UDF metadata (sha256, exports, fuel budget) |
| `PipelinePermission` | `pipeline_permissions` | Per-pipeline RBAC (owner/runner/viewer) |
| `StreamingStats` | `streaming_stats` | Streaming execution metrics (batches, throughput, consumer lag) |
| `DataAsset` | `data_assets` | Global catalog assets (file, column, pipeline, topic) |
| `AssetRelationship` | `asset_relationships` | Directed catalog edges (reads_from, writes_to, transforms, joins) |

---

## Deployment

### Production (Current)

| Service | Platform | URL |
|---------|----------|-----|
| Backend API | Render.com | https://pipelineiq-api.onrender.com |
| Frontend | Vercel | https://pipeline-iq0.vercel.app |
| Database | Neon.tech PostgreSQL | us-east-1 connection pooler |
| Cache + Queue | Upstash Redis (TLS) | rediss:// endpoint |

Both Render and Vercel auto-deploy on push to `main`. No GitHub secrets needed.

> Render free tier sleeps after 15 min of inactivity. First request after cold start takes ~30–60s. Blueprint defined in `render.yaml`.

### Self-Hosted (Docker Compose — 21 Services)

```bash
cp .env.example .env
# Edit .env with your secrets
docker compose up --build -d
```

| Service | Containers | Purpose |
|---------|-----------|---------|
| `nginx` | 1 | Reverse proxy, security headers, SSE passthrough, CORS |
| `api` | 1 | FastAPI (Gunicorn + 4 Uvicorn workers) |
| `sse-service` | 1 | Dedicated SSE FastAPI (2 workers, 600s timeout) |
| `frontend` | 1 | Next.js SSR (standalone output) |
| `postgres` | 1 | Primary DB (WAL replication, 200 max connections) |
| `postgres-replica` | 1 | Read replica (streaming replication) |
| `pgbouncer` | 1 | Connection pooler for primary (transaction mode, 10K clients) |
| `pgbouncer-replica` | 1 | Connection pooler for replica |
| `redis-broker` | 1 | Celery broker (512MB, allkeys-lru) |
| `redis-pubsub` | 1 | SSE event pub/sub (512MB, allkeys-lru) |
| `redis-cache` | 1 | LRU cache (1GB, allkeys-lru) |
| `redis-yjs` | 1 | Yjs CRDT persistence (128MB, append-only) |
| `worker-critical` | 1 | High-priority Celery (concurrency=2) |
| `worker-default` | 2 | Standard tasks (concurrency=4) |
| `worker-bulk` | 1 | Bulk processing (concurrency=2) |
| `worker-gemini` | 1 | AI/healing (concurrency=1, rate-limited) |
| `worker-streaming` | 1 | Redpanda streaming (concurrency=4) |
| `celery-beat` | 1 | Cron scheduler (PersistentScheduler) |
| `y-websocket` | 1 | Yjs WebSocket server (80MB memory limit) |
| `minio` | 1 | S3-compatible object storage |
| `flower` | 1 | Celery monitoring (basic auth) |
| `redpanda` | 1 | Kafka-compatible streaming (2G memory) |
| `redpanda-console` | 1 | Redpanda management UI |
| `prometheus` | 1 | Metrics scraping (15-day retention) |
| `jaeger` | 1 | Distributed tracing (in-memory, 100K traces) |
| `grafana` | 1 | Observability dashboards (auto-provisioned) |

### Kubernetes

Manifests in `k8s/` deploying to Civo k3s:

```bash
kubectl create namespace pipelineiq
kubectl apply -f k8s/secrets.yaml   # fill in secrets first
kubectl apply -f k8s/
```

Includes: backend + 6 Celery worker types, PostgreSQL, Redis (4 instances), MinIO, Nginx ingress with TLS, ConfigMaps, Secrets, Horizontal Pod Autoscaling by CPU.

---

## CI/CD

GitHub Actions runs on every push to `main`/`develop` and every PR to `main`.

### Job 1: Backend Tests

- Python 3.11 with PostgreSQL 15 + Redis 7 services
- Install dependencies, run Alembic migrations
- Execute 206+ pytest tests with coverage report
- Ruff linting + Black formatting check

### Job 2: Frontend Check

- Node.js 20
- TypeScript check (`tsc --noEmit`)
- 93+ Vitest unit tests
- Production build verification (`next build`)
- ESLint check

### Job 3: Docker Smoke Test

- Builds all Docker Compose services
- Starts all 21 containers, waits for healthy state
- Executes full integration test: health check → login → upload CSV → run pipeline → poll for completion → verify results

### Job 4: E2E Tests (Playwright)

- Chromium against production/staging URL
- Authentication, file upload, pipeline builder, templates, and lineage specs

### Job 5: Streaming E2E

- Full Docker Compose stack with Redpanda
- Tests streaming pipeline execution end-to-end

### CD

- On successful `main` branch CI: deploy backend to Render via webhook trigger
- Conditional on `render.com` in git remote URL

---

## Project Structure

```
PipelineIQ/
├── backend/                    # FastAPI Python backend (~11K lines)
│   ├── api/                    # FastAPI route handlers (18 modules)
│   │   ├── auth.py             # Registration, login, user management
│   │   ├── files.py            # File upload, preview, schema
│   │   ├── pipelines.py        # Pipeline CRUD, validate, plan, run
│   │   ├── lineage.py          # Column lineage endpoints
│   │   ├── versions.py         # Pipeline versioning
│   │   ├── webhooks.py         # Webhook CRUD + delivery
│   │   ├── audit.py            # Audit log retrieval
│   │   ├── schedules.py        # Cron schedule management
│   │   ├── templates.py        # Pipeline template listing
│   │   ├── notifications.py    # Slack/email notification configs
│   │   ├── dashboard.py        # Personal analytics
│   │   ├── permissions.py      # Per-pipeline RBAC
│   │   ├── contracts.py        # Data contracts
│   │   ├── sse.py              # SSE streaming endpoint
│   │   ├── debug.py            # Debug routes (non-production)
│   │   └── router.py           # Router aggregation
│   ├── pipeline/               # Core engine
│   │   ├── parser.py           # YAML → typed dataclasses
│   │   ├── runner.py           # Step execution orchestration
│   │   ├── steps/              # Step implementations
│   │   │   ├── _core.py        # Pandas-based step executors
│   │   │   ├── sql_step.py     # SQL/DuckDB step executor
│   │   │   └── wasm_compute.py # WebAssembly UDF execution
│   │   ├── lineage.py          # NetworkX graph construction (1101 lines)
│   │   ├── validators.py       # 12 data quality checks
│   │   ├── planner.py          # Dry-run execution estimates
│   │   ├── versioning.py       # Version save/restore/diff
│   │   ├── schema_drift.py     # Schema comparison & detection
│   │   ├── diff_utils.py       # Unified diff generation
│   │   ├── contracts.py        # Data contract definitions
│   │   ├── cache.py            # Pipeline result caching
│   │   ├── exceptions.py       # Domain-specific exceptions
│   │   └── definitions.py      # Step type enum, shared types
│   ├── services/               # Business logic services
│   │   ├── webhook_service.py  # HMAC signing, delivery, retry
│   │   ├── audit_service.py    # Immutable audit logging
│   │   └── notification_service.py # Slack/email dispatch
│   ├── tasks/                  # Celery task definitions
│   │   ├── pipeline_task.py    # Pipeline execution task
│   │   ├── webhook_task.py     # Webhook delivery task
│   │   ├── schedule_task.py    # Scheduled pipeline trigger
│   │   ├── notification_task.py # Notification dispatch
│   │   ├── profiling_task.py   # Data profiling task
│   │   ├── gemini_task.py      # AI healing task
│   │   └── streaming_task.py   # Redpanda streaming tasks
│   ├── models/                 # SQLAlchemy ORM models (23 tables)
│   ├── ai/                     # Gemini AI integration
│   ├── streaming/              # Redpanda/Kafka streaming
│   ├── storage/                # S3/MinIO file storage + lifecycle
│   ├── security/               # Column security policies
│   ├── execution/              # DuckDB execution engine
│   ├── scheduling/             # Cron-based scheduler + Celery Beat
│   ├── profiling/              # File auto-profiling
│   ├── contracts/              # Data contract enforcement
│   ├── routers/                # Additional route handlers
│   ├── alembic/                # 8 database migrations
│   ├── tests/                  # 206+ tests across ~80 files
│   ├── scripts/                # seed_demo.py, wait_for_db.py
│   ├── sample_data/            # 4 CSVs + 3 pipeline YAMLs
│   ├── main.py                 # App factory, middleware, health checks
│   ├── config.py               # 60+ env vars via Pydantic-Settings
│   ├── models.py               # (legacy) model imports
│   ├── schemas.py              # 20+ Pydantic response schemas
│   ├── metrics.py              # Prometheus metric definitions
│   ├── auth.py                 # JWT + bcrypt (ProcessPoolExecutor)
│   ├── celery_app.py           # Celery config + SSL + Beat scheduler
│   ├── database.py             # Engine + session factory
│   ├── sse_app.py              # Dedicated SSE FastAPI app
│   ├── telemetry.py            # OpenTelemetry auto-instrumentation
│   └── Dockerfile              # Multi-stage build (production)

├── frontend/                   # Next.js 15 React/TypeScript (~9K lines)
│   ├── app/                    # Next.js App Router pages
│   │   ├── dashboard/          # Main dashboard (5 workspaces)
│   │   ├── login/              # Login page
│   │   ├── register/           # Registration page
│   │   ├── pipelines/          # Pipeline detail pages
│   │   ├── runs/               # Run history pages
│   │   ├── files/              # File management pages
│   │   ├── schedules/          # Schedule management pages
│   │   ├── templates/          # Template pages
│   │   ├── catalog/            # Data catalog pages
│   │   ├── storage/            # Storage management pages
│   │   ├── wasm-modules/       # WASM module pages
│   │   ├── globals.css         # Global styles + 7 themes
│   │   ├── layout.tsx          # Root layout with providers
│   │   ├── providers.tsx       # Auth, Query, Theme providers
│   │   └── page.tsx            # Landing page
│   ├── components/
│   │   ├── layout/             # TopBar, WidgetGrid, CommandPalette, Terminal
│   │   ├── widgets/            # 20+ dashboard widgets
│   │   ├── lineage/            # Lineage graph + typed nodes
│   │   ├── theme/              # ThemeSelector + CustomThemeBuilder
│   │   ├── pipeline-builder/   # Visual DAG editor
│   │   ├── collaboration/      # Yjs remote cursors + presence
│   │   └── runs/               # Run cards, Gantt chart
│   ├── hooks/                  # Custom React hooks
│   ├── store/                  # Zustand stores (pipeline, widget, theme, keybinding)
│   ├── lib/                    # API client, types, utilities (902 lines)
│   ├── __tests__/              # 93+ unit tests
│   └── Dockerfile              # Next.js standalone output

├── y-websocket/                # Yjs CRDT WebSocket collaboration server
│   ├── server.js               # WebSocket server with y-redis persistence
│   └── package.json

├── e2e/                        # Playwright E2E tests (14+ spec files)
├── k6/                         # k6 load-testing scripts
├── chaos/                      # Chaos engineering fault injection
├── k8s/                        # Kubernetes deployment manifests
├── docs/                       # Architecture documentation
├── postman/                    # Postman collection (23 requests)
├── grafana/                    # Grafana auto-provisioning
├── prometheus/                 # Prometheus scrape config
├── pgbouncer/                  # PgBouncer Docker build
├── postgres/                   # PostgreSQL init scripts
├── nginx/                      # Nginx reverse proxy config
├── scripts/                    # Utility scripts
├── .github/workflows/          # CI/CD (5 jobs: backend, frontend, smoke, e2e, streaming)
├── docker-compose.yml          # 21 services
├── render.yaml                 # Render.com blueprint
└── .env.example                # 108 environment variables
```

**Codebase**: ~30,500 lines backend · ~18,000 lines tests · ~23,000 lines frontend · ~1,700 lines infrastructure config · ~1,200 lines y-websocket · ~2,400 lines E2E · ~300 lines load/chaos.

---

## Environment Variables

Full list in `.env.example` (108 variables). Key variables:

### Application

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `PipelineIQ` | Application name |
| `APP_VERSION` | `12.7.3` | Application version |
| `DEBUG` | `false` | Enable debug mode |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `ENVIRONMENT` | `development` | Runtime environment |

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | PostgreSQL connection string (primary) |
| `DATABASE_WRITE_URL` | — | Write-specific connection (defaults to DATABASE_URL) |
| `DATABASE_READ_URL` | — | Read replica connection (defaults to DATABASE_WRITE_URL) |
| `SECRET_KEY` | — | JWT signing key (auto-generated if weak, required in production) |

### Redis / Celery

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_BROKER_URL` | — | Celery broker Redis (port 6379) |
| `REDIS_PUBSUB_URL` | — | Pub/sub Redis (port 6380) |
| `REDIS_CACHE_URL` | — | Cache Redis (port 6381) |
| `REDIS_YJS_URL` | — | Yjs collaboration Redis (port 6382) |
| `CELERY_WORKERS_CRITICAL` | `2` | Concurrency for critical queue |
| `CELERY_WORKERS_DEFAULT` | `3` | Concurrency for default queue |
| `CELERY_WORKERS_BULK` | `2` | Concurrency for bulk queue |

### Pipeline Execution

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_PIPELINE_STEPS` | `50` | Max steps per pipeline |
| `MAX_ROWS_PER_FILE` | `1,000,000` | Max rows per uploaded file |
| `STEP_TIMEOUT_SECONDS` | `300` | 5 min per step timeout |
| `WORKER_MEMORY_LIMIT_GB` | `2` | Memory limit per Celery worker |
| `AUTONOMOUS_HEALING_MAX_ATTEMPTS` | `3` | Max healing retries |

### Rate Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_PIPELINE_RUN` | `10/minute` | Pipeline execution |
| `RATE_LIMIT_FILE_UPLOAD` | `30/minute` | File upload |
| `RATE_LIMIT_VALIDATION` | `60/minute` | YAML validation |
| `RATE_LIMIT_READ` | `120/minute` | Read operations |

### AI / Gemini

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Primary LLM model |
| `GEMINI_FALLBACK_MODELS` | `gemini-2.0-flash,gemini-1.5-flash` | Fallback models |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `SENTRY_DSN` | — | Sentry error tracking (empty = disabled) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://jaeger:4317` | Jaeger/OTLP endpoint |
| `OTEL_SAMPLE_RATE` | `0.1` | Trace sampling rate |

---

## Postman Collection

Import `postman/PipelineIQ.postman_collection.json` — 23 requests in 7 folders:

| Folder | Requests |
|--------|----------|
| Authentication | Register, Login, Get Me, Logout |
| Files | Upload, List, Preview, Schema History, Delete |
| Pipelines | Validate, Plan, Run, Get, List, SSE Stream |
| Lineage | Full Graph, Column Lineage, Impact Analysis |
| Versioning | List, Get, Diff, Restore |
| Webhooks | Create, List, Test, Deliveries, Delete |
| Observability | Health, Metrics, Audit Logs, My Audit Logs |

Variables (`token`, `file_id`, `run_id`, `webhook_id`) are auto-extracted from responses.

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

- **Backend**: Python 3.11, follow existing patterns (typed dataclasses, dependency injection, dispatch dicts)
- **Frontend**: TypeScript strict mode, Zustand for global state, Tailwind CSS v4 for styling
- **Tests**: Write tests for new features (backend: pytest, frontend: Vitest + React Testing Library)
- **Commits**: Conventional Commits format (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`)
- **Lint**: `ruff` for Python, `eslint` for TypeScript

---

## License

[Apache 2.0](LICENSE) — Copyright 2026 Siddharth

```
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
