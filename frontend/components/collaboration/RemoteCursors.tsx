import React from 'react'
import type { CollaboratorState } from '@/hooks/useCollaborativePipeline'

interface RemoteCursorsProps {
  collaborators: CollaboratorState[]
}

export function RemoteCursors({ collaborators }: RemoteCursorsProps) {
  return (
    <div
      className="remote-cursors"
      style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 1000 }}
      data-testid="remote-cursors"
    >
      {collaborators.map(({ user, cursor }) => {
        if (!cursor) return null
        return (
          <div
            key={user.id}
            className="remote-cursor"
            style={{
              position: 'absolute',
              left: cursor.x,
              top: cursor.y,
              transform: 'translate(0, 0)',
              pointerEvents: 'none',
              userSelect: 'none',
            }}
            data-testid={`remote-cursor-${user.id}`}
          >
            <svg
              width="16"
              height="20"
              viewBox="0 0 16 20"
              fill={user.color}
              style={{ display: 'block', filter: 'drop-shadow(1px 1px 2px rgba(0,0,0,0.5))' }}
              aria-hidden="true"
            >
              <path d="M0 0L0 16L4 12L8 20L10 19L6 11L12 11Z" />
            </svg>
            <span
              className="remote-cursor-label"
              style={{
                position: 'absolute',
                top: 18,
                left: 8,
                background: user.color,
                color: '#fff',
                fontSize: '10px',
                fontWeight: 600,
                padding: '1px 6px',
                borderRadius: '4px',
                whiteSpace: 'nowrap',
                boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
                fontFamily: 'system-ui, sans-serif',
              }}
            >
              {user.name}
            </span>
          </div>
        )
      })}
    </div>
  )
}
