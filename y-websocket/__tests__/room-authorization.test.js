/* Regression test for M-005: the Yjs relay must authorize room membership.
 *
 * Threat: any valid JWT could open wss://host/yjs/<any-pipeline-name> and
 * read/edit that pipeline's live YAML. The relay must check, server-side,
 * that the authenticated user is a member of the room (pipeline
 * ownership/grants), deny by default, and never leak doc state to a
 * rejected peer.
 *
 * The relay under test is spawned as a real process (no Redis needed: it
 * warns and continues). Membership is supplied via YJS_ROOM_MEMBERS_JSON
 * (static map) or a stub HTTP membership backend via PIPELINE_API_URL.
 *
 * Note: the WS handshake itself completes before authorization runs, so a
 * denied peer may briefly see `open` client-side — the assertions that
 * matter are the server's close code/reason and zero doc frames received.
 */
const path = require('node:path')
const { spawn } = require('node:child_process')
const { describe, test, before, after } = require('node:test')
const assert = require('node:assert')

const JWT_SECRET = 'test-room-auth-secret-minimum-32-chars!!'
const jwt = require('jsonwebtoken')

function signToken(sub, extra = {}) {
  return jwt.sign(
    { sub, exp: Math.floor(Date.now() / 1000) + 300, ...extra },
    JWT_SECRET,
  )
}

// Resolves once the server closes the socket OR after a grace period when
// the peer stays admitted (client then closes; closeCode 1000).
function connectOnce(port, room, token) {
  return new Promise((resolve, reject) => {
    const qs = token ? `?token=${encodeURIComponent(token)}` : ''
    const ws = new WebSocket(`ws://127.0.0.1:${port}/${room}${qs}`)
    const timer = setTimeout(() => {
      try { ws.close() } catch {}
      reject(new Error('connect timeout'))
    }, 8000)
    let settled = false
    const seen = []
    ws.addEventListener('message', (event) => {
      seen.push(
        typeof event.data === 'string'
          ? event.data.length
          : event.data?.byteLength ?? 0,
      )
    })
    ws.addEventListener('close', (event) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve({
        ws,
        closeCode: event.code,
        closeReason: event.reason,
        bytesReceived: seen.reduce((a, b) => a + b, 0),
        framesReceived: seen.length,
      })
    })
    ws.addEventListener('error', () => {
      // 'close' follows; resolve there.
    })
    // An admitted peer is still open at this point: close from our side so
    // the outcome is observable without hanging the test.
    setTimeout(() => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        try { ws.close() } catch {}
        resolve({
          ws,
          closeCode: 1000,
          closeReason: 'client done',
          bytesReceived: seen.reduce((a, b) => a + b, 0),
          framesReceived: seen.length,
        })
      }
    }, 1500)
  })
}

function spawnRelay({ port, extraEnv = {} }) {
  const proc = spawn(process.execPath, [path.join(__dirname, '..', 'server.js')], {
    env: {
      ...process.env,
      PORT: String(port),
      JWT_SECRET,
      REDIS_YJS_URL: 'redis://127.0.0.1:6399',
      ...extraEnv,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('relay did not start')), 15000)
    let out = ''
    const onData = (chunk) => {
      out += String(chunk)
      if (out.includes('running on port')) {
        clearTimeout(timer)
        proc.stdout.off('data', onData)
        resolve(proc)
      }
    }
    proc.stdout.on('data', onData)
    proc.stderr.on('data', () => {})
    proc.on('error', reject)
    proc.on('exit', (code) => {
      clearTimeout(timer)
      reject(new Error(`relay exited early with code ${code}: ${out}`))
    })
  })
}

describe('M-005 room authorization (static membership map)', () => {
  let proc = null
  const port = 18731 + (process.pid % 500)
  const members = { 'alice-pipeline': ['user-a'] }

  before(async () => {
    proc = await spawnRelay({
      port,
      extraEnv: { YJS_ROOM_MEMBERS_JSON: JSON.stringify(members) },
    })
  })

  after(() => {
    if (proc) proc.kill('SIGTERM')
  })

  test('owner can join their own room', async () => {
    const res = await connectOnce(port, 'alice-pipeline', signToken('user-a'))
    assert.strictEqual(res.closeCode, 1000)
  })

  test('user B with a valid JWT cannot join user A’s room', async () => {
    const res = await connectOnce(port, 'alice-pipeline', signToken('user-b'))
    assert.strictEqual(res.closeCode, 4401)
    assert.strictEqual(res.closeReason, 'Not authorized for this room')
  })

  test('rejected peer receives no doc state', async () => {
    const res = await connectOnce(port, 'alice-pipeline', signToken('user-b'))
    assert.notStrictEqual(res.closeCode, 1000, 'server must close the socket')
    assert.strictEqual(res.framesReceived, 0)
    assert.strictEqual(res.bytesReceived, 0)
  })

  test('admin bypass still works', async () => {
    const res = await connectOnce(
      port,
      'alice-pipeline',
      signToken('user-admin', { role: 'admin' }),
    )
    assert.strictEqual(res.closeCode, 1000)
  })

  test('token without subject identity is rejected', async () => {
    const token = jwt.sign(
      { exp: Math.floor(Date.now() / 1000) + 300, role: 'viewer' },
      JWT_SECRET,
    )
    const res = await connectOnce(port, 'alice-pipeline', token)
    assert.notStrictEqual(res.closeCode, 1000, 'server must close the socket')
    assert.strictEqual(res.closeCode, 4001)
  })
})

describe('M-005 membership via backend API (stub)', () => {
  const http = require('node:http')
  let proc = null
  let stub = null
  const port = 19731 + (process.pid % 500)
  const stubPort = 20231 + (process.pid % 500)
  const memberToken = signToken('user-a')

  before(async () => {
    // Minimal stand-in for GET /api/v1/versions/:room: 200 = member,
    // 401 = token revoked / user inactive, 404 = no grant (or no pipeline).
    stub = http.createServer((req, res) => {
      const m = /^\/api\/v1\/versions\/([^/]+)$/.exec(req.url || '')
      if (!m) {
        res.writeHead(404).end()
        return
      }
      const room = decodeURIComponent(m[1])
      if (req.headers.authorization !== `Bearer ${memberToken}`) {
        res.writeHead(401).end()
        return
      }
      if (room === 'alice-pipeline') {
        res.writeHead(200, { 'Content-Type': 'application/json' }).end('{}')
      } else if (room === 'revoked-room') {
        res.writeHead(401).end() // e.g. token revoked / user deactivated
      } else {
        res.writeHead(404).end()
      }
    })
    await new Promise((resolve) => stub.listen(stubPort, '127.0.0.1', resolve))
    proc = await spawnRelay({
      port,
      extraEnv: { PIPELINE_API_URL: `http://127.0.0.1:${stubPort}` },
    })
  })

  after(() => {
    if (proc) proc.kill('SIGTERM')
    if (stub) stub.close()
  })

  test('backend member is admitted', async () => {
    const res = await connectOnce(port, 'alice-pipeline', memberToken)
    assert.strictEqual(res.closeCode, 1000)
  })

  test('backend 401 (revoked/inactive) becomes auth failure', async () => {
    const res = await connectOnce(port, 'revoked-room', memberToken)
    assert.strictEqual(res.closeCode, 4001)
    assert.strictEqual(res.closeReason, 'Token revoked or user inactive')
    assert.strictEqual(res.framesReceived, 0)
  })

  test('backend 404 (no grant) becomes room forbidden', async () => {
    const res = await connectOnce(port, 'other-pipeline', memberToken)
    assert.strictEqual(res.closeCode, 4401)
    assert.strictEqual(res.closeReason, 'Not authorized for this room')
    assert.strictEqual(res.framesReceived, 0)
  })
})

describe('M-005 deny by default (no membership source configured)', () => {
  let proc = null
  const port = 19231 + (process.pid % 500)

  before(async () => {
    proc = await spawnRelay({ port })
  })

  after(() => {
    if (proc) proc.kill('SIGTERM')
  })

  test('valid JWT alone does not open an arbitrary room', async () => {
    const res = await connectOnce(port, 'some-pipeline', signToken('user-a'))
    assert.notStrictEqual(res.closeCode, 1000, 'relay must deny by default')
    assert.strictEqual(res.closeCode, 4401)
    assert.strictEqual(res.closeReason, 'Room authorization unavailable')
  })

  test('relay stays alive after denials', async () => {
    const proc2 = proc
    assert.ok(proc2.exitCode === null, 'relay process died')
    const res = await connectOnce(port, 'other-room', signToken('user-a'))
    assert.strictEqual(res.closeCode, 4401)
    assert.ok(proc2.exitCode === null, 'relay process died after denial')
  })
})
