# Wave 2 SSE Remediation Report (branch `fix/w2-sse`)

Scope: `backend/sse_app.py`, `backend/api/sse.py`,
`backend/scheduling/cron_utils.py` (tz only), plus sibling
`backend/utils/sse_security.py` (root-cause fix for M-156) and docs.
Untouched per ownership: `tasks/streaming_pipeline.py`,
`routers/streaming.py`, `api/schedules.py`.

## M-106 (Medium) — SSE has no per-user cap; shared pool exhausts; streams pin DB sessions

- Verified: `stream_pipeline_progress` injected a `Session` held for the
  whole stream lifetime; no cap tracking; loop yielded every Redis
  message with no coalescing.
- Fix (`backend/api/sse.py`): `SSE_MAX_STREAMS_PER_USER` (5) /
  `SSE_MAX_STREAMS_TOTAL` (200) slots, 429 on excess, released in the
  generator `finally`; endpoint copies ORM fields then `db.close()`
  before streaming (short-lived reads, heartbeat loop holds no DB);
  backpressure coalesces bursts to latest state (terminal events never
  dropped, `SSE_MAX_DRAIN_PER_TICK`).

## M-156 (Low) — SSE signature fails open in test env

- Verified: `verify_sse_payload` returned `ENVIRONMENT == "test"` for
  missing signatures, and `_parse_message_payload` returned in-memory
  dicts without verification.
- Fix (`backend/utils/sse_security.py`, `backend/api/sse.py`): unsigned
  payloads rejected by default; opt-in only via
  `PIPELINEIQ_ALLOW_UNSIGNED_SSE=true`, never honored in production;
  dict payloads verified like wire payloads.

## M-169 (Medium) — no graceful-shutdown budget; SIGTERM hangs; lifespan leaks

- Verified: `sse_app` had no lifespan; engines/pools never disposed.
- Fix (`backend/sse_app.py`, `docs/sse-streaming.md`): lifespan disposes
  DB engines and disconnects all Redis pools within
  `SSE_SHUTDOWN_TIMEOUT_SECONDS` (10 s), then forces close; k8s
  `terminationGraceSeconds: 35` documented (above the 10 s budget and
  gunicorn's 30 s graceful timeout).

## M-170 (Medium) — dependency-down behavior (PG-down boot crash; cache-down lies)

- Verified: no startup PG check; `/health` always 200; cache outage
  silently yielded empty streams.
- Fix: lifespan fail-fasts with a clear `RuntimeError` when PostgreSQL
  is unreachable (skipped for non-Postgres URLs); endpoint pre-flight
  pings pub/sub + cache and returns honest 503 on outage; `/readyz`
  returns 200 only when PG + Redis answer, else 503 `degraded`;
  `/livez` stays dependency-free.

## M-171 (Medium) — retry storms; blind retries; critical queue on default worker

- Verified: unbounded `retry_on_timeout` path with no backoff/jitter or
  breaker; no queue-routing policy doc.
- Fix (`backend/api/sse.py`, `docs/`): `_redis_op_with_retry` — max
  `SSE_REDIS_MAX_RETRIES` (3) attempts, exponential backoff + jitter,
  per-op timeout, cooldown circuit breaker; in-loop failures
  degraded-close the stream; queue-routing isolation policy documented
  (`worker-default` draining `critical` flagged; strict isolation =
  `critical` only on `worker-critical`).

## M-117 (Low) — cron UTC-only

- Verified: `get_next_run_at`/`get_next_n_runs` hardcoded UTC; no tz param.
- Fix (`backend/scheduling/cron_utils.py`, tz-only): `resolve_timezone()`
  (IANA, `ValueError` on unknown), `tz` params defaulting to `"UTC"` on
  `get_next_run_at`/`get_next_n_runs`/`parse_celery_crontab`; Celery 5.7
  carries the zone via a cached per-zone app (crontab `tz` comes from
  `app.timezone`); returned datetimes aware in the schedule's zone.

## Verification

- Repro probe: 8/8 findings confirmed before, 0 after.
- `test_sse.py` + `test_cron_utils.py` + `test_sse_lifecycle.py`: 53 passed.
- `unit/scheduling` + `unit/streaming` + `unit/infrastructure`: 202 passed.
- `unit/api`: 96 passed, 2 skipped.
- `ruff check` clean on all touched files; `mypy` clean.
