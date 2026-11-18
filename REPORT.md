# w1-daemon remediation report

Branch: `fix/w1-daemon`. Scope: `backend/tasks/streaming_pipeline.py`,
`backend/scheduling/**`, `backend/api/schedules.py` (+ one-line M-152
whitelist in `backend/api/pipelines.py`). This worktree was shared with a
parallel session; commits below are the joint branch state. All tests run
on the hermetic SQLite `test_db` (fakes for Kafka/Redis); live PG :5432 +
Redis :6379-6382 were up for gate checks. Venv was rebuilt at
`/tmp/remediation-pipelineiq-20261116-071013/venv-baseline` (py3.11) after
a host restart wiped `/tmp`.

## Commits (this round)

- `1c5b643` M-009 — seed registry with consume-step name; chain
  unresolvable single-input steps with warning; joins untouched.
- `62f62b7` M-010 — publish/DLQ durability gates offset commit;
  per-partition high-water commit; commit withheld on persist failure.
- `05f8c2d` M-011 — stats row get-or-create; restart resets counters.
- `d0a5a24` M-042 — atomic PENDING/STOPPED->ACTIVE claim (single-flight),
  ACTIVE adoption only with matching task id, explicit
  `visibility_timeout=1800`, time limits stay None.
- `9668780` M-044/M-152 — transient DB errors (DBAPIError/pool timeout)
  keep the daemon alive with rollback; interruptible pause wait;
  pause checked every iteration.
- `5a2dbd0` M-078 — `request_pause/resume/stop/restart(run_id, user) ->
  (ok, error)` with conditional-UPDATE state machine; ownership check
  (owner/admin, foreign runs read as "not found"); stop revokes
  best-effort; restart dispatches then claims, revoking orphans on lost
  race; loop exit settles via `_settle_final_status` (never clobbers
  FAILED/CANCELLED). Matches sibling `fix/w1-streamapi` caller contract
  (`_call_transition` + 404/409/400 error mapping).
- `5b9499b` M-028 — `PREVIEW_MAX_N=100`, `RUN_HISTORY_MAX_LIMIT=500`,
  clamped in `get_next_n_runs` and at both endpoints.
- `a37171e` M-006/M-160/M-131 — daemon topic-ownership check at startup
  (DataAsset `topic` catalog, first-use open, admin bypass, deny
  malformed); batch-only `load`/`save` steps fail fast instead of
  silent drop; backpressure skips fetching with interruptible cooldown
  + visible SSE event (never silently skips backlog).
- `c23a053` M-071 — pause/resume conditional-UPDATE with 409 on conflict;
  toggle serializes via row lock; owner grant moved after validation
  (no orphan grants); beat uses SELECT..FOR UPDATE SKIP LOCKED,
  same-commit next_run_at advance, post-commit dispatch, dispatch
  failure marks run/ScheduleRun FAILED (never stranded PENDING).
- `37b913e` M-152 — cancel whitelist admits HEALING (revoke SIGTERMs the
  healing worker). Verified regression-first: test fails 409 pre-fix.
- `693806b` M-131 — daemon consumer uses `auto_offset_reset="earliest"`
  via `_daemon_consumer` so fresh groups replay backlog.

## Verification

- `backend/tests/unit/streaming/test_daemon_fixes.py`: 45 passed.
- `backend/tests/unit/scheduling/test_schedule_atomicity.py`: 10 passed.
- `backend/tests/unit/streaming/test_streaming_unit.py`: all pass
  (59 combined with daemon fixes).
- `backend/tests/unit/pipeline/test_cancel_healing.py` (parallel
  session): 2 passed.
- ruff clean and mypy clean on every touched source file.

## Notes / residual risks

- M-044/M-071/M-152 have uncommitted parallel-session extensions in the
  tree (`schedule_tasks` run-sync/backlog reporting,
  `test_schedule_beat_resilience.py`); left for that session to commit.
- M-006 daemon rule is first-claim-wins via the asset catalog; strict
  deny-by-default lives in sibling `backend/streaming/access.py` (their
  dir, not touched). Converge the two rules at merge.
- No migration was needed (all guards are conditional UPDATEs).
- Sibling-owned `backend/routers/streaming.py` untouched; sibling
  already routes through the M-078 transition functions.
