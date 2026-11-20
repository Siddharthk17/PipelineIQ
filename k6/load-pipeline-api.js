import http from 'k6/http'
import { check } from 'k6'
import { Rate, Trend } from 'k6/metrics'

const BASE_URL = __ENV.BASE_URL
if (!BASE_URL) {
  throw new Error('BASE_URL environment variable is required')
}
// M-029/M-139: refuse to load-test production hosts by default. Local and
// throwaway stacks (localhost, 127.0.0.1, 10/172.16/192.168 ranges) always
// pass; anything else must opt in with ALLOW_PROD_LOAD=1.
if (!/^(http:\/\/localhost|http:\/\/127\.|http:\/\/10\.|http:\/\/172\.(1[6-9]|2\d|3[01])\.|http:\/\/192\.168\.)/.test(BASE_URL) && __ENV.ALLOW_PROD_LOAD !== '1') {
  throw new Error(`Refusing to load-test non-local target ${BASE_URL} without ALLOW_PROD_LOAD=1`)
}
const TOKEN = __ENV.AUTH_TOKEN || ''

const validateSuccess = new Rate('validate_success')
const validateDuration = new Trend('validate_duration_ms')

const SAMPLE_YAML = [
  'pipeline:',
  '  name: load_test_pipeline',
  '  steps:',
  '    - name: load_data',
  '      type: load',
  '      file_id: "00000000-0000-0000-0000-000000000001"',
  '    - name: filter_rows',
  '      type: filter',
  '      input: load_data',
  '      column: amount',
  '      operator: gt',
  '      value: 100',
].join('\n')

export const options = {
  scenarios: {
    validate_yaml: {
      executor: 'constant-arrival-rate',
      rate: 200,
      timeUnit: '1s',
      duration: '60s',
      preAllocatedVUs: 50,
      maxVUs: 200,
    },
  },
  thresholds: {
    http_req_duration: ['p(99)<100'],
    validate_success: ['rate>0.99'],
  },
}

export default function () {
  const start = Date.now()
  const resp = http.post(
    `${BASE_URL}/api/ai/validate-yaml`,
    JSON.stringify({ yaml_text: SAMPLE_YAML }),
    {
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${TOKEN}`,
      },
    },
  )
  const duration = Date.now() - start

  const ok = check(resp, {
    'status 200': (r) => r.status === 200,
    'has valid field': (r) => {
      try { return JSON.parse(r.body).valid !== undefined } catch { return false }
    },
    'response time <100ms': () => duration < 100,
  })

  validateSuccess.add(ok)
  validateDuration.add(duration)
}
