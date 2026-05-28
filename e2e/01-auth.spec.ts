import { test, expect } from './fixtures/auth'

test.describe('Authentication Flow', () => {
  test('Login page renders correctly', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('[data-testid="email-input"]')).toBeVisible()
    await expect(page.locator('[data-testid="password-input"]')).toBeVisible()
    await expect(page.locator('[data-testid="login-btn"]')).toBeVisible()
  })

  test('Login with valid credentials redirects to dashboard', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })
  })

  test('Login with invalid credentials shows error', async ({ page }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', 'notreal@example.com')
    await page.fill('[data-testid="password-input"]', 'wrongpassword')
    await page.click('[data-testid="login-btn"]')
    await expect(
      page.locator('[data-testid="login-error"]').or(
        page.locator('text=Invalid').or(page.locator('text=incorrect')),
      ),
    ).toBeVisible({ timeout: 5000 })
  })

  test('Unauthenticated user is redirected to login', async ({ page }) => {
    await page.context().clearCookies()
    await page.goto('/pipelines')
    await page.waitForURL((url) => url.pathname.endsWith('/login'), { timeout: 5000 })
  })

  test('Authenticated user can access protected page', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })
    await page.goto('/pipelines')
    await expect(page).not.toHaveURL(/login/)
  })
})
