import * as Y from 'yjs'
import { WebsocketProvider } from 'y-websocket'
import { useState, useEffect, useCallback, useRef } from 'react'
import { type Node, type Edge } from '@xyflow/react'
import { useDebouncedCallback } from 'use-debounce'
import {
  serializeNode,
  deserializeNode,
  serializeEdge,
  deserializeEdge,
  getColorForUser,
  type SerializedNode,
  type SerializedEdge,
} from '@/lib/yjsUtils'

const YJS_SERVER_URL = process.env.NEXT_PUBLIC_YJS_URL || 'ws://localhost:1234'
const SYNC_DEBOUNCE_MS = 300

export interface CollaboratorState {
  user: {
    id: string
    name: string
    color: string
    initials: string
  }
  cursor?: {
    x: number
    y: number
  }
  selectedNode?: string | null
}

interface UseCollaborativePipelineOptions {
  pipelineName: string
  initialNodes: Node[]
  initialEdges: Edge[]
  initialYaml: string
  currentUser: { id: string; name: string; email?: string }
  authToken: string
  onNodesChange: (nodes: Node[]) => void
  onEdgesChange: (edges: Edge[]) => void
  onYamlChange: (yaml: string) => void
  onNodeConfigure: (nodeId: string) => void
}

export function useCollaborativePipeline({
  pipelineName,
  initialNodes,
  initialEdges,
  initialYaml,
  currentUser,
  authToken,
  onNodesChange,
  onEdgesChange,
  onYamlChange,
  onNodeConfigure,
}: UseCollaborativePipelineOptions) {
  const [doc] = useState(() => new Y.Doc())

  const [provider] = useState(() => {
    return new WebsocketProvider(YJS_SERVER_URL, pipelineName, doc, {
      params: { token: authToken },
    })
  })

  const yNodes = doc.getMap<SerializedNode>('nodes')
  const yEdges = doc.getMap<SerializedEdge>('edges')
  const yYaml = doc.getText('yaml')
  const awareness = provider.awareness

  const [collaborators, setCollaborators] = useState<CollaboratorState[]>([])

  const localSyncingRef = useRef(false)
  const yjsSyncingRef = useRef(false)

  useEffect(() => {
    const userColor = getColorForUser(currentUser.id)
    const userInitials = currentUser.name.slice(0, 2).toUpperCase()

    awareness.setLocalState({
      user: {
        id: currentUser.id,
        name: currentUser.name,
        color: userColor,
        initials: userInitials,
      },
      cursor: null,
      selectedNode: null,
    })

    return () => {
      awareness.setLocalState(null)
      provider.destroy()
    }
  }, [])

  useEffect(() => {
    const handleAwarenessChange = () => {
      const localClientId = awareness.clientID
      const states: CollaboratorState[] = []

      awareness.getStates().forEach((state, clientId) => {
        if (clientId === localClientId) return
        if (!state?.user) return
        states.push(state as unknown as CollaboratorState)
      })

      setCollaborators(states)
    }

    awareness.on('change', handleAwarenessChange)
    return () => awareness.off('change', handleAwarenessChange)
  }, [awareness])

  useEffect(() => {
    if (yNodes.size === 0 && initialNodes.length > 0) {
      doc.transact(() => {
        initialNodes.forEach(node => yNodes.set(node.id, serializeNode(node)))
        initialEdges.forEach(edge => yEdges.set(edge.id, serializeEdge(edge)))
      })
    }

    if (yYaml.length === 0 && initialYaml) {
      doc.transact(() => {
        yYaml.insert(0, initialYaml)
      })
    }
  }, [])

  useEffect(() => {
    const observer = () => {
      if (localSyncingRef.current) return

      yjsSyncingRef.current = true
      try {
        const nodes = Array.from(yNodes.values()).map(n =>
          deserializeNode(n, onNodeConfigure)
        )
        const edges = Array.from(yEdges.values()).map(deserializeEdge)
        onNodesChange(nodes)
        onEdgesChange(edges)
      } finally {
        setTimeout(() => { yjsSyncingRef.current = false }, 0)
      }
    }

    yNodes.observe(observer)
    yEdges.observe(observer)
    return () => {
      yNodes.unobserve(observer)
      yEdges.unobserve(observer)
    }
  }, [yNodes, yEdges, onNodesChange, onEdgesChange, onNodeConfigure])

  useEffect(() => {
    const observer = () => {
      if (localSyncingRef.current) return

      yjsSyncingRef.current = true
      try {
        const newYaml = yYaml.toString()
        onYamlChange(newYaml)
      } finally {
        setTimeout(() => { yjsSyncingRef.current = false }, 0)
      }
    }

    yYaml.observe(observer)
    return () => yYaml.unobserve(observer)
  }, [yYaml, onYamlChange])

  const syncNodesToYjs = useDebouncedCallback(
    (nodes: Node[]) => {
      if (yjsSyncingRef.current) return

      localSyncingRef.current = true
      try {
        doc.transact(() => {
          yNodes.clear()
          nodes.forEach(node => yNodes.set(node.id, serializeNode(node)))
        })
      } finally {
        setTimeout(() => { localSyncingRef.current = false }, 0)
      }
    },
    SYNC_DEBOUNCE_MS
  )

  const syncEdgesToYjs = useDebouncedCallback(
    (edges: Edge[]) => {
      if (yjsSyncingRef.current) return

      localSyncingRef.current = true
      try {
        doc.transact(() => {
          yEdges.clear()
          edges.forEach(edge => yEdges.set(edge.id, serializeEdge(edge)))
        })
      } finally {
        setTimeout(() => { localSyncingRef.current = false }, 0)
      }
    },
    SYNC_DEBOUNCE_MS
  )

  const syncYamlToYjs = useDebouncedCallback(
    (newYaml: string) => {
      if (yjsSyncingRef.current) return

      localSyncingRef.current = true
      try {
        doc.transact(() => {
          if (yYaml.toString() !== newYaml) {
            yYaml.delete(0, yYaml.length)
            yYaml.insert(0, newYaml)
          }
        })
      } finally {
        setTimeout(() => { localSyncingRef.current = false }, 0)
      }
    },
    SYNC_DEBOUNCE_MS
  )

  const updateCursor = useCallback((x: number, y: number) => {
    const currentState = awareness.getLocalState() || {}
    awareness.setLocalState({ ...currentState, cursor: { x, y } })
  }, [awareness])

  const updateSelectedNode = useCallback((nodeId: string | null) => {
    const currentState = awareness.getLocalState() || {}
    awareness.setLocalState({ ...currentState, selectedNode: nodeId })
  }, [awareness])

  return {
    syncNodesToYjs,
    syncEdgesToYjs,
    syncYamlToYjs,
    collaborators,
    updateCursor,
    updateSelectedNode,
    awareness,
    provider,
    yYaml,
  }
}
