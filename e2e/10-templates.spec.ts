import { test, expect } from './fixtures/auth'
import { uploadSampleCSV } from './fixtures/pipeline-helpers'

test.describe('Pipeline Templates', () => {
  test('Templates page shows templates', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/templates')
    await page.waitForSelector('[data-testid="template-card"]', { timeout: 10_000 })
    const count = await page.locator('[data-testid="template-card"]').count()
    expect(count).toBeGreaterThanOrEqual(1)
  })

  test('Fork template via API with correct file mappings', async ({
    apiContext,
    user,
  }) => {
    const fileId = await uploadSampleCSV(apiContext, user.token)

    const resp = await apiContext.post('/api/templates/customer_segmentation/fork', {
      data: {
        pipeline_name: `e2e_forked_${Date.now()}`,
        file_mappings: { orders_file_id: fileId },
      },
    })
    expect(resp.ok()).toBeTruthy()
    const data = await resp.json()
    expect(data.yaml).toContain(fileId)
  })

  test('Fork template with missing placeholder returns 400', async ({ apiContext }) => {
    const resp = await apiContext.post('/api/templates/sales_revenue_report/fork', {
      data: {
        pipeline_name: 'bad_fork',
        file_mappings: {},
      },
    })
    expect(resp.status()).toBe(400)
  })
})
