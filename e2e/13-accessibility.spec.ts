import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ storageState: "e2e/.auth/demo.json" });

const baseUrl = process.env.E2E_BASE_URL;

/**
 * Accessibility smoke: critical pages must have no serious or critical
 * axe violations. Moderate/minor issues are reported but do not fail —
 * they are tracked for follow-up, not release blockers.
 */
const PAGES = ["/login", "/register", "/files", "/runs", "/pipelines/new", "/schedules", "/templates"];

test.describe("Accessibility", () => {
  for (const path of PAGES) {
    test(`${path} has no serious accessibility violations`, async ({ page }) => {
      test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
      await page.goto(`${baseUrl}${path}`, { waitUntil: "domcontentloaded" });
      // Let client-side auth settle and transient states (spinners, toasts)
      // disappear so we scan the steady page, not a loading flash.
      await page.waitForLoadState("networkidle").catch(() => {});
      await page.locator(".animate-spin").waitFor({ state: "detached", timeout: 10_000 }).catch(() => {});
      await page.waitForTimeout(1000);
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa"])
        .analyze();
      const blocking = results.violations.filter((v) =>
        ["serious", "critical"].includes(v.impact ?? ""),
      );
      for (const v of blocking) {
        console.log(`AXE ${path} [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length} nodes)`);
      }
      expect(blocking, `${path} axe violations`).toEqual([]);
    });
  }
});
