# SSE Streaming Service

Dedicated service (`backend/sse_app.py`, port 8001) for Server-Sent Events
progress streams. Route logic lives in `backend/api/sse.py`.

## Connection caps (M-106)

- `SSE_MAX_STREAMS_PER_USER` (default 5): concurrent streams per user.
  Excess opens get an immediate HTTP 429 — fast rejection instead of
  exhausting the shared Redis pub/sub pool.
- `SSE_MAX_STREAMS_TOTAL` (default 200): service-wide ceiling.
- Slots are acquired in the endpoint and released in the generator's
  `finally` block, so cancelled/disconnected streams always free capacity.

## Database sessions (M-106)

The endpoint performs short-lived reads (run lookup + access check),
copies the needed fields out of the ORM object, then calls `db.close()`
**before** returning the `StreamingResponse`. The long-lived stream holds
no DB session and no pooled connection — only the 15 s heartbeat loop and
Redis reads run for the stream lifetime. Never re-attach ORM objects
inside `_live_event_generator`.

## Backpressure drop policy (M-106)

Redis can publish bursts faster than a client consumes. Per loop tick the
generator drains up to `SSE_MAX_DRAIN_PER_TICK` (default 50) already-
buffered messages and emits only the **latest non-terminal** state,
logging the coalesced count at debug. **Terminal events
(`pipeline_completed` / `pipeline_failed` / `pipeline_cancelled` /
`stream_end`) are never dropped** — a terminal payload short-circuits the
drain and closes the stream.

## Signature verification (M-156)

All Redis payloads (wire bytes and in-memory dicts) must carry an HMAC
signature (`backend/utils/sse_security.py`). Unsigned payloads are
**rejected by default**. The old behavior (accept unsigned when
`ENVIRONMENT == "test"`) failed open: any test-env Redis writer could
forge progress events. Opt-in for local debugging only:

```bash
PIPELINEIQ_ALLOW_UNSIGNED_SSE=true  # never honored in production
```

## Retries and circuit breaker (M-171)

- Cache reads and subscribe go through `_redis_op_with_retry`: at most
  `SSE_REDIS_MAX_RETRIES` (default 3) attempts with exponential backoff
  plus jitter (`SSE_REDIS_BASE_DELAY_SECONDS`, default 0.1 s) and a
  per-op timeout (`SSE_REDIS_OP_TIMEOUT_SECONDS`, default 5 s).
- Consecutive failures trip a circuit breaker (`SSE_CIRCUIT_THRESHOLD`,
  default 5; cooldown `SSE_CIRCUIT_COOLDOWN_SECONDS`, default 30 s) so a
  down Redis degrades instead of becoming a retry storm.
- In-loop read failures are bounded the same way: after
  `SSE_REDIS_MAX_RETRIES` consecutive failures the stream closes
  (degraded-close) and the client reconnects with its own backoff.

## Dependency-down behavior (M-170)

- **Boot**: the lifespan checks PostgreSQL (`SELECT 1`) and fail-fasts
  with a clear `RuntimeError` when unreachable — the service refuses to
  boot broken. Skipped for non-Postgres URLs (sqlite test/dev).
- **Cache/Redis down at request time**: `_require_redis_available`
  pings pub/sub + cache before committing to a 200 stream. Failure logs
  `SSE stream refused (degraded)` and returns **503** (honest degraded
  signal), not a 200 that silently yields nothing.
- **Readiness**: `/readyz` returns 200 only when PG + Redis answer,
  else 503 `{"status": "degraded", ...}`. `/livez` and `/health` stay
  dependency-free.

## Graceful shutdown (M-169)

Lifespan shutdown disposes the DB engines and disconnects all Redis
pools inside `SSE_SHUTDOWN_TIMEOUT_SECONDS` (default 10 s). On timeout it
logs and forces close rather than hanging SIGTERM until SIGKILL.

Kubernetes must give the pod longer than this budget to drain:

```yaml
terminationGraceSeconds: 35   # > SSE_SHUTDOWN_TIMEOUT_SECONDS (10 s)
```

(Gunicorn `graceful_timeout` is 30 s; keep
`terminationGraceSeconds` above both.)

## Queue routing (M-171 context)

Celery queue responsibilities (see `docs/celery-task-queues.md`):

| Queue | Intended work | Worker |
|-------|---------------|--------|
| `critical` | Webhooks, notifications (latency-sensitive) | `worker-critical` (dedicated) + overflow from `worker-default` |
| `default` | Pipeline execution | `worker-default` |
| `bulk` | Scheduling, profiling, maintenance, reconcile | `worker-bulk` |
| `gemini` | AI calls (rate-limited) | `worker-gemini` (single replica) |
| `streaming` | Streaming daemon | `worker-streaming` |

Note: `worker-default` currently also drains `critical`
(`--queues=critical,default`). That overflow means a saturated default
queue delays critical notifications — acceptable as a fallback, but for
strict isolation run `worker-critical` dedicated and remove `critical`
from the default worker's queue list.

## Timezone-aware schedules (M-117)

`backend/scheduling/cron_utils.py`: cron wall-times are interpreted in
an IANA timezone per schedule (`tz` parameter on `get_next_run_at`,
`get_next_n_runs`, `parse_celery_crontab`; default `"UTC"`). Unknown
zones raise `ValueError` at creation. Returned datetimes are aware in the
requested zone; compare with UTC via `.astimezone(UTC)`.

## Key source files

| File | Purpose |
|------|---------|
| `backend/sse_app.py` | SSE FastAPI app, lifespan, `/livez` `/readyz` `/health` |
| `backend/api/sse.py` | Stream endpoints, caps, backpressure, retry/circuit |
| `backend/utils/sse_security.py` | HMAC sign/verify (deny unsigned by default) |
| `backend/scheduling/cron_utils.py` | Cron validation, tz-aware next-run, Celery crontab |
