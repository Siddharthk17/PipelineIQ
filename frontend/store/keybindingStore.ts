import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface Keybinding {
  id: string;
  description: string;
  keys: string[]; // e.g., ["Alt", "Enter"]
  action: string;
  defaultKeys: string[];
}

export const DEFAULT_KEYBINDINGS: Keybinding[] = [
  { id: "open-launcher", description: "Open Terminal Launcher", keys: ["Alt", "Enter"], action: "launcher:open", defaultKeys: ["Alt", "Enter"] },
  { id: "close-widget", description: "Close Active Widget", keys: ["Alt", "Q"], action: "widget:close", defaultKeys: ["Alt", "Q"] },
  { id: "open-theme", description: "Open Theme Builder", keys: ["Alt", "T"], action: "theme:open", defaultKeys: ["Alt", "T"] },
  { id: "open-keybindings", description: "Customize Keybindings", keys: ["Alt", "K"], action: "keybindings:open", defaultKeys: ["Alt", "K"] },
  { id: "upload-file", description: "Upload File", keys: ["Alt", "U"], action: "file:upload", defaultKeys: ["Alt", "U"] },
  { id: "reset-layout", description: "Reset Layout", keys: ["Alt", "Shift", "R"], action: "layout:reset", defaultKeys: ["Alt", "Shift", "R"] },
  { id: "open-command", description: "Open Command Palette", keys: ["Ctrl", "K"], action: "command:open", defaultKeys: ["Ctrl", "K"] },
  { id: "run-pipeline", description: "Run Pipeline", keys: ["Ctrl", "Enter"], action: "pipeline:run", defaultKeys: ["Ctrl", "Enter"] },
  
  { id: "workspace-1", description: "Switch to Workspace 1", keys: ["Alt", "1"], action: "workspace:1", defaultKeys: ["Alt", "1"] },
  { id: "workspace-2", description: "Switch to Workspace 2", keys: ["Alt", "2"], action: "workspace:2", defaultKeys: ["Alt", "2"] },
  { id: "workspace-3", description: "Switch to Workspace 3", keys: ["Alt", "3"], action: "workspace:3", defaultKeys: ["Alt", "3"] },
  { id: "workspace-4", description: "Switch to Workspace 4", keys: ["Alt", "4"], action: "workspace:4", defaultKeys: ["Alt", "4"] },
  { id: "workspace-5", description: "Switch to Workspace 5", keys: ["Alt", "5"], action: "workspace:5", defaultKeys: ["Alt", "5"] },

  { id: "toggle-upload", description: "Toggle File Upload", keys: ["Ctrl", "Shift", "1"], action: "widget:toggle-upload", defaultKeys: ["Ctrl", "Shift", "1"] },
  { id: "toggle-editor", description: "Toggle Pipeline Editor", keys: ["Ctrl", "Shift", "2"], action: "widget:toggle-editor", defaultKeys: ["Ctrl", "Shift", "2"] },
  { id: "toggle-monitor", description: "Toggle Run Monitor", keys: ["Ctrl", "Shift", "3"], action: "widget:toggle-monitor", defaultKeys: ["Ctrl", "Shift", "3"] },
  { id: "toggle-lineage", description: "Toggle Lineage Graph", keys: ["Ctrl", "Shift", "4"], action: "widget:toggle-lineage", defaultKeys: ["Ctrl", "Shift", "4"] },
  { id: "toggle-history", description: "Toggle Run History", keys: ["Ctrl", "Shift", "5"], action: "widget:toggle-history", defaultKeys: ["Ctrl", "Shift", "5"] },
  { id: "toggle-registry", description: "Toggle File Registry", keys: ["Ctrl", "Shift", "6"], action: "widget:toggle-registry", defaultKeys: ["Ctrl", "Shift", "6"] },
];

interface KeybindingState {
  keybindings: Keybinding[];
  updateKeybinding: (id: string, newKeys: string[]) => void;
  resetKeybindings: () => void;
}

export const useKeybindingStore = create<KeybindingState>()(
  persist(
    (set) => ({
      keybindings: DEFAULT_KEYBINDINGS,
      updateKeybinding: (id, newKeys) => set((state) => ({
        keybindings: state.keybindings.map(kb => 
          kb.id === id ? { ...kb, keys: newKeys } : kb
        )
      })),
      resetKeybindings: () => set({ keybindings: DEFAULT_KEYBINDINGS }),
    }),
    { 
      name: "pipelineiq-keybindings-v1",
      // Zustand persist merge requires any for persistedState
      merge: (persistedState: any, currentState) => {
        const persistedKeybindings = persistedState?.keybindings || [];
        const mergedKeybindings = currentState.keybindings.map(defaultKb => {
          const persistedKb = persistedKeybindings.find((k: any) => k.id === defaultKb.id);
          if (persistedKb) {
            return { ...defaultKb, keys: persistedKb.keys };
          }
          return defaultKb;
        });
        return { ...currentState, ...persistedState, keybindings: mergedKeybindings };
      }
    }
  )
);
