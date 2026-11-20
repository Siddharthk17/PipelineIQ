# REPORT — M-027 starlette/fastapi remediation (+ M-121 wasmtime pin)

## Scope kept
Only `backend/requirements.txt`, `backend/requirements.lock` (this report).
No app-code changes were required (see §5). No `backend/vendor/`
directory exists, so there was nothing to sync.

## 1. Baseline audit (pip-audit, OSV)
Full-requirements audit resolves `fastapi==0.109.1` → `starlette==0.35.1`.
Seven unique advisories on starlette 0.35.1 (each dual-reported, hence
"14 advisories"):
| CVE | alias | fixed in | reachable? |
|---|---|---|---|
| CVE-2024-47874 | GHSA-f96h-pmfr-66vw | starlette 0.40.0 | YES — unbounded multipart buffering via authenticated uploads |
| CVE-2025-54121 | GHSA-2c2j-9gv5-cj73 | starlette 0.47.2 | YES — spool rollover / event-loop blocking via authenticated uploads |
| CVE-2026-48710 | GHSA-86qp-5c8j-p5mr | starlette 1.0.1 | host-header → `request.url` poisoning (mitigated in-app by TrustedHostMiddleware, still patched) |
| CVE-2026-54282 | GHSA-jp82-jpqv-5vv3 | starlette 1.3.0 | request-path validation in `request.url` |
| CVE-2026-54283 | GHSA-82w8-qh3p-5jfq | starlette 1.3.1 | `max_fields`/`max_part_size` ignored for urlencoded forms |
| CVE-2026-48817 | GHSA-x746-7m8f-x49c | starlette 1.1.0 | HTTPEndpoint unrestricted method dispatch |
| CVE-2026-48818 | GHSA-wqp7-x3pw-xc5r | starlette 1.1.0 | StaticFiles Windows UNC SSRF (Linux deploy; still patched) |
Direct pins (`python-multipart==0.0.32`, `httpx==0.26.0`, `wasmtime==50.0.1`,
`pydantic` line): no known vulns. `wasmtime>=18.0.0` was unpinned (M-121).

## 2. Version choice (changelogs read: fastapi 0.109→0.133 notes, starlette notes 0.35→1.10)
Smallest fastapi clearing the reachable pair via its floor alone would be
0.116.x + starlette 0.47.x, but that leaves the five 1.x advisories
(incl. the host-header / form-limits issues named in the finding).
`fastapi==0.133.1` is the **smallest minor whose starlette range admits
1.x** (0.128–0.132 cap at `<1.0.0`; 0.133.0 "Add support for Starlette
1.0.0+", floor `starlette>=0.40.0`), so with an explicit pin it clears
**all seven CVEs**. Pinned `starlette==1.10.0` outright because 0.133.x
leaves the upper bound open (pip would float). Chosen set:
- `fastapi==0.133.1`, `starlette==1.10.0` (latest audited 1.x; needs py≥3.11, docker uses 3.11.13 ✓)
- `pydantic==2.7.0` — **forced**: fastapi≥0.126 requires `pydantic>=2.7.0`
  (audited clean; lowest satisfying version, minimal bump)
- `wasmtime==50.0.1` — pins the M-121 float at the already-deployed,
  audited-clean, test-verified minor (no 50→51 churn for zero benefit)
- unchanged: `python-multipart==0.0.32`, `httpx==0.26.0`, everything else

## 3. Breaking-change review (0.109.1 → 0.133.1 / 0.35.1 → 1.10.0)
- No 0.110–0.124 breaking changes. Relevant later ones, all checked: 0.126
  drops py3.8 (n/a); 0.129 `pydantic.v1` deprecation → 0.130 removal —
  repo never imports `pydantic.v1` (verified by grep); 0.131 deprecates
  `ORJSONResponse` — still functional, kept as `default_response_class`
  (deprecation warning only; switch is a behavior-adjacent change, left out);
  0.132 `strict_content_type` for JSON — tests/clients send proper
  `Content-Type` (no `data=` JSON posts in suite; TestClient `json=` sets it).
- Starlette 1.0 removals (`on_startup`/`on_event`/`add_event_handler`,
  `@app.route`/`@app.middleware`/`@app.exception_handler` decorators on
  Starlette): app already uses `lifespan=`; the `@app.middleware("http")` /
  `@app.exception_handler(...)` used in `main.py` are **FastAPI-level**
  decorators, retained in 0.133.1 (import of `backend.main` succeeds).
- slowapi 0.1.9 imports only stable starlette APIs; TestClient/UploadFile/
  StreamingResponse/CORSMiddleware/TrustedHostMiddleware/GZip paths
  unchanged in behavior for our usage.

## 4. Verification (CPython 3.11 clean venvs)
- `backend/tests/unit`: **1383 passed, 10 skipped** — identical to the
  pre-upgrade baseline run (1383/10).
- `backend/tests/security` + `backend/tests/e2e/test_week8_wasm_compute.py`
  (covers authenticated uploads + wasmtime execution): **120 passed, 3 skipped**.
- `ruff check backend/ --select E9,F63,F82` (CI blocking gate): **passed**.
  Full `ruff check backend/`: 2 pre-existing findings in untouched test
  files (SIM300 Yoda condition, RUF100 unused-noqa — out of scope, noted only).
- `mypy backend/` (repo config, upgrade env): **no issues in 384 files**.
- Upgraded pin set re-audited with pip-audit (direct + new transitives
  `annotated-doc`, `typing-inspection`, `pydantic-core`, `anyio`):
  **no known vulnerabilities**. Zero residual advisories — fallback
  (starlette-only 0.49.x bump) not needed.
- Lock regenerated via `pip freeze` in the clean upgrade env (installer
  shims excluded); all 49 requirements pins verified byte-identical in
  the lock; fresh-env install from the lock succeeds with identical versions.

## 5. Follow-ups (deliberately NOT done — out of scope)
- `StarletteDeprecationWarning: HTTP_422_UNPROCESSABLE_ENTITY` (use
  `HTTP_422_UNPROCESSABLE_CONTENT`) in 7 app files — warning-only, will
  break on a future starlette; recommend a rename-only commit.
- `ORJSONResponse` deprecation (fastapi 0.131) — functional; migrate
  deliberately later.
- Consider promoting key transitive pins if the lock policy changes;
  current lock now fully freezes transitives (122 entries).
