# Wave 3 — Data layer remediation report (M-035, M-079, M-095, M-097, M-099, M-107, M-137, M-162, M-172)

Scope: `backend/alembic/versions/*` (new revisions only), `backend/models/**`
(model-side), `backend/db/**` (read paths), `scripts/backup.sh` /
`scripts/restore.sh` (object-backup additions only). No other files touched.
Base: post-Wave-2 HEAD. Env: shared venv, throwaway PG `m035_scratch`
(empty) + `m035_pop` (populated), SQLite in-memory.

## M-035 — 1a158ef49016 dropped ~14 indexes/constraints: FIXED

Verified first: at head (0019) on a fresh PG, 19 of 21 dropped objects
were missing (`pg_indexes`/`pg_constraint`); only the replacement unique
indexes `ix_users_email/username` and the unnamed-constraint artifact
`file_profiles_file_id_key` existed.
Fix: new revision `0020_restore_dropped_indexes` re-creates every dropped
object with its ORIGINAL definition (cross-checked against the introducing
revision and the 1a158ef49016 downgrade): 17 indexes — incl. GIN-trigram
`idx_data_assets_name_trgm … USING gin (name gin_trgm_ops)`, both
`(x, created_at DESC)` run-listing indexes, partial
`idx_pipeline_schedules_active WHERE (is_active = true)` — plus 4 named
unique constraints (`users_email_key`, `users_username_key`,
`pipeline_permissions_pipeline_name_user_id_key`,
`uq_healing_attempts_run_attempt`) and the unique index
`idx_file_profiles_file_id`. Models declare the same objects, so
`alembic check` is clean for all of them.
Tested: `upgrade head` on empty PG (17 indexes + 4 constraints present,
indexdefs verified verbatim); `downgrade 0019` (objects gone, named
`file_profiles_file_id_key` restored); full down→up cycle on a populated
DB (13 rows across 11 tables intact); duplicate email / file-profile /
healing-attempt inserts correctly rejected afterwards.

## M-079 — downgrade / offline-SQL broken; PG-only chain: FIXED (in scope)

Verified: `alembic upgrade base:head --sql` dies at `a1b2c3d4e5f6`
(`op.get_bind()` + `sa.inspect` on a MockConnection); downgrade of
1a158ef49016 dies with `Can't emit DROP CONSTRAINT … it has no name`
(the `create_unique_constraint(None, …)` on file_profiles).
Fix (forward, new revision only — old revisions are frozen): 0020 uses
exclusively NAMED constraints; PG-only DDL (pg_trgm, catalog surgery) is
guarded by `op.get_context().dialect.name`, which works online AND
offline (`--sql`) — verified for both `postgresql` and `sqlite` URLs.
The file_profiles artifact is dropped via a catalog lookup (no stable
name exists) and the downgrade restores the same state with an explicit
name, so this revision round-trips.
Honest limits: full-chain offline `--sql` is STILL broken by the frozen
revisions (`a1b2c3d4e5f6`, `b315b6809272` use `op.get_bind()`); anyone
needing offline SQL must fix those in their owning wave. SQLite dev DBs
remain `create_all`-based; `alembic upgrade` on SQLite is best-effort.

## M-095 — save step uploads to public S3 under default local: VERIFIED, HANDOFF

`backend/execution/steps/save_step.py::execute_save_step` unconditionally
`put_object`s to MinIO (`_get_minio_client`) with no local-storage
branch; the storage backend selector lives in
`backend/services/storage_service.py` + `backend/config.py` — all outside
this worktree's owned files. Model-side is neutral (StepResult columns
are backend-agnostic; no S3 defaults in models). Handoff: owning wave
must make `STORAGE_TYPE=local` (default) keep save-step output local and
require explicit opt-in (e.g. `SAVE_STEP_ALLOW_S3=true` + warning) before
any user bytes leave the box.

## M-097 — runtime paths broken on SQLite: FIXED (owned paths)

`create_all` + inserts across all 13 touched tables pass on SQLite and
every restored index/unique-constraint is emitted (plain btree
equivalents; GIN/partial kwargs are PG-only and ignored elsewhere).
Concrete fixes: `PipelineSchedule` partial index uses
`postgresql_where` + `sqlite_where` (same pattern as `data_contract.py`);
`DataAsset`/`AssetRelationship` PKs moved from
`server_default=func.gen_random_uuid()` (PG-only, dashless-hex hack on
SQLite) to portable Python-side `default=_generate_uuid`, matching every
other model. `backend/db/*` read paths needed no change (`minio_client`
already fails closed without an endpoint).

## M-099 — String() overflow on long input: VERIFIED (model-side), HANDOFF

Metadata scan: zero bare `String()` columns — every bounded column
carries an explicit length; the 21 unbounded columns are all intentional
`Text()` (YAML configs, error messages, user agents). 500-vs-400
behavior is boundary validation in routes/services (not owned files).
Handoff: owning wave should validate lengths at the API boundary and
return 400 (e.g. shared `String(max)` / pydantic `max_length` guards);
DB lengths are hereby confirmed as the source of truth.

## M-107 — orphaned uploads/files GC: PARTIAL (model-side), HANDOFF

Verified: no sweep exists for `uploaded_files`/storage objects (only
shm/spill cleanup in `arrow_bus` and the catalog-orphan *listing*).
In-scope fix: new `ix_uploaded_files_created_at` index supports the
sweep query (unreferenced rows older than retention horizon, oldest
first); delete-ordering note — row commit then storage delete strands
objects on crash, storage delete then commit strands rows; either way a
periodic reconciler is required, which is why the index matters.
Handoff: owning wave must add the Celery-beat orphan-sweep task
(DB-vs-storage reconciliation + consistent delete ordering) reusing the
`reconciliation.py`/beat patterns.

## M-137 — backups cover only PG: FIXED + TESTED

`scripts/backup.sh`: after the untouched PG dump, captures objects —
`mc mirror --overwrite` of `$S3_BUCKET` (+ `$OBJECT_EXTRA_BUCKETS`)
to `backups/objects-<stamp>/` with a manifest, and/or tars
`$OBJECT_SOURCE_DIR` (local mode) to `backups/objects-local-<stamp>.tar.gz`.
`OBJECT_BACKUP_ENABLED=auto|1|0` (+ `--objects-only`, `MC_BIN`,
`MC_ALIAS` overrides); `=1` with no source fails loudly.
`scripts/restore.sh`: `--with-objects DIR` validated BEFORE writers stop
(manifest/tarball integrity, `mc` present), restored AFTER the DB load.
Tested with a stub `mc` (alias+mirror as `cp -r`) over a fake MinIO root
plus a real local dir: backup captured 2 bucket objects + 1 local file;
after simulated total loss, restore reproduced all bytes identically;
missing-objects-dir aborts with zero `docker` calls; `auto` with no
source skips gracefully; `bash -n` clean.

## M-162 — UUID-vs-string on SQLite: FIXED

SQLite's dynamic typing hid three lying annotations, all corrected to
`Mapped[uuid.UUID | None]`: `PipelineRun.user_id`,
`UploadedFile.previous_version_id`, `AuditLog.user_id` (each is a
`Uuid` FK column; assigning `str(...)` fails on PG). `DataAsset`/`AssetRelationship`
PKs no longer depend on the DB `gen_random_uuid()` (see M-097).
Insert + UUID-type assertions pass on both SQLite and PG.

## M-172 — schema-vs-models drift: RECONCILED (owned paths)

`alembic check` on the populated head DB is now clean for every owned
object. Drift fixed: `WasmModule.name` lost its stale `index=True`
(0019 replaced the global unique index with composite
`uq_wasm_modules_user_name`); `HealingAttempt` declares the pre-existing
`ix_healing_attempts_pipeline_run_id` explicitly (its `index=True`
generated a phantom `ix_healing_attempts_run_id` instead);
`FileProfile` uses the original unique *index* (not a constraint);
`User` email/username use explicitly named constraints + unique indexes
(PG-identical names, M-079-safe); `PipelineRun` keeps the surviving ASC
`ix_pipeline_runs_user_created` alongside the restored DESC `idx_`
variants; `DataAsset`/`AssetRelationship` PK defaults match.
Remaining `alembic check` output (21 items: `storage_events` table absent
from models, catalog/webhook/idempotency extras, `consumers` JSON/JSONB)
is pre-existing drift in other waves' files — listed, not touched.
