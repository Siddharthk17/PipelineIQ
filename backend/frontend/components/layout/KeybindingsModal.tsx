"use client";

import React, { useState, useEffect } from "react";
import { motion, AnimatePresence } from "motion/react";
import { X, RotateCcw, Edit2, Check } from "lucide-react";
import { useKeybindingStore, Keybinding } from "@/store/keybindingStore";

interface KeybindingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function KeybindingsModal({ isOpen, onClose }: KeybindingsModalProps) {
  const { keybindings, updateKeybinding, resetKeybindings } = useKeybindingStore();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [currentKeys, setCurrentKeys] = useState<string[]>([]);

  useEffect(() => {
    if (!editingId) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      e.preventDefault();
      
      const key = e.key;
      if (key === "Escape") {
        setEditingId(null);
        return;
      }
      if (key === "Enter") {
        if (currentKeys.length > 0) {
          updateKeybinding(editingId, currentKeys);
        }
        setEditingId(null);
        return;
      }

      const isCtrl = e.ctrlKey || e.metaKey;
      const isAlt = e.altKey;
      const isShift = e.shiftKey;

      const keys = [];
      if (isCtrl) keys.push("Ctrl");
      if (isAlt) keys.push("Alt");
      if (isShift) keys.push("Shift");

      if (key === "Enter") keys.push("Enter");
      else if (key !== "Control" && key !== "Alt" && key !== "Shift" && key !== "Meta") {
        keys.push(key.toUpperCase());
      }

      setCurrentKeys(keys);
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [editingId, currentKeys, updateKeybinding]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="w-full max-w-2xl bg-[var(--bg-surface)] border border-[var(--widget-border)] rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[80vh]"
      >
        <div className="flex items-center justify-between p-4 border-b border-[var(--widget-border)] bg-[var(--widget-header-bg)]">
          <h2 className="text-lg font-bold text-[var(--text-primary)]">Keybindings</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={resetKeybindings}
              className="flex items-center gap-2 px-3 py-1.5 text-sm rounded bg-[var(--bg-base)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
            >
              <RotateCcw className="w-4 h-4" />
              Reset Defaults
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-2">
          {keybindings.map((kb) => (
            <div
              key={kb.id}
              className="flex items-center justify-between p-3 rounded-lg bg-[var(--bg-base)] border border-[var(--widget-border)] hover:border-[var(--accent-primary)] transition-colors group"
            >
              <span className="text-[var(--text-primary)] font-medium">{kb.description}</span>
              
              <div className="flex items-center gap-3">
                {editingId === kb.id ? (
                  <div className="flex items-center gap-2 bg-[var(--interactive-hover)] px-3 py-1.5 rounded border border-[var(--accent-primary)]">
                    <span className="text-sm font-mono text-[var(--accent-primary)] animate-pulse">
                      {currentKeys.length > 0 ? currentKeys.join(" + ") : "Press keys..."}
                    </span>
                    <button onClick={() => {
                       if (currentKeys.length > 0) updateKeybinding(kb.id, currentKeys);
                       setEditingId(null);
                    }} className="ml-2 text-[var(--accent-success)] hover:text-green-400">
                      <Check className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <div className="flex gap-1">
                      {kb.keys.map((k, i) => (
                        <kbd key={i} className="px-2 py-1 text-xs font-mono rounded bg-[var(--bg-surface)] border border-[var(--widget-border)] text-[var(--text-secondary)]">
                          {k}
                        </kbd>
                      ))}
                    </div>
                    <button
                      onClick={() => {
                        setEditingId(kb.id);
                        setCurrentKeys(kb.keys);
                      }}
                      className="p-1.5 rounded opacity-0 group-hover:opacity-100 hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-all"
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
          
          <div className="mt-6 p-4 rounded-lg bg-[var(--bg-base)] border border-[var(--widget-border)]">
            <h3 className="text-sm font-bold text-[var(--text-primary)] mb-2">Hardcoded Workspaces</h3>
            <div className="space-y-2 text-sm text-[var(--text-secondary)]">
              <div className="flex justify-between">
                <span>Switch Workspace 1-5</span>
                <kbd className="px-2 py-1 text-xs font-mono rounded bg-[var(--bg-surface)] border border-[var(--widget-border)]">Alt + 1-5</kbd>
              </div>
              <div className="flex justify-between">
                <span>Move Widget to Workspace 1-5</span>
                <kbd className="px-2 py-1 text-xs font-mono rounded bg-[var(--bg-surface)] border border-[var(--widget-border)]">Alt + Shift + 1-5</kbd>
              </div>
            </div>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
