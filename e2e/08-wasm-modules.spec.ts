import { test, expect } from './fixtures/auth'

test.describe('Wasm Module Management', () => {
  test('Wasm modules page is accessible', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/wasm-modules')
    await expect(
      page.locator('[data-testid="wasm-manager-page"]').or(
        page.locator('text=Wasm Modules'),
      ),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('Wasm upload zone is visible', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/wasm-modules')
    await expect(page.locator('[data-testid="wasm-upload-zone"]')).toBeVisible({
      timeout: 10_000,
    })
  })

  test('Upload invalid file shows error', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/wasm-modules')
    await page.waitForSelector('[data-testid="wasm-upload-zone"]')

    const [fileChooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.click('[data-testid="wasm-upload-zone"]'),
    ])
    await fileChooser.setFiles({
      name: 'not_a_module.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('this is not wasm'),
    })

    await expect(
      page.locator('[data-testid="upload-error"]').or(
        page.locator('text=.wasm').or(page.locator('text=Invalid')),
      ),
    ).toBeVisible({ timeout: 5000 })
  })
})
