import http from 'k6/http'
import { check, sleep } from 'k6'
import { Rate, Trend } from 'k6/metrics'

const BASE_URL = __ENV.BASE_URL
if (!BASE_URL) {
  throw new Error('BASE_URL environment variable is required')
}
const TOKEN = __ENV.AUTH_TOKEN || ''

const uploadSuccess = new Rate('upload_success')
const uploadDuration = new Trend('upload_duration_ms')

function generateCSV(rows) {
  const header = 'id,name,amount,status\n'
  const lines = Array.from({ length: rows }, function (_, i) {
    return i + ',User' + i + ',' + (Math.random() * 1000).toFixed(2) + ',' + (i % 2 === 0 ? 'active' : 'inactive')
  })
  return header + lines.join('\n')
}

export const options = {
  stages: [
    { duration: '20s', target: 10 },
    { duration: '60s', target: 50 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    upload_success: ['rate>0.95'],
  },
}

export default function () {
  const csvContent = generateCSV(50)
  const start = Date.now()
  const resp = http.post(
    BASE_URL + '/api/files/upload',
    { file: http.file(csvContent, 'load_test_' + Date.now() + '.csv', 'text/csv') },
    { headers: { Authorization: 'Bearer ' + TOKEN } },
  )

  const duration = Date.now() - start
  const ok = check(resp, {
    'upload status 2xx': function (r) { return r.status >= 200 && r.status < 300 },
    'has file_id': function (r) {
      try { return JSON.parse(r.body).file_id !== undefined } catch (e) { return false }
    },
    'upload under 2s': function () { return duration < 2000 },
  })

  uploadSuccess.add(ok)
  uploadDuration.add(duration)
  sleep(0.5)
}
