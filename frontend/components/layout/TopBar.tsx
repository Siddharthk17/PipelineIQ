"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useThemeStore } from "@/store/themeStore";
import { useWidgetStore, getAllWidgets } from "@/store/widgetStore";
import { useKeybindingStore } from "@/store/keybindingStore";
import { checkHealth } from "@/lib/api";
import { Activity, Clock, Command, LayoutDashboard, Palette, RefreshCw, Keyboard, LogOut, User } from "lucide-react";
import { AuthUser } from "@/lib/auth-context";

export function TopBar({ onOpenTheme, onOpenCommand, onOpenKeybindings, user, onLogout }: { onOpenTheme: () => void; onOpenCommand: () => void; onOpenKeybindings: () => void; user?: AuthUser | null; onLogout?: () => void }) {
  const router = useRouter();
  const [time, setTime] = useState<Date | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const { resetLayout, workspaces, activeWorkspaceId, switchWorkspace } = useWidgetStore();
  const { keybindings } = useKeybindingStore();

  const getShortcut = (action: string) => {
    const kb = keybindings.find(k => k.action === action);
    return kb ? ` (${kb.keys.join("+")})` : "";
  };

  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);
    const initialTimer = setTimeout(() => setTime(new Date()), 0);
    return () => {
      clearInterval(timer);
      clearTimeout(initialTimer);
    };
  }, []);

  useEffect(() => {
    const check = async () => {
      try {
        await checkHealth();
        setIsConnected(true);
      } catch {
        setIsConnected(false);
      }
    };
    check();
    const interval = setInterval(check, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="relative flex items-center justify-between px-4" style={{ height: "var(--topbar-height)", backgroundColor: "var(--topbar-bg)", borderBottom: "1px solid var(--topbar-border)" }}>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          {[1, 2, 3, 4, 5].map(id => {
            const hasWindows = getAllWidgets(workspaces[id]).length > 0;
            return (
              <button
                key={id}
                onClick={() => switchWorkspace(id)}
                onDragOver={(e) => {
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const widgetId = e.dataTransfer.getData("widgetId");
                  if (widgetId) {
                    useWidgetStore.getState().moveToWorkspace(widgetId, id);
                  }
                }}
                className={`w-6 h-6 rounded flex items-center justify-center text-xs font-bold transition-all duration-200 ${
                  activeWorkspaceId === id
                    ? "bg-[var(--accent-primary)] text-white"
                    : hasWindows
                      ? "bg-[var(--widget-border)] text-[var(--text-primary)] hover:bg-[var(--accent-primary)] hover:text-white"
                      : "bg-[var(--widget-bg)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                }`}
              >
                {id}
              </button>
            );
          })}
        </div>
      </div>

      <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex items-center gap-3">
        <div className="flex items-center justify-center w-6 h-6 rounded bg-[var(--accent-primary)] text-[var(--bg-base)] font-bold">
          P
        </div>
        <span className="font-bold text-[var(--text-primary)] tracking-tight">PipelineIQ</span>
        <span className="px-2 py-0.5 text-xs rounded bg-[var(--bg-surface)] text-[var(--text-secondary)]">v3.6.2</span>
      </div>

      <div className="flex items-center gap-4 text-[var(--text-secondary)]">
        {user && (
          <>
            <div className="flex items-center gap-2 text-xs">
              <User className="w-4 h-4" />
              {!user.isDemo && <span className="text-[var(--text-primary)] max-w-[96px] truncate">{user.username}</span>}
              {user.isDemo ? (
                <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/20 text-emerald-400">DEMO</span>
              ) : user.role === "admin" ? (
                <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-violet-500/20 text-violet-400">ADMIN</span>
              ) : (
                <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-blue-500/20 text-blue-400">VIEWER</span>
              )}
            </div>
            {onLogout && (
              <button onClick={onLogout} className="flex items-center gap-1 text-xs text-[var(--text-secondary)] hover:text-[var(--accent-error)] transition-colors" title="Logout">
                <LogOut className="w-3 h-3" />
                Logout
              </button>
            )}
            <div className="w-px h-4 bg-[var(--topbar-border)]" />
          </>
        )}
        <div className="flex items-center gap-2 text-xs mr-2">
          <div className={`w-2 h-2 rounded-full ${isConnected ? "bg-[var(--accent-success)]" : "bg-[var(--accent-error)]"} animate-pulse`} />
          <span className="text-[var(--text-secondary)]">{isConnected ? "Connected" : "Unreachable"}</span>
        </div>
        <div className="flex items-center gap-2 text-sm font-mono mr-2">
          <Clock className="w-4 h-4" />
          {time ? time.toLocaleTimeString() : "..."}
        </div>
        <button onClick={onOpenKeybindings} className="p-1.5 rounded hover:bg-[var(--interactive-hover)] hover:text-[var(--text-primary)] transition-colors" title={`Keybindings${getShortcut("keybindings:open")}`}>
          <Keyboard className="w-4 h-4" />
        </button>
        <button onClick={onOpenTheme} className="p-1.5 rounded hover:bg-[var(--interactive-hover)] hover:text-[var(--text-primary)] transition-colors" title={`Theme${getShortcut("theme:open")}`}>
          <Palette className="w-4 h-4" />
        </button>
        <button onClick={onOpenCommand} className="p-1.5 rounded hover:bg-[var(--interactive-hover)] hover:text-[var(--text-primary)] transition-colors" title={`Command Palette${getShortcut("command:open")}`}>
          <Command className="w-4 h-4" />
        </button>
        <button onClick={resetLayout} className="p-1.5 rounded hover:bg-[var(--interactive-hover)] hover:text-[var(--text-primary)] transition-colors" title={`Reset Layout${getShortcut("layout:reset")}`}>
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
