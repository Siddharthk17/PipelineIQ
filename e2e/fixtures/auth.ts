import { test as base, expect, Page, APIRequestContext } from '@playwright/test'

const API_URL = process.env.E2E_API_URL || process.env.E2E_BASE_URL || 'http://localhost:8000'

export interface TestUser {
  email: string
  password: string
  name: string
  token: string
}

export interface AuthFixtures {
  user: TestUser
  apiContext: APIRequestContext
}

async function createTestUser(request: APIRequestContext): Promise<TestUser> {
  const suffix = Date.now().toString(36)
  const email = `test_${suffix}@pipelineiq.test`
  const password = 'TestPassword@2024!'
  const name = `Test User ${suffix}`

  const registerResp = await request.post(`${API_URL}/api/auth/register`, {
    data: { email, password, name },
  })

  if (!registerResp.ok()) {
    const loginResp = await request.post(`${API_URL}/api/auth/login`, {
      data: { email, password },
    })
    if (!loginResp.ok()) {
      throw new Error(`Cannot authenticate test user: ${loginResp.status()}`)
    }
    const loginData = await loginResp.json()
    return { email, password, name, token: loginData.access_token }
  }

  const loginResp = await request.post(`${API_URL}/api/auth/login`, {
    data: { email, password },
  })
  const loginData = await loginResp.json()
  return { email, password, name, token: loginData.access_token }
}

export const test = base.extend<AuthFixtures>({
  user: async ({ request }, use) => {
    const testUser = await createTestUser(request)
    await use(testUser)
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
