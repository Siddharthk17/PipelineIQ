"use client";

import React, { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "motion/react";
import { useWidgetStore, getAllWidgets } from "@/store/widgetStore";
import { Terminal } from "lucide-react";

export function TerminalLauncher({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const { widgets, workspaces, activeWorkspaceId, addWidget } = useWidgetStore();
  const visibleWidgetIds = getAllWidgets(workspaces[activeWorkspaceId]);
  const widgetsArray = Array.isArray(widgets) ? widgets : [];
  const availableWidgets = widgetsArray.filter(w => !visibleWidgetIds.includes(w.id));
  
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [search, setSearch] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = availableWidgets.filter(w => w.title.toLowerCase().includes(search.toLowerCase()));

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearch(e.target.value);
    setSelectedIndex(0);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (filtered.length === 0) {
      if (e.key === "Escape") onClose();
      return;
    }
    if (e.key === "ArrowDown") {
      setSelectedIndex(prev => (prev + 1) % filtered.length);
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      setSelectedIndex(prev => (prev - 1 + filtered.length) % filtered.length);
      e.preventDefault();
    } else if (e.key === "Enter") {
      if (filtered[selectedIndex]) {
        addWidget(filtered[selectedIndex].id);
        onClose();
      }
    } else if (e.key === "Escape") {
      onClose();
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
            onClick={onClose}
          />
          <motion.div
            initial={{ opacity: 0, y: -20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -20, scale: 0.95 }}
            transition={{ duration: 0.15 }}
            className="fixed top-[20%] left-1/2 -translate-x-1/2 z-50 w-full max-w-2xl rounded-none shadow-2xl font-mono"
            style={{ backgroundColor: "var(--bg-base)", border: "2px solid var(--accent-primary)" }}
          >
            <div className="flex items-center p-4 border-b" style={{ borderColor: "var(--widget-border)", backgroundColor: "var(--bg-surface)" }}>
              <Terminal className="w-5 h-5 text-[var(--accent-primary)] mr-3" />
              <span className="text-[var(--accent-primary)] mr-2">~ λ</span>
              <input
                ref={inputRef}
                type="text"
                value={search}
                onChange={handleSearchChange}
                onKeyDown={handleKeyDown}
                className="flex-1 bg-transparent border-none outline-none text-[var(--text-primary)]"
                placeholder="launch..."
              />
            </div>
            <div className="max-h-[400px] overflow-y-auto p-2">
              {filtered.length === 0 ? (
                <div className="p-4 text-[var(--text-secondary)]">No modules found.</div>
              ) : (
                filtered.map((w, idx) => (
                  <div
                    key={w.id}
                    className={`flex items-center px-4 py-2 cursor-pointer transition-colors ${
                      idx === selectedIndex ? "bg-[var(--accent-primary)] text-[var(--bg-base)] font-bold" : "text-[var(--text-primary)] hover:bg-[var(--interactive-hover)]"
                    }`}
                    onClick={() => {
                      addWidget(w.id);
                      onClose();
                    }}
                  >
                    <span className="w-8">{idx === selectedIndex ? ">" : " "}</span>
                    <span>{w.title}</span>
                  </div>
                ))
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
