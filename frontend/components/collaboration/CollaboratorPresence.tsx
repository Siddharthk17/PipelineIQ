import React from 'react'
import type { CollaboratorState } from '@/hooks/useCollaborativePipeline'

interface CollaboratorPresenceProps {
  collaborators: CollaboratorState[]
  currentUserName?: string
}

export function CollaboratorPresence({
  collaborators,
  currentUserName,
}: CollaboratorPresenceProps) {
  if (collaborators.length === 0) {
    return (
      <div
        className="presence-panel presence-panel--solo"
        data-testid="presence-panel"
      >
        <span className="presence-solo-text">Editing solo</span>
      </div>
    )
  }

  return (
    <div
      className="presence-panel"
      data-testid="presence-panel"
      title={`${collaborators.length} other${collaborators.length > 1 ? 's' : ''} editing`}
    >
      <div className="presence-avatars">
        {collaborators.slice(0, 4).map(({ user }) => (
          <div
            key={user.id}
            className="presence-avatar"
            style={{ background: user.color }}
            title={user.name}
            data-testid={`presence-avatar-${user.id}`}
          >
            {user.initials}
          </div>
        ))}
        {collaborators.length > 4 && (
          <div className="presence-avatar presence-avatar--overflow">
            +{collaborators.length - 4}
          </div>
        )}
      </div>
      <span className="presence-count">
        {collaborators.length} other{collaborators.length > 1 ? 's' : ''} here
      </span>
    </div>
  )
}
