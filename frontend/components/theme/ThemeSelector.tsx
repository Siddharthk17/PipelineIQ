import React from "react";
import { useThemeStore } from "@/store/themeStore";
import { motion, AnimatePresence } from "motion/react";
import { Check, X, Plus } from "lucide-react";

const BUILT_IN_THEMES = [
  { id: "catppuccin-mocha", name: "Catppuccin Mocha", colors: ["#1e1e2e", "#cba6f7", "#a6e3a1", "#f9e2af", "#f38ba8"] },
  { id: "tokyo-night", name: "Tokyo Night", colors: ["#1a1b26", "#7aa2f7", "#9ece6a", "#e0af68", "#f7768e"] },
  { id: "gruvbox-dark", name: "Gruvbox Dark", colors: ["#282828", "#d79921", "#b8bb26", "#fabd2f", "#cc241d"] },
  { id: "nord", name: "Nord", colors: ["#2e3440", "#88c0d0", "#a3be8c", "#ebcb8b", "#bf616a"] },
  { id: "rose-pine", name: "Rosé Pine", colors: ["#191724", "#ebbcba", "#31748f", "#f6c177", "#eb6f92"] },
  { id: "pipelineiq-dark", name: "PipelineIQ Dark", colors: ["#0d1117", "#00d9ff", "#3fb950", "#d29922", "#f85149"] },
  { id: "pipelineiq-light", name: "PipelineIQ Light", colors: ["#f6f8fa", "#0969da", "#1a7f37", "#9a6700", "#cf222e"] },
];

export function ThemeSelector({ isOpen, onClose, onOpenBuilder }: { isOpen: boolean; onClose: () => void; onOpenBuilder: () => void }) {
  const { activeTheme, setTheme, customThemes } = useThemeStore();

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -10 }}
        className="absolute top-12 right-4 z-50 w-80 rounded-xl shadow-2xl overflow-hidden"
        style={{ backgroundColor: "var(--bg-elevated)", border: "1px solid var(--widget-border)" }}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "var(--widget-border)" }}>
          <h3 className="font-medium text-[var(--text-primary)]">Themes</h3>
          <button onClick={onClose} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            <X className="w-4 h-4" />
          </button>
        </div>
        
        <div className="p-4 max-h-96 overflow-y-auto">
          <div className="grid grid-cols-2 gap-3 mb-4">
            {BUILT_IN_THEMES.map((theme) => (
              <button
                key={theme.id}
                onClick={() => setTheme(theme.id)}
                className={`flex flex-col items-start p-3 rounded-lg border transition-all ${
                  activeTheme === theme.id ? "border-[var(--accent-primary)] bg-[var(--interactive-active)]" : "border-[var(--widget-border)] hover:bg-[var(--interactive-hover)]"
                }`}
              >
                <div className="flex items-center justify-between w-full mb-2">
                  <span className="text-xs font-medium text-[var(--text-primary)] truncate">{theme.name}</span>
                  {activeTheme === theme.id && <Check className="w-3 h-3 text-[var(--accent-primary)]" />}
                </div>
                <div className="flex gap-1">
                  {theme.colors.map((c, i) => (
                    <div key={i} className="w-3 h-3 rounded-full" style={{ backgroundColor: c }} />
                  ))}
                </div>
              </button>
            ))}
          </div>

          {customThemes.length > 0 && (
            <div className="mb-4">
              <h4 className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-2">Custom Themes</h4>
              <div className="grid grid-cols-2 gap-3">
                {customThemes.map((theme) => (
                  <button
                    key={theme.name}
                    onClick={() => setTheme(theme.name)}
                    className={`flex flex-col items-start p-3 rounded-lg border transition-all ${
                      activeTheme === theme.name ? "border-[var(--accent-primary)] bg-[var(--interactive-active)]" : "border-[var(--widget-border)] hover:bg-[var(--interactive-hover)]"
                    }`}
                  >
                    <div className="flex items-center justify-between w-full mb-2">
                      <span className="text-xs font-medium text-[var(--text-primary)] truncate">{theme.name}</span>
                      {activeTheme === theme.name && <Check className="w-3 h-3 text-[var(--accent-primary)]" />}
                    </div>
                    <div className="flex gap-1">
                      <div className="w-3 h-3 rounded-full" style={{ backgroundColor: theme.variables["--bg-base"] }} />
                      <div className="w-3 h-3 rounded-full" style={{ backgroundColor: theme.variables["--accent-primary"] }} />
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={() => { onClose(); onOpenBuilder(); }}
            className="w-full flex items-center justify-center gap-2 py-2 rounded-lg border border-dashed border-[var(--widget-border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--text-primary)] transition-colors text-sm"
          >
            <Plus className="w-4 h-4" />
            Create Theme
          </button>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
