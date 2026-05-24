const { WebSocketServer } = require('ws')
const { setupWSConnection, setPersistence } = require('y-websocket/bin/utils')
const jwt = require('jsonwebtoken')

const JWT_SECRET = process.env.JWT_SECRET || 'change-me-in-production'
const REDIS_YJS_URL = process.env.REDIS_YJS_URL || 'redis://redis-yjs:6382'
const PORT = parseInt(process.env.PORT || '1234', 10)

try {
  const { RedisPersistence } = require('y-redis')
  const redisUrl = new URL(REDIS_YJS_URL)
  const redisPersistence = new RedisPersistence({
    redisOpts: {
      host: redisUrl.hostname,
      port: parseInt(redisUrl.port || '6379', 10),
    },
  })
  setPersistence(redisPersistence)
  console.log(`Y-Redis persistence connected: ${REDIS_YJS_URL}`)
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

const wss = new WebSocketServer({
  port: PORT,
  maxPayload: 100 * 1024 * 1024,
})

wss.on('connection', (ws, req) => {
  let roomName, token

  try {
    const url = new URL(req.url, `ws://localhost:${PORT}`)
    roomName = decodeURIComponent(url.pathname.slice(1))
    token = url.searchParams.get('token')
  } catch (e) {
    console.warn(`Invalid WebSocket URL: ${req.url}`)
    ws.close(1002, 'Invalid URL')
    return
  }

  const payload = verifyJWT(token)
  if (!payload) {
    console.warn(`Rejected unauthenticated connection to room: ${roomName}`)
    ws.close(1008, 'Unauthorized: Invalid or missing JWT')
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
  console.log(`Redis persistence: ${REDIS_YJS_URL}`)
  console.log(`JWT auth: ${JWT_SECRET !== 'change-me-in-production' ? 'configured' : 'WARNING: default secret'}`)
})

process.on('SIGTERM', () => {
  console.log('Y-WebSocket server shutting down...')
  wss.close(() => {
    console.log('Server closed.')
    process.exit(0)
  })
})
