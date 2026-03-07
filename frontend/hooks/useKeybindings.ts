import { useEffect } from "react";
import { useWidgetStore, getAllWidgets } from "@/store/widgetStore";
import { useKeybindingStore } from "@/store/keybindingStore";

export function useKeybindings(
  onLauncherOpen: () => void,
  onCommandPaletteOpen: () => void,
  onThemeBuilderOpen: () => void,
  onKeybindingsOpen: () => void
) {
  useEffect(() => {
    const toggleWidget = (id: string) => {
      const state = useWidgetStore.getState();
      const visibleWidgetIds = getAllWidgets(state.workspaces[state.activeWorkspaceId]);
      if (visibleWidgetIds.includes(id)) {
        state.removeWidget(id);
      } else {
        state.addWidget(id);
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      const isAlt = e.altKey;
      const isCtrl = e.ctrlKey || e.metaKey;
      const isShift = e.shiftKey;
      const key = e.key.toLowerCase();

      const { keybindings } = useKeybindingStore.getState();

      const pressedKeys = [];
      if (isCtrl) pressedKeys.push("Ctrl");
      if (isAlt) pressedKeys.push("Alt");
      if (isShift) pressedKeys.push("Shift");
      
      // Handle special keys
      if (key === "enter") pressedKeys.push("Enter");
      else if (key !== "control" && key !== "alt" && key !== "shift" && key !== "meta") {
        pressedKeys.push(key.toUpperCase());
      }

      const pressedKeyString = pressedKeys.join("+");

      // Avoid triggering when typing in inputs
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target as HTMLElement).isContentEditable
      ) {
        // Allow Ctrl+Enter for running pipeline even in textareas
        const runPipelineKb = keybindings.find(kb => kb.action === "pipeline:run");
        if (runPipelineKb && runPipelineKb.keys.join("+") === pressedKeyString) {
          window.dispatchEvent(new CustomEvent("pipeline:run"));
          e.preventDefault();
        }
        return;
      }

      // Check custom keybindings
      for (const kb of keybindings) {
        if (kb.keys.join("+") === pressedKeyString) {
          switch (kb.action) {
            case "launcher:open":
              onLauncherOpen();
              e.preventDefault();
              return;
            case "widget:close":
              const { activeWidgetId, removeWidget } = useWidgetStore.getState();
              if (activeWidgetId) {
                removeWidget(activeWidgetId);
              }
              e.preventDefault();
              return;
            case "theme:open":
              onThemeBuilderOpen();
              e.preventDefault();
              return;
            case "keybindings:open":
              onKeybindingsOpen();
              e.preventDefault();
              return;
            case "file:upload":
              window.dispatchEvent(new CustomEvent("pipeline:upload"));
              e.preventDefault();
              return;
            case "layout:reset":
              useWidgetStore.getState().resetLayout();
              e.preventDefault();
              return;
            case "command:open":
              onCommandPaletteOpen();
              e.preventDefault();
              return;
            case "pipeline:run":
              window.dispatchEvent(new CustomEvent("pipeline:run"));
              e.preventDefault();
              return;
            case "workspace:1":
              useWidgetStore.getState().switchWorkspace(1);
              e.preventDefault();
              return;
            case "workspace:2":
              useWidgetStore.getState().switchWorkspace(2);
              e.preventDefault();
              return;
            case "workspace:3":
              useWidgetStore.getState().switchWorkspace(3);
              e.preventDefault();
              return;
            case "workspace:4":
              useWidgetStore.getState().switchWorkspace(4);
              e.preventDefault();
              return;
            case "workspace:5":
              useWidgetStore.getState().switchWorkspace(5);
              e.preventDefault();
              return;
            case "widget:toggle-upload":
              toggleWidget("file-upload");
              e.preventDefault();
              return;
            case "widget:toggle-editor":
              toggleWidget("pipeline-editor");
              e.preventDefault();
              return;
            case "widget:toggle-monitor":
              toggleWidget("run-monitor");
              e.preventDefault();
              return;
            case "widget:toggle-lineage":
              toggleWidget("lineage-graph");
              e.preventDefault();
              return;
            case "widget:toggle-history":
              toggleWidget("run-history");
              e.preventDefault();
              return;
            case "widget:toggle-registry":
              toggleWidget("file-registry");
              e.preventDefault();
              return;
          }
        }
      }

      // Hardcoded workspace moving (Alt+Shift+1-5)
      if (isAlt && isShift && key >= "1" && key <= "5") {
        const { activeWidgetId, moveToWorkspace } = useWidgetStore.getState();
        if (activeWidgetId) {
          moveToWorkspace(activeWidgetId, parseInt(key));
        }
        e.preventDefault();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onLauncherOpen, onCommandPaletteOpen, onThemeBuilderOpen, onKeybindingsOpen]);
}
