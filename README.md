# PipelineIQ

![CI](https://github.com/Siddharthk17/PipelineIQ/actions/workflows/ci.yml/badge.svg)
![Tests](https://img.shields.io/badge/tests-206%20passing-brightgreen)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Python](https://img.shields.io/badge/python-3.11-blue)
![Next.js](https://img.shields.io/badge/Next.js-15-black)

> A production-grade data pipeline orchestration platform with column-level lineage tracking, real-time execution monitoring, schema drift detection, JWT authentication, and full observability.

## ✨ Features

- **8 pipeline step types** — load, filter, join, aggregate, sort, rename, validate, save
- **Column-level data lineage** tracking with impact analysis
- **Real-time execution streaming** via Server-Sent Events (SSE)
- **Schema drift detection** with breaking/warning/info severities
- **Data quality validation** with 12 check types (not_null, unique, range, regex, etc.)
- **Pipeline versioning** with git-style diffs and restore
- **Dry-run execution planner** with 8 heuristics and row estimates
- **JWT authentication** with role-based access control (admin/viewer)
- **Redis caching** (3x speedup on lineage queries)
- **Rate limiting** with 4 tiers per endpoint type
- **Celery async execution** with Flower monitoring dashboard
- **Prometheus metrics** + Grafana dashboards (10 panels)
- **Sentry error tracking** with Celery integration
- **Webhook system** with HMAC SHA256 signatures and retry logic
- **Immutable audit logging** with database-level enforcement
- **Nginx reverse proxy** with SSE-safe configuration
- **GitHub Actions CI/CD** pipeline

## 🏗️ Architecture

```
Browser → Nginx (:80)
            ├── /api/    → FastAPI (:8000)
            │              ├── PostgreSQL (:5432)
            │              ├── Redis (:6379) [cache + broker]
            │              └── Celery Worker
            ├── /        → Next.js (:3000)
            ├── /flower/ → Flower (:5555)
            └── /grafana/→ Grafana (:3001)
                            └── Prometheus (:9090)
```

## 🛠️ Tech Stack

| Category | Technology | Version | Purpose |
|----------|-----------|---------|---------|
| Backend | FastAPI | 0.109 | REST API + SSE |
| Frontend | Next.js | 15 | React dashboard |
| Database | PostgreSQL | 15 | UUID PKs, JSONB |
| Cache/Queue | Redis | 7 | Caching + Celery broker |
| Task Queue | Celery | 5.3 | Async pipeline execution |
| Reverse Proxy | Nginx | latest | Port 80, SSE support |
| Monitoring | Prometheus + Grafana | 2.48 / 10.2 | Metrics + dashboards |
| Error Tracking | Sentry | 1.39 | Error monitoring |
| Auth | python-jose + passlib | - | JWT + bcrypt |
| Testing | pytest | 7.4 | 206+ tests |
| CI/CD | GitHub Actions | - | Auto test + deploy |

## 🚀 Quick Start (Local)

**Prerequisites:** Docker, Docker Compose, Git

```bash
git clone https://github.com/Siddharthk17/PipelineIQ.git
cd PipelineIQ
cp .env.example .env        # Edit with your values
docker-compose up -d
```

Visit **http://localhost** — first registered user becomes admin.

## 📋 Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://pipelineiq:...@db:5432/pipelineiq` |
| `REDIS_URL` | Redis connection string | `redis://redis:6379/0` |
| `SECRET_KEY` | JWT signing key (min 32 chars) | `change-me-in-production` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT token expiry | `1440` (24h) |
| `POSTGRES_PASSWORD` | Database password | - |
| `FLOWER_USER` / `FLOWER_PASSWORD` | Flower dashboard credentials | `admin` / - |
| `GRAFANA_USER` / `GRAFANA_PASSWORD` | Grafana dashboard credentials | `admin` / - |
| `SENTRY_DSN` | Sentry DSN (optional) | empty |
| `ENVIRONMENT` | `development` or `production` | `development` |

## 🧪 Running Tests

```bash
docker-compose exec api pytest backend/tests/ -v
```

Expected: **206+ tests, 0 failures**

## 📡 API Reference

- **Swagger UI**: http://localhost/docs
- **ReDoc**: http://localhost/redoc

## 📊 Observability

- **Metrics**: http://localhost/metrics
- **Grafana**: http://localhost/grafana/ (credentials from .env)
- **Flower**: http://localhost:5555/flower/ (credentials from .env)

## 🔐 Authentication

First registered user becomes **admin** automatically. Subsequent users are **viewers**.

- `POST /auth/register` — Create account
- `POST /auth/login` — Get JWT token
- `GET /auth/me` — Current user profile
- Protected routes require `Authorization: Bearer <token>` header

## 📄 License

Apache 2.0 — see [LICENSE](LICENSE)

## 👨‍💻 Author

**Siddharth Kulkarni** — 3rd year IT, PCCOE Pune
GitHub: [@Siddharthk17](https://github.com/Siddharthk17)

## What It Does

Users define transformation pipelines in YAML. PipelineIQ executes them, tracks every column's journey through the pipeline as a directed acyclic graph, and exposes that graph via API for interactive visualization.

This solves the problem every data team faces:

> *"Where did this output column come from, and what transformations touched it on the way?"*

## Architecture

```
YAML Config → Parser → Validator → Runner → Step Executor → Lineage Recorder
                                                                    ↓
    FastAPI ← API Layer ← Database (SQLAlchemy) ← Lineage Graph (NetworkX)
                ↑
    Celery Worker ← Redis (broker + pub/sub for SSE)
```

### Key Design Decisions

- **Typed all the way down**: YAML is parsed into dataclass hierarchies, not dicts
- **Dispatch dict pattern**: Step execution uses `Dict[StepType, Callable]`, not if-elif
- **Dependency inversion**: Runner takes a progress callback, doesn't know about Redis
- **Fuzzy suggestions**: `ColumnNotFoundError` uses `difflib.get_close_matches` for typo hints
- **Pre-computed layouts**: React Flow graph is computed once and stored, not recomputed per API call

## Quick Start

```bash
# 1. Clone and start services
cp .env.example .env
docker-compose up --build

# 2. Health check
curl http://localhost:8000/health

# 3. Upload data files
curl -X POST http://localhost:8000/api/v1/files/upload \
  -F "file=@backend/sample_data/sales.csv"

# 4. Validate a pipeline
curl -X POST http://localhost:8000/api/v1/pipelines/validate \
  -H "Content-Type: application/json" \
  -d '{"yaml_config": "..."}'

# 5. Run a pipeline (returns immediately)
curl -X POST http://localhost:8000/api/v1/pipelines/run \
  -H "Content-Type: application/json" \
  -d '{"yaml_config": "..."}'

# 6. Stream progress via SSE
curl http://localhost:8000/api/v1/pipelines/{run_id}/stream

# 7. Get lineage graph
curl http://localhost:8000/api/v1/lineage/{run_id}

# 8. Trace column ancestry
curl "http://localhost:8000/api/v1/lineage/{run_id}/column?step=save_report&column=revenue_sum"
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check (DB + Redis) |
| POST | `/api/v1/files/upload` | Upload CSV/JSON file |
| GET | `/api/v1/files/` | List uploaded files |
| POST | `/api/v1/pipelines/validate` | Validate YAML config |
| POST | `/api/v1/pipelines/run` | Start async pipeline run |
| GET | `/api/v1/pipelines/{run_id}` | Get run details |
| GET | `/api/v1/pipelines/{run_id}/stream` | SSE progress stream |
| GET | `/api/v1/lineage/{run_id}` | React Flow graph |
| GET | `/api/v1/lineage/{run_id}/column` | Column ancestry |
| GET | `/api/v1/lineage/{run_id}/impact` | Impact analysis |

## Pipeline Step Types

| Step | Description | Lineage |
|------|-------------|---------|
| `load` | Load CSV/JSON file | Creates file + column nodes |
| `filter` | Row-level filtering (12 operators) | Passthrough columns |
| `select` | Column projection | Tracks dropped columns |
| `rename` | Column renaming | Maps old → new names |
| `join` | DataFrame merge (inner/left/right/outer) | Two-input edges, join key marking |
| `aggregate` | Group-by with aggregation functions | Group-by passthrough, new agg columns |
| `sort` | Row sorting | Passthrough columns |
| `save` | Save to output file | Creates output file node |

## Running Tests

```bash
cd pipelineiq
pip install -r backend/requirements.txt
pytest backend/tests/ -v
```

## Tech Stack

- **FastAPI** — async REST API with OpenAPI docs
- **Celery + Redis** — async pipeline execution with progress pub/sub
- **SQLAlchemy** — ORM with typed mapped columns
- **Pydantic** — request/response validation
- **NetworkX** — directed graph for lineage tracking
- **pandas** — DataFrame transformations

## Project Structure

```
pipelineiq/
├── backend/
│   ├── main.py              # FastAPI app factory
│   ├── config.py             # Pydantic BaseSettings
│   ├── database.py           # SQLAlchemy engine/session
│   ├── models.py             # ORM models
│   ├── schemas.py            # API schemas
│   ├── dependencies.py       # DI providers
│   ├── celery_app.py         # Celery configuration
│   ├── pipeline/
│   │   ├── exceptions.py     # Exception hierarchy
│   │   ├── parser.py         # YAML parser + validator
│   │   ├── lineage.py        # NetworkX lineage recorder
│   │   ├── steps.py          # Step executor
│   │   └── runner.py         # Pipeline orchestrator
│   ├── api/
│   │   ├── files.py          # File upload endpoints
│   │   ├── pipelines.py      # Pipeline run + SSE endpoints
│   │   ├── lineage.py        # Lineage query endpoints
│   │   └── router.py         # Router aggregation
│   ├── tasks/
│   │   └── pipeline_tasks.py # Celery task
│   ├── utils/
│   │   ├── string_utils.py   # Fuzzy matching, sanitization
│   │   └── time_utils.py     # Timing, formatting
│   ├── tests/
│   │   ├── conftest.py       # Shared fixtures
│   │   ├── test_parser.py    # Parser tests
│   │   ├── test_steps.py     # Step executor tests
│   │   └── test_lineage.py   # Lineage tests
│   └── sample_data/
│       ├── sales.csv
│       ├── customers.csv
│       ├── products.csv
│       ├── orders.csv
│       ├── simple_pipeline.yaml
│       ├── complex_pipeline.yaml
│       └── broken_pipeline.yaml
├── docker-compose.yml
├── Dockerfile
├── .env.example
└── README.md
```
