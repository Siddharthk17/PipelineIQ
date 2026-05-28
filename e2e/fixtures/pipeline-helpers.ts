import { APIRequestContext } from '@playwright/test'

const API_URL = process.env.E2E_API_URL || process.env.E2E_BASE_URL || 'http://localhost:8000'

export async function uploadSampleCSV(
  apiCtx: APIRequestContext,
  token: string,
): Promise<string> {
  const csvContent = [
    'customer_id,region,amount,status,order_date',
    '1,North,150.00,completed,2024-01-15',
    '2,South,320.50,completed,2024-01-16',
    '3,East,89.99,pending,2024-01-17',
    '4,North,445.00,completed,2024-01-18',
    '5,West,210.75,cancelled,2024-01-19',
    '6,South,675.00,completed,2024-01-20',
    '7,East,125.00,completed,2024-01-21',
    '8,North,890.25,pending,2024-01-22',
  ].join('\n')

  const resp = await apiCtx.post(`${API_URL}/api/files/upload`, {
    multipart: {
      file: {
        name: 'sample_orders.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from(csvContent),
      },
    },
  })

  const data = await resp.json()
  return data.file_id || data.id
}

export function buildSimplePipelineYAML(fileId: string, pipelineName: string): string {
  return [
    `pipeline:`,
    `  name: ${pipelineName}`,
    `  steps:`,
    `    - name: load_orders`,
    `      type: load`,
    `      file_id: "${fileId}"`,
    `    - name: filter_completed`,
    `      type: filter`,
    `      input: load_orders`,
    `      column: status`,
    `      operator: equals`,
    `      value: completed`,
    `    - name: aggregate_by_region`,
    `      type: aggregate`,
    `      input: filter_completed`,
    `      group_by: [region]`,
    `      aggregations:`,
    `        - column: amount`,
    `          function: sum`,
    `        - column: customer_id`,
    `          function: count`,
    `    - name: sort_by_revenue`,
    `      type: sort`,
    `      input: aggregate_by_region`,
    `      by: [amount_sum]`,
    `      ascending: [false]`,
    `    - name: save_output`,
    `      type: save`,
    `      input: sort_by_revenue`,
    `      filename: revenue_by_region.csv`,
  ].join('\n')
}

export async function submitPipelineRun(
  apiCtx: APIRequestContext,
  pipelineYaml: string,
  pipelineName: string,
): Promise<string> {
  const resp = await apiCtx.post(`${API_URL}/api/runs`, {
    data: {
      pipeline_name: pipelineName,
      pipeline_yaml: pipelineYaml,
    },
  })
  const data = await resp.json()
  return data.run_id || data.id
}

export async function waitForRunStatus(
  apiCtx: APIRequestContext,
  runId: string,
  expectedStatus: string,
  timeoutMs: number = 60_000,
): Promise<void> {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    const resp = await apiCtx.get(`${API_URL}/api/runs/${runId}`)
    const data = await resp.json()
    const status = data.status

    if (status === expectedStatus) return
    if (['failed', 'timeout', 'contract_violation'].includes(status)) {
      throw new Error(
        `Run ${runId} ended with unexpected status: ${status} (expected: ${expectedStatus})`,
      )
    }
    await new Promise(r => setTimeout(r, 2000))
  }
  throw new Error(
    `Run ${runId} did not reach status '${expectedStatus}' within ${timeoutMs}ms`,
  )
}
