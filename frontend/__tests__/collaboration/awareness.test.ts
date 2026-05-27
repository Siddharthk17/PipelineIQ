import { describe, test, expect } from 'vitest'
import * as Y from 'yjs'

describe('Yjs Awareness protocol', () => {
  test('local state is settable and retrievable', () => {
    const { Awareness } = require('y-protocols/awareness')
    const doc = new Y.Doc()
    const awareness = new Awareness(doc)

    const userState = {
      user: { id: 'u1', name: 'Alice', color: '#EF4444', initials: 'AL' },
      cursor: { x: 100, y: 200 },
    }

    awareness.setLocalState(userState)
    const local = awareness.getLocalState()
    expect(local).toBeDefined()
    expect(local.user.id).toBe('u1')
    expect(local.user.name).toBe('Alice')
    expect(local.cursor.x).toBe(100)
  })

  test('state encoding produces non-null output', () => {
    const { Awareness, encodeAwarenessUpdate } = require('y-protocols/awareness')
    const doc1 = new Y.Doc()
    const doc2 = new Y.Doc()
    const aw1 = new Awareness(doc1)
    const aw2 = new Awareness(doc2)

    aw1.setLocalState({ user: { id: 'u1', name: 'Alice', color: '#EF4444', initials: 'AL' } })
    aw2.setLocalState({ user: { id: 'u2', name: 'Bob', color: '#3B82F6', initials: 'BO' } })

    const enc1 = encodeAwarenessUpdate(aw1, [aw1.clientID])
    expect(enc1).toBeTruthy()
  })

  test('setting null state indicates disconnect', () => {
    const { Awareness } = require('y-protocols/awareness')
    const doc = new Y.Doc()
    const awareness = new Awareness(doc)

    awareness.setLocalState({ user: { name: 'Alice' } })
    expect(awareness.getLocalState()).not.toBeNull()

    awareness.setLocalState(null)
    expect(awareness.getLocalState()).toBeNull()
  })

  test('clientID is unique per document', () => {
    const { Awareness } = require('y-protocols/awareness')
    const doc1 = new Y.Doc()
    const doc2 = new Y.Doc()
    const aw1 = new Awareness(doc1)
    const aw2 = new Awareness(doc2)
    expect(aw1.clientID).not.toBe(aw2.clientID)
  })
})
