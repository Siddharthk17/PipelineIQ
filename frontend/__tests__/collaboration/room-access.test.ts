import { describe, test, expect } from 'vitest'
import {
  AUTH_FAILURE_CLOSE_CODE,
  ROOM_FORBIDDEN_CLOSE_CODE,
  isAccessDeniedCloseCode,
} from '@/hooks/useCollaborativePipeline'

// M-005: the relay closes denied peers with 4001 (bad/expired/revoked
// token, inactive user) or 4401 (authenticated but not a room member).
// The hook must treat both as final and stop reconnecting.
describe('collab room-access close codes', () => {
  test('relay close codes match the hook constants', () => {
    expect(AUTH_FAILURE_CLOSE_CODE).toBe(4001)
    expect(ROOM_FORBIDDEN_CLOSE_CODE).toBe(4401)
  })

  test('auth and room-forbidden codes stop reconnection', () => {
    expect(isAccessDeniedCloseCode(4001)).toBe(true)
    expect(isAccessDeniedCloseCode(4401)).toBe(true)
  })

  test('transient codes keep reconnecting', () => {
    for (const code of [1000, 1001, 1005, 1006, 1011, 1012, 3000]) {
      expect(isAccessDeniedCloseCode(code)).toBe(false)
    }
  })

  test('missing code never counts as denied', () => {
    expect(isAccessDeniedCloseCode(undefined)).toBe(false)
    expect(isAccessDeniedCloseCode(null)).toBe(false)
  })
})
