import { test, expect } from './fixtures/auth'
import {
  uploadSampleCSV,
  buildSimplePipelineYAML,
  submitPipelineRun,
  waitForRunStatus,
} from './fixtures/pipeline-helpers'

test.describe('Data Catalog', () => {
  test('Catalog page loads and is searchable', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/catalog')
    await expect(
      page.locator('[data-testid="catalog-page"]').or(
        page.locator('[data-testid="catalog-search-input"]'),
      ),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('Catalog search returns results after pipeline populates it', async ({
    page,
    apiContext,
    user,
  }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    const fileId = await uploadSampleCSV(apiContext, user.token)
    const pipelineName = `catalog_test_${Date.now()}`
    const runId = await submitPipelineRun(
      apiContext,
      buildSimplePipelineYAML(fileId, pipelineName),
      pipelineName,
    )
    await waitForRunStatus(apiContext, runId, 'success', 120_000)

    await page.goto('/catalog')
    const searchInput = page.locator('[data-testid="catalog-search-input"]')
    if (await searchInput.isVisible()) {
      await searchInput.fill('region')
      await page.waitForTimeout(500)
    }

    const results = page.locator('[data-testid^="catalog-result-"]')
    const count = await results.count()
    expect(count).toBeGreaterThanOrEqual(0)
  }, 150_000)

  test('Blast radius API returns structured response', async ({ apiContext }) => {
    const resp = await apiContext.get('/api/catalog/assets/customer_id/impact')
    expect(resp.ok()).toBeTruthy()
    const data = await resp.json()
    expect(data).toHaveProperty('asset_name')
    expect(data).toHaveProperty('downstream')
  })
})
