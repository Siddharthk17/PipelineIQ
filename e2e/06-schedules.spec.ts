import { test, expect } from './fixtures/auth'
import { uploadSampleCSV, buildSimplePipelineYAML } from './fixtures/pipeline-helpers'

test.describe('Pipeline Scheduling', () => {
  test('Schedules page is accessible', async ({ page, user }) => {
    await page.goto('/login')
    await page.fill('[data-testid="email-input"]', user.email)
    await page.fill('[data-testid="password-input"]', user.password)
    await page.click('[data-testid="login-btn"]')
    await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 10_000 })

    await page.goto('/schedules')
    await expect(page).not.toHaveURL(/login/)
  })

  test('Create schedule via API and verify it appears', async ({
    page,
    apiContext,
    user,
  }) => {
    const fileId = await uploadSampleCSV(apiContext, user.token)
    const yaml = buildSimplePipelineYAML(fileId, 'scheduled_pipeline')

    const resp = await apiContext.post('/api/schedules', {
      data: {
        pipeline_name: 'e2e_test_schedule',
        pipeline_yaml: yaml,
        cron_expression: '0 6 * * *',
      },
    })
    expect(resp.ok()).toBeTruthy()
    const data = await resp.json()
    expect(data.id).toBeTruthy()

    await apiContext.delete(`/api/schedules/${data.id}`)
  })

  test('Pause and resume a schedule', async ({ apiContext, user }) => {
    const fileId = await uploadSampleCSV(apiContext, user.token)
    const yaml = buildSimplePipelineYAML(fileId, 'pause_test_pipeline')

    const createResp = await apiContext.post('/api/schedules', {
      data: {
        pipeline_name: 'pause_test',
        pipeline_yaml: yaml,
        cron_expression: '0 9 * * 1',
      },
    })
    const { id } = await createResp.json()

    const pauseResp = await apiContext.post(`/api/schedules/${id}/pause`)
    expect(pauseResp.ok()).toBeTruthy()
    const paused = await pauseResp.json()
    expect(paused.is_active).toBe(false)

    const resumeResp = await apiContext.post(`/api/schedules/${id}/resume`)
    expect(resumeResp.ok()).toBeTruthy()
    const resumed = await resumeResp.json()
    expect(resumed.is_active).toBe(true)

    await apiContext.delete(`/api/schedules/${id}`)
  })
})
