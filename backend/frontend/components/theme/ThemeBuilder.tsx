import React, { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { X, Save, Download, Upload } from "lucide-react";
import { useThemeStore } from "@/store/themeStore";

export function ThemeBuilder({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const { addCustomTheme, setTheme } = useThemeStore();
  const [themeName, setThemeName] = useState("My Custom Theme");
  const [variables, setVariables] = useState<Record<string, string>>({
    "--bg-base": "#0d1117",
    "--bg-surface": "#161b22",
    "--bg-elevated": "#21262d",
    "--widget-bg": "rgba(22, 27, 34, 0.85)",
    "--widget-border": "#30363d",
    "--widget-radius": "6px",
    "--widget-shadow": "0 4px 16px rgba(0, 0, 0, 0.4)",
    "--widget-header-bg": "rgba(13, 17, 23, 0.9)",
    "--text-primary": "#e6edf3",
    "--text-secondary": "#7d8590",
    "--text-accent": "#00d9ff",
    "--text-code": "#e6edf3",
    "--accent-primary": "#00d9ff",
    "--accent-secondary": "#a371f7",
    "--accent-success": "#3fb950",
    "--accent-warning": "#d29922",
    "--accent-error": "#f85149",
    "--interactive-hover": "rgba(0, 217, 255, 0.1)",
    "--interactive-active": "rgba(0, 217, 255, 0.2)",
    "--interactive-focus": "#00d9ff",
    "--grid-gap": "10px",
    "--grid-bg": "#010409",
    "--topbar-bg": "rgba(13, 17, 23, 0.95)",
    "--topbar-border": "#21262d",
    "--topbar-height": "48px",
    "--scrollbar-thumb": "#30363d",
    "--scrollbar-track": "#0d1117",
  });

  const handleSave = () => {
    const newTheme = { name: themeName, author: "user", variables };
    addCustomTheme(newTheme);
    
    // Apply theme dynamically by injecting a style tag
    let styleEl = document.getElementById(`theme-${themeName}`);
    if (!styleEl) {
      styleEl = document.createElement("style");
      styleEl.id = `theme-${themeName}`;
      document.head.appendChild(styleEl);
    }
    
    const cssVars = Object.entries(variables).map(([k, v]) => `${k}: ${v};`).join("\n  ");
    styleEl.innerHTML = `[data-theme="${themeName}"] {\n  ${cssVars}\n}`;
    
    setTheme(themeName);
    onClose();
  };

  const handleExport = () => {
    const json = JSON.stringify({ name: themeName, author: "user", variables }, null, 2);
    navigator.clipboard.writeText(json);
    alert("Theme JSON copied to clipboard!");
  };

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, x: "100%" }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: "100%" }}
        transition={{ type: "spring", damping: 25, stiffness: 200 }}
        className="fixed inset-y-0 right-0 z-50 w-[500px] shadow-2xl flex flex-col"
        style={{ backgroundColor: "var(--bg-elevated)", borderLeft: "1px solid var(--widget-border)" }}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b" style={{ borderColor: "var(--widget-border)" }}>
          <h2 className="text-lg font-medium text-[var(--text-primary)]">Theme Builder</h2>
          <button onClick={onClose} className="p-2 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          <div>
            <label className="block text-sm font-medium text-[var(--text-secondary)] mb-2">Theme Name</label>
            <input
              type="text"
              value={themeName}
              onChange={(e) => setThemeName(e.target.value)}
              className="w-full px-3 py-2 rounded bg-[var(--bg-surface)] border text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
              style={{ borderColor: "var(--widget-border)" }}
            />
          </div>

          <div className="space-y-4">
            <h3 className="text-sm font-medium text-[var(--text-primary)] border-b pb-2" style={{ borderColor: "var(--widget-border)" }}>Colors</h3>
            {Object.entries(variables).filter(([k]) => k.includes("color") || k.includes("bg") || k.includes("accent") || k.includes("text")).map(([key, value]) => (
              <div key={key} className="flex items-center justify-between">
                <label className="text-xs font-mono text-[var(--text-secondary)]">{key}</label>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={value}
                    onChange={(e) => setVariables({ ...variables, [key]: e.target.value })}
                    className="w-24 px-2 py-1 text-xs rounded bg-[var(--bg-surface)] border text-[var(--text-primary)] outline-none"
                    style={{ borderColor: "var(--widget-border)" }}
                  />
                  {value.startsWith("#") && (
                    <input
                      type="color"
                      value={value}
                      onChange={(e) => setVariables({ ...variables, [key]: e.target.value })}
                      className="w-6 h-6 rounded cursor-pointer border-0 p-0"
                    />
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="p-4 border-t flex items-center justify-between gap-2" style={{ borderColor: "var(--widget-border)", backgroundColor: "var(--bg-surface)" }}>
          <div className="flex gap-2">
            <button onClick={handleExport} className="p-2 rounded border hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]" style={{ borderColor: "var(--widget-border)" }} title="Export JSON">
              <Download className="w-4 h-4" />
            </button>
            <button className="p-2 rounded border hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]" style={{ borderColor: "var(--widget-border)" }} title="Import JSON">
              <Upload className="w-4 h-4" />
            </button>
          </div>
          <button onClick={handleSave} className="flex items-center gap-2 px-4 py-2 rounded bg-[var(--accent-primary)] text-[var(--bg-base)] font-medium hover:opacity-90 transition-opacity">
            <Save className="w-4 h-4" />
            Save Theme
          </button>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
