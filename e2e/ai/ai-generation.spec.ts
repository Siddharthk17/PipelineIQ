// Week 5 e2e scaffold intentionally kept framework-agnostic to avoid tooling drift.
// Playwright scenarios to automate in a dedicated e2e setup:
// 1. Login via /login using [data-testid="email-input"], [data-testid="password-input"], [data-testid="login-btn"].
// 2. Open /pipelines/new, click [data-testid="open-ai-generate-btn"], confirm [data-testid="ai-generate-modal"] is visible.
// 3. Close modal with [data-testid="ai-modal-close"] and verify modal hides.
// 4. Validate [data-testid="ai-generate-btn"] remains disabled until prompt text exists in [data-testid="ai-description-input"] and a file is selected.
// 5. Open /runs and verify failed rows ([data-status="failed"]) expose [data-testid="repair-pipeline-btn"].
export {};

