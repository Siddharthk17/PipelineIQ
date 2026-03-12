"use client";

interface PresenceUser {
  id: string;
  username: string;
  color: string;
}

const PRESENCE_COLORS = [
  "#3fb950", "#58a6ff", "#d2a8ff", "#f78166",
  "#ff7b72", "#79c0ff", "#7ee787", "#ffa657",
];

function getPresenceColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = ((hash << 5) - hash + id.charCodeAt(i)) | 0;
  }
  return PRESENCE_COLORS[Math.abs(hash) % PRESENCE_COLORS.length];
}

export function PresenceIndicator({ username }: { username?: string }) {
  const displayName = username || "You";
  const users: PresenceUser[] = [{
    id: "self",
    username: displayName,
    color: getPresenceColor(displayName),
  }];

  return (
    <div className="flex items-center gap-1.5">
      {users.map((user) => (
        <div
          key={user.id}
          className="flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium"
          style={{ backgroundColor: `${user.color}20`, color: user.color }}
          title={user.username}
        >
          <span
            className="w-2 h-2 rounded-full animate-pulse"
            style={{ backgroundColor: user.color }}
          />
          {user.username}
        </div>
      ))}
    </div>
  );
}
