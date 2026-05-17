// Week 4 e2e scaffold intentionally kept framework-agnostic to avoid tooling drift.
// Scenarios to automate with Playwright/Cypress in a dedicated e2e setup:
// 1. Open /pipelines/new and verify step palette + canvas render.
// 2. Drag load/filter/save nodes and connect them without cycle.
// 3. Open config panel, edit SQL query, save, and confirm YAML sync.
// 4. Upload a file, run the pipeline, and verify run monitor progress.
// 5. Validate failed graph connection rules (duplicate edge, cycle, join over-capacity).
export {};
