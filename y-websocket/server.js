const { WebSocketServer } = require('ws')
const { setupWSConnection, setPersistence } = require('y-websocket/bin/utils')
const jwt = require('jsonwebtoken')
const crypto = require('crypto')

function hkdfHex(master, info) {
  return Buffer.from(crypto.hkdfSync(
    'sha256',
    Buffer.from(master, 'utf8'),
    Buffer.from('pipelineiq-salt', 'utf8'),
    Buffer.from(info, 'utf8'),
    32,
  )).toString('hex')
}

const ACCESS_TOKEN_SIGNING_SECRET = process.env.ACCESS_TOKEN_SECRET ||
  (process.env.SECRET_KEY ? hkdfHex(process.env.SECRET_KEY, 'pipelineiq-jwt-signing-v1') : '')
const JWT_ISSUER = process.env.JWT_ISSUER || 'pipelineiq'
const JWT_AUDIENCE = process.env.JWT_AUDIENCE || 'pipelineiq-api'
const REDIS_YJS_URL = process.env.REDIS_YJS_URL || 'redis://redis-yjs:6382'
const BACKEND_INTERNAL_URL = (process.env.BACKEND_INTERNAL_URL || 'http://api:8000').replace(/\/$/, '')
const PORT = parseInt(process.env.PORT || '1234', 10)
const MAX_CONNECTIONS_PER_IP = parseInt(process.env.MAX_CONNECTIONS_PER_IP || '20', 10)

if (!ACCESS_TOKEN_SIGNING_SECRET || ACCESS_TOKEN_SIGNING_SECRET.length < 32 || ACCESS_TOKEN_SIGNING_SECRET.startsWith('change-me-')) {
  console.error('ACCESS_TOKEN_SECRET or SECRET_KEY must be a non-default secret with at least 32 characters')
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
    return jwt.verify(token, ACCESS_TOKEN_SIGNING_SECRET, {
      issuer: JWT_ISSUER,
      audience: JWT_AUDIENCE,
      algorithms: ['HS256'],
    })
  } catch (e) {
    return null
  }
}

async function authorizeRoom(roomName, token) {
  const url = `${BACKEND_INTERNAL_URL}/api/v1/pipelines/${encodeURIComponent(roomName)}/collaboration-authorize`
  const response = await fetch(url, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
    },
  })
  return response.ok
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
  maxPayload: 5 * 1024 * 1024,
})

const CLOSE_CODE_AUTH_FAILURE = 4001
const CLOSE_CODE_AUTHZ_FAILURE = 4003
const CLOSE_CODE_RATE_LIMIT = 4008
const CLOSE_REASON_INVALID_TOKEN = 'Invalid or missing JWT token'
const CLOSE_REASON_TOKEN_EXPIRED = 'JWT token expired'
const activeConnectionsByIp = new Map()

wss.on('connection', async (ws, req) => {
  let roomName, token
  const ip = req.socket.remoteAddress || 'unknown'
  const activeForIp = activeConnectionsByIp.get(ip) || 0
  if (activeForIp >= MAX_CONNECTIONS_PER_IP) {
    ws.close(CLOSE_CODE_RATE_LIMIT, 'Too many WebSocket connections')
    return
  }
  activeConnectionsByIp.set(ip, activeForIp + 1)

  ws.on('close', () => {
    const count = activeConnectionsByIp.get(ip) || 1
    if (count <= 1) activeConnectionsByIp.delete(ip)
    else activeConnectionsByIp.set(ip, count - 1)
  })

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

  try {
    if (!(await authorizeRoom(roomName, token))) {
      const count = authRejectionCounts.get(roomName) || 0
      authRejectionCounts.set(roomName, count + 1)
      ws.close(CLOSE_CODE_AUTHZ_FAILURE, 'Not authorized for collaboration room')
      return
    }
  } catch (e) {
    console.warn(`Collaboration authorization check failed for room "${roomName}": ${e.message}`)
    ws.close(CLOSE_CODE_AUTHZ_FAILURE, 'Collaboration authorization unavailable')
    return
  }

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
