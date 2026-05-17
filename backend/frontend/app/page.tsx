"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { TopBar } from "@/components/layout/TopBar";
import { WidgetGrid } from "@/components/layout/WidgetGrid";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { ThemeSelector } from "@/components/theme/ThemeSelector";
import { ThemeBuilder } from "@/components/theme/ThemeBuilder";
import { TerminalLauncher } from "@/components/layout/TerminalLauncher";
import { KeybindingsModal } from "@/components/layout/KeybindingsModal";
import { useKeybindings } from "@/hooks/useKeybindings";
import { useAuth } from "@/lib/auth-context";

export default function Dashboard() {
  const router = useRouter();
  const { user, isLoading, logout } = useAuth();
  const [isCommandOpen, setIsCommandOpen] = useState(false);
  const [isThemeSelectorOpen, setIsThemeSelectorOpen] = useState(false);
  const [isThemeBuilderOpen, setIsThemeBuilderOpen] = useState(false);
  const [isLauncherOpen, setIsLauncherOpen] = useState(false);
  const [isKeybindingsOpen, setIsKeybindingsOpen] = useState(false);

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
    }
  }, [isLoading, user, router]);

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  useEffect(() => {
    // Force focus on the window so keybindings work immediately without requiring a click
    window.focus();
    document.body.focus();

    const handlePointerMove = () => {
      if (!document.hasFocus()) {
        window.focus();
      }
    };

    window.addEventListener("pointermove", handlePointerMove);
    return () => window.removeEventListener("pointermove", handlePointerMove);
  }, []);

  useKeybindings(
    () => setIsLauncherOpen(true),
    () => setIsCommandOpen(true),
    () => setIsThemeBuilderOpen(true),
    () => setIsKeybindingsOpen(true)
  );

  if (isLoading || !user) {
    return (
      <div className="flex flex-col items-center justify-center h-screen w-screen bg-[var(--bg-base)]">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-8 h-8 rounded bg-[var(--accent-primary)] text-[var(--bg-base)] font-bold flex items-center justify-center text-lg">P</div>
          <span className="text-xl font-bold text-[var(--text-primary)]">PipelineIQ</span>
        </div>
        <div className="w-5 h-5 border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)] rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <main 
      className="flex flex-col h-screen w-screen overflow-hidden bg-[var(--bg-base)] text-[var(--text-primary)] outline-none"
      tabIndex={0}
      onPointerEnter={(e) => e.currentTarget.focus({ preventScroll: true })}
      onPointerMove={(e) => {
        if (document.activeElement !== e.currentTarget && !e.currentTarget.contains(document.activeElement)) {
          e.currentTarget.focus({ preventScroll: true });
        }
      }}
    >
      <TopBar 
        onOpenTheme={() => setIsThemeSelectorOpen(!isThemeSelectorOpen)}
        onOpenCommand={() => setIsCommandOpen(true)}
        onOpenKeybindings={() => setIsKeybindingsOpen(true)}
        user={user}
        onLogout={handleLogout}
      />
      
      <div className="flex-1 relative overflow-hidden">
        <WidgetGrid />
        
        <ThemeSelector 
          isOpen={isThemeSelectorOpen} 
          onClose={() => setIsThemeSelectorOpen(false)} 
          onOpenBuilder={() => {
            setIsThemeSelectorOpen(false);
            setIsThemeBuilderOpen(true);
          }}
        />
      </div>

      <TerminalLauncher
        isOpen={isLauncherOpen}
        onClose={() => setIsLauncherOpen(false)}
      />

      <CommandPalette 
        isOpen={isCommandOpen} 
        onClose={() => setIsCommandOpen(false)} 
        onOpenThemeBuilder={() => setIsThemeBuilderOpen(true)}
        onOpenKeybindings={() => setIsKeybindingsOpen(true)}
      />
      
      <ThemeBuilder 
        isOpen={isThemeBuilderOpen} 
        onClose={() => setIsThemeBuilderOpen(false)} 
      />

      <KeybindingsModal
        isOpen={isKeybindingsOpen}
        onClose={() => setIsKeybindingsOpen(false)}
      />
    </main>
  );
}
