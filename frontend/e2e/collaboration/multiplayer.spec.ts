import { test, expect, chromium } from '@playwright/test'

const BASE_URL = process.env.BASE_URL ?? 'http://localhost:3000'

test.describe('Multiplayer Pipeline Collaboration', () => {
  test.beforeAll(async () => {
    const net = await import('net')
    const isOpen = await new Promise<boolean>(resolve => {
      const socket = net.createConnection(1234, 'localhost')
      socket.on('connect', () => { socket.destroy(); resolve(true) })
      socket.on('error', () => { resolve(false) })
    })
    if (!isOpen) {
      console.error('Y-WebSocket server is not running on port 1234')
      test.skip()
    }
  })

  test('Y-WebSocket server is accessible', async ({ page }) => {
    const resp = await page.request.get('http://localhost:1234')
    expect(resp.status()).toBeLessThan(500)
  })

  test('two users editing the same pipeline see the canvas', async () => {
    const browser = await chromium.launch()

    const contextA = await browser.newContext()
    const pageA = await contextA.newPage()

    const contextB = await browser.newContext()
    const pageB = await contextB.newPage()

    try {
      const pipelineUrl = `${BASE_URL}/pipelines/new`
      await pageA.goto(pipelineUrl)
      await pageB.goto(pipelineUrl)

      await pageA.waitForSelector('[data-testid="pipeline-editor-widget"]', { timeout: 15_000 })
      await pageB.waitForSelector('[data-testid="pipeline-editor-widget"]', { timeout: 15_000 })

      await pageA.click('[data-testid="mode-visual-btn"]')
      await pageB.click('[data-testid="mode-visual-btn"]')

      await expect(pageA.locator('[data-testid="pipeline-canvas"]')).toBeVisible()
      await expect(pageB.locator('[data-testid="pipeline-canvas"]')).toBeVisible()
    } finally {
      await contextA.close()
      await contextB.close()
      await browser.close()
    }
  })

  test('presence panel renders in solo mode when no other users connected', async ({ page }) => {
    await page.goto(`${BASE_URL}/pipelines/new`)

    await page.waitForSelector('[data-testid="pipeline-editor-widget"]', { timeout: 15_000 })

    await page.click('[data-testid="mode-visual-btn"]')

    await expect(page.locator('[data-testid="presence-panel"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="presence-panel"]')).toContainText('Editing solo')
  })
})
