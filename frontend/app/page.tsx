"use client";

import React, { useState, useEffect } from "react";
import { TopBar } from "@/components/layout/TopBar";
import { WidgetGrid } from "@/components/layout/WidgetGrid";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { ThemeSelector } from "@/components/theme/ThemeSelector";
import { ThemeBuilder } from "@/components/theme/ThemeBuilder";
import { TerminalLauncher } from "@/components/layout/TerminalLauncher";
import { KeybindingsModal } from "@/components/layout/KeybindingsModal";
import { useKeybindings } from "@/hooks/useKeybindings";

export default function Dashboard() {
  const [isCommandOpen, setIsCommandOpen] = useState(false);
  const [isThemeSelectorOpen, setIsThemeSelectorOpen] = useState(false);
  const [isThemeBuilderOpen, setIsThemeBuilderOpen] = useState(false);
  const [isLauncherOpen, setIsLauncherOpen] = useState(false);
  const [isKeybindingsOpen, setIsKeybindingsOpen] = useState(false);

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
