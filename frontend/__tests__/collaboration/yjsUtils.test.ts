import { describe, test, expect, vi } from 'vitest'
import { serializeNode, deserializeNode, serializeEdge, deserializeEdge, getColorForUser } from '@/lib/yjsUtils'
import { type Node, type Edge } from '@xyflow/react'

const mockOnConfigure = vi.fn()

const sampleNode: Node = {
  id: 'filter_revenue',
  type: 'stepNode',
  position: { x: 200, y: 150 },
  data: {
    type: 'filter',
    name: 'filter_revenue',
    config: { column: 'amount', operator: 'gt', value: 100 },
    onConfigure: mockOnConfigure,
  },
}

const sampleEdge: Edge = {
  id: 'load__filter',
  source: 'load_data',
  target: 'filter_revenue',
  sourceHandle: 'out',
  targetHandle: 'in',
  animated: true,
}

describe('serializeNode', () => {
  test('preserves id, type, and position', () => {
    const s = serializeNode(sampleNode)
    expect(s.id).toBe('filter_revenue')
    expect(s.type).toBe('stepNode')
    expect(s.position).toEqual({ x: 200, y: 150 })
  })

  test('preserves data.type, data.name, data.config', () => {
    const s = serializeNode(sampleNode)
    expect(s.data.type).toBe('filter')
    expect(s.data.name).toBe('filter_revenue')
    expect(s.data.config.column).toBe('amount')
  })

  test('excludes onConfigure function', () => {
    const s = serializeNode(sampleNode)
    expect((s.data as Record<string, unknown>).onConfigure).toBeUndefined()
  })
})

describe('deserializeNode', () => {
  test('re-injects onConfigure callback', () => {
    const s = serializeNode(sampleNode)
    const node = deserializeNode(s, mockOnConfigure)
    expect((node.data as Record<string, unknown>).onConfigure).toBe(mockOnConfigure)
  })

  test('roundtrip preserves position', () => {
    const s = serializeNode(sampleNode)
    const node = deserializeNode(s, mockOnConfigure)
    expect(node.position).toEqual({ x: 200, y: 150 })
  })

  test('roundtrip preserves config', () => {
    const s = serializeNode(sampleNode)
    const node = deserializeNode(s, mockOnConfigure)
    expect(((node.data as Record<string, unknown>).config as Record<string, unknown>).operator).toBe('gt')
  })
})

describe('serializeEdge', () => {
  test('preserves id, source, target, handles', () => {
    const s = serializeEdge(sampleEdge)
    expect(s.id).toBe('load__filter')
    expect(s.source).toBe('load_data')
    expect(s.target).toBe('filter_revenue')
    expect(s.sourceHandle).toBe('out')
    expect(s.targetHandle).toBe('in')
  })
})

describe('deserializeEdge', () => {
  test('roundtrip preserves id and endpoints', () => {
    const s = serializeEdge(sampleEdge)
    const e = deserializeEdge(s)
    expect(e.id).toBe('load__filter')
    expect(e.source).toBe('load_data')
    expect(e.target).toBe('filter_revenue')
  })
})

describe('getColorForUser', () => {
  test('returns a valid hex color', () => {
    const color = getColorForUser('user-123')
    expect(color).toMatch(/^#[0-9A-Fa-f]{6}$/)
  })

  test('same user always gets same color', () => {
    const c1 = getColorForUser('user-abc')
    const c2 = getColorForUser('user-abc')
    expect(c1).toBe(c2)
  })

  test('different users get different colors (for common pairs)', () => {
    const users = ['user-1', 'user-2', 'user-3', 'user-4']
    const colors = users.map(getColorForUser)
    const unique = new Set(colors)
    expect(unique.size).toBeGreaterThanOrEqual(3)
  })

  test('handles empty string without crashing', () => {
    expect(() => getColorForUser('')).not.toThrow()
  })
})
