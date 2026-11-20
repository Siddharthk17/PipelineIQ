# W2 Contract Remediation Report

Worktree: `/var/tmp/remediation-wt/w2-contract`, branch `fix/w2-contract`.
Scope owned: `backend/api/contracts.py`, `backend/contracts/**`,
`backend/models/data_contract.py`, `backend/api/pipelines.py` (run-claim +
name validation + idempotency on run/create endpoints only),
`backend/api/versions.py` (reviewed, no change), new Alembic revisions only.

All findings were reproduced with failing regression tests BEFORE fixing
(16-test temp file, since removed); all passed AFTER. Module suites:
`unit/contracts` + `unit/api/test_api.py` + `test_run_ownership_oracle.py` +
`test_contract_isolation.py` + `unit/week11` + `unit/pipeline/test_versioning`
green; `ruff` + `mypy` green on all touched files.

## M-007 — concurrent first-run claim grants OWNER twice (High)

Verified: two threads racing `_check_pipeline_permission(..., grant_owner=True)`
both read "name free" and both inserted OWNER rows.
Root cause: check-then-act with no uniqueness guard on the owner grant.
Fix (`pipelines.py` + migration `0018`): attempt the OWNER insert and catch
`IntegrityError`; on conflict re-read — proceed only if this user already
holds a row, else 404. DB backstop: partial unique index
`uq_pipeline_permissions_owner_name` on `(pipeline_name)` WHERE
`permission_level='owner'` (the `(pipeline_name,user_id)` unique constraint
already existed from `1a158ef49016` and was not recreated).
PG-verified: second OWNER insert fails, RUNNER coexists.

## M-008 — contract create/update race (High)

Verified: racing creates produced duplicate `version` values and two active rows
(`create` never deactivated; `update` read-then-inserted).
Fix (`contracts.py::_create_contract_version`, model, migration `0018`):
single-statement deactivate (`UPDATE ... WHERE is_active`) + insert in one
transaction; base version from `MAX(version)`; retry (x3) on `IntegrityError`;
unique `(pipeline_name, version)` + partial unique active index named
`uq_pipeline_contracts_name_version` / `uq_pipeline_contracts_active_name`
(model + migration, SQLite `sqlite_where` + PG `postgresql_where`).
All `order_by(version)` readers gained deterministic `id` tiebreakers.

## M-030 — /status /breaches shadowed by /{contract_id} (High)

Verified: `GET .../status` was captured as `contract_id="status"` and 500'd
on UUID bind (`StatementError`).
Fix: literal routes registered before `/{contract_id}` in `contracts.py`;
plus `validate_uuid_format(contract_id)` (422) on get/update/delete.

## M-073 — contracts fail open (High), M-059 — min/max aborts (Med)

Verified in `backend/contracts/validator.py` (post-run engine): `pattern`/`regex`
keys were never evaluated; non-dict specs silently skipped; NULLs allowed when
`nullable` unspecified; `pc.less/greater` on a wrong-typed column raised and
aborted the whole check (caught upstream as "run continues" = breach missed).
Fix: `pattern`/`regex` evaluated (`pattern_mismatch`/`invalid_regex` errors);
unknown declared `type` -> `unknown_dtype` error; non-dict spec ->
`invalid_spec` error; nullable is fail-closed (`nullable: true` explicitly
required, string spellings like `"false"` do NOT permit nulls); every
per-column compute (min/max/allowed/unique/thresholds/row bounds) wrapped so
evaluation errors become `error` violations instead of aborting validation.
Sibling (NOT in scope, needs owner): `backend/pipeline/contracts.py`
`ColumnContract.validate` still ignores `regex`, its dtype check
`DTYPE_MAP.get(actual)` skips unknown actual dtypes, and `not_null` defaults
off — same fail-closed treatment should be applied there.

## M-062 — no idempotency on creates (High)

Verified: repeated `POST /pipelines/run` / contract-create with the same
`Idempotency-Key` created duplicate resources.
Fix: new `idempotency_keys` table (model `IdempotencyKey` + migration `0018`,
unique `(scope,key,user_id)`); claim-placeholder pattern collapses concurrent
duplicates (placeholder row, 5s in-flight wait, loser replays winner);
sequential replays return the original body. Scopes implemented:
`pipeline_run` (both `/run` endpoints) and `contract_create`.
Out of scope (other owners): file/schedule/webhook creates. Cancel of an
already-cancelled run still 409s in `pipelines.py::cancel_pipeline_run` —
changing it to 200 was explicitly outside the allowed edit area.

## M-088 — invalid YAML stored active (Med)

Verified: garbage/non-dict YAML `POST`ed to contract-create returned 201.
Fix: `_validate_contract_yaml` (parse + non-empty mapping, else 422) in
create/update. Shape checks stay lenient (schemaless dicts still accepted —
`test_contract_isolation` stores `{"contracts": [...]}` and expects 201).

## M-089 — name squatting before validation (Med)

Verified: `POST /pipelines/run` with an unknown `name` + validation-failing
YAML created an OWNER permission row for the attacker before the 404.
Fix: `run_pipeline` + legacy endpoint now order as parse -> `_validate_claim_name`
(format: alnum start, `[A-Za-z0-9_.\\- ]`, <=255, else 422) -> parser validation
-> schema check -> permission claim -> create. Sibling (NOT in scope):
`schedules.py` also calls `_check_pipeline_permission(..., grant_owner=True)`.

## M-076 — consumers block others' schedules (Med)

NOT in scope: enforcement lives in
`backend/tasks/pipeline_tasks.py::_block_downstream_schedules` (untouched).
Recommend scoping downstream blocks to schedules owned by the contract owner
or gating on an explicit opt-in.

## Migration testing

`0018_contract_race_idempotency` (down_revision `0017`): full `upgrade head`
from scratch on throwaway PG `w2_scratch`, semantic checks (2nd OWNER blocked,
RUNNER allowed, dup version blocked, 2nd active blocked), `downgrade -1` +
re-`upgrade head` clean, single head. SQLite `create_all` verified for model
constraints. Note: the single-owner partial index exists only in the migration
(`PipelinePermission` model file is outside this worktree's scope); fresh
`create_all` DBs should run migrations to get it.

## Commits

- `d20c799` validator fail-closed (M-073, M-059)
- DB hardening (M-007, M-008, M-062) / contracts.py (M-030, M-008, M-088, M-062)
  / pipelines.py (M-007, M-089, M-062) / this report

## Follow-up round

### M-076 — consumers block other tenants' schedules (Med)

Verified: a contract with `consumers: [victim_pipeline]` returned 201; on a
block-severity breach `_block_downstream_schedules` (pipeline_tasks, not in
scope) pauses every active schedule with that name — including the victim's.
Fix (`contracts.py::_validate_consumers`, enforced on create+update):
entries matching an existing pipeline require owner-level access (own
contract, OWNER grant, or admin), else 422. Non-matching entries (e-mails /
Slack targets, future names) stay allowed.
Residuals: (a) the worker-side matcher itself is untouched — recommend the
exec cluster scope it to (owner, pipeline); (b) a predicted future name
cannot be distinguished from a notification target.

### M-154 — WASM module name globally unique (Low)

Verified: same name registered by two users hit `UNIQUE (wasm_modules.name)`
-> IntegrityError/500, and the 409-vs-500 difference oracles name ownership.
Fix: `WasmModule.name` lost `unique=True`, gained
`UniqueConstraint("user_id","name")` (`uq_wasm_modules_user_name`) in the
model plus new revision `0019_wasm_user_scoped_names` (down_revision `0018`;
id kept <=32 chars for `alembic_version.version_num`). 0010 created the
enforcement as unique *index* `ix_wasm_modules_name` (unique+index on one
column), so the migration drops that index, not a table constraint. No data
migration needed — the old global uniqueness precludes duplicates.
Verified on throwaway PG: full-chain `upgrade head`, cross-user same name
allowed, same-user duplicate blocked, `downgrade -1` (refuses while dupes
exist, succeeds once clean), re-`upgrade head`, single head.

### M-077 remainder — wire aiwasm ownership helpers into run start (Med)

Verified: a run referencing another user's `wasm_file_id` returned 202 and
queued execution (the worker `_load_wasm_modules` loads any id by design).
Fix (`pipelines.py`): `_validate_wasm_ownership` collects `wasm_file_id`s
from the parsed config and calls aiwasm's `assert_wasm_ids_owned` inside
both run-start validators, before the ownership claim — missing and foreign
ids both 404 as "Wasm module not found" (no oracle), no run row created.
No SAVE-time YAML hookup exists in owned code (`increment_pipeline_usage`
has no callers); the queue-time check is the single choke point, and the
worker-side loader remains unscoped — recommend the exec cluster re-check
there (defence in depth).
