const { WebSocketServer } = require('ws')
const { setupWSConnection, setPersistence } = require('y-websocket/bin/utils')
const jwt = require('jsonwebtoken')

const JWT_SECRET = process.env.JWT_SECRET
const REDIS_YJS_URL = process.env.REDIS_YJS_URL || 'redis://redis-yjs:6382'
const PORT = parseInt(process.env.PORT || '1234', 10)

// ---- M-005 room authorization ----
//
// Authentication (who you are) is NOT authorization (which rooms you may
// join). Every connection must additionally prove membership of the room
// (pipeline ownership/grants). Deny by default; never call
// setupWSConnection for a rejected peer (no doc state leaks).
//
// Membership sources, checked in order:
//   1. admin bypass — JWT `role: "admin"` may join any room (mirrors the
//      backend's admin bypass in _check_pipeline_permission).
//   2. JWT room claims (forward-compat) — `rooms` / `pipelines` array claims
//      listing rooms the token may join. The current backend does not issue
//      these; they are honored if a future issuer does.
//   3. Static map — YJS_ROOM_MEMBERS_JSON='{"room":["user-id", ...]}'.
//      Intended for tests and tiny single-host deployments.
//   4. Backend API — PIPELINE_API_URL (or API_BASE_URL). The relay forwards
//      the caller's own JWT as a Bearer token to
//      YJS_AUTH_CHECK_PATH (default `/api/v1/versions/{room}`, a read-only
//      endpoint that requires an owner/runner/viewer grant on the pipeline).
//      200 = member. 401 = token revoked or user inactive. 403/404 = no
//      grant (404 is deliberate: the backend hides other users' pipelines).
//      Any error or non-200 denies (fail closed). This is the production
//      mechanism: revocation and deactivation are enforced by the backend.
//   5. Otherwise deny. YJS_ALLOW_UNVERIFIED_ROOMS=1 disables the deny for
//      local development ONLY (logs a loud warning). It must never be set in
//      any shared deployment.
const PIPELINE_API_URL = (
  process.env.PIPELINE_API_URL ||
  process.env.API_BASE_URL ||
  ''
).replace(/\/+$/, '')
const YJS_AUTH_CHECK_PATH =
  process.env.YJS_AUTH_CHECK_PATH || '/api/v1/versions/{room}'
const YJS_AUTH_TIMEOUT_MS = parseInt(
  process.env.YJS_AUTH_TIMEOUT_MS || '3000',
  10,
)
const YJS_AUTH_CACHE_TTL_MS = parseInt(
  process.env.YJS_AUTH_CACHE_TTL_MS || '10000',
  10,
)
const YJS_ALLOW_UNVERIFIED_ROOMS =
  process.env.YJS_ALLOW_UNVERIFIED_ROOMS === '1'
if (
  YJS_ALLOW_UNVERIFIED_ROOMS &&
  (process.env.NODE_ENV === 'production' ||
    process.env.ENVIRONMENT === 'production')
) {
  console.error(
    'Refusing to start: YJS_ALLOW_UNVERIFIED_ROOMS=1 disables all room '
    + 'authorization and must never be enabled in production.',
  );
  process.exit(1);
}

let STATIC_ROOM_MEMBERS = {}
try {
  if (process.env.YJS_ROOM_MEMBERS_JSON) {
    const parsed = JSON.parse(process.env.YJS_ROOM_MEMBERS_JSON)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      STATIC_ROOM_MEMBERS = parsed
    } else {
      console.warn('YJS_ROOM_MEMBERS_JSON must be a JSON object; ignoring')
    }
  }
} catch (e) {
  console.warn(`YJS_ROOM_MEMBERS_JSON is not valid JSON; ignoring: ${e.message}`)
}

// Short-lived allow/deny cache so reconnect storms do not hammer the
// backend. Keyed by (user, room); bounded; revocation/deactivation still
// takes effect on the backend immediately and at worst within the TTL for
// *new* connections here (existing sockets are unaffected either way).
const authzCache = new Map()
const AUTHZ_CACHE_MAX_ENTRIES = 5000

function authzCacheGet(key) {
  const entry = authzCache.get(key)
  if (!entry) return null
  if (entry.expiresAt < Date.now()) {
    authzCache.delete(key)
    return null
  }
  return entry.verdict
}

function authzCacheSet(key, verdict) {
  if (YJS_AUTH_CACHE_TTL_MS <= 0) return
  if (authzCache.size >= AUTHZ_CACHE_MAX_ENTRIES) {
    const oldest = authzCache.keys().next()
    if (!oldest.done) authzCache.delete(oldest.value)
  }
  authzCache.set(key, { verdict, expiresAt: Date.now() + YJS_AUTH_CACHE_TTL_MS })
}

function userIdFromPayload(payload) {
  const raw = payload.sub ?? payload.user_id ?? ''
  return String(raw).trim()
}

function payloadGrantsRoom(payload, roomName) {
  for (const claim of ['rooms', 'pipelines']) {
    const list = payload[claim]
    if (Array.isArray(list) && list.map(String).includes(roomName)) {
      return true
    }
  }
  return false
}

function staticMapGrantsRoom(roomName, userId) {
  const members = STATIC_ROOM_MEMBERS[roomName]
  return Array.isArray(members) && members.map(String).includes(String(userId))
}

async function checkMembershipViaApi(token, roomName) {
  const url =
    PIPELINE_API_URL +
    YJS_AUTH_CHECK_PATH.replace('{room}', encodeURIComponent(roomName))
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), YJS_AUTH_TIMEOUT_MS)
  try {
    const res = await fetch(url, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/json',
      },
      signal: controller.signal,
    })
    if (res.status === 200) return { allowed: true }
    if (res.status === 401) return { allowed: false, revokedOrInactive: true }
    return { allowed: false }
  } catch (e) {
    console.warn(`Room membership check failed for "${roomName}": ${e.message}`)
    return { allowed: false, unavailable: true }
  } finally {
    clearTimeout(timer)
  }
}

// Returns { allowed, closeCode, reason }. Deny by default.
async function authorizeRoom({ payload, token, roomName }) {
  const userId = userIdFromPayload(payload)
  if (!userId || userId === 'unknown') {
    return {
      allowed: false,
      closeCode: CLOSE_CODE_AUTH_FAILURE,
      reason: CLOSE_REASON_INVALID_TOKEN,
    }
  }

  // Admins bypass per-pipeline checks (mirrors backend behavior), but a
  // bare JWT claim is not re-verified: if PIPELINE_API_URL is configured,
  // confirm admin status against the backend so a demoted admin loses
  // access at most one cache TTL after revocation (R-S-004). Without an
  // API (offline/dev), the claim stands.
  if (payload.role === 'admin') {
    if (!PIPELINE_API_URL) {
      return { allowed: true }
    }
    const cacheKey = `admin|${userId}`;
    const cached = authzCacheGet(cacheKey);
    if (cached !== null) {
      return cached.allowed ? { allowed: true } : { ...cached.denied };
    }
    const verdict = await checkMembershipViaApi(token, roomName);
    if (verdict.allowed) {
      authzCacheSet(cacheKey, { allowed: true });
      return { allowed: true };
    }
    const denied = verdict.revokedOrInactive
      ? {
        allowed: false,
        closeCode: CLOSE_CODE_AUTH_FAILURE,
        reason: CLOSE_REASON_TOKEN_REVOKED,
      }
      : {
        allowed: false,
        closeCode: CLOSE_CODE_ROOM_FORBIDDEN,
        reason: CLOSE_REASON_ROOM_FORBIDDEN,
      };
    authzCacheSet(cacheKey, { allowed: false, denied });
    return denied;
  }

  if (payloadGrantsRoom(payload, roomName)) {
    return { allowed: true }
  }

  if (staticMapGrantsRoom(roomName, userId)) {
    return { allowed: true }
  }

  // The static map names this room but does not list the user: an explicit
  // membership denial (same code/reason as a backend 403/404, so room
  // existence cannot be probed via the close reason).
  if (Object.hasOwn(STATIC_ROOM_MEMBERS, roomName)) {
    return {
      allowed: false,
      closeCode: CLOSE_CODE_ROOM_FORBIDDEN,
      reason: CLOSE_REASON_ROOM_FORBIDDEN,
    }
  }

  if (PIPELINE_API_URL) {
    const cacheKey = `${userId}|${roomName}`
    const cached = authzCacheGet(cacheKey)
    if (cached !== null) {
      return cached.allowed ? { allowed: true } : { ...cached.denied }
    }
    const verdict = await checkMembershipViaApi(token, roomName)
    if (verdict.allowed) {
      authzCacheSet(cacheKey, { allowed: true })
      return { allowed: true }
    }
    const denied = verdict.revokedOrInactive
      ? {
          allowed: false,
          closeCode: CLOSE_CODE_AUTH_FAILURE,
          reason: CLOSE_REASON_TOKEN_REVOKED,
        }
      : verdict.unavailable
        ? {
            allowed: false,
            closeCode: CLOSE_CODE_ROOM_FORBIDDEN,
            reason: CLOSE_REASON_AUTHZ_UNAVAILABLE,
          }
        : {
            allowed: false,
            closeCode: CLOSE_CODE_ROOM_FORBIDDEN,
            reason: CLOSE_REASON_ROOM_FORBIDDEN,
          }
    authzCacheSet(cacheKey, { allowed: false, denied })
    return denied
  }

  if (YJS_ALLOW_UNVERIFIED_ROOMS) {
    console.warn(
      `YJS_ALLOW_UNVERIFIED_ROOMS=1: admitting user "${userId}" to room ` +
        `"${roomName}" without a membership check (dev-only, insecure)`,
    )
    return { allowed: true }
  }

  return {
    allowed: false,
    closeCode: CLOSE_CODE_ROOM_FORBIDDEN,
    reason: CLOSE_REASON_AUTHZ_UNAVAILABLE,
  }
}

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
  // y-redis throws when the same doc is bound twice (second client joining
  // a room that is already persisted). That must not take down the server:
  // the doc is already syncing, so joining can proceed.
  const redisBindState = (docName, doc) => {
    try {
      return redisPersistence.bindState(docName, doc)
    } catch (err) {
      if (String((err && err.message) || err).includes("already bound")) {
        return undefined
      }
      throw err
    }
  }
  setPersistence({
    provider: redisPersistence,
    bindState: redisBindState,
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
  // Yjs sync messages are small (KBs). Cap well below the container memory
  // limit so a malicious peer cannot OOM the relay with one frame.
  maxPayload: 10 * 1024 * 1024,
})

const CLOSE_CODE_AUTH_FAILURE = 4001
const CLOSE_REASON_INVALID_TOKEN = 'Invalid or missing JWT token'
const CLOSE_REASON_TOKEN_EXPIRED = 'JWT token expired'
const CLOSE_REASON_TOKEN_REVOKED = 'Token revoked or user inactive'
// Application-defined range (4000-4999): authenticated but not a member of
// this room, or membership could not be verified (fail closed).
const CLOSE_CODE_ROOM_FORBIDDEN = 4401
const CLOSE_REASON_ROOM_FORBIDDEN = 'Not authorized for this room'
const CLOSE_REASON_AUTHZ_UNAVAILABLE = 'Room authorization unavailable'

wss.on('connection', async (ws, req) => {
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

  if (!roomName) {
    ws.close(CLOSE_CODE_ROOM_FORBIDDEN, 'Missing room name')
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

  const userId = userIdFromPayload(payload) || 'unknown'
  const userName = payload.name || payload.email || userId

  // M-005: authorize room membership BEFORE touching any Yjs state.
  // Denied peers are closed here, so setupWSConnection (which binds/creates
  // the shared doc) never runs for them: no doc state can leak.
  let verdict
  try {
    verdict = await authorizeRoom({ payload, token, roomName })
  } catch (err) {
    console.error(
      `Room authorization error for room "${roomName}": ${err.message}`,
    )
    ws.close(CLOSE_CODE_ROOM_FORBIDDEN, CLOSE_REASON_AUTHZ_UNAVAILABLE)
    return
  }
  if (!verdict.allowed) {
    const count = authRejectionCounts.get(roomName) || 0
    authRejectionCounts.set(roomName, count + 1)
    console.warn(
      `Room authorization denied for user "${userName}" on room ` +
        `"${roomName}": ${verdict.reason}`,
    )
    ws.close(verdict.closeCode, verdict.reason)
    return
  }

  console.log(`User "${userName}" joined room: "${roomName}"`)

  // A faulty connection must never crash the relay for all other rooms.
  try {
    setupWSConnection(ws, req, {
      docName: roomName,
      gc: true,
    })
  } catch (err) {
    console.error(`Failed to set up room "${roomName}": ${err.message}`)
    try {
      ws.close(1011, "Internal error")
    } catch (_) { /* ignore */ }
    return
  }

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
  const sources = ['jwt-claims']
  if (Object.keys(STATIC_ROOM_MEMBERS).length > 0) {
    sources.push(`static-map(${Object.keys(STATIC_ROOM_MEMBERS).length} rooms)`)
  }
  if (PIPELINE_API_URL) {
    sources.push(`api(${PIPELINE_API_URL}${YJS_AUTH_CHECK_PATH})`)
  }
  if (YJS_ALLOW_UNVERIFIED_ROOMS) {
    console.warn(
      'WARNING: YJS_ALLOW_UNVERIFIED_ROOMS=1 admits connections without a ' +
        'membership check. Dev-only: never enable in a shared deployment.',
    )
    sources.push('UNVERIFIED-ALLOW (dev-only, insecure)')
  } else if (!PIPELINE_API_URL && Object.keys(STATIC_ROOM_MEMBERS).length === 0) {
    console.warn(
      'WARNING: no room membership source configured (set PIPELINE_API_URL ' +
        'or YJS_ROOM_MEMBERS_JSON). All rooms deny by default.',
    )
  }
  console.log(`Room authorization sources: ${sources.join(', ')}; deny by default`)
})

process.on('SIGTERM', () => {
  console.log('Y-WebSocket server shutting down...')
  wss.close(() => {
    console.log('Server closed.')
    process.exit(0)
  })
})
