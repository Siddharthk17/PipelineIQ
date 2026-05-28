import { test, expect } from './fixtures/auth'
import {
  uploadSampleCSV,
  buildSimplePipelineYAML,
  submitPipelineRun,
  waitForRunStatus,
} from './fixtures/pipeline-helpers'

test.describe('Pipeline Run Lifecycle', () => {
  test('Submit pipeline run and see it reach success', async ({
    apiContext,
    user,
  }) => {
    const fileId = await uploadSampleCSV(apiContext, user.token)
    const pipelineName = `e2e_test_${Date.now()}`
    const yaml = buildSimplePipelineYAML(fileId, pipelineName)

    const runId = await submitPipelineRun(apiContext, yaml, pipelineName)
    expect(runId).toBeTruthy()

    await waitForRunStatus(apiContext, runId, 'success', 120_000)
  }, 150_000)

  test('Run detail page shows status progression', async ({
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
    const pipelineName = `e2e_sse_${Date.now()}`
    const yaml = buildSimplePipelineYAML(fileId, pipelineName)
    const runId = await submitPipelineRun(apiContext, yaml, pipelineName)

    await page.goto(`/runs/${runId}`)
    await page.waitForSelector('[data-testid="run-status"]', { timeout: 5000 })
    await expect(page.locator('[data-testid="run-status"]')).toContainText(
      /pending|running|success/i,
      { timeout: 5000 },
    )
  })

  test('Completed run shows download button', async ({
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
    const pipelineName = `e2e_download_${Date.now()}`
    const yaml = buildSimplePipelineYAML(fileId, pipelineName)
    const runId = await submitPipelineRun(apiContext, yaml, pipelineName)

    await waitForRunStatus(apiContext, runId, 'success', 120_000)
    await page.goto(`/runs/${runId}`)
    await page.reload()

    await expect(
      page.locator('[data-testid="download-output-btn"]').or(
        page.locator('text=Download'),
      ),
    ).toBeVisible({ timeout: 10_000 })
  }, 150_000)

  test('Execution Gantt chart renders on completed run', async ({
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
    const pipelineName = `e2e_gantt_${Date.now()}`
    const yaml = buildSimplePipelineYAML(fileId, pipelineName)
    const runId = await submitPipelineRun(apiContext, yaml, pipelineName)

    await waitForRunStatus(apiContext, runId, 'success', 120_000)
    await page.goto(`/runs/${runId}`)
    await page.reload()

    await expect(page.locator('[data-testid="execution-gantt"]')).toBeVisible({
      timeout: 10_000,
    })
  }, 150_000)
})
