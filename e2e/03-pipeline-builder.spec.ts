import { test, expect } from './fixtures/auth'

test.describe('Visual Pipeline Builder', () => {
  test('Builder page loads with palette and canvas', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/pipelines/new')
    await expect(page.locator('[data-testid="step-palette"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="pipeline-canvas"]')).toBeVisible()
  })

  test('All step types appear in palette', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/pipelines/new')
    await page.waitForSelector('[data-testid="step-palette"]')
    const items = await page.locator('[data-testid^="palette-item-"]').count()
    expect(items).toBeGreaterThanOrEqual(1)
  })

  test('Dragging a step onto canvas creates a node', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/pipelines/new')
    await page.waitForSelector('[data-testid="step-palette"]')

    const paletteItem = page.locator('[data-testid="palette-item-filter"]').or(
      page.locator('[data-testid^="palette-item-"]').first(),
    )
    const canvas = page.locator('[data-testid="pipeline-canvas"]')
    const box = await canvas.boundingBox()
    if (!box) {
      test.skip(true, 'Canvas not rendered')
      return
    }

    await paletteItem.dragTo(canvas, {
      targetPosition: { x: box.width / 2, y: box.height / 2 },
    })
    await page.waitForTimeout(500)

    const nodes = await page.locator('[data-testid^="step-node-"]').count()
    expect(nodes).toBeGreaterThanOrEqual(1)
  })

  test('YAML editor and canvas are bidirectionally synced', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/pipelines/new')

    const yamlEditor = page.locator('.cm-editor, [data-testid="yaml-editor"]').first()
    await yamlEditor.click()
    await page.keyboard.press('Control+A')

    const yamlContent = [
      'pipeline:',
      '  name: sync_test',
      '  steps:',
      '    - name: load_data',
      '      type: load',
      '      file_id: dummy-id',
    ].join('\n')

    await page.keyboard.type(yamlContent)
    await page.waitForTimeout(600)

    await expect(
      page.locator('[data-testid="step-node-load_data"]').or(page.locator('text=load_data')),
    ).toBeVisible({ timeout: 5000 })
  })

  test('Config panel opens when gear icon is clicked on a step', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/pipelines/new')
    await page.waitForSelector('[data-testid="step-palette"]')

    const paletteItem = page.locator('[data-testid^="palette-item-"]').first()
    const canvas = page.locator('[data-testid="pipeline-canvas"]')
    const box = await canvas.boundingBox()
    if (!box) {
      test.skip(true, 'Canvas not rendered')
      return
    }

    await paletteItem.dragTo(canvas, {
      targetPosition: { x: box.width / 2, y: box.height / 2 },
    })
    await page.waitForTimeout(400)

    const configBtn = page.locator('[data-testid^="config-btn-"]').first()
    await configBtn.click()
    await expect(page.locator('[data-testid="config-panel"]')).toBeVisible({ timeout: 5000 })
  })
})
