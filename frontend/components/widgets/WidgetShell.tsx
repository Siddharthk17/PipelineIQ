"use client";

import React, { useState } from "react";
import { motion } from "motion/react";
import { X } from "lucide-react";
import type { WidgetConfig } from "@/lib/types";
import { useWidgetStore } from "@/store/widgetStore";

interface WidgetShellProps {
  config: WidgetConfig;
  isActive: boolean;
  onClick: () => void;
  onPointerEnter?: () => void;
  onPointerMove?: () => void;
  children: React.ReactNode;
}

export function WidgetShell({ config, isActive, onClick, onPointerEnter, onPointerMove, children }: WidgetShellProps) {
  const { swapWidgets, removeWidget } = useWidgetStore();
  const [isDragOver, setIsDragOver] = useState(false);

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData("widgetId", config.id);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const draggedId = e.dataTransfer.getData("widgetId");
    if (draggedId && draggedId !== config.id) {
      swapWidgets(draggedId, config.id);
    }
  };

  return (
    <motion.div
      onClick={onClick}
      onPointerEnter={onPointerEnter}
      onPointerMove={onPointerMove}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.95 }}
      transition={{ duration: 0.2 }}
      className="flex flex-col w-full h-full overflow-hidden"
    >
      <div
        className="flex flex-col h-full overflow-hidden transition-colors duration-200"
        style={{
          backgroundColor: "var(--widget-bg)",
          border: `2px solid ${isDragOver ? "var(--accent-secondary)" : isActive ? "var(--accent-primary)" : "var(--widget-border)"}`,
          borderRadius: "var(--widget-radius)",
          boxShadow: isDragOver ? "0 0 15px var(--interactive-active)" : isActive ? "0 0 15px var(--interactive-hover)" : "var(--widget-shadow)",
        }}
      >
        <div
          draggable
          onDragStart={handleDragStart}
          className="flex items-center justify-between px-3 py-1.5 select-none cursor-grab active:cursor-grabbing"
          style={{ backgroundColor: "var(--widget-header-bg)", borderBottom: "1px solid var(--widget-border)" }}
        >
          <span className={`text-xs font-bold tracking-wide ${isActive ? "text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}>
            {config.title}
          </span>
          <div className="flex items-center gap-1">
            {isActive && <div className="w-2 h-2 rounded-full bg-[var(--accent-primary)] shadow-[0_0_8px_var(--accent-primary)]" />}
            <button
              onClick={(e) => { e.stopPropagation(); removeWidget(config.id); }}
              className="p-0.5 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--accent-error)] transition-colors"
              title="Close widget"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-hidden relative p-3">
          {children}
        </div>
      </div>
    </motion.div>
  );
}
