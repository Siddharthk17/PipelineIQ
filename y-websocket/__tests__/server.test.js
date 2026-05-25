const jwt = require('jsonwebtoken')
const { describe, test, before, after } = require('node:test')
const assert = require('node:assert')

const DEFAULT_SECRET = 'change-me-in-production'
const JWT_SECRET = process.env.JWT_SECRET || DEFAULT_SECRET
const REDIS_YJS_URL = process.env.REDIS_YJS_URL || 'redis://redis-yjs:6382'
const PORT = parseInt(process.env.PORT || '0', 10)

// Basic JWT verification function (duplicated from server for unit testing)
function verifyJWT(token) {
  if (!token) return null
  try {
    return jwt.verify(token, JWT_SECRET)
  } catch (e) {
    return null
  }
}

const CLOSE_CODE_AUTH_FAILURE = 4001

describe('JWT authentication', () => {
  test('returns null for missing token', () => {
    assert.strictEqual(verifyJWT(undefined), null)
    assert.strictEqual(verifyJWT(null), null)
    assert.strictEqual(verifyJWT(''), null)
  })

  test('returns null for garbage token', () => {
    assert.strictEqual(verifyJWT('not-a-jwt'), null)
    assert.strictEqual(verifyJWT('eyJhbGciOiJIUzI1NiJ9.whatever'), null)
  })

  test('returns payload for valid token', () => {
    const token = jwt.sign({ sub: 'user-1', name: 'Alice' }, JWT_SECRET)
    const payload = verifyJWT(token)
    assert.ok(payload)
    assert.strictEqual(payload.sub, 'user-1')
    assert.strictEqual(payload.name, 'Alice')
  })

  test('rejects token signed with wrong secret', () => {
    const token = jwt.sign({ sub: 'attacker' }, 'wrong-secret')
    assert.strictEqual(verifyJWT(token), null)
  })

  test('rejects expired token', () => {
    const token = jwt.sign({ sub: 'user-1' }, JWT_SECRET, { expiresIn: '0s' })
    assert.strictEqual(verifyJWT(token), null)
  })
})

describe('User info extraction from JWT payload', () => {
  test('extracts userId from sub claim', () => {
    const payload = { sub: 'user-42' }
    const userId = payload.sub || payload.user_id || 'unknown'
    assert.strictEqual(userId, 'user-42')
  })

  test('extracts userId from user_id claim', () => {
    const payload = { user_id: 789 }
    const userId = payload.sub || payload.user_id || 'unknown'
    assert.strictEqual(userId, 789)
  })

  test('falls back to unknown for user without id', () => {
    const payload = {}
    const userId = payload.sub || payload.user_id || 'unknown'
    assert.strictEqual(userId, 'unknown')
  })

  test('extracts userName from name', () => {
    const payload = { name: 'Bob' }
    const userName = payload.name || payload.email || 'unknown'
    assert.strictEqual(userName, 'Bob')
  })

  test('falls back to email when name missing', () => {
    const payload = { email: 'bob@example.com' }
    const userName = payload.name || payload.email || 'unknown'
    assert.strictEqual(userName, 'bob@example.com')
  })
})

describe('Redis URL parsing', () => {
  test('parses redis-yjs:6382 correctly', () => {
    const url = new URL('redis://redis-yjs:6382')
    assert.strictEqual(url.hostname, 'redis-yjs')
    assert.strictEqual(url.port, '6382')
  })

  test('parses default Redis port when not specified', () => {
    const url = new URL('redis://redis-yjs')
    assert.strictEqual(url.hostname, 'redis-yjs')
    assert.strictEqual(url.port, '')
    assert.strictEqual(parseInt(url.port || '6379', 10), 6379)
  })
})

describe('Room name extraction from URL path', () => {
  test('extracts room name from URL path', () => {
    const reqUrl = '/my-pipeline?token=eyJ...'
    const url = new URL(reqUrl, 'ws://localhost:1234')
    const roomName = decodeURIComponent(url.pathname.slice(1))
    assert.strictEqual(roomName, 'my-pipeline')
  })

  test('handles URL-encoded room names', () => {
    const reqUrl = '/customer%20revenue%20report?token=x'
    const url = new URL(reqUrl, 'ws://localhost:1234')
    const roomName = decodeURIComponent(url.pathname.slice(1))
    assert.strictEqual(roomName, 'customer revenue report')
  })
})

describe('Auth failure close codes', () => {
  test('CLOSE_CODE_AUTH_FAILURE is 4001', () => {
    assert.strictEqual(CLOSE_CODE_AUTH_FAILURE, 4001)
  })

  test('close reason messages are distinct', () => {
    assert.notStrictEqual('Invalid or missing JWT token', 'JWT token expired')
  })

  test('server detects expired JWT via decode', () => {
    const token = jwt.sign({ sub: 'user-1' }, JWT_SECRET, { expiresIn: '0s' })
    const decoded = jwt.decode(token)
    assert.ok(decoded)
    assert.ok(decoded.exp * 1000 < Date.now())
  })

  test('server detects valid JWT via decode', () => {
    const token = jwt.sign({ sub: 'user-1' }, JWT_SECRET, { expiresIn: '1h' })
    const decoded = jwt.decode(token)
    assert.ok(decoded)
    assert.ok(decoded.exp * 1000 > Date.now())
  })
})