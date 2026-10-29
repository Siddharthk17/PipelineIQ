import { test as base, expect, Page, APIRequestContext } from '@playwright/test'

const API_URL = process.env.E2E_API_URL || 'http://127.0.0.1:8000'

export interface TestUser {
  email: string
  password: string
  name: string
  token: string
}

export interface AuthFixtures {
  user: TestUser
  authenticatedPage: Page
  apiContext: APIRequestContext
}

async function postWithRetry(
  request: APIRequestContext,
  url: string,
  data: Record<string, string>,
) {
  let resp = await request.post(url, { data })
  // Auth endpoints allow 5/min; a busy suite trips 429s, so retry patiently.
  for (let i = 0; i < 11 && resp.status() === 429; i++) {
    await new Promise((r) => setTimeout(r, 15_000))
    resp = await request.post(url, { data })
  }
  return resp
}

async function createTestUser(request: APIRequestContext): Promise<TestUser> {
  const suffix = Date.now().toString(36)
  const email = `test_${suffix}@pipelineiq.test`
  const password = 'TestPassword@2024!'
  const name = `Test User ${suffix}`
  const username = `test_${suffix}`.replace(/[^a-zA-Z0-9_]/g, '_')

  const registerResp = await postWithRetry(request, `${API_URL}/auth/register`, {
    email,
    username,
    password,
  })

  if (!registerResp.ok()) {
    const loginResp = await postWithRetry(request, `${API_URL}/auth/login`, {
      email,
      password,
    })
    if (!loginResp.ok()) {
      throw new Error(`Cannot authenticate test user: ${loginResp.status()}`)
    }
    const loginData = await loginResp.json()
    return { email, password, name, token: loginData.access_token }
  }

  const loginResp = await postWithRetry(request, `${API_URL}/auth/login`, {
    email,
    password,
  })
  if (!loginResp.ok()) {
    throw new Error(`Cannot authenticate test user: ${loginResp.status()} ${await loginResp.text()}`)
  }
  const loginData = await loginResp.json()
  return { email, password, name, token: loginData.access_token }
}

export const test = base.extend<AuthFixtures>({
  user: async ({ request }, use) => {
    const testUser = await createTestUser(request)
    await use(testUser)
  },

  authenticatedPage: async ({ browser, user }, use) => {
    const baseURL = process.env.E2E_BASE_URL || 'http://localhost';
    const context = await browser.newContext({ baseURL });
    const page = await context.newPage();

    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    try {
      await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 15_000 })
    } catch {
      // Auth limiter (5/min) under a busy suite: back off and retry.
      for (let attempt = 0; attempt < 10; attempt++) {
        await new Promise((r) => setTimeout(r, 15_000))
        await page.goto('/login').catch(() => {})
        await page.fill('[data-testid="email-input"]', user.email)
        await page.fill('[data-testid="password-input"]', user.password)
        await page.click('[data-testid="login-btn"]')
        try {
          await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 15_000 })
          break
        } catch {
          if (attempt >= 9) throw new Error('authenticatedPage: login keeps failing')
        }
      }
    }

    await use(page)
    await context.close()
  },

  apiContext: async ({ playwright, user }, use) => {
    const ctx = await playwright.request.newContext({
      baseURL: API_URL,
      extraHTTPHeaders: {
        Authorization: `Bearer ${user.token}`,
        'Content-Type': 'application/json',
      },
    })
    await use(ctx)
    await ctx.dispose()
  },
})

export { expect }
