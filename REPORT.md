# W1 Execution Remediation Report (M-001, M-013, M-014/M-161, M-032)

Worktree branch `fix/w1-exec`, base `de93315`. Owned files only:
`backend/execution/*`, `backend/pipeline/steps/_core.py`. All verification
below was reproduced against the pre-fix code with DuckDB 2.0.0 / pandas 2.1.4
before fixing (throwaway local venv, no shared services).

## M-001 High — SQL step sandbox escape: CONFIRMED, fixed in depth

Reproduced pre-fix (validator `validate_sql_step_query` was denylist-only,
masked `'…'` literals and `"…"` identifiers before scanning, so every vector
below passed validation; execution confirmed on a live connection):
- `"read_text"('/etc/hostname')` quoted function call — passed validation;
  as a table function it returned file rows (`filename,content,size,…`).
- `query('SELECT 99 AS a')` generic inner-SQL hatch — executed, 3 rows.
- Replacement scans `FROM '/tmp/probe.csv'` / `FROM "…"` — executed, file rows.
- `glob('/etc/hostname')`, `parquet_scan`, `read_ndjson`, `csv_scan`,
  `delta_scan`, `sqlite_scan`, `query_table`, `pragma_*`, `read_*` variants —
  passed validation (name not on the short denylist).
- `WITH RECURSIVE t … SELECT x+1 … WHERE x<200000000` — wedged the
  validating python process for >120s (had to SIGKILL); the timeout probe
  `SET statement_timeout='2s'` fails on DuckDB 2.0 (no such setting).
- `{{input}}` smuggled in a comment/string still satisfied the
  placeholder check. No sub-vector was a false positive.

Fix (committed `7bcdb30`):
- Validation is deny-by-default: expanded file-reader function denylist
  (`read_*`, `*_scan`, `*_query`, `glob`, `query(_table)?`, `pragma_*`, …);
  quoted names followed by `(` stay visible to the scan; `WITH RECURSIVE`
  rejected; new `_check_table_references` allowlists every FROM/JOIN factor
  (`__input__/__left__/__right__`, CTEs defined in-query, `{…}` executor
  placeholders, `range/generate_series/unnest`) and rejects string-literal
  factors (replacement scans); `{{input}}` must appear outside
  literals/comments. 19 negative cases in
  `backend/tests/unit/execution/test_m001_sql_sandbox.py`.
- Class-level connection hardening (`harden_connection`, applied to every
  worker/local connection): `enable_external_access=false`
  (blocks readers/glob/replacement scans/`COPY…TO` even past validation),
  extension autoinstall/autoload off (no silent `sqlite_scan` reopen),
  `max_execution_time` (default 60000ms, `DUCKDB_STATEMENT_TIMEOUT_MS`
  override) since DuckDB 2.0 has no `statement_timeout`.
- Deliberate trade-off: `DUCKDB_ENABLE_HTTPFS=true` S3-direct reads now fail
  closed to the download fallback (external access stays off); silica of
  generated step SQL is unaffected (only registered Arrow relations).

## M-013 High — DuckDB join changes columns/values >50k rows: CONFIRMED, fixed

Reproduced: with overlapping non-key column `v`, pandas
`merge(…, suffixes=("_left","_right"))` yields
`[k, v_left, w, v_right, z]` while DuckDB `SELECT * … USING(k)` yields
`[k, v, w, v, z]` — duplicate column name, silent downstream corruption,
only on the DuckDB path (≥50k rows via `SmartExecutor`). Values additionally
diverge for NULL keys (SQL `=` never matches NULL; pandas matches NaN/None).
Fix (committed `78e75bd`): schema-aware join SQL — explicit aliased
projection reproducing pandas names/order/suffixes, `IS NOT DISTINCT FROM`
null-safe key matching, per-`how` key coalescing (`FULL OUTER` keeps
`COALESCE(l,r)`); legacy `USING` kept only when schemas are unknown.
8 equivalence tests (4 join kinds × plain/NULL-key + no-overlap) in
`test_m013_join_equivalence.py`.

## M-014/M-161 Medium — `contains` regex (pandas) vs literal LIKE (DuckDB), LIKE wildcards unescaped: CONFIRMED, fixed by aligning to literal

Reproduced: value `50%` matched `500 off`/`50X off` on the DuckDB path
(`%` wildcard) while pandas treated it as regex; value `a_b` matched `axb`
in DuckDB but literally-nowhere/elsewhere in pandas — engines disagreed in
both directions. Fix: pandas `contains`/`not_contains` now pass
`regex=False` (`_core.py`); `build_filter_sql` backslash-escapes
`\`/`%`/`_` and appends `ESCAPE '\'` for all four LIKE operators
(`contains`, `not_contains`, `starts_with`, `ends_with`). 9 tests in
`test_m014_like_escaping.py` assert identical literal results on both engines.

## M-032 High — user-supplied regex ReDoS: CONFIRMED, fixed (same root cause)

Reproduced: `(a+)+$` against `"a"*30+"!"` via `Series.str.contains`
(regex default) hung the worker (probe had to be killed — catastrophic
backtracking, linear in nothing). Fix: the literal-`contains` change above
removes the regex engine from the user-controlled path entirely, so no
pattern can backtrack; 3 timing tests in `test_m032_redos.py` prove the evil
pattern completes in milliseconds and matches only literal text. No other
user-controlled regex sink exists in the owned files (`re.*` in
`sql_builder.py` scans fixed SQL text, never a user pattern).

## Verification (fresh venv, Python 3.11, repo requirements)

- 43/43 new regression tests pass (M-001/M-013/M-014/M-032 files).
- 110/110 pass for `backend/tests/unit/execution/` + `test_steps.py`
  (no existing `contains`/join/SQL-builder behavior broken).
- `ruff check` clean on owned source + new tests (repo format not enforced —
  base files already non-`ruff-format`-clean, left untouched).
- `mypy` clean on `sql_builder.py`, `duckdb_executor.py`, `_core.py`.
