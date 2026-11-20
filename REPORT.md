# Wave-3 Reliability Report (w3-reliab)

Branch: `fix/w3-reliab`. All work is LOCAL only (throwaway PG on
`/tmp/pg-w3-run:55433`, closed-port Redis simulation). No streaming
daemon, schedule, contract, or auth redesign — as scoped.

## M-026 — webhook delete 500 (append-only RULE vs cascade)

* Verified: `DELETE FROM webhooks` with history fails on PG under the
  `webhook_deliveries_no_delete` RULE (`InternalError` — the 500), and
  silently cascades on SQLite.
* Fix (`backend/api/webhooks.py`): delete with history now archives
  (`is_active=False`, deliveries retained and still listed); delete
  without history still hard-deletes. Archived webhooks stop firing
  (fan-out already filters `is_active`) and no longer consume the
  10-active cap. Commit failures roll back (M-173).
* Tests: `test_m026_archive_delete.py` (3). Mechanism proven on
  throwaway PG with the RULE installed.

## M-034 — limiter fail-closed on Redis outage

* Verified: slowapi `Limiter` without fallback raises `ConnectionError`
  on dead Redis (control test).
* Fix (`backend/utils/rate_limiter.py`): `in_memory_fallback_enabled=True`
  — per-process memory accounting inherits route limits; slowapi probes
  and heals when Redis returns. Plus import-time backend self-check log
  (`log_rate_limit_backend_status()`). Auth revocation untouched
  (still fail-closed in `backend/auth.py`).
* Tests: `test_m034_limiter_fallback.py` (5).

## M-080 — dropped unique constraints (permissions/healing)

* Verified: duplicates insertable on SQLite (models lacked constraints
  the migrations declared).
* Fix: `UniqueConstraint`s restored in models + new idempotent PG
  migration `0020_perm_heal_uq_backfill` (skips when the column set is
  already enforced, e.g. auto-named legacy constraint; aborts
  fail-closed listing duplicate groups). Revision id kept ≤32 chars —
  `alembic_version.version_num VARCHAR(32)` rejects longer.
* Verified on throwaway PG: upgrade + downgrade + re-upgrade on empty
  DB; upgrade on populated-clean DB; abort on populated-dirty DB.
  Heads: single head `0020_perm_heal_uq_backfill`.
* Tests: `test_m080_unique_constraints.py` (4).

## M-081 — append-only RULEs vs deletion

* Webhook half (owned): archive pattern above. Remaining RULE tables
  (`contract_violations`, `healing_attempts`, audit trigger) belong to
  contract/data waves — no change here; same pattern recommended.

## M-082 — AUTO_CREATE_TABLES quoting

* Verified root cause: plain-string `server_default`s render quoted —
  `"'warn'"` → `DEFAULT '''warn'''`, rejected by PG
  (`invalid input value for enum contractseverity`), so `create_all`
  failed on fresh PG.
* Fix: all string defaults converted to `text(...)` across
  `models/__init__.py`, `data_contract.py`, `column_policy.py`.
  Verified `create_all` + actual default values on fresh PG, and full
  SQLite suite green.
* Tests: `test_m082_server_defaults.py` (4).

## M-116 — dead fallback machinery

* Audited owned files: every function is referenced
  (`deliver_webhooks_task` ← `pipeline_tasks`,
  `fire_webhooks_for_event` ← `webhook_service`, all helpers internal;
  reconciler helpers + beat task all live). Redpanda fallback
  (`try_drain_fallback`) is drained from `streaming_pipeline` (Wave-1).
  Nothing dead — locked with wiring tests instead of deleting.
* Tests: `test_m116_wired.py` (4).

## M-168 — shared mutable state

* `FileProfile.file_id unique=True` confirmed — headline race kept
  impossible. `to_thread`-with-Session (`routers/catalog.py`),
  `RLock`s, signal use: none in owned modules (verified by grep);
  sibling owners handle their instances.
* Tests: `test_m168_shared_state.py` (uniqueness + static guard).

## M-173 — atomicity / session leaks

* `reconcile_stuck_runs`: per-run commit now rolls back and continues
  (one poisoned commit no longer wedges the sweep).
* `webhooks.py` create/delete: commit failures roll back before
  propagating. `webhook_tasks.py` fan-out sessions: explicit rollback
  on query failure.
* Tests: `test_m173_reconciler_atomicity.py`,
  `test_m173_webhook_atomicity.py` — all fail pre-fix, pass post-fix.

## M-162 — UUID-vs-string on SQLite

* Reproduced live: string `user_id` vs `Uuid` column raised
  `StatementError` on SQLite in `fire_webhooks_for_event`.
* Fix (`webhook_tasks.py`): `_coerce_user_id()` best-effort coercion in
  both fan-out paths; garbage ids skip (never broaden to all users).
* Tests: `test_m162_sqlite_uuid.py` (4, all failed pre-fix).

## Validation

* Owned areas: 162 passed, 1 skipped.
* Full `backend/tests/unit`: 1247 passed, 10 skipped.
* `ruff check` clean on all touched files (pre-existing format drift
  left alone); `mypy` clean on all touched sources.
* Commits: one per finding (`M-026`, `M-034`, `M-080`, `M-082`,
  `M-173`, `M-162+M-116`, `M-168`), plus this report.
