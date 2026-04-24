'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Search, X, Palette, Keyboard, RotateCcw, Layout } from 'lucide-react';
import { useWidgetStore, ALL_WIDGETS } from '@/store/widgetStore';
import { useThemeStore } from '@/store/themeStore';

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenThemeBuilder: () => void;
  onOpenKeybindings: () => void;
}

interface Command {
  id: string;
  label: string;
  category: string;
  icon: React.ReactNode;
  action: () => void;
}

export function CommandPalette({ isOpen, onClose, onOpenThemeBuilder, onOpenKeybindings }: CommandPaletteProps) {
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const { widgets, addWidget, removeWidget, resetLayout, workspaces, activeWorkspaceId } = useWidgetStore();
  const { setTheme } = useThemeStore();

  const currentLayout = workspaces[activeWorkspaceId];

  const isWidgetInLayout = (node: unknown, id: string): boolean => {
    if (!node || typeof node !== 'object') return false;
    const n = node as Record<string, unknown>;
    if (n.type === 'widget') return n.id === id;
    return isWidgetInLayout(n.first, id) || isWidgetInLayout(n.second, id);
  };

  const themes = ['catppuccin-mocha', 'tokyo-night', 'gruvbox-dark', 'nord', 'rose-pine', 'pipelineiq-dark', 'pipelineiq-light'];

  const commands: Command[] = [
    ...ALL_WIDGETS.map((w) => {
      const vis = isWidgetInLayout(currentLayout, w.id);
      return {
        id: `toggle-${w.id}`,
        label: `${vis ? 'Hide' : 'Show'} ${w.title}`,
        category: 'Widgets',
        icon: <Layout className="w-4 h-4" />,
        action: () => { vis ? removeWidget(w.id) : addWidget(w.id); onClose(); },
      };
    }),
    ...themes.map((t) => ({
      id: `theme-${t}`,
      label: `Theme: ${t}`,
      category: 'Themes',
      icon: <Palette className="w-4 h-4" />,
      action: () => { setTheme(t); onClose(); },
    })),
    {
      id: 'open-theme-builder',
      label: 'Open Theme Builder',
      category: 'Actions',
      icon: <Palette className="w-4 h-4" />,
      action: () => { onOpenThemeBuilder(); onClose(); },
    },
    {
      id: 'open-keybindings',
      label: 'View Keybindings',
      category: 'Actions',
      icon: <Keyboard className="w-4 h-4" />,
      action: () => { onOpenKeybindings(); onClose(); },
    },
    {
      id: 'reset-layout',
      label: 'Reset Widget Layout',
      category: 'Actions',
      icon: <RotateCcw className="w-4 h-4" />,
      action: () => { resetLayout(); onClose(); },
    },
  ];

  const filtered = query
    ? (Array.isArray(commands) ? commands : []).filter((c) => c.label.toLowerCase().includes(query.toLowerCase()))
    : (Array.isArray(commands) ? commands : []);

  const prevIsOpen = useRef(isOpen);
  useEffect(() => {
    if (isOpen && !prevIsOpen.current) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional reset on open transition
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
    prevIsOpen.current = isOpen;
  }, [isOpen]);

  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    setSelectedIndex(0);
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && filtered[selectedIndex]) {
      e.preventDefault();
      filtered[selectedIndex].action();
    } else if (e.key === 'Escape') {
      onClose();
    }
  }, [filtered, selectedIndex, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[20vh]" onClick={onClose}>
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-lg rounded-xl border shadow-2xl overflow-hidden"
        style={{
          background: 'var(--bg-elevated)',
          borderColor: 'var(--widget-border)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-4 py-3 border-b" style={{ borderColor: 'var(--widget-border)' }}>
          <Search className="w-5 h-5 shrink-0" style={{ color: 'var(--text-secondary)' }} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => handleQueryChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a command..."
            className="flex-1 bg-transparent border-none outline-none text-sm"
            style={{ color: 'var(--text-primary)' }}
          />
          <button onClick={onClose} className="p-1 rounded hover:opacity-80">
            <X className="w-4 h-4" style={{ color: 'var(--text-secondary)' }} />
          </button>
        </div>

        <div className="max-h-72 overflow-y-auto py-2">
          {filtered.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm" style={{ color: 'var(--text-secondary)' }}>
              No commands found
            </div>
          ) : (
            filtered.map((cmd, i) => (
              <button
                key={cmd.id}
                onClick={cmd.action}
                className="w-full flex items-center gap-3 px-4 py-2 text-left text-sm transition-colors"
                style={{
                  color: 'var(--text-primary)',
                  background: i === selectedIndex ? 'var(--interactive-hover)' : 'transparent',
                }}
                onMouseEnter={() => setSelectedIndex(i)}
              >
                <span style={{ color: 'var(--accent-primary)' }}>{cmd.icon}</span>
                <span className="flex-1">{cmd.label}</span>
                <span className="text-xs opacity-50">{cmd.category}</span>
              </button>
            ))
          )}
        </div>

        <div
          className="px-4 py-2 border-t text-xs flex gap-4"
          style={{ borderColor: 'var(--widget-border)', color: 'var(--text-secondary)' }}
        >
          <span>↑↓ Navigate</span>
          <span>↵ Select</span>
          <span>Esc Close</span>
        </div>
      </div>
    </div>
  );
}