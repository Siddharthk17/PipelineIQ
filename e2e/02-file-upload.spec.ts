import { test, expect } from './fixtures/auth'
import { uploadSampleCSV } from './fixtures/pipeline-helpers'

test.describe('File Upload and Profiling', () => {
  test('Upload CSV file and see it appear on files page', async ({ page, apiContext, user }) => {
    const fileId = await uploadSampleCSV(apiContext, user.token)
    expect(fileId).toBeTruthy()

    await page.goto('/files')
    await page.waitForSelector('[data-testid="file-list"]', { timeout: 10_000 })
    await expect(page.locator('text=sample_orders.csv')).toBeVisible({ timeout: 10_000 })
  })

  test('Uploaded file shows profiling status', async ({ page, apiContext, user }) => {
    const fileId = await uploadSampleCSV(apiContext, user.token)
    await page.goto(`/files/${fileId}`)

    await page.waitForSelector('[data-testid="profile-status-complete"]', { timeout: 30_000 })

    await expect(page.locator('text=customer_id')).toBeVisible()
    await expect(page.locator('text=amount')).toBeVisible()
  })

  test('Upload via file chooser on upload zone', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/files')
    await page.waitForSelector('[data-testid="upload-zone"]')

    const csvContent = 'id,name,value\n1,Alice,100\n2,Bob,200\n'
    const [fileChooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.click('[data-testid="upload-zone"]'),
    ])
    await fileChooser.setFiles({
      name: 'test_upload.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(csvContent),
    })

    await expect(
      page.locator('text=test_upload.csv').or(page.locator('[data-testid="upload-success"]')),
    ).toBeVisible({ timeout: 15_000 })
  })
})
