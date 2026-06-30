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
const AUTH_FAILURE_CLOSE_CODE = 4001
const MAX_BACKOFF_TIME = 30000

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
  currentUser?: { id: string; name: string; email?: string }
  authToken?: string
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

  const providerRef = useRef<WebsocketProvider | null>(null)

  const authFailedRef = useRef(false)
  const authFailedForRoomRef = useRef<string | null>(null)

  const [provider, setProvider] = useState<WebsocketProvider | null>(null)
  const [yNodes] = useState(() => doc.getMap<SerializedNode>('nodes'))
  const [yEdges] = useState(() => doc.getMap<SerializedEdge>('edges'))
  const [yYaml] = useState(() => doc.getText('yaml'))

  useEffect(() => {
    if (!pipelineName) {
      if (providerRef.current) {
        providerRef.current.destroy()
        providerRef.current = null
        setProvider(null)
      }
      return
    }

    const roomName = currentUser?.id ? `${currentUser.id}:${pipelineName}` : pipelineName

    if (authFailedRef.current && authFailedForRoomRef.current === roomName) {
      return
    }

    if (providerRef.current) {
      providerRef.current.destroy()
      providerRef.current = null
    }

    const wsProvider = new WebsocketProvider(
      YJS_SERVER_URL,
      roomName,
      doc,
      {
        params: authToken ? { token: authToken } : {},
        maxBackoffTime: MAX_BACKOFF_TIME,
        connect: true,
      },
    )

    wsProvider.on('connection-close', (event: unknown) => {
      const closeEvent = event as CloseEvent | null
      if (closeEvent && closeEvent.code === AUTH_FAILURE_CLOSE_CODE) {
        authFailedRef.current = true
        authFailedForRoomRef.current = roomName
        console.error(
          `[YJS] Authentication failed for room "${roomName}". ` +
          `Reason: ${closeEvent.reason || 'Unknown'}. Stopping reconnection.`
        )
        wsProvider.disconnect()
      }
    })

    wsProvider.on('connection-error', () => {
      // connection-error fires before connection-close; close handler above
      // decides whether to stop reconnecting.
    })

    providerRef.current = wsProvider
    setProvider(wsProvider)

    return () => {
      wsProvider.destroy()
      if (providerRef.current === wsProvider) {
        providerRef.current = null
      }
      setProvider(null)
    }
  }, [authToken, pipelineName, currentUser?.id, doc])

  useEffect(() => {
    const roomName = currentUser?.id ? `${currentUser.id}:${pipelineName}` : pipelineName
    if (authFailedRef.current && authFailedForRoomRef.current === roomName) {
      authFailedRef.current = false
      authFailedForRoomRef.current = null
    }
  }, [authToken, pipelineName, currentUser?.id])

  const awareness = provider?.awareness ?? null

  const [collaborators, setCollaborators] = useState<CollaboratorState[]>([])

  const localSyncingRef = useRef(false)
  const yjsSyncingRef = useRef(false)

  useEffect(() => {
    const aw = provider?.awareness
    if (!aw || !currentUser) return

    const userColor = getColorForUser(currentUser.id)
    const userInitials = currentUser.name.slice(0, 2).toUpperCase()

    aw.setLocalState({
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
      aw.setLocalState(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-set awareness when user identity or provider changes
  }, [provider?.awareness, currentUser?.id, currentUser?.name])

  useEffect(() => {
    const aw = provider?.awareness
    if (!aw) return

    const handleAwarenessChange = () => {
      const localClientId = aw.clientID
      const states: CollaboratorState[] = []

      aw.getStates().forEach((state, clientId) => {
        if (clientId === localClientId) return
        if (!state?.user) return
        states.push(state as unknown as CollaboratorState)
      })

      setCollaborators(states)
    }

    aw.on('change', handleAwarenessChange)
    return () => aw.off('change', handleAwarenessChange)
  }, [provider?.awareness])

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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional mount-only initialization
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
    const aw = provider?.awareness
    if (!aw) return
    const currentState = aw.getLocalState() || {}
    aw.setLocalState({ ...currentState, cursor: { x, y } })
  }, [provider])

  const updateSelectedNode = useCallback((nodeId: string | null) => {
    const aw = provider?.awareness
    if (!aw) return
    const currentState = aw.getLocalState() || {}
    aw.setLocalState({ ...currentState, selectedNode: nodeId })
  }, [provider])

  return {
    syncNodesToYjs,
    syncEdgesToYjs,
    syncYamlToYjs,
    collaborators,
    updateCursor,
    updateSelectedNode,
    awareness: provider?.awareness ?? null,
    provider,
    yYaml,
  }
}
