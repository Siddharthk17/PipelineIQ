import { test, expect } from './fixtures/auth'

test.describe('AI Pipeline Generation', () => {
  test('AI Generate modal opens from builder toolbar', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/pipelines/new')
    const btn = page.locator('[data-testid="open-ai-generate-btn"]').or(
      page.locator('text=Generate with AI'),
    )
    await expect(btn).toBeVisible({ timeout: 10_000 })
    await btn.click()
    await expect(
      page.locator('[data-testid="ai-generate-modal"]').or(page.locator('[role="dialog"]')),
    ).toBeVisible({ timeout: 5000 })
  })

  test('Generate button is disabled until description and files are provided', async ({
    page,
    user,
  }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/pipelines/new')
    await page.locator('[data-testid="open-ai-generate-btn"]').click()
    const generateBtn = page.locator('[data-testid="ai-generate-btn"]')
    await expect(generateBtn).toBeDisabled()
  })

  test('AI validate YAML endpoint returns structured response', async ({
    page,
    user,
  }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    const token = await page.evaluate(() => localStorage.getItem('pipelineiq_token'))
    if (!token) {
      test.skip(true, 'No token found in localStorage')
      return
    }

    const baseUrl = process.env.E2E_BASE_URL || 'http://localhost:8000'
    const response = await page.request.post(`${baseUrl}/api/ai/validate-yaml`, {
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      data: {
        yaml_text: [
          'pipeline:',
          '  name: validate_test',
          '  steps:',
          '    - name: one',
          '      type: load',
          '      file_id: dummy',
        ].join('\n'),
      },
    })
    expect(response.ok()).toBeTruthy()
    const payload = await response.json()
    expect(payload).toHaveProperty('valid')
  })
})
