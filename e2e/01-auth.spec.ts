import { expect, test } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL;
const email = process.env.E2E_EMAIL ?? "demo@pipelineiq.app";
const password = process.env.E2E_PASSWORD ?? "Demo1234!";

test.describe("Authentication Flow", () => {
  test.beforeEach(({ page }) => {
    test.skip(!baseUrl, "Set E2E_BASE_URL to run Playwright tests.");
  });

  test("Login page renders correctly", async ({ page }) => {
    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("email-input")).toBeVisible();
    await expect(page.getByTestId("password-input")).toBeVisible();
    await expect(page.getByTestId("login-btn")).toBeVisible();
  });

  test("Login with valid credentials redirects away from login", async ({ page }) => {
    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
    await page.getByTestId("email-input").fill(email);
    await page.getByTestId("password-input").fill(password);
    await page.getByTestId("login-btn").click();
    await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 15_000 });
  });

  test("Login with invalid credentials shows error", async ({ page }) => {
    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
    await page.getByTestId("email-input").fill("notreal@example.com");
    await page.getByTestId("password-input").fill("wrongpassword");
    await page.getByTestId("login-btn").click();
    await expect(page.getByTestId("login-error")).toBeVisible({ timeout: 5_000 });
  });

  test("Unauthenticated user is redirected to login", async ({ page }) => {
    await page.context().clearCookies();
    await page.goto(`${baseUrl}/pipelines`, { waitUntil: "domcontentloaded" });
    await page.waitForURL((url) => url.pathname.endsWith("/login"), { timeout: 10_000 });
  });

  test("Authenticated user can access protected page", async ({ page }) => {
    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
    await page.getByTestId("email-input").fill(email);
    await page.getByTestId("password-input").fill(password);
    await page.getByTestId("login-btn").click();
    await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 15_000 });
    await page.goto(`${baseUrl}/catalog`, { waitUntil: "domcontentloaded" });
    await expect(page).not.toHaveURL(/login/);
  });
});
