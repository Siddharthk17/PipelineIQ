# Design Spec: A–D Bug Fixes (Redpanda Console, AI Generation, Gemini 503s, Lineage 404s)

Date: 2026-05-19

## Goal
Resolve the four scoped issues (A–D) with minimal, production-safe changes:
1. Redpanda Console connection refusal.
2. AI pipeline generation missing step `type`.
3. Gemini 503 retries and fallback behavior.
4. Lineage API 404 handling in the UI.

## Non-goals
- Broader observability/logging overhaul.
- Redpanda cluster hardening or streaming audit items beyond A–D.
- New features unrelated to the four issues.

## Current State (Verified)
- **Redpanda Console**: docker-compose already points the console at `redpanda:9092`, but Redpanda advertises `PLAINTEXT://localhost:9092`, which leads clients to attempt `localhost` from inside containers.
- **AI generation**: `backend/ai/generation.py` requests YAML from Gemini and validates; it retries once with a self-fix prompt. Missing `type` fields cause validation failures.
- **Gemini retries**: `backend/tasks/gemini_tasks.py` uses exponential backoff for 429/RESOURCE_EXHAUSTED but fixed 30s retries for 500/503. Model fallback only handles model-not-found.
- **Lineage 404**: `backend/api/lineage.py` returns 404 when lineage is missing. `frontend/hooks/useLineage.ts` does not handle 404s specially.

## Proposed Changes

### A) Redpanda Console Connection Refusal
- Update console config to use the **internal** broker address: `redpanda:9093`.
- Keep external PLAINTEXT advertise (`localhost:9092`) for host access.

**Files**
- `docker-compose.yml`: update `redpanda-console` command to use `redpanda:9093`.

### B) AI Pipeline Generation Validation Errors (Missing `type`)
- Switch to **structured JSON output** from Gemini with a strict schema that requires `type`.
- Convert the JSON pipeline structure into YAML for the existing pipeline parser.
- Keep the existing self-fix loop as a fallback if structured output fails or JSON cannot be converted cleanly.

**Files**
- `backend/ai/generation.py`: add structured output request + JSON-to-YAML conversion.
- `backend/ai/prompts.py` (or equivalent): update prompt scaffolding to match JSON schema.
- Tests for AI generation and validation.

### C) Gemini 503 Retry & Fallback
- Replace static 30s retry for 500/503 with **jittered exponential backoff**.
- On the first 503, **rotate to the next fallback model** (e.g., `gemini-2.0-flash`).

**Files**
- `backend/tasks/gemini_tasks.py`: jittered backoff for 500/503.
- `backend/clients/gemini_client.py`: support rotating to the next fallback model on transient server errors.
- Tests covering backoff timing and fallback selection.

### D) Lineage 404 Handling
- Keep backend 404 behavior unchanged.
- Frontend treats 404 as “Lineage not available yet”:
  - Show empty state (no crash).
  - Retry with backoff.

**Files**
- `frontend/hooks/useLineage.ts`: add retry logic and 404 handling.
- `frontend/components` lineage UI: render empty state for 404.

## Error Handling
- Structured output failures: log and fall back to the existing YAML repair path.
- Gemini 503 after fallback: surface error and keep retry logic bounded.
- Lineage 404: handled client-side with empty state and retry; no backend change.

## Testing
- **Backend**:
  - Gemini task retry logic (500/503 with jitter).
  - Model fallback on 503.
  - AI generation JSON schema -> YAML conversion.
- **Frontend**:
  - useLineageGraph 404 handling and retry.
  - Empty state rendering.

## Rollout Notes
- No DB migrations required.
- Changes are additive and scoped to AI generation, Gemini task handling, and UI behavior.
