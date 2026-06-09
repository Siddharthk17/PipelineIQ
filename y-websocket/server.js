const { WebSocketServer } = require('ws')
const { setupWSConnection, setPersistence } = require('y-websocket/bin/utils')
const jwt = require('jsonwebtoken')

const JWT_SECRET = process.env.JWT_SECRET
const REDIS_YJS_URL = process.env.REDIS_YJS_URL || 'redis://redis-yjs:6382'
const PORT = parseInt(process.env.PORT || '1234', 10)

if (!JWT_SECRET || JWT_SECRET.length < 32 || JWT_SECRET.startsWith('change-me-')) {
  console.error('JWT_SECRET must be a non-default secret with at least 32 characters')
  process.exit(1)
}

try {
  const { RedisPersistence } = require('y-redis')
  const redisUrl = new URL(REDIS_YJS_URL)
  const redisDb = redisUrl.pathname && redisUrl.pathname.length > 1
    ? parseInt(redisUrl.pathname.slice(1), 10)
    : 0
  const redisOpts = {
    host: redisUrl.hostname,
    port: parseInt(redisUrl.port || '6379', 10),
    db: Number.isNaN(redisDb) ? 0 : redisDb,
  }
  if (redisUrl.password) {
    redisOpts.password = decodeURIComponent(redisUrl.password)
  }
  if (redisUrl.protocol === 'rediss:') {
    redisOpts.tls = {}
  }
  const redactedRedisUrl = new URL(REDIS_YJS_URL)
  if (redactedRedisUrl.password) {
    redactedRedisUrl.password = 'REDACTED'
  }
  const redisPersistence = new RedisPersistence({
    redisOpts,
  })
  // y-websocket expects a persistence object with writeState(docName, doc),
  // bindState(docName, doc), and provider properties.
  // y-redis handles persistence in real-time via Redis pub/sub, so
  // writeState is a no-op (no need to flush on disconnect).
  setPersistence({
    provider: redisPersistence,
    bindState: (docName, doc) => redisPersistence.bindState(docName, doc),
    writeState: async (docName, doc) => {},
  })
  console.log(`Y-Redis persistence connected: ${redactedRedisUrl.toString()}`)
} catch (e) {
  console.warn(`Redis persistence unavailable: ${e.message}`)
  console.warn('Documents will not persist across server restarts.')
}

function verifyJWT(token) {
  if (!token) return null
  try {
    return jwt.verify(token, JWT_SECRET)
  } catch (e) {
    return null
  }
}

function tokenFromCookie(cookieHeader) {
  if (!cookieHeader) return null
  for (const part of cookieHeader.split(';')) {
    const [name, ...valueParts] = part.trim().split('=')
    if (name === 'pipelineiq_token') {
      return decodeURIComponent(valueParts.join('='))
    }
  }
  return null
}

const AUTH_REJECTION_LOG_INTERVAL_MS = 5000
const authRejectionCounts = new Map()

setInterval(() => {
  for (const [room, count] of authRejectionCounts) {
    if (count > 1) {
      console.warn(`Auth rejections for room "${room}": ${count} in last ${AUTH_REJECTION_LOG_INTERVAL_MS}ms`)
    }
    authRejectionCounts.delete(room)
  }
}, AUTH_REJECTION_LOG_INTERVAL_MS)

const wss = new WebSocketServer({
  port: PORT,
  maxPayload: 100 * 1024 * 1024,
})

const CLOSE_CODE_AUTH_FAILURE = 4001
const CLOSE_REASON_INVALID_TOKEN = 'Invalid or missing JWT token'
const CLOSE_REASON_TOKEN_EXPIRED = 'JWT token expired'

wss.on('connection', (ws, req) => {
  let roomName, token

  try {
    const url = new URL(req.url, `ws://localhost:${PORT}`)
    roomName = decodeURIComponent(url.pathname.slice(1))
    token = url.searchParams.get('token') || tokenFromCookie(req.headers.cookie)
  } catch (e) {
    console.warn(`Invalid WebSocket URL: ${req.url}`)
    ws.close(1002, 'Invalid URL')
    return
  }

  if (!token) {
    const count = authRejectionCounts.get(roomName) || 0
    authRejectionCounts.set(roomName, count + 1)
    ws.close(CLOSE_CODE_AUTH_FAILURE, CLOSE_REASON_INVALID_TOKEN)
    return
  }

  const payload = verifyJWT(token)
  if (!payload) {
    const count = authRejectionCounts.get(roomName) || 0
    authRejectionCounts.set(roomName, count + 1)

    let reason = CLOSE_REASON_INVALID_TOKEN
    try {
      const decoded = jwt.decode(token)
      if (decoded && decoded.exp && decoded.exp * 1000 < Date.now()) {
        reason = CLOSE_REASON_TOKEN_EXPIRED
      }
    } catch (_) { /* ignore */ }

    ws.close(CLOSE_CODE_AUTH_FAILURE, reason)
    return
  }

  const userId = payload.sub || payload.user_id || 'unknown'
  const userName = payload.name || payload.email || userId

  console.log(`User "${userName}" joined room: "${roomName}"`)

  setupWSConnection(ws, req, {
    docName: roomName,
    gc: true,
  })

  ws.on('close', () => {
    console.log(`User "${userName}" left room: "${roomName}"`)
  })

  ws.on('error', (err) => {
    console.error(`WebSocket error for user "${userName}": ${err.message}`)
  })
})

wss.on('error', (err) => {
  console.error(`Y-WebSocket server error: ${err.message}`)
})

wss.on('listening', () => {
  console.log(`Y-WebSocket server running on port ${PORT}`)
  console.log('JWT auth: configured')
})

process.on('SIGTERM', () => {
  console.log('Y-WebSocket server shutting down...')
  wss.close(() => {
    console.log('Server closed.')
    process.exit(0)
  })
})
