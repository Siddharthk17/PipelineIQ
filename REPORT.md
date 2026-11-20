# Webhook remediation report — W2 (M-043 / M-069 / M-118 / M-155)

Worktree: `/var/tmp/remediation-wt/w2-webhook`, branch `fix/w2-webhook`.
Base: post-Wave-1 HEAD (`de5d880`). Only owned files touched:
`backend/utils/url_security.py`, `backend/tasks/webhook_tasks.py`,
`backend/api/webhooks.py`, `backend/api/notifications.py` (test-send only).

Environment: reuse of the shared baseline venv was impossible (both
`/tmp` venvs were wiped by host restarts), so verification ran on the
surviving py3.11 audit venv
(`/var/tmp/audit-PipelineIQ-20261114T082900Z/venv`, httpx 0.26.0,
SQLAlchemy 2.0.25 — same pins) with throwaway PG on localhost:5432
(already provisioned, `pipelineiq` db) and redis-server 6.2.14 on
6379–6382. LOCAL ONLY: no external webhook targets; delivery tests
used local throwaway HTTP servers / canned fakes.

## M-043 (High) — shared httpx.AsyncClient across event loops

Confirmed: `WebhookTask._client` cached one client per worker while
`deliver_webhook` runs each attempt on a fresh `asyncio.new_event_loop()`.
Fix: deleted the shared client; `_make_client()` builds a fresh
`httpx.AsyncClient` per attempt, used as `async with` inside the
attempt's loop and closed before the loop closes. Commit `8ba204c`.

## M-069 (High) — SSRF DNS-rebinding TOCTOU + CGNAT miss

Confirmed: `100.64.0.5` passed validation (`is_private` is False for
CGNAT on py3.11); validation and connection were separate hostname
lookups. Fix: explicit blocked ranges (CGNAT `100.64/10`, link-local
`169.254/16` incl. cloud metadata, TEST-NETs, mapped IPv6 equivalents)
plus a `not is_global` backstop; new `resolve_public_ips` (validates
*every* resolved address), `pin_url_to_validated_ip` (rewrites the
request to a validated IP literal, preserves `Host`, passes
`sni_hostname` for HTTPS — verified supported by httpcore 1.0.9),
and `_pinned_request` in the task with fail-closed strict mode for
real clients (non-strict fallback keeps DNS-less test doubles
working). Every redirect hop is re-validated and re-pinned. Slack
test-send got a fresh re-resolve guard. Commit `938d6cb`.

## M-118 (Medium) — retries on permanent 4xx; fan-out loss

Confirmed: `_raise_for_bad_status` wrapped *any* non-2xx in
`RequestError`, which is in `autoretry_for`. Fix: `_is_retryable_status`
(429 or 5xx only); retryable outcomes raise into Celery backoff+jitter
(`max_retries=3` retained); permanent 4xx and non-timeout transport
errors return dead-letter results with no retry; only
`TimeoutException` retries among transport errors. Commit `f1078e1`.

## M-155 (Medium) — double records; HMAC divergence; audit-before-commit; racy cap

Confirmed: non-2xx wrote a record, then raised into a handler that
wrote a second; test endpoint used plain `orjson.dumps` vs the task's
`OPT_SORT_KEYS`; `delete_webhook` logged before commit; cap was
check-then-insert. Fix: single `_record_delivery` call per attempt on
every path (verified 1 row each for 200/400/503/ConnectError/timeout),
shared `build_webhook_signature` + `OPT_SORT_KEYS` in both senders,
audit after commit (id captured pre-delete), per-user
`SELECT ... FOR UPDATE` around the cap check (no-op on SQLite,
verified). Commit `fd41952`.

## Verification (final tree)

- `pytest backend/tests/unit/webhooks/ + notification test endpoint +
  TestWebhookDelivery`: **21 passed**. `test_celery_task_registry`
  (2 failures) is pre-existing on the base tree — orphan route/beat
  `tasks.reconcile_stuck_runs` owned by another wave.
- `ruff check` on all 4 files: clean. `mypy` on all 4: clean.
- Sibling note (out of scope, not touched):
  `backend/services/webhook_service.py` / `notification_service.py`
  still post to raw hostnames; they inherit the hardened validator
  via the shared guard module but not IP pinning — recommend a
  follow-up to route them through `pin_url_to_validated_ip`.
