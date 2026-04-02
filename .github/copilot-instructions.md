# Copilot Instructions — PipelineIQ

This file defines exactly how code is written in this codebase.
It does not repeat the universal production standards in `~/.copilot/instructions.md`.
It does not repeat the system overview, entity definitions, or API contracts in `AGENTS.md`.
It contains only what is specific to writing correct, consistent code in PipelineIQ.

**Read `AGENTS.md` completely before reading this file. Read this file completely before touching code.**

---

## The three-part contract — read this first

Every new pipeline step type is incomplete until all three of these simultaneously exist:

1. **Execution method** in `backend/pipeline/steps.py` → `StepExecutor._dispatch`
2. **Lineage recording method** in `backend/pipeline/lineage.py` → `LineageRecorder`
3. **Test** in `backend/tests/test_steps.py`

This is the product guarantee. PipelineIQ's entire value is column-level traceability.
A step with no lineage recording silently produces a broken lineage graph — no error,
no exception, just wrong output. A step with no test cannot be trusted.
The existing 9 step types (`load`, `filter`, `select`, `rename`, `join`, `aggregate`,
`sort`, `validate`, `save`) all satisfy this contract. New steps must too.

---

## Project file structure

```
pipelineiq/
├── backend/
│   ├── api/            ← 13 routers: router.py + 12 domain files
│   ├── pipeline/       ← Core engine: parser, runner, steps, lineage, validators,
│   │                     schema_drift, planner, versioning, exceptions
│   ├── services/       ← audit_service.py, webhook_service.py, notification_service.py
│   ├── tasks/          ← pipeline_tasks.py, webhook_tasks.py, schedule_tasks.py
│   ├── utils/          ← cache.py, rate_limiter.py, uuid_utils.py, string_utils.py
│   ├── alembic/        ← 8 migration revisions
│   ├── tests/          ← 206 tests across 14 files
│   ├── scripts/        ← seed_demo.py
│   ├── sample_data/    ← 4 CSVs + 3 YAML examples
│   ├── main.py         ← App factory, middleware, health, Sentry init
│   ├── config.py       ← Pydantic BaseSettings (55+ variables)
│   ├── models.py       ← ALL 14 SQLAlchemy models (single file, never split)
│   ├── schemas.py      ← ALL Pydantic schemas (single file, never split)
│   ├── metrics.py      ← ALL Prometheus metric definitions (single file)
│   ├── auth.py         ← JWT utilities, get_current_user, get_current_admin
│   ├── celery_app.py   ← Celery config + Redis TLS handling + Beat schedules
│   ├── database.py     ← SQLAlchemy engine + connection pool + get_db
│   └── dependencies.py ← Shared FastAPI dependencies
└── frontend/
    ├── app/            ← dashboard/, login/, register/ (Next.js App Router)
    ├── components/
    │   ├── layout/     ← TopBar, WidgetGrid, CommandPalette, KeybindingsModal, PresenceIndicator
    │   ├── widgets/    ← 8 widgets + WidgetShell + StepDAG
    │   ├── lineage/    ← LineageGraph, LineageSidebar, 4 custom ReactFlow nodes
    │   ├── ui/         ← shadcn/ui base components (NEVER MODIFY)
    │   └── theme/      ← ThemeSelector (7 themes), ThemeBuilder
    ├── hooks/          ← usePipelineRun, useKeybindings, useLineage, useTheme, useIsMobile
    ├── store/          ← widgetStore.ts, pipelineStore.ts, themeStore.ts, keybindingStore.ts
    ├── lib/            ← api.ts, auth-context.tsx, types.ts, constants.ts
    ├── __tests__/      ← 93 tests across 8 files
    └── middleware.ts   ← Auth redirect
```

---

## Backend — Python / FastAPI conventions

### Configuration — always use `settings`

`backend/config.py` is a Pydantic `BaseSettings` class with 55+ variables.
Never reach for `os.environ` or `os.getenv()` directly. Always use the `settings`
singleton:

```python
from backend.config import settings

# Correct
stored_path = settings.UPLOAD_DIR / stored_filename
max_steps = settings.MAX_PIPELINE_STEPS
expire_minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES

# Wrong
stored_path = Path("/app/uploads") / stored_filename  # Hardcoded, differs from production
max_steps = int(os.getenv("MAX_PIPELINE_STEPS", "50"))  # Duplicates config validation
```

When a new feature needs a configurable value, add it to `config.py` with a
sensible default. Group it with related settings. Document it in `AGENTS.md`
and `.env.example`.

### Database — SQLAlchemy 2.0 syntax only

All ORM models are in `backend/models.py` (348 lines, 14 models). New models
go here. Never create a model file elsewhere.

**Use SQLAlchemy 2.0 `Mapped`/`mapped_column` syntax exclusively:**

```python
# Correct — modern syntax
class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    status: Mapped[PipelineStatus] = mapped_column(
        SQLEnum(PipelineStatus), nullable=False, default=PipelineStatus.PENDING
    )
    yaml_config: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )

# Wrong — old syntax never used in new code
class PipelineRun(Base):
    id = Column(String(36), primary_key=True)
    status = Column(String, nullable=False)
```

**JSON columns always use `PgJSONB`:**

```python
from backend.models import PgJSONB  # Custom type defined in models.py

# Correct — maps to JSONB on PostgreSQL, JSON on SQLite
graph_data: Mapped[Optional[dict]] = mapped_column(PgJSONB, nullable=True)

# Wrong — only works on PostgreSQL
graph_data = mapped_column(JSONB, nullable=True)
```

**Relationships always declare `lazy="selectin"`:**

```python
# Correct — no implicit lazy loads
user: Mapped["User"] = relationship(back_populates="runs", lazy="selectin")
step_results: Mapped[list["StepResult"]] = relationship(
    back_populates="pipeline_run", cascade="all, delete-orphan", lazy="selectin"
)

# Wrong — lazy=True causes MissingGreenlet in async contexts
user: Mapped["User"] = relationship(back_populates="runs")
```

### Database sessions

Always use `get_db` from `backend/database.py`. Never create a session directly:

```python
from backend.database import get_db
from sqlalchemy.orm import Session

# Correct — dependency injection
@router.get("/{run_id}")
async def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(PipelineRun, as_uuid(run_id))
    ...

# Wrong — session never closed on exception
@router.get("/{run_id}")
async def get_run(run_id: str):
    db = SessionLocal()
    run = db.get(PipelineRun, run_id)
    ...
```

**Never query the database inside a loop. N+1 bugs are silent performance killers:**

```python
# Correct — single query for all IDs
file_ids = [step.file_id for step in config.steps if step.type == "load"]
files = (
    db.query(UploadedFile)
    .filter(UploadedFile.id.in_([as_uuid(fid) for fid in file_ids]))
    .all()
)
file_map = {str(f.id): f for f in files}

# Wrong — one query per step
for step in config.steps:
    if step.type == "load":
        file = db.query(UploadedFile).filter_by(id=step.file_id).first()
```

### UUID handling

All incoming UUID strings from path parameters or query parameters must be
converted with `as_uuid()` before any database operation. Raw UUID strings
may be uppercase or non-standard format and will fail silently:

```python
from backend.utils.uuid_utils import as_uuid, validate_uuid_format

# Correct — always convert
run = db.get(PipelineRun, as_uuid(run_id))

# For validation of format before querying
if not validate_uuid_format(run_id):
    raise HTTPException(status_code=422, detail="Invalid UUID format")

# Wrong — case-sensitive, format-sensitive
run = db.get(PipelineRun, run_id)
```

### Pydantic schemas — always in schemas.py

All request and response models live in `backend/schemas.py` (334 lines, 20+ models).
Never define a schema in a router file. Never return a raw dict where a schema exists.

**All fields must have `Field(description=...)`** — this generates the OpenAPI docs:

```python
# Correct
class PipelineRunRequest(BaseModel):
    yaml_config: str = Field(..., description="Complete YAML pipeline definition")
    name: str | None = Field(
        None, description="Optional display name for this run"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "yaml_config": "pipeline:\n  name: example\n  steps:\n    ...",
                "name": "My Sales Report"
            }
        }
    }

# Wrong — missing descriptions, missing example
class PipelineRunRequest(BaseModel):
    yaml_config: str
    name: str | None = None
```

**Use Pydantic v2 validators:**

```python
# Correct — @field_validator (Pydantic v2)
@field_validator("cron_expression")
@classmethod
def validate_cron(cls, v: str) -> str:
    try:
        croniter(v)
    except ValueError as exc:
        raise ValueError(f"Invalid cron expression: {v}") from exc
    return v

# Wrong — @validator is Pydantic v1
@validator("cron_expression")
def validate_cron(cls, v):
    ...
```

### Error handling — raise typed exceptions, never return errors

The error model is raise-based. All domain errors come from the 14-class hierarchy
in `backend/pipeline/exceptions.py`. The global handler in `main.py` converts them
to structured responses with `error_code`, `message`, and `request_id`.

```python
from backend.pipeline.exceptions import (
    PipelineNotFoundError,
    PipelineExecutionError,
    ColumnNotFoundError,
    InvalidStepTypeError,
)

# Correct — typed exception with context
run = db.get(PipelineRun, as_uuid(run_id))
if run is None:
    raise PipelineNotFoundError(run_id=run_id)

# Wrong — bypasses global handler, loses error_code
if run is None:
    return JSONResponse(status_code=404, content={"detail": "not found"})

# Wrong — generic exception becomes 500 with no useful error code
if run is None:
    raise ValueError(f"Run {run_id} not found")
```

**The exception hierarchy (all in `backend/pipeline/exceptions.py`, 443 lines):**

Configuration errors (raised during YAML parsing):
- `PipelineConfigError` (base)
- `InvalidStepTypeError` — includes fuzzy-matched suggestion via `difflib.get_close_matches`
- `ColumnNotFoundError` — includes available columns and fuzzy match
- `InvalidOperatorError`
- `InvalidJoinTypeError`
- `StepNameConflictError`
- `CircularDependencyError`
- `MissingInputError`

Runtime errors (raised during execution):
- `PipelineExecutionError` (base)
- `StepExecutionError`
- `FileNotFoundError`
- `DataValidationError`
- `PipelineNotFoundError`
- `PermissionDeniedError`

When adding a new error condition, add the exception class to `exceptions.py` first.
Give it a specific `error_code` string — the frontend catches by this code.

### Logging — structured with context, never print

```python
from backend.utils.logger import logger   # Always import this specific logger

# Correct — structured key=value pairs, searchable in Sentry
logger.info("pipeline_execution_started",
    run_id=str(run_id), user_id=str(user_id), step_count=len(config.steps))
logger.warning("filter_produced_empty_result",
    run_id=str(run_id), step=step_name, input_rows=rows_in)
logger.error("step_execution_failed",
    run_id=str(run_id), step=step_name, error=str(e), user_id=str(user_id))

# Wrong — unstructured, cannot be filtered in Sentry
print(f"Pipeline {run_id} started")
import logging; logging.error("step failed")
```

Every `error` log must include: `run_id`, `step` (if applicable), `error` (exact
exception string), `user_id`. These four fields are what make Sentry errors findable.

**Never log:** `yaml_config` content, file content, database connection strings,
JWT tokens, any `os.environ` value that could be a secret.

### Request IDs

Every request gets a `request_id` injected by middleware in `main.py`.
Include it in logs for multi-operation endpoints:

```python
request_id = request.headers.get("X-Request-ID", "unknown")
logger.info("pipeline_run_queued",
    run_id=str(run.id), request_id=request_id, user_id=str(user.id))
```

The `X-Request-ID` appears in all responses and in Sentry error reports.

### Prometheus metrics — metrics.py only

All metric definitions live in `backend/metrics.py`. Defining a `Counter`,
`Histogram`, or `Gauge` anywhere else causes a circular import when `main.py`
initialises the Prometheus instrumentator:

```python
# In backend/metrics.py — define here, nowhere else
from prometheus_client import Counter, Histogram, Gauge

PIPELINE_EXECUTIONS_TOTAL = Counter(
    "pipelineiq_pipeline_executions_total",
    "Total pipeline executions",
    ["status"]   # Labels: "completed", "failed", "cancelled"
)

# In the service/task that uses it — import only
from backend.metrics import PIPELINE_EXECUTIONS_TOTAL
PIPELINE_EXECUTIONS_TOTAL.labels(status="completed").inc()
```

Naming convention: all metrics prefixed `pipelineiq_`.

Existing metrics (do not duplicate):
- `pipelineiq_pipeline_runs_total` — Counter, label: `status`
- `pipelineiq_pipeline_duration_seconds` — Histogram
- `pipelineiq_active_workers` — Gauge
- `pipelineiq_files_uploaded_total` — Counter
- `pipelineiq_celery_queue_depth` — Gauge

### Audit logging — required for all state changes

Every state-changing API handler calls `log_action` from
`backend/services/audit_service.py`. The audit table is immutable at the
database level (PostgreSQL trigger). Never skip audit logging for write operations:

```python
from backend.services.audit_service import log_action

# Required after every mutation
await log_action(
    db=db,
    user_id=current_user.id,
    action="pipeline.run",              # Format: resource.action
    resource_type="pipeline_run",
    resource_id=run.id,
    ip_address=request.client.host,
    user_agent=request.headers.get("User-Agent"),
    details={"pipeline_name": run.name, "step_count": len(config.steps)}
)
```

State-changing actions that require audit logging:
`file.upload`, `file.delete`, `pipeline.run`, `pipeline.cancel`,
`user.register`, `user.login`, `role.change`, `webhook.create`,
`webhook.delete`, `schedule.create`, `schedule.delete`,
`permission.grant`, `permission.revoke`

### Input sanitization

Pipeline and step names from user input must be sanitized before storage
or filesystem use:

```python
from backend.utils.string_utils import sanitize_pipeline_name, sanitize_step_name

safe_name = sanitize_pipeline_name(request.name)
safe_step = sanitize_step_name(step.name)
```

Never use raw user-supplied names in filesystem paths, Redis keys, or database
queries without sanitization first.

### Rate limiting

Every new public endpoint requires a rate limiter dependency from
`backend/utils/rate_limiter.py`:

```python
from backend.utils.rate_limiter import (
    rate_limit_pipeline_execution,  # 10/min per user
    rate_limit_file_upload,          # 30/min per user
    rate_limit_validation,           # 60/min per user
    rate_limit_read,                 # 120/min per user
    rate_limit_auth,                 # 5/min per IP
)

# Applied as FastAPI dependency
@router.post("/run", dependencies=[Depends(rate_limit_pipeline_execution)])
async def run_pipeline(...):
    ...

@router.get("/", dependencies=[Depends(rate_limit_read)])
async def list_runs(...):
    ...
```

### Pagination — all list endpoints

Every endpoint returning a collection is paginated. No endpoint returns all records.
The pattern (`page`, `limit`, max 200) is enforced consistently:

```python
from sqlalchemy import select, func

@router.get("/")
async def list_pipeline_runs(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    limit: int = Query(50, ge=1, le=200, description="Records per page"),
    status_filter: PipelineStatus | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    offset = (page - 1) * limit
    query = select(PipelineRun).where(PipelineRun.user_id == current_user.id)
    if status_filter:
        query = query.where(PipelineRun.status == status_filter)
    runs = db.scalars(query.offset(offset).limit(limit)).all()
    total = db.scalar(
        select(func.count())
        .select_from(PipelineRun)
        .where(PipelineRun.user_id == current_user.id)
    )
    return PaginatedRunsResponse(items=runs, total=total, page=page, limit=limit)
```

---

## Backend — pipeline engine conventions

### YAML parsing — always use parse_pipeline_config()

Never call `yaml.safe_load()` directly on user-supplied YAML:

```python
from backend.pipeline.parser import parse_pipeline_config
from backend.pipeline.exceptions import PipelineConfigError

# Correct — validates 13 rules, returns typed PipelineConfig
try:
    config = parse_pipeline_config(yaml_string, registered_file_ids=file_id_set)
except PipelineConfigError as e:
    raise HTTPException(status_code=422, detail=e.to_dict())

# Wrong — raw dict, no validation, no typed errors, no fuzzy suggestions
import yaml
config = yaml.safe_load(yaml_string)
```

The parser returns a `PipelineConfig` dataclass with `steps: list[StepConfig]`.
Each step is a typed subclass (`FilterStepConfig`, `JoinStepConfig`, etc.).
The YAML never passes the parser boundary as a raw dict.

### StepExecutor — dispatch dict, never if/elif

Add new step types to `StepExecutor._dispatch` in `backend/pipeline/steps.py`:

```python
class StepExecutor:
    def __init__(self):
        self._dispatch: dict[str, Callable] = {
            "load":      self._execute_load,
            "filter":    self._execute_filter,
            "select":    self._execute_select,
            "rename":    self._execute_rename,
            "join":      self._execute_join,
            "aggregate": self._execute_aggregate,
            "sort":      self._execute_sort,
            "validate":  self._execute_validate,
            "save":      self._execute_save,
            # New step type — add here, never in an if/elif chain
            "pivot":     self._execute_pivot,
        }

    def execute(self, step: StepConfig, df: pd.DataFrame) -> pd.DataFrame:
        handler = self._dispatch.get(step.type)
        if handler is None:
            raise InvalidStepTypeError(step.type, list(self._dispatch.keys()))
        return handler(step, df)
```

Every execution method signature:
```python
def _execute_{type}(self, step: {Type}StepConfig, df: pd.DataFrame) -> pd.DataFrame:
    ...
```

The method receives the current DataFrame and returns the transformed DataFrame.
No database calls. No Redis calls. No external service calls. Pure Pandas.

### Pandas memory management in steps

The Celery worker has bounded RAM. Release DataFrames immediately after use:

```python
# Correct — original released before returning
filtered_df = df[df[column].between(lower, upper)].copy()
del df
return filtered_df

# Also correct for chained operations
result = (
    df.groupby(group_by)[agg_columns]
    .agg(agg_functions)
    .reset_index()
)
del df
return result

# Wrong — original held in scope through the return
return df[df[column].between(lower, upper)].copy()
```

Specify dtypes when reading CSV files to avoid Pandas defaulting to `object`:

```python
# Correct — explicit dtypes prevent 3× memory overhead
df = pd.read_csv(file_path, dtype=detected_dtypes)

# Wrong — all columns become object dtype
df = pd.read_csv(file_path)
```

For file previews, use `nrows` — never load the full file:

```python
# Correct — reads only N rows regardless of file size
preview_df = pd.read_csv(file_path, nrows=settings.PREVIEW_ROWS)

# Wrong — loads entire file (could be 50MB) for a preview of 100 rows
df = pd.read_csv(file_path)
preview = df.head(settings.PREVIEW_ROWS)
```

### LineageRecorder — every new step needs a recording method

`LineageRecorder` in `backend/pipeline/lineage.py` (634 lines) maintains the
NetworkX DiGraph. Every new step type needs a corresponding recording method:

```python
class LineageRecorder:
    def record_pivot_step(
        self,
        step_name: str,
        input_columns: list[str],
        output_columns: list[str],
        pivot_on: str,
        value_column: str
    ) -> None:
        # Add output column nodes
        for col in output_columns:
            self.graph.add_node(
                f"{step_name}.{col}",
                type="column",
                step=step_name
            )
        # Add edges from input columns that contributed to each output
        for in_col in input_columns:
            for out_col in output_columns:
                self.graph.add_edge(
                    in_col,
                    f"{step_name}.{out_col}",
                    transformation="pivot",
                    pivot_on=pivot_on,
                    value_column=value_column
                )
```

**Node naming convention:**
- Source file: `"file::{file_id}"`
- Step column: `"{step_name}.{column_name}"`
- Output file: `"output::{step_name}::{filename}"`

A step that produces output columns without recording edges creates orphan
nodes in the React Flow visualization and breaks ancestry queries. There is
no error — the graph is silently incomplete.

### PipelineRunner — ProgressCallback, no infrastructure

The runner (`backend/pipeline/runner.py`) has zero infrastructure dependencies.
All side effects are injected:

```python
# ProgressCallback is a protocol — the caller decides what to do with progress
ProgressCallback = Callable[[StepProgressEvent], None]

class PipelineRunner:
    def __init__(self, config: PipelineConfig, progress_callback: ProgressCallback):
        self.config = config
        self.progress_callback = progress_callback

# In the Celery task — the callback publishes to Redis
def execute_pipeline_task(run_id: str):
    def on_progress(event: StepProgressEvent):
        redis_client.publish(f"pipeline_progress:{run_id}", event.model_dump_json())

    runner = PipelineRunner(config=config, progress_callback=on_progress)
    runner.execute()

# In tests — the callback records events for assertion
recorded_events: list[StepProgressEvent] = []
runner = PipelineRunner(config=config, progress_callback=recorded_events.append)
runner.execute()
assert len(recorded_events) == expected_step_count
```

Never add a Redis import to `runner.py`, `steps.py`, or `lineage.py`.
These three files must remain infrastructure-independent.

---

## Backend — Celery conventions

### Task structure

Tasks are defined in `backend/tasks/`. Never define a task in a router or service:

```python
from backend.celery_app import celery_app

@celery_app.task(
    bind=True,
    name="pipeline.execute",
    acks_late=True,
    max_retries=3,
    default_retry_delay=60
)
def execute_pipeline_task(self, run_id: str) -> dict:
    ...
```

**`acks_late=True`** — the task is acknowledged only after completion, not on pickup.
This prevents task loss if the worker crashes mid-execution.

**`prefetch_multiplier=1`** — configured in `celery_app.py`, not per-task.
Never change this to a higher value without profiling. Each task can load
gigabyte-scale DataFrames. Prefetching more than one task risks OOM.

### Webhook delivery isolation

HTTP calls to external webhook endpoints run in `backend/tasks/webhook_tasks.py`
as a separate Celery task. Never add external HTTP calls to `execute_pipeline_task`:

```python
# Correct — dispatched as separate background task after pipeline completion
from backend.tasks.webhook_tasks import deliver_webhook_task
deliver_webhook_task.delay(webhook_id=str(webhook.id), run_id=str(run.id))

# Wrong — external HTTP call in the primary pipeline task blocks completion
async with httpx.AsyncClient() as client:
    await client.post(webhook.url, json=payload)  # Inside execute_pipeline_task
```

### Redis — SCAN, not KEYS

The cache utility in `backend/utils/cache.py` provides pattern-based deletion.
Always use `cache_delete_pattern()` — never raw `KEYS *pattern*`:

```python
from backend.utils.cache import cache_delete_pattern

# Correct — non-blocking, uses SCAN cursor iteration
await cache_delete_pattern(f"lineage:{run_id}:*")

# Wrong — KEYS blocks Redis, dangerous under load
keys = redis_client.keys(f"lineage:{run_id}:*")
redis_client.delete(*keys)
```

All cache operations catch `RedisError` and fall through gracefully:

```python
from backend.utils.cache import cache_get, cache_set

# Correct pattern — graceful degradation if Redis is down
try:
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
except RedisError:
    pass  # Fall through to database query

result = db.execute(expensive_query)
try:
    cache_set(cache_key, result, ttl=3600)
except RedisError:
    pass  # Cache failure does not break the API
return result
```

---

## Backend — security conventions

### File upload paths

Never use a user-supplied filename as a filesystem path:

```python
import uuid
from backend.config import settings

# Correct — UUID path, user filename stored separately for display
ext = "csv" if file.content_type == "text/csv" else "json"
stored_filename = f"{uuid.uuid4()}.{ext}"
stored_path = settings.UPLOAD_DIR / stored_filename

# Wrong — path traversal vulnerability
stored_path = settings.UPLOAD_DIR / file.filename
# Attacker sends filename: "../../etc/passwd" or "../backend/auth.py"
```

### Webhook HMAC signing

All outbound webhooks are signed. Use the service, never reimplement signing:

```python
from backend.services.webhook_service import deliver_webhook

# Correct — HMAC-SHA256 signing included
await deliver_webhook(webhook=webhook, payload=event_data, db=db)

# Wrong — unsigned webhook, no X-PipelineIQ-Signature header
async with httpx.AsyncClient() as client:
    await client.post(webhook.url, json=event_data)
```

`WebhookResponse` schema returns `has_secret: bool`, never the raw secret:

```python
# Correct schema
class WebhookResponse(BaseModel):
    id: str
    name: str
    url: str
    has_secret: bool    # True if a secret is configured — raw secret never exposed
    events: list[str]
    is_active: bool
```

### JWT tokens

JWT tokens use HS256 signed with `settings.SECRET_KEY`. The `get_current_user`
dependency rejects expired tokens with a 401.

Never add `options={"verify_exp": False}` to any JWT decode call. Expired tokens
must not be accepted under any circumstance.

Never hardcode the `SECRET_KEY`. The `config.py` startup validator blocks
production startup if the key matches a known placeholder.

### YAML size limits

`parse_pipeline_config()` enforces `settings.MAX_PIPELINE_STEPS`. Never call
`yaml.safe_load()` before size validation. The validation happens inside the
parser — do not duplicate it in the router.

---

## Backend — database migrations

Every change to `backend/models.py` requires an Alembic migration:

```bash
cd backend
alembic revision --autogenerate -m "add_pivot_step_output_column_to_step_results"

# ALWAYS review the generated file — autogenerate makes mistakes on:
# - server_default changes
# - Index renames
# - PostgreSQL-specific constraint changes

# Test the round-trip before merging:
alembic upgrade head
alembic downgrade -1          # Verify downgrade works
alembic upgrade head          # Re-apply
```

**Every migration must have a working `downgrade()` function.** A migration with
`pass` in `downgrade()` is a one-way door. The downgrade is the rollback path
during a failed deployment.

**Adding a NOT NULL column to a table with existing rows is a 3-step process:**

```python
# Step 1 — Add nullable (migration 1, deploy 1)
op.add_column("step_results",
    sa.Column("output_format", sa.String(10), nullable=True))

# Application code writes the value for all new records
# Existing records remain null

# Step 2 — Backfill existing rows (migration 2, between deploys)
op.execute("UPDATE step_results SET output_format = 'csv' WHERE output_format IS NULL")

# Step 3 — Add NOT NULL constraint (migration 3, deploy 2)
op.alter_column("step_results", "output_format", nullable=False)
```

Attempting this in a single migration fails on the NOT NULL check against
existing rows.

**Naming conventions:**
- Tables: `snake_case_plural` (e.g., `pipeline_runs`, `step_results`)
- Columns: `snake_case_singular` (e.g., `created_at`, `user_id`)
- New tables always include `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- UUID primary keys always: `id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)`

**Use `datetime.now(timezone.utc)` — `datetime.utcnow()` is deprecated:**

```python
from datetime import datetime, timezone

# Correct
token_expiry = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

# Wrong — deprecated
token_expiry = datetime.utcnow() + timedelta(minutes=...)
```

---

## Backend — testing conventions

### Test file structure

```
backend/tests/
├── conftest.py           ← test_db, auth_client, sample_sales_df, pipeline YAML builders
├── test_api.py           ← 34 endpoint integration tests
├── test_steps.py         ← 25 StepExecutor unit tests
├── test_validators.py    ← 22 validation check tests
├── test_parser.py        ← 18 YAML parsing tests
├── test_lineage.py       ← 18 lineage graph tests
├── test_auth.py          ← 17 auth tests
├── test_planner.py       ← 15 dry-run tests
├── test_versioning.py    ← 12 version/diff tests
├── test_schema_drift.py  ← 10 drift detection tests
├── test_webhooks.py      ← 9 webhook tests
├── test_caching.py       ← 8 cache tests
├── test_security.py      ← 7 security tests
├── test_rate_limiting.py ← 6 rate limit tests
└── test_performance.py   ← 5 performance tests
```

Test database is always SQLite via `conftest.py`'s `test_db` fixture.
Never use the PostgreSQL connection in tests.

**Test naming — describes exact behaviour:**

```python
# Correct — describes what should happen
def test_filter_step_removes_rows_where_condition_is_false():
    ...

def test_pipeline_run_status_is_pending_immediately_after_creation():
    ...

# Regression test naming — names the bug it prevents from returning
def test_aggregate_step_does_not_raise_on_empty_dataframe():
    # Regression: StepExecutor raised KeyError when aggregate input was empty
    ...

def test_schema_drift_detector_handles_file_with_no_columns():
    # Regression: IndexError on zero-column CSV
    ...
```

**Every new feature has tests. Every bug fix has a regression test written
before the fix:**

1. Write a test that reproduces the bug
2. Confirm the test fails
3. Write the fix
4. Confirm the test passes
5. That test is permanent

---

## Frontend — TypeScript / React conventions

### API client — always use apiClient

All API calls go through `frontend/lib/api.ts` (252 lines).
Never use raw `fetch` in a component, hook, or store:

```typescript
import { apiClient, ApiError } from "@/lib/api"

// Correct
const run = await apiClient.pipelines.run({ yaml_config: yaml, name })
const files = await apiClient.files.list({ page: 1, limit: 50 })
const lineage = await apiClient.lineage.getGraph(runId)

// Wrong — raw fetch bypasses auth, error parsing, 401 redirect
const response = await fetch("/api/v1/pipelines/run", {
  method: "POST",
  headers: { "Authorization": `Bearer ${token}` },
  body: JSON.stringify({ yaml_config: yaml })
})
```

**Always catch `ApiError` specifically:**

```typescript
try {
  const result = await apiClient.pipelines.run(request)
  setActiveRun(result.run_id)
} catch (error) {
  if (error instanceof ApiError) {
    // Handle by error code — the backend provides specific codes for specific conditions
    if (error.code === "INVALID_STEP_TYPE") {
      setEditorError(`Unknown step type: ${error.details?.step_type}. Did you mean: ${error.details?.suggestion}?`)
      return
    }
    if (error.code === "COLUMN_NOT_FOUND") {
      setEditorError(`Column not found: ${error.details?.column}. Available: ${error.details?.available?.join(", ")}`)
      return
    }
    toast.error(error.message)
    return
  }
  throw error  // Re-throw — unexpected errors must not be swallowed
}
```

**`ApiError` properties:** `code: string`, `message: string`, `status: number`, `details?: object`

### Type definitions — always update types.ts

When `backend/schemas.py` changes, `frontend/lib/types.ts` must be updated
to match. TypeScript types are the frontend's API contract documentation:

```typescript
// frontend/lib/types.ts

export interface PipelineRun {
  id: string
  name: string
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"
  yaml_config: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  total_rows_in: number | null
  total_rows_out: number | null
  duration_ms: number | null
  step_results: StepResult[]
}
```

A mismatch between `schemas.py` and `types.ts` is a runtime error waiting to happen.
TypeScript will not catch it — the types are just documentation until the API is called.

### Zustand stores

All cross-component state lives in Zustand stores. Local `useState` is for ephemeral
component-local state only:

```typescript
// Cross-component state — Zustand store
import { usePipelineStore } from "@/store/pipelineStore"
const { activeRunId, setActiveRunId, yamlContent, setYamlContent } = usePipelineStore()

// Component-local state — useState
const [isMenuOpen, setIsMenuOpen] = useState(false)
const [inputValue, setInputValue] = useState("")
```

**What belongs in stores:**
- Active run ID and run data
- YAML editor content (shared between editor widget and run button)
- Widget layout configuration
- Theme selection
- Keyboard shortcut registrations
- User preferences

**What never belongs in stores:**
- Large arrays of row objects from file data
- Full CSV/JSON file content
- API response data that changes frequently (use React Query or local state)
- Form validation state

All stores use `persist` middleware. The persistence config uses `partialize`
to exclude transient fields:

```typescript
create<PipelineState>()(
  persist(
    (set, get) => ({
      activeRunId: null,
      yamlContent: "",
      runData: null,
      ...
    }),
    {
      name: "pipelineiq-pipeline-store",
      partialize: (state) => ({
        yamlContent: state.yamlContent,
        // Exclude runData — it's transient, fetched fresh each session
      })
    }
  )
)
```

### SSE streaming — usePipelineRun hook only

Never create a raw `EventSource` in a component:

```typescript
// Correct — managed hook with exponential backoff reconnection
import { usePipelineRun } from "@/hooks/usePipelineRun"
const { events, isConnected, connectionError } = usePipelineRun(runId)

// Wrong — raw EventSource bypasses backoff, cleanup, and error handling
useEffect(() => {
  const es = new EventSource(`/api/v1/pipelines/${runId}/stream`)
  es.onmessage = (e) => setEvents(prev => [...prev, JSON.parse(e.data)])
  return () => es.close()
}, [runId])
```

The `usePipelineRun` hook implements:
- `EventSource` connection to `/api/v1/pipelines/{runId}/stream`
- Exponential backoff: 1s → 2s → 4s → 8s → 16s
- Maximum 5 retries before surfacing connection error
- Cleanup on component unmount

SSE events: `step_started`, `step_completed`, `pipeline_completed`

### Keyboard shortcuts — keybindingStore.ts

Global keyboard shortcuts are registered through `keybindingStore.ts`.
Never use raw `addEventListener("keydown", ...)` for global shortcuts:

```typescript
import { useKeybindingStore } from "@/store/keybindingStore"

const { registerBinding, unregisterBinding } = useKeybindingStore()

useEffect(() => {
  const id = registerBinding({
    key: "mod+shift+r",           // mod = Cmd on Mac, Ctrl on Windows/Linux
    action: "run_active_pipeline",
    description: "Execute the pipeline in the active editor",
    scope: "global",
    handler: () => apiClient.pipelines.run({ yaml_config: yamlContent })
  })
  return () => unregisterBinding(id)  // Always clean up on unmount
}, [yamlContent])
```

Before registering a new shortcut, verify it does not conflict with existing
Vim-style bindings (`j`/`k`/`h`/`l` for navigation) or application bindings
already in the store.

### Themes — 7 total, both files must be updated

There are 7 built-in themes. Both `ThemeSelector.tsx` and `CommandPalette.tsx`
maintain independent arrays of theme names. When adding a new theme, both files
must be updated:

```typescript
// In ThemeSelector.tsx — update BUILT_IN_THEMES array
const BUILT_IN_THEMES = [
  "pipelineiq-dark",
  "pipelineiq-light",
  "nord",
  "dracula",
  "solarized-dark",
  "gruvbox-dark",
  "monokai",
  "new-theme-name",  // Add here
]

// Also update CommandPalette.tsx — themes array
const themes = [
  "pipelineiq-dark",
  "pipelineiq-light",
  ...
  "new-theme-name",  // Add here too
]
```

Theme naming: lowercase, hyphenated, `pipelineiq-` prefix for custom themes.

### TypeScript — strict mode, no `any`

`tsconfig.json` has `"strict": true`. Never disable strict checks.
`any` is only used for genuinely polymorphic external API payloads and must
be narrowed immediately through a type guard:

```typescript
// Acceptable — external payload, narrowed immediately
const rawPayload: unknown = await response.json()
if (!isStepProgressEvent(rawPayload)) {
  throw new ApiError("INVALID_SSE_PAYLOAD", "Unexpected SSE message shape", 0)
}
const event = rawPayload  // Now typed as StepProgressEvent

// Wrong — any with no narrowing
const event: any = await response.json()
event.step_name  // No type safety, runtime error risk
```

---

## Frontend — testing conventions

```
frontend/__tests__/
├── setup.ts              ← Test environment setup (jsdom, RTL matchers)
├── api.test.ts           ← 26 tests: fetchWithAuth, all API functions, 401 redirect
├── stores.test.ts        ← 26 tests: all 4 Zustand stores
├── pages.test.tsx        ← 12 tests: login, register forms
├── widgets.test.tsx      ← 11 tests: QuickStats, FileUpload, RunHistory, FileRegistry
├── utils.test.ts         ← 7 tests: cn(), constants
├── middleware.test.ts    ← 4 tests: auth redirect
├── auth-context.test.tsx ← 4 tests: AuthProvider
└── hooks.test.ts         ← 3 tests: layout toggle, workspace switching
```

Test behaviour — what the user sees and does — not implementation details:

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

// Wrong — tests implementation details
it("calls setError when API returns 401", () => {
  const setError = vi.fn()
  render(<LoginPage setError={setError} />)
  ...
  expect(setError).toHaveBeenCalledWith("invalid credentials")
})
```

---

## What to always do

- [ ] Update `backend/schemas.py` before changing any API contract
- [ ] Create an Alembic migration before deploying any `backend/models.py` change
- [ ] Write a working `downgrade()` function for every migration — test it with `alembic downgrade -1`
- [ ] Add a lineage recording method in `LineageRecorder` for every new step type
- [ ] Add the new step to `StepExecutor._dispatch`
- [ ] Add tests for the new step in `test_steps.py`
- [ ] Call `log_action()` in every state-changing API handler
- [ ] Define new Prometheus metrics in `backend/metrics.py` only
- [ ] Apply a rate limiter dependency to every new public endpoint
- [ ] Use `Field(description=...)` on every Pydantic schema field
- [ ] Register new global keyboard shortcuts in `keybindingStore.ts`
- [ ] Update `frontend/lib/types.ts` when backend schemas change
- [ ] Use `as_uuid()` on all UUID path parameters before database queries
- [ ] Sanitize pipeline and step names through `backend/utils/string_utils.py`
- [ ] Use `cache_delete_pattern()` not raw `KEYS` for Redis pattern operations
- [ ] Use `datetime.now(timezone.utc)` not `datetime.utcnow()`
- [ ] Use `PgJSONB` not `JSONB` or `JSON` for JSON columns

---

## What to never do

- **Never** define an ORM model outside `backend/models.py`
- **Never** define a Pydantic schema outside `backend/schemas.py`
- **Never** define Prometheus metrics outside `backend/metrics.py` — circular import
- **Never** use `print()` for logging — use `logger`
- **Never** import `backend/main.py` into any other module — circular import
- **Never** use `yaml.safe_load()` directly on user YAML — use `parse_pipeline_config()`
- **Never** use the user's `original_filename` as a filesystem path — path traversal
- **Never** add a step type without the lineage recording method — silent graph corruption
- **Never** add a step type without tests — untested transformation ships to production
- **Never** call Redis, the database, or external services from `PipelineRunner`, `StepExecutor`, or `LineageRecorder`
- **Never** query the database inside a loop — N+1 bug
- **Never** use `datetime.utcnow()` — deprecated since Python 3.12
- **Never** use `KEYS *pattern*` in Redis — use `SCAN` via `cache_delete_pattern()`
- **Never** return a raw webhook secret in an API response — return `has_secret: bool`
- **Never** set `verify_exp: False` in JWT decode — expired tokens become permanent
- **Never** add a NOT NULL column to a populated table in a single migration step
- **Never** write a migration without a tested `downgrade()` function
- **Never** call `pdf.safe_load()` without size validation — use the parser which enforces `MAX_PIPELINE_STEPS`
- **Never** use `any` in TypeScript without immediate type narrowing
- **Never** create raw `EventSource` in components — use `usePipelineRun` hook
- **Never** call the backend API with raw `fetch` — use `apiClient` from `lib/api.ts`
- **Never** put large data arrays in Zustand state — browser memory exhaustion
- **Never** update the theme list in only one file — `ThemeSelector.tsx` and `CommandPalette.tsx` both maintain it
- **Never** skip the full test suite before declaring a change complete
- **Never** re-add `@google/genai`, `firebase-tools`, `aioredis`, or `aiofiles` — removed with specific reasons
- **Never** enable `/docs` or `/redoc` in production (`ENVIRONMENT=production` disables them automatically)

---

## Before declaring done

```bash
# Backend
cd backend
pytest tests/ -v                              # All 206+ tests pass, zero new failures
python -m py_compile main.py                  # No syntax errors in main

# Frontend
cd frontend
npx tsc --noEmit                              # Zero TypeScript errors
npm run lint                                  # Zero ESLint errors
npm run test                                  # All 93+ tests pass

# Integration
docker compose up --build -d                  # All 9 services start cleanly
curl http://localhost/health                  # Returns 200 {"status": "healthy", "db": "ok", "redis": "ok"}
```

**Per-feature checklist:**

- New step type: `StepExecutor._dispatch`, `LineageRecorder` method, `test_steps.py` test all exist
- Schema change: migration created and committed, `downgrade()` tested
- API contract change: `schemas.py` first, router second, `frontend/lib/types.ts` updated
- New endpoint: rate limiter applied, `log_action()` called, `Field(description=...)` on all schema fields, pagination if returns a list
- New metric: defined in `backend/metrics.py`, `pipelineiq_` prefix, correct type (Counter/Histogram/Gauge)
- New keyboard shortcut: registered in `keybindingStore.ts`, no conflicts with existing bindings, documented in help modal
- Memory check: the change does not cause the Celery worker to exceed available RAM under a 100k-row test dataset
