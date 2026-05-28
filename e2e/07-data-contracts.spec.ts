import { test, expect } from './fixtures/auth'
import {
  uploadSampleCSV,
  buildSimplePipelineYAML,
  submitPipelineRun,
  waitForRunStatus,
} from './fixtures/pipeline-helpers'

test.describe('Data Contracts', () => {
  test('Create a data contract via API', async ({ apiContext }) => {
    const resp = await apiContext.post('/api/contracts', {
      data: {
        pipeline_name: 'e2e_contract_test',
        output_schema: {
          region: { type: 'object' },
          amount_sum: { type: 'float64' },
          customer_id_count: { type: 'int64' },
        },
        consumers: [],
        severity: 'warn',
      },
    })
    expect(resp.ok()).toBeTruthy()
    const data = await resp.json()
    expect(data.id).toBeTruthy()
    expect(data.severity).toBe('warn')

    await apiContext.delete(`/api/contracts/${data.id}`)
  })

  test('Contract breach is recorded when output schema mismatches', async ({
    apiContext,
    user,
  }) => {
    const fileId = await uploadSampleCSV(apiContext, user.token)
    const yaml = buildSimplePipelineYAML(fileId, 'breach_test_pipeline')

    const contractResp = await apiContext.post('/api/contracts', {
      data: {
        pipeline_name: 'breach_test_pipeline',
        output_schema: {
          nonexistent_column: { type: 'float64' },
        },
        severity: 'warn',
      },
    })
    const contract = await contractResp.json()

    const runId = await submitPipelineRun(apiContext, yaml, 'breach_test_pipeline')
    await waitForRunStatus(apiContext, runId, 'success', 120_000)

    const breachesResp = await apiContext.get(`/api/contracts/${contract.id}/breaches`)
    const breaches = await breachesResp.json()
    expect(breaches.total).toBeGreaterThanOrEqual(1)

    await apiContext.delete(`/api/contracts/${contract.id}`)
  }, 150_000)
})
