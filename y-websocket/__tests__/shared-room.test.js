/* Regression test: two clients joining the same room must not crash
 * the relay (y-redis throws "already bound" on the second bindState).
 *
 * Spawns the real server on an ephemeral port against the local Redis.
 * Skips when the stack is not reachable.
 */
const net = require('node:net')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { describe, test, before, after } = require('node:test')
const assert = require('node:assert')

const JWT_SECRET = 'test-jwt-secret-minimum-32-characters-long!!'
const REDIS_PASSWORD = process.env.TEST_REDIS_PASSWORD || ''
const REDIS_YJS_URL =
  process.env.TEST_REDIS_YJS_URL ||
  (REDIS_PASSWORD
    ? `redis://:${REDIS_PASSWORD}@127.0.0.1:6382`
    : 'redis://127.0.0.1:6382')
const REDIS_CHECK = { host: '127.0.0.1', port: 6382 }

function redisReachable() {
  return new Promise((resolve) => {
    const socket = net.createConnection({ ...REDIS_CHECK, timeout: 2000 })
    socket.on('connect', () => {
      socket.end()
      resolve(true)
    })
    socket.on('error', () => resolve(false))
    socket.on('timeout', () => {
      socket.destroy()
      resolve(false)
    })
  })
}

describe('shared room stability', () => {
  let serverProc = null
  let port = 0
  let WebSocket = null
  let jwt = null

  before(async () => {
    if (!process.env.TEST_REDIS_PASSWORD) {
      console.log(
        'SKIP: set TEST_REDIS_PASSWORD to run the y-websocket integration test',
      )
      return
    }
    if (!(await redisReachable())) {
      console.log('SKIP: y-websocket integration test needs local redis on 127.0.0.1:6382')
      return
    }
    WebSocket = require('ws')
    jwt = require('jsonwebtoken')
    port = 18734 + (process.pid % 1000)
    serverProc = spawn(
      process.execPath,
      [path.join(__dirname, '..', 'server.js')],
      {
        env: {
          ...process.env,
          PORT: String(port),
          JWT_SECRET,
          REDIS_YJS_URL,
        },
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    )
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('server did not start')), 15000)
      serverProc.stdout.on('data', (chunk) => {
        if (String(chunk).includes('running on port')) {
          clearTimeout(timer)
          resolve()
        }
      })
      serverProc.on('error', reject)
    })
  })

  after(() => {
    if (serverProc) serverProc.kill('SIGTERM')
  })

  function connectAs(sub, room) {
    const token = jwt.sign(
      { sub, exp: Math.floor(Date.now() / 1000) + 300 },
      JWT_SECRET,
    )
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(
        `ws://127.0.0.1:${port}/${room}?token=${token}`,
      )
      ws.on('open', () => resolve(ws))
      ws.on('error', reject)
      setTimeout(() => reject(new Error('open timeout')), 8000)
    })
  }

  test('two clients in one room do not kill the server', async (t) => {
    if (!serverProc) {
      t.skip('no local stack')
      return
    }
    const room = `shared_${Date.now()}`
    const a = await connectAs('user-a', room)
    t.after(() => { try { a.close() } catch {} })
    const b = await connectAs('user-b', room)
    t.after(() => { try { b.close() } catch {} })
    await new Promise((r) => setTimeout(r, 2000))
    // Server must still accept connections after the shared-room join.
    const c = await connectAs('user-c', room)
    t.after(() => { try { c.close() } catch {} })
    assert.ok(serverProc.exitCode === null, 'server process died')
  })
})
