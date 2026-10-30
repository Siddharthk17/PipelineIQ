# Manual testing logs — 2026-10-29 (user session)

Source: `docker compose up --build` output captured during manual browser
testing of the local stack. Kept verbatim (abridged to the distinct error
signatures) so each finding below stays reproducible.

## Finding A — recursive CTE rejected by Postgres (db-replica ERRORs)

Repeated on every `/api/catalog/assets/<name>/impact` call:

```
ERROR:  recursive reference to query "downstream" must not appear within a subquery
STATEMENT:
    WITH RECURSIVE downstream(id, depth) AS (
        ...
        UNION ALL
        SELECT ar.target_id, d.depth + 1
        ...
        WHERE d.depth < 10
          AND NOT EXISTS (
              SELECT 1 FROM downstream d2 WHERE d2.id = ar.target_id
          )
```

The endpoint returned HTTP 200 with an empty result body while the
database logged the error above, so the UI showed no blast-radius data
and no error. Same pattern existed in the upstream-lineage CTE, plus an
illegal inner `LIMIT :cte_limit` (`LIMIT in a recursive query is not
implemented`). Fix: array-path cycle detection + deduped edge source.
Ledger: S57.

## Finding B — profiling crash loop on nullable columns

`pipeline/worker-bulk` repeating every 30s (retry):

```
File "/app/backend/profiling/analyzer.py", line 271, in profile_dataframe
    if not math.isnan(numeric_series.std())
TypeError: must be real number, not NAType
...
Task tasks.profile_file[...] retry: Retry in 30s: TypeError('must be real number, not NAType')
```

`Series.std()` on a single-row nullable (Int64) frame yields `pd.NA`,
which `math.isnan` rejects. Fix: `_safe_float` helper (NA/NaN/inf → 0.0).
Ledger: S58.

## Finding C — gemini worker exception serialization chain

With no `GEMINI_API_KEY` set (expected locally), every AI call produced:

```
Task tasks.generate_pipeline_description[...] retry: ... / succeeded in 0.00...
WARNING ... gemini_client_error error=GEMINI_API_KEY environment variable not set...
ERROR/MainProcess] Pool callback raised exception: ValueError('Exception information must include the exception type')
Traceback (most recent call last): ... celery/backends/base.py ... exception_to_python ...
KeyError: 'exc_type'
```

Root cause: the task wrote a plain-dict FAILURE state via `update_state`
and then raised, so Celery's JSON result backend tried to decode the
plain dict as an encoded exception on `mark_as_failure`. Fix: drop the
manual `update_state` calls; the raised exception alone records FAILURE
and round-trips through the JSON backend. Ledger: S59.

(Informational, not a bug: `GEMINI_API_KEY environment variable not set`
warnings are expected without a key; AI features degrade gracefully.)

## Finding D — catalog N+1 description fan-out (observed, not a failure)

Opening `/catalog/pipelines` fires one `/description` request per listed
pipeline (~40 sequential GETs, all 200). Works, but chatty; batch endpoint
is a follow-up, not a defect.

## Finding E — frontend broken in manual testing + Alt+Enter report (RESOLVED where reproduced)

Manual browser pass (login, dashboard, builder, catalog+blast-radius, runs,
run detail, files, schedules, templates, wasm, storage, AI modal, command
palette): all pages render with zero console errors. No blanket frontend
breakage found; the concrete defect found was the launcher one below.

User report (2026-10-29): "entire frontend is just not working" in manual
browser testing, and "1 is just not visible when you add it into the
frontend using ALT ENTER". Under dedicated investigation with browser
automation; see ledger S60/S61.
