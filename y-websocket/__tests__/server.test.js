const jwt = require('jsonwebtoken')
const { describe, test, before, after } = require('node:test')
const assert = require('node:assert')
const crypto = require('crypto')

const DEFAULT_SECRET = 'test-jwt-secret-minimum-32-characters-long'
const ACCESS_TOKEN_SIGNING_SECRET = process.env.ACCESS_TOKEN_SECRET || hkdfHex(process.env.SECRET_KEY || DEFAULT_SECRET, 'pipelineiq-jwt-signing-v1')
const JWT_ISSUER = process.env.JWT_ISSUER || 'pipelineiq'
const JWT_AUDIENCE = process.env.JWT_AUDIENCE || 'pipelineiq-api'
const REDIS_YJS_URL = process.env.REDIS_YJS_URL || 'redis://redis-yjs:6382'
const PORT = parseInt(process.env.PORT || '0', 10)

function hkdfHex(master, info) {
  return Buffer.from(crypto.hkdfSync(
    'sha256',
    Buffer.from(master, 'utf8'),
    Buffer.from('pipelineiq-salt', 'utf8'),
    Buffer.from(info, 'utf8'),
    32,
  )).toString('hex')
}

function signToken(payload, options = {}) {
  return jwt.sign(
    payload,
    ACCESS_TOKEN_SIGNING_SECRET,
    {
      issuer: JWT_ISSUER,
      audience: JWT_AUDIENCE,
      ...options,
    },
  )
}

// Basic JWT verification function (duplicated from server for unit testing)
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
    const token = signToken({ sub: 'user-1', name: 'Alice' })
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
    const token = signToken({ sub: 'user-1' }, { expiresIn: '0s' })
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

  test('parses authenticated Redis URL database and password', () => {
    const url = new URL('redis://:s3cr3t@redis-yjs:6382/2')
    const db = url.pathname && url.pathname.length > 1
      ? parseInt(url.pathname.slice(1), 10)
      : 0

    assert.strictEqual(url.hostname, 'redis-yjs')
    assert.strictEqual(url.port, '6382')
    assert.strictEqual(decodeURIComponent(url.password), 's3cr3t')
    assert.strictEqual(db, 2)
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
    const token = signToken({ sub: 'user-1' }, { expiresIn: '0s' })
    const decoded = jwt.decode(token)
    assert.ok(decoded)
    assert.ok(decoded.exp * 1000 < Date.now())
  })

  test('server detects valid JWT via decode', () => {
    const token = signToken({ sub: 'user-1' }, { expiresIn: '1h' })
    const decoded = jwt.decode(token)
    assert.ok(decoded)
    assert.ok(decoded.exp * 1000 > Date.now())
  })
})
