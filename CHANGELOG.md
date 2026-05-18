# Changelog

All notable changes to PipelineIQ are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/)

## [9.8.16] — Week 16: Redpanda Unified Streaming

*Note: This release transforms PipelineIQ from a batch-only system into a unified batch and streaming platform. Using the exact same YAML pipeline format, users can now build real-time streaming pipelines. It also resolves Bottleneck #15 by introducing an 8-partition Redpanda architecture for high-throughput parallel consumption.*

### Performance & Scalability
- **Redpanda Streaming Engine (Bottleneck #15)** — Replaced the single-consumer bottleneck with a high-throughput parallel architecture. All topics are now automatically created with 8 partitions by default, and a dedicated `worker-streaming` Celery service runs with `concurrency=8`. Kafka's partition assignment protocol automatically distributes 1 partition per worker, achieving an 8x throughput increase over the single-consumer baseline.
- **Lightweight Broker** — Integrated Redpanda (a C++, Kafka-compatible broker) instead of JVM-based Kafka. This keeps the entire streaming infrastructure memory footprint under 1.2GB, making it safe for single-node and laptop deployments while maintaining full `confluent-kafka` client compatibility.

### Added
- **Streaming Daemon Task** — Introduced a new long-running Celery execution mode (`tasks.run_streaming_pipeline`) on a dedicated `streaming` queue. Unlike batch tasks, this daemon runs indefinitely with no time limits and `acks_late=True`, polling micro-batches, executing the SmartExecutor, and publishing results in a continuous loop.
- **2 New Pipeline Step Types (Total: 19)**:
  - `stream_consume`: Reads micro-batches from a Redpanda topic with configurable `batch_size`, `batch_timeout_ms`, `consumer_group`, and `deserialize` formats (JSON/CSV).
  - `stream_publish`: Writes processed DataFrame results back to a Redpanda topic with configurable `serialize` formats and `key_column` routing for guaranteed ordering.
- **Dead Letter Queue (DLQ) System** — Failed micro-batches no longer crash the pipeline. They are automatically routed to a `{topic}.dlq` topic (with a 7-day retention policy) using strong delivery guarantees (`acks='all'`). Messages are enriched with diagnostic headers including `x-error`, `x-original-topic`, and `x-failed-at`.
- **Streaming Control & DLQ API** — Added full control APIs (`POST /api/streaming/runs/{id}/pause|resume|stop`) to gracefully manage the daemon state. Added endpoints to inspect DLQ messages (`GET /api/streaming/topics/{topic}/dlq`) and replay them back to the source topic for reprocessing (`POST /api/streaming/topics/{topic}/dlq/replay`).
- **Streaming Stats Tracking** — Added Alembic migration `0012_add_streaming_stats.py` to create the `streaming_stats` table. Tracks `batches_processed`, `messages_processed`, `messages_failed`, `messages_dlq`, and `throughput_per_sec` in real-time.
- **Frontend Streaming UI** — Added the `StreamingRunCard` component displaying live throughput, processed counts, and DLQ warnings, along with Pause/Resume/Stop controls. Added dynamic configuration panels for the two new streaming step types in the visual builder.
- **Test Suite Expansion** — Added 15 new backend unit tests covering Redpanda client constants, deserialization, task configuration, and DLQ routing. Added 4 new Playwright E2E tests for the streaming UI and topic API.

### Changed
- **Pipeline Run Status Machine** — Expanded `pipeline_runs.status` to include `streaming_active`, `streaming_paused`, and `streaming_stopped` to support the lifecycle of long-running daemon tasks.

---

## [9.1.0] — Week 15: WebAssembly UDF Sandbox

*Note: This release adds the most technically sophisticated security boundary in the project. It introduces the `wasm_compute` step, allowing users to execute custom user-defined functions (UDFs) written in Rust (or any Wasm-compilable language) at near-native C speed. The execution is strictly isolated inside a WebAssembly virtual machine with zero access to the host filesystem or network, and strict CPU budgets to prevent infinite loops.*

### Security & Isolation
- **WebAssembly Sandbox** — Integrated the `wasmtime` runtime to execute user-provided `.wasm` binaries in a strictly isolated virtual machine, mirroring the architecture of serverless edge platforms.
- **Zero Host Access** — Enforced an empty Linker (no WASI imports), guaranteeing that Wasm modules have absolutely zero access to the host filesystem, network, environment variables, or system calls.
- **CPU Fuel Budget** — Implemented a deterministic CPU budget (10,000,000 fuel per step, 1,000 fuel per row) that automatically aborts infinite loops (`TrapError`) without hanging the Celery worker.
- **Memory & Type Safety** — Enforced a strict 1MB stack size limit to prevent stack overflow attacks. The host↔Wasm boundary strictly allows only `f64` (float64) types to cross, preventing type confusion vulnerabilities.

### Performance & Scalability
- **WasmExecutor Engine** — Created a highly optimized execution engine (`backend/execution/wasm_executor.py`). It operates as a module-level singleton per Celery worker, caching compiled Wasm modules via SHA256 to eliminate the ~100ms compilation overhead on subsequent runs.
- **Zero-Leakage State** — While the compiled module is cached, the executor provisions a completely fresh Store (memory and fuel budget) for every single execution, ensuring zero state leakage between pipeline runs.

### Added
- **New Step Type: `wasm_compute` (Total: 17)** — Added a new pipeline step that executes a custom Wasm function against every row of a DataFrame. Users map input columns to function arguments, and the return value is appended as a new output column.
- **Wasm Module API & Storage** — Added full CRUD capabilities for Wasm modules (`/api/wasm`) backed by a new `wasm_modules` PostgreSQL table.
  - Supports direct uploads for files < 10MB and presigned MinIO URLs for larger binaries.
  - Stores `.wasm` files securely in a dedicated `pipelineiq-wasm` MinIO bucket.
  - Automatically validates uploaded binaries to extract and verify exported functions.
- **Wasm Module Manager UI** — Added a new frontend page (`/wasm-modules`) featuring a drag-and-drop upload zone, validation status indicators, a list of exported functions, and an expandable guide on how to compile Rust to WebAssembly.
- **Visual Builder Integration** — Added a dynamic configuration panel for `wasm_compute` steps in the pipeline editor. It includes a dropdown of validated modules, auto-populates available functions, and provides a multi-select interface for mapping input columns to Wasm arguments.
- **Security & Unit Test Suite** — Added a rigorous security test suite (`test_wasm_sandbox_security.py`) that actively attempts to breach the sandbox (e.g., filesystem access, infinite loops, type confusion) to guarantee isolation. Added 20+ unit tests using inline WAT (WebAssembly Text) fixtures to verify execution correctness.

---

## [8.2.6] — Week 14: Scheduling, Templates & Real File Output

*Note: This release transforms PipelineIQ from a manual tool into an automated platform. It introduces cron-based scheduling, pre-built pipeline templates, and real downloadable file outputs.*

### Added
- **Pipeline Scheduling** — Pipelines can now be scheduled to run automatically using cron expressions.
  - Added full CRUD API for schedules (`POST`, `GET`, `DELETE`) with pause/resume capabilities.
  - Integrated dynamic Celery Beat registration to load and manage schedules without restarting the worker.
  - Added run history tracking (`schedule_runs` table) and a preview endpoint for the next N scheduled runs.
  - Included a human-readable cron translator (e.g., "0 6 * * 1" → "every Monday at 6:00 AM").
- **Pipeline Templates** — Added 5 pre-built, forkable pipeline templates to accelerate development:
  - *Sales Revenue Report*, *Data Quality Audit*, *Weekly Rollup*, *Multi-Source Merge*, and *Customer Segmentation*.
  - Added a Fork API (`POST /api/templates/{id}/fork`) that safely replaces `{{placeholder}}` variables with actual file UUIDs and returns ready-to-run YAML.
- **Real MinIO File Output** — The `save` step now writes actual data to the `pipelineiq-outputs` MinIO bucket.
  - Supported formats: CSV, JSON (with strict NaN/Inf handling via `orjson`), and Parquet (Snappy compressed for large datasets).
  - Generates secure, presigned download URLs valid for 48 hours.
  - Added a `GET /api/runs/{id}/download` endpoint to refresh expired URLs.
- **Frontend Updates** — Added a Schedule creation UI with a cron builder, a Templates browser page, and a "Download Output" button on the run detail page displaying the file format and size.
- **Test Suite Expansion** — Added 34 new tests covering cron validation, schedule API CRUD, template loading/forking, save step serialization (CSV/JSON/Parquet), and Playwright E2E flows for scheduling.

---

## [7.9.16] — Week 13: Autonomous AI Healing Agent

*Note: This release implements the most architecturally complex feature in the roadmap: self-correcting pipelines. When a run fails due to schema drift at any hour, the system detects the error, calls Gemini for a JSON patch, validates it in an ephemeral DuckDB sandbox on 100 rows, and resumes the pipeline automatically. Zero human intervention required.*

### Added
- **Autonomous Healing Agent** — Introduced `backend/execution/healing_agent.py`, the core orchestrator. When a healable step error is caught, the agent:
  - Pauses the run (`status: healing` — not `failed`) and publishes a `healing_started` SSE event.
  - Computes the schema diff between old and new file profiles.
  - Calls Gemini via the rate-limited `gemini` Celery queue (`temperature=0.0` for deterministic output) with a JSON patch prompt — not a full YAML rewrite, to minimize hallucination surface.
  - Tests the patch in an ephemeral DuckDB sandbox on 100 rows.
  - On sandbox pass: applies the patch, creates a new pipeline version, marks the run as `healed`.
  - On 3 consecutive sandbox failures: marks the run as `failed` with a full audit trail.
- **Healable Error Classifier** — Added `backend/execution/healing_classifier.py`. Classifies exceptions as healable (schema drift: `ColumnNotFoundError`, `KeyError`, `MergeError`, `ValueError` with column-reference message) vs. non-healable (code bugs: `AttributeError`, `MemoryError`, `ZeroDivisionError`, `ConnectionError`). Non-healable errors skip the agent and fail immediately.
- **Schema Diff Engine** — Added `backend/execution/schema_diff.py`. Detects removed columns, added columns, and rename candidates using Jaro-Winkler string similarity (0.85 threshold). Rename confidence is boosted when semantic types match across old and new schemas.
- **JSON Patch Applier** — Added `backend/execution/patch_applier.py`. Applies Gemini's structured JSON patch to the pipeline YAML string. Handles `column`, `on`, `group_by`, `by`, `columns`, `mapping`, `aggregations`, `left`, `right`, and generic fields. Re-serializes to clean YAML after patching.
- **DuckDB Ephemeral Sandbox** — Added `backend/execution/sandbox.py`. Opens a fresh `duckdb.connect(':memory:')` per healing attempt — never reuses the worker's persistent connection. Loads 100 rows per file from MinIO, executes all 16 step types via DuckDB SQL builders, and closes the connection in a `finally` block. Completely side-effect-free: no writes to any database or object store.
- **Healing Prompt Engineering** — Added `backend/ai/healing_prompts.py`. Builds the Gemini prompt injecting the broken YAML, error type, error message, failed step name, old schema, new schema, and formatted rename candidates. Asks for a JSON object with `confidence`, `change_description`, and a `patches` array. Includes `validate_healing_patch()` to verify patch structure before sandbox execution.
- **`healing_attempts` Table** — Added Alembic migration `0009_add_healing_attempts_table.py`. Stores every healing attempt as an immutable audit record with: `run_id`, `error_type`, `error_message`, `failed_step`, `old_schema`, `new_schema`, `removed_columns`, `added_columns`, `renamed_candidates`, `gemini_patch`, `sandbox_result`, `applied`, `attempt_number`, `confidence`, and `healed_at`. Immutability enforced via PostgreSQL RULES (`healing_attempts_no_delete`, `healing_attempts_no_update`).
- **`HealingAttempt` Model** — Added `backend/models/healing_attempt.py` with full SQLAlchemy mapping of the `healing_attempts` table.
- **Healing History API** — Added `GET /api/runs/{run_id}/healing-history` returning all healing attempts for a run, including the Gemini patch, sandbox result, confidence score, schema diff, and application status.
- **Frontend Healing UI** — Added `HealingBanner` component rendering three states: `healing` (spinner, "Auto-healing in progress…"), `healed` (confidence %, description, "View what changed" button), and `failed` (attempt count, "View AI attempts" button). SSE handler extended to process `healing_started`, `healing_complete`, and `healing_failed` events. `RunStatusBadge` updated with `healing` (⚡) and `healed` (✓✓) configs.
- **Test Suite Expansion** — Added 41 new tests across 4 unit test files:
  - `test_healing_classifier.py` (9 tests): healable vs. non-healable classification for all error types.
  - `test_schema_diff.py` (12 tests): removed/added column detection, rename candidates, confidence sorting, summary string.
  - `test_patch_applier.py` (8 tests): filter column patch, group_by patch, invalid step name, empty patches, YAML output validity.
  - `test_sandbox.py` (12 tests): filter/sort/aggregate/deduplicate SQL builders, ephemeral connection isolation, `finally` block enforcement, prompt structure.

### Changed
- **Pipeline Run Status Machine** — Added two new status values to `pipeline_runs.status`: `healing` (agent is actively attempting repair) and `healed` (agent fixed and resumed the run successfully). Full status set: `pending` | `running` | `healing` | `healed` | `success` | `failed` | `timeout`.
- **Pipeline Executor** — Integrated the healing agent into the step execution loop. Healable exceptions now route to `attempt_heal()` before the run is marked failed. Non-healable exceptions fail immediately as before.

---

## [7.2.5] — Week 12: AI Generation & Error Repair

*Note: This release introduces powerful AI capabilities to PipelineIQ, allowing users to generate pipelines from plain English and automatically repair failed runs. It also resolves critical bottlenecks related to LLM rate limiting and YAML parsing overhead.*

### Performance & Scalability
- **Gemini Rate Management (Bottleneck #9)** — Completely overhauled how AI calls are dispatched to prevent Gemini free-tier rate limit exhaustion (429 errors):
  - Created a dedicated `gemini` Celery queue processed by a strictly configured `worker-gemini` service (exactly 1 replica, `concurrency=1`).
  - Enforced a strict `50/m` Celery rate limit with exponential backoff (10s to 160s) for `RESOURCE_EXHAUSTED` errors.
  - Implemented a rolling 60-second token budget tracker in Redis (capped at 900,000 tokens/minute).
  - Added a SHA256-keyed response cache (1-hour TTL) to eliminate duplicate API calls for identical prompts.
- **YAML Parse Caching (Bottleneck #12)** — Eliminated CPU overhead from repetitive YAML parsing. `get_parsed_pipeline()` now caches parsed pipeline objects in `redis-cache` using a SHA256 hash of the raw YAML text, falling back to a fresh parse only on cache misses or edits.

### Added
- **AI Pipeline Generation** — Added `POST /api/ai/generate` to convert natural language descriptions into valid, runnable PipelineIQ YAML.
  - **Two-Attempt Self-Fix Flow:** If the AI generates invalid YAML, the backend automatically feeds the validation error back to Gemini for a deterministic self-correction before returning the result to the user.
  - **Context-Aware Prompting:** The system prompt dynamically injects actual file schemas (from the Week 2 Data Profiler) and strict step definitions to prevent column hallucinations.
- **AI Error Repair** — Added `POST /api/ai/runs/{run_id}/repair` for failed pipeline runs. 
  - Users can click "Ask AI to fix this" on a failed run. Gemini analyzes the original YAML, the specific failing step, and the error message to generate a corrected YAML.
  - Includes a frontend Diff Viewer showing line-by-line added/removed/unchanged highlights before applying the fix.
- **Column Autocomplete** — Added `POST /api/ai/autocomplete/column` to catch and suggest fixes for user typos in column names. Uses the `jellyfish` library's Jaro-Winkler similarity algorithm (0.85 threshold) to heavily weight prefix matches and avoid false positives.
- **Frontend AI Integration** — Added the `AIGenerateModal` to the visual builder, allowing users to select uploaded files and describe their desired pipeline, complete with loading states and attempt-count indicators.
- **Test Suite Expansion** — Added 38+ new tests covering Gemini queue routing, token budgeting, YAML caching, Jaro-Winkler autocomplete logic, prompt structure validation, and Playwright E2E flows for the AI modal.

---

## [6.3.0] — Week 11: Visual Pipeline Builder

*Note: This release introduces the identity-defining feature of PipelineIQ: a fully interactive, drag-and-drop visual pipeline builder. Users can now build complex data pipelines without writing any code, while maintaining real-time, bidirectional synchronization with the underlying YAML definition.*

### Added
- **Three-Panel Visual IDE** — Introduced a comprehensive visual editing interface consisting of a Step Palette, an interactive Canvas, and dynamic Configuration Panels.
- **Step Palette** — A left-hand sidebar featuring all 16 pipeline step types, categorized by role (Source, Combine, Transform, Reshape, Quality, Advanced, Output), ready to be dragged onto the canvas.
- **Interactive Pipeline Canvas** — Powered by React Flow, featuring:
  - Custom `StepNode` components with category-specific colored borders, schema hints (e.g., "3 → 2 cols"), and inline validation error badges.
  - Dynamic connection handles (e.g., `join` steps automatically render distinct left and right input handles).
  - Snap-to-grid, minimap, and animated connection edges.
- **Dynamic Configuration Panels** — Context-aware right-hand slide-out panels for all 16 step types. Includes specialized UI controls like:
  - Multi-column selectors for `aggregate` and `select`.
  - Dynamic row builders for `rename` mappings and `validate` quality checks.
  - A dedicated DuckDB SQL editor for the `sql` step.
- **Bidirectional YAML ↔ Graph Sync** — Real-time synchronization between the visual canvas and the YAML editor with a 300ms debounce:
  - **YAML → Graph (`yamlToGraph`)**: Automatically parses YAML and applies a depth-based auto-layout algorithm (left-to-right by dependency level) to render the visual graph. Handles invalid YAML gracefully.
  - **Graph → YAML (`graphToYAML`)**: Converts visual nodes and edges back to clean YAML. Uses Kahn's algorithm for topological sorting to guarantee steps are written in strict dependency order.
- **Strict Edge Validation** — Prevents invalid pipeline states visually before they are executed:
  - `load` steps cannot have incoming edges (`isSource = true`).
  - `save` steps cannot have outgoing edges (`isTerminal = true`).
  - `join` steps are strictly limited to exactly 2 inputs.
  - Cycle detection prevents circular dependencies (back-edges).
  - Toast notifications alert users when a connection rule is violated.
- **Frontend Test Suite Expansion** — Added 50+ new frontend tests:
  - Unit tests for topological sorting, cycle detection, and YAML/Graph roundtrip serialization.
  - React Testing Library (RTL) tests for the custom `StepNode` component.
  - Playwright E2E tests covering drag-and-drop, YAML syncing, and configuration panel interactions.

---

## [5.2.10] — Week 10: Zero-Copy Compute Engine (Arrow + DuckDB)

*Note: This release represents a massive architectural rewrite of the core execution engine. It replaces Pandas serialization with a zero-copy Apache Arrow architecture and introduces DuckDB's vectorized SQL engine, achieving up to 105x performance speedups on large datasets.*

### Performance & Scalability
- **Apache Arrow Data Bus (Bottleneck #10 & #13)** — Replaced expensive Python pickling between pipeline steps with the Arrow IPC binary format. This enables true zero-copy data transfer and releases the Python GIL during data operations.
- **Tiered Storage Strategy** — Intermediate step data is now intelligently routed based on size to prevent memory exhaustion:
  - `< 10MB`: Stored in Redis Broker (fast, shared across workers).
  - `< 500MB`: Stored in `/dev/shm` (zero-network in-memory filesystem) with automatic run-scoped cleanup.
  - `≥ 500MB`: Spilled to MinIO as Snappy-compressed Parquet files. DuckDB queries these directly via the `httpfs` extension without downloading the full file.
- **DuckDB Vectorized Engine (Bottleneck #6)** — Implemented a highly optimized DuckDB execution path. Solved concurrent access issues by initializing exactly one DuckDB connection per Celery worker process (via `worker_init`).
- **Dynamic Thread Tuning** — DuckDB thread counts are now dynamically calculated as `CPU_cores / CELERY_CONCURRENCY` to prevent CPU oversaturation and context-switching overhead across multiple workers.

### Added
- **SmartExecutor Routing** — The execution engine now automatically routes steps to DuckDB if the DataFrame has ≥ 50,000 rows AND the step type is DuckDB-capable. Smaller DataFrames automatically fall back to Pandas to avoid DuckDB's startup overhead.
- **DuckDB SQL Builders** — Added native SQL translation for 10 core step types: `filter`, `aggregate`, `sort`, `select`, `deduplicate`, `sample`, `fill_nulls`, `pivot`, `unpivot`, and `join`. Utilizes advanced DuckDB features like `PIVOT`, `UNPIVOT`, and `TABLESAMPLE`.
- **New Step Type: `sql` (Total: 16)** — Added a powerful new step type that allows users to execute arbitrary DuckDB SQL (including window functions, CTEs, and LATERAL joins) against the upstream DataFrame using the `{{input}}` placeholder. Includes strict validation to block DML/DDL (e.g., `DROP`, `INSERT`).
- **Benchmark Suite** — Added a dedicated benchmark suite (`benchmark/duckdb_vs_pandas.py`) and performance regression tests. Verified a ~105x speedup on 1M row `GROUP BY` aggregations (Pandas ~4200ms → DuckDB ~40ms).
- **Test Suite Expansion** — Added 35+ new tests covering the Arrow bus, DuckDB executor, SQL builders, and performance regression guards.

---

## [4.1.7] — Week 9: Data Profiling & 15 Step Types

*Note: This release introduces the first major user-facing product capabilities on top of the Week 1 infrastructure foundation. It adds automatic data profiling and expands the pipeline engine to 15 total step types.*

### Added
- **Automatic Data Profiler** — Every uploaded file is now automatically analyzed asynchronously via a Celery task on the `bulk` queue, ensuring zero blocking on the API upload response.
- **Column-Level Statistics** — Computes comprehensive stats including null counts/percentages, unique counts, min/max/mean/median/std_dev, IQR-based outlier detection, and 10-bucket histograms for numeric columns. Computes top values and length stats for categorical columns.
- **Semantic Type Inference** — Automatically infers the semantic meaning of columns (e.g., `numeric`, `categorical`, `email`, `integer_id`, `datetime`, `boolean`, `url`, `identifier`).
- **Data Quality & PII Flags** — Automatically tags columns with boolean flags such as `likely_pii`, `likely_id`, `likely_date`, `high_null_rate`, `high_cardinality`, and `constant`.
- **Large File Sampling** — The profiler automatically detects files larger than 1,000,000 rows and deterministically samples 100,000 rows to ensure fast, memory-safe profiling.
- **Profile API Endpoints** — Added `GET /api/files/{id}/profile` to retrieve the computed JSON profile and `POST /api/files/{id}/profile/refresh` to manually re-trigger profiling.
- **6 New Pipeline Step Types (Total: 15)**:
  - `pivot`: Reshapes data from long to wide format with configurable `aggfunc` (sum, mean, count, min, max, first) and `fill_value`.
  - `unpivot`: Reshapes data from wide to long format (inverse of pivot).
  - `deduplicate`: Removes duplicate rows with configurable `keep` strategies (first, last, none) and `subset` column targeting.
  - `fill_nulls`: Handles missing data using 6 distinct strategies (`constant`, `forward_fill`, `backward_fill`, `mean`, `median`, `mode`).
  - `rename`: Standardizes column names using a mapping dictionary, complete with circular-rename and conflict detection.
  - `sample`: Performs deterministic random sampling by exact row count (`n`) or percentage (`fraction`), with support for `random_state` (reproducibility) and `stratify_by` (distribution preservation).
- **Database Migration** — Added Alembic migration `0008_add_file_profiles.py` to create the `file_profiles` table with JSONB storage and upsert capabilities.
- **Test Suite Expansion** — Added 75+ new tests (20 profiler unit tests, 46+ step type unit tests, 8 integration tests). The total test count is now 280+ with ≥ 85% coverage on the new modules.

---

## [3.0.2] — Week 8: Production Infrastructure Foundation

*Note: This release contains zero user-visible features. It is a complete architectural overhaul of the infrastructure layer to resolve 5 critical scaling bottlenecks, laying the foundation for the Unified Data Operating System roadmap.*

### Performance & Scalability
- **Multi-Worker Celery Architecture (Bottleneck #1)** — Replaced the single Celery worker with three dedicated priority queues (`critical`, `default`, `bulk`). Set `worker_prefetch_multiplier = 1` to prevent task hoarding and ensure true priority execution.
- **Gunicorn & API Scaling (Bottleneck #2)** — Migrated from a single Uvicorn process to Gunicorn with 4 workers. Configured `max-requests=10000` and jitter to prevent Python memory leaks over time.
- **Dedicated SSE Service (Bottleneck #5)** — Extracted Server-Sent Events (SSE) streaming into a completely isolated FastAPI service (`sse_app.py`) running on port 8001. This prevents long-lived pipeline monitoring connections from starving the main API coroutine slots.
- **PgBouncer Connection Pooling (Bottleneck #3)** — Integrated PgBouncer in `transaction` pool mode. Safely multiplexes up to 10,000 client connections down to 25 real PostgreSQL connections, eliminating DB connection crashes under load.
- **PostgreSQL Read Replica** — Deployed a hot-standby read replica. Split database dependencies into `get_read_db` (for GET requests) and `get_write_db` (for mutations) to distribute database load.
- **Redis Instance Splitting (Bottleneck #4)** — Split the single overloaded Redis instance into 4 dedicated, role-specific instances: `redis-broker`, `redis-pubsub`, `redis-cache`, and `redis-yjs`. Implemented module-level connection pools to eliminate per-request connection overhead.
- **Zero-Memory File Uploads (Bottleneck #7)** — Eliminated `await file.read()` memory bombs. Files ≤ 10MB now stream directly to MinIO in 1MB chunks. Files > 10MB now utilize presigned MinIO URLs, allowing the client to upload directly to object storage and bypassing FastAPI entirely.
- **Rust-Based JSON Serialization (Bottleneck #10)** — Replaced Python's standard `json` library with `orjson` across the entire codebase. Set FastAPI's `default_response_class` to `ORJSONResponse`, releasing the GIL and achieving 5-10x faster serialization.

### Added
- **SSE Lifecycle Management** — Added a 15-second heartbeat to the SSE stream to prevent Nginx `proxy_read_timeout` drops. Implemented auto-close on terminal states and immediate client disconnect detection to free server resources instantly.
- **Database Indexes** — Added Alembic migration `0007_add_performance_indexes.py` to create 9 critical performance indexes (e.g., `idx_pipeline_runs_user_created`, `idx_step_results_run_id`, `idx_data_assets_name_trgm`).
- **Infrastructure Test Suite** — Added comprehensive unit and integration tests for the new infrastructure (`test_celery_queues.py`, `test_redis_connections.py`, `test_file_upload.py`, `test_sse_lifecycle.py`, `test_infrastructure.py`).

### Changed
- **Nginx Routing** — Updated `pipelineiq.conf` to route `/api/runs/*/stream` traffic to the new dedicated SSE service on port 8001 with `proxy_buffering off`.
- **Environment Variables** — Updated `.env.example` to include new distinct Redis URLs (`REDIS_BROKER_URL`, `REDIS_PUBSUB_URL`, etc.) and split Database URLs (`DATABASE_WRITE_URL`, `DATABASE_READ_URL`).

---

## [2.3.14] — Week 7: Post-Audit Polish

*Note: This is a targeted patch release fixing two theme-related defects discovered during the Week 6-7 audit: "PipelineIQ Light" was missing from both `ThemeSelector` and `CommandPalette`. The audit report is also updated to reflect all verified fixes.*

### Bug Fixes
- **ThemeSelector missing theme** — added "PipelineIQ Light" to `BUILT_IN_THEMES` array in `ThemeSelector.tsx` (was only 6 of 7 themes)
- **CommandPalette missing theme** — added `'pipelineiq-light'` to themes array in `CommandPalette.tsx` (was only 6 of 7 themes)

### Documentation
- **AUDIT_REPORT.md** — updated to reflect all v2.1.0+ fixes: 7 themes (was 6), 14 models (was 10), 13 API routers (was 6), 93 frontend tests acknowledged, fixed issues marked with ✅ status, recommendations updated
- **CHANGELOG.md** — added v2.1.4 entry for post-audit fixes

---

## [2.1.3] — Week 6 & 7: Codebase Audit: 43 Items Implemented

*Note: This release consolidates 43 individual improvements identified during a systematic codebase audit covering security hardening, performance tuning, architectural consistency, and correctness fixes. It also introduces several new product features including pipeline scheduling and per-pipeline RBAC.*

### Bug Fixes
- **Pagination** — `list_pipeline_runs` now uses `page`, `limit`, and `status_filter` query params with proper `OFFSET`/`LIMIT` (was loading all runs with `.all()`)
- **UTC datetime** — replaced deprecated `datetime.utcnow()` with `datetime.now(timezone.utc)` in auth token creation
- **Shared UUID utils** — extracted duplicated `_validate_uuid_format` and `_as_uuid` to `backend/utils/uuid_utils.py` (was copy-pasted 3-4 times)
- **Redis connection pool** — health check now reuses a module-level `redis.ConnectionPool` instead of creating a new client per call
- **Unified fetch** — merged duplicate `fetchApi`/`fetchAuth` into a single `fetchWithAuth` function parameterized by base URL
- **Package name** — fixed `package.json` name from `ai-studio-applet` to `pipelineiq`

### Security
- **Metrics restricted** — `/metrics` endpoint restricted to internal networks (10.x, 172.16.x, 192.168.x, 127.0.0.1) in nginx
- **Docs disabled in production** — `/docs` and `/redoc` set to `None` when `ENVIRONMENT=production`
- **SameSite=Strict** — `piq_auth` cookie changed from `Lax` to `Strict`
- **Webhook secrets hidden** — `WebhookResponse` now returns `has_secret: bool` instead of the raw secret
- **Password complexity** — registration requires uppercase letter, number, and special character
- **Secret key validation** — `config.py` model validator fails startup if default `SECRET_KEY` is used in production

### Performance
- **ID-only query** — `validate_pipeline` now queries `db.query(UploadedFile.id).all()` instead of loading full objects
- **Referenced file loading** — pipeline tasks only load files referenced in the YAML config, not all files
- **Database indexes** — added Alembic migration with indexes on `pipeline_runs.status`, `pipeline_runs.created_at`, `step_results.pipeline_run_id`, `webhook_deliveries.webhook_id`, and `audit_logs.user_id + created_at`
- **Cache TTL** — all lineage cache entries now expire after 1 hour (`ttl=3600`)
- **SCAN over KEYS** — `cache_delete_pattern` uses `SCAN` with cursor iteration instead of blocking `KEYS` command
- **Preview optimization** — file preview uses `pd.read_csv(nrows=N)` instead of reading the entire file

### Architecture
- **Error boundaries** — React `ErrorBoundary` and `WidgetErrorBoundary` components wrap the app and individual widgets
- **SSE reconnect** — exponential backoff (1s → 2s → 4s → 8s → 16s, max 5 retries) on SSE disconnect
- **Async webhook delivery** — webhook HTTP calls run in a separate Celery task (`webhook_tasks.py`)
- **API versioning headers** — `X-API-Version` and `X-App-Version` response headers via middleware
- **Centralized metrics** — all Prometheus metric definitions extracted to `backend/metrics.py` (resolves circular import)
- **Top-level audit imports** — `from backend.services.audit_service import log_action` moved to module top level

### New Features
- **Pipeline scheduling** — cron-based recurring execution via Celery Beat with CRUD endpoints
- **Pipeline templates** — 5 pre-built templates (ETL, cleaning, validation, aggregation, merge/join)
- **Slack notifications** — notification config with webhook URL, event subscriptions, and test endpoint
- **Data preview in editor** — preview button shows sample data at each pipeline step
- **Step DAG visualization** — horizontal flow diagram showing step execution order with status colors
- **Multi-file pipeline outputs** — support for multiple save steps writing to different files
- **User dashboard** — personal analytics (runs, success rate, most-used pipelines, recent activity)
- **Per-pipeline RBAC** — owner/runner/viewer permissions per pipeline with admin override
- **Pipeline cancellation** — cancel running pipelines with `CANCELLED` status and Celery task revocation
- **File versioning** — version counter and `previous_version_id` FK on uploaded files
- **Export pipeline results** — download output CSV/JSON from completed pipeline runs
- **Light mode** — PipelineIQ Light theme added (7 total themes)
- **Mobile responsive** — stacked single-column widget layout on mobile devices
- **Presence indicators** — online user indicator in top bar (WebSocket-ready)

### Dependency Cleanup
- Removed `@google/genai` from frontend dependencies (unused, 500KB+ savings)
- Removed `firebase-tools` from devDependencies (unused)
- Removed `aioredis` from requirements.txt (deprecated, replaced by `redis.asyncio`)
- Removed `aiofiles` from requirements.txt (not imported anywhere)
- Added `croniter==2.0.1` for cron expression parsing

---

## [1.3.9] — Week 5: Frontend Testing

*Note: This release establishes the frontend test infrastructure from scratch, adding 93 tests across 8 files covering the API layer, Zustand stores, page components, widgets, utilities, and middleware. The CI pipeline is updated to enforce TypeScript correctness and all frontend tests on every push.*

### Added
- Vitest + React Testing Library + jsdom test infrastructure
- 93 frontend tests across 8 test files:
  - API layer tests (26): token management, all API functions, error handling, 401 redirect
  - Zustand store tests (26): pipeline, widget binary tree, theme, keybinding stores
  - Page component tests (12): login/register forms, validation, error states, demo login
  - Widget tests (11): QuickStats, FileUpload, RunHistory, FileRegistry
  - Utility tests (7): cn() classname merging, API constants
  - Middleware tests (4): auth redirect logic
  - Auth context tests (4): AuthProvider login, logout, demo login
  - Hook tests (3): widget layout toggle, workspace switching
- `npm run test` and `npm run test:watch` scripts
- CI pipeline now runs frontend tests (tsc → vitest → build)
- Total project tests: 299 (206 backend + 93 frontend)

### Changed
- CI job renamed: "Frontend TypeScript + Build" → "Frontend TypeScript + Tests + Build"
- README testing section split into Backend and Frontend subsections
- AUDIT_REPORT test gaps updated: frontend unit tests now ✅

---

## [1.2.7] — Week 4: Auth, Observability, Deploy

*Note: This release introduces the security, observability, and deployment layer: JWT authentication with role-based access, Prometheus metrics, Grafana dashboards, Sentry error tracking, HMAC-signed webhooks, and immutable audit logging enforced by a database trigger.*

### Added
- JWT authentication (register, login, roles: admin/viewer)
- Prometheus metrics endpoint with 5 custom counters
- Grafana dashboard with 10 monitoring panels
- Sentry error tracking (FastAPI + Celery + SQLAlchemy)
- Webhook system with HMAC SHA256 signatures and 3-attempt retry
- Immutable audit logging with database trigger enforcement
- Railway production deployment config
- `/auth/register`, `/auth/login`, `/auth/me`, `/auth/users` endpoints
- `/webhooks/` CRUD endpoints with delivery log
- `/audit/logs` admin endpoint, `/audit/logs/mine` user endpoint
- Login and register pages in Next.js frontend
- User info and logout in TopBar
- Debug/sentry-test endpoint (non-production only)

---

## [0.3.12] — Week 3: DevOps

*Note: This release focuses entirely on infrastructure and developer operations: a production-ready Nginx reverse proxy with SSE-safe configuration, automated CI/CD via GitHub Actions, and the Flower dashboard for Celery monitoring. Frontend Week 2 features are also surfaced in the UI.*

### Added
- Nginx reverse proxy (port 80, SSE-safe, security headers)
- GitHub Actions CI/CD (backend tests + frontend check)
- Flower Celery monitoring dashboard
- Frontend Week 2 UI: schema drift badge, dry-run plan, version history,
  validate results display, schema history modal

---

## [0.2.3] — Week 2: Data Platform

*Note: This release upgrades PipelineIQ from a prototype to a production data platform. PostgreSQL replaces the prior database layer, Redis caching delivers a 3x speedup on lineage queries, and schema drift detection, pipeline versioning, and a dry-run execution planner are all introduced.*

### Added
- PostgreSQL migration (UUID PKs, JSONB, connection pooling)
- Alembic migrations (auto-run on startup)
- Redis caching (3x speedup, lineage forever, stats 30s TTL)
- Rate limiting with slowapi (4 tiers)
- Schema drift detection (breaking/warning/info)
- Validate step with 12 check types
- Pipeline versioning with git-style diffs and restore
- Dry-run execution planner (8 heuristics)
- 83 new tests (180 total)

---

## [0.1.2] — Week 1: Foundation

*Note: This is the initial release establishing the full core stack: a FastAPI backend with 8 step types, NetworkX column-level lineage, Celery async execution, SSE real-time streaming, and a Next.js frontend with the Hyprland-inspired widget system. Everything in subsequent weeks builds on this.*

### Added
- FastAPI backend with 8 pipeline step types
- Column-level data lineage with NetworkX
- Impact analysis and column ancestry
- Celery + Redis async pipeline execution
- SSE real-time streaming
- Next.js 15 frontend with Hyprland-inspired widget system
- 6 themes including custom PipelineIQ Dark
- CommandPalette, keybindings (Vim-style)
- 7 widgets: FileUpload, FileRegistry, PipelineEditor,
  RunMonitor, LineageGraph, RunHistory, QuickStats
- Docker Compose (4 → 5 → 7 → 9 services)
- Apache 2.0 license
- 97 initial tests