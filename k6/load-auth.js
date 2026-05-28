import http from 'k6/http'
import { check, sleep } from 'k6'
import { Rate, Trend } from 'k6/metrics'

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000'
const loginSuccess = new Rate('login_success')
const loginDuration = new Trend('login_duration_ms')

export const options = {
  stages: [
    { duration: '30s', target: 20 },
    { duration: '60s', target: 100 },
    { duration: '60s', target: 100 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    login_success: ['rate>0.99'],
    http_req_failed: ['rate<0.01'],
  },
}

const TEST_EMAIL = __ENV.TEST_EMAIL || 'loadtest@pipelineiq.test'
const TEST_PASSWORD = __ENV.TEST_PASSWORD || 'LoadTest@2024!'

export default function () {
  const start = Date.now()
  const resp = http.post(
    `${BASE_URL}/api/auth/login`,
    JSON.stringify({ email: TEST_EMAIL, password: TEST_PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  )
  const duration = Date.now() - start

  const success = check(resp, {
    'login status 200': (r) => r.status === 200,
    'has access_token': (r) => {
      try { return JSON.parse(r.body).access_token !== undefined } catch { return false }
    },
    'response time <500ms': () => duration < 500,
  })

  loginSuccess.add(success)
  loginDuration.add(duration)
  sleep(1)
}
