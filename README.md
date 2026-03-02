# PipelineIQ

A data pipeline orchestration engine with **automatic column-level lineage tracking**.

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
