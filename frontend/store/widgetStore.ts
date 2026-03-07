import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { WidgetConfig } from "@/lib/types";

export type LayoutNode = 
  | { type: 'widget', id: string }
  | { type: 'split', direction: 'row' | 'col', first: LayoutNode, second: LayoutNode };

export const ALL_WIDGETS: WidgetConfig[] = [
  { id: "quick-stats", title: "Quick Stats", icon: "activity", visible: true, gridColumn: "", gridRow: "", minWidth: 1, minHeight: 1, locked: false },
  { id: "run-monitor", title: "Run Monitor", icon: "play-circle", visible: true, gridColumn: "", gridRow: "", minWidth: 1, minHeight: 1, locked: false },
  { id: "file-registry", title: "File Registry", icon: "database", visible: true, gridColumn: "", gridRow: "", minWidth: 1, minHeight: 1, locked: false },
  { id: "pipeline-editor", title: "Pipeline Editor", icon: "code", visible: true, gridColumn: "", gridRow: "", minWidth: 1, minHeight: 1, locked: false },
  { id: "lineage-graph", title: "Lineage Graph", icon: "git-merge", visible: true, gridColumn: "", gridRow: "", minWidth: 1, minHeight: 1, locked: false },
  { id: "file-upload", title: "File Upload", icon: "upload-cloud", visible: true, gridColumn: "", gridRow: "", minWidth: 1, minHeight: 1, locked: false },
  { id: "run-history", title: "Run History", icon: "history", visible: true, gridColumn: "", gridRow: "", minWidth: 1, minHeight: 1, locked: false },
  { id: "version-history", title: "Pipeline Versions", icon: "history", visible: true, gridColumn: "", gridRow: "", minWidth: 1, minHeight: 1, locked: false },
];

const DEFAULT_LAYOUT: LayoutNode = {
  type: 'split',
  direction: 'row',
  first: { type: 'widget', id: 'quick-stats' },
  second: {
    type: 'split',
    direction: 'col',
    first: { type: 'widget', id: 'pipeline-editor' },
    second: { type: 'widget', id: 'run-monitor' }
  }
};

export function getAllWidgets(node: LayoutNode | null): string[] {
  if (!node) return [];
  if (node.type === 'widget') return [node.id];
  return [...getAllWidgets(node.first), ...getAllWidgets(node.second)];
}

function containsWidget(node: LayoutNode | null, id: string | null): boolean {
  if (!node || !id) return false;
  if (node.type === 'widget') return node.id === id;
  return containsWidget(node.first, id) || containsWidget(node.second, id);
}

function addWidgetToTree(root: LayoutNode | null, newId: string, activeId: string | null, parentDir: 'row' | 'col' = 'col'): LayoutNode {
  if (!root) return { type: 'widget', id: newId };
  
  if (root.type === 'widget') {
    if (root.id === activeId || !activeId) {
      return {
        type: 'split',
        direction: parentDir === 'row' ? 'col' : 'row',
        first: root,
        second: { type: 'widget', id: newId }
      };
    }
    return root;
  }

  const inFirst = containsWidget(root.first, activeId);
  const inSecond = containsWidget(root.second, activeId);

  if (inFirst) {
    return { ...root, first: addWidgetToTree(root.first, newId, activeId, root.direction) };
  } else if (inSecond) {
    return { ...root, second: addWidgetToTree(root.second, newId, activeId, root.direction) };
  } else {
    // If activeId is not found, default to splitting the first node
    return { ...root, first: addWidgetToTree(root.first, newId, activeId, root.direction) };
  }
}

function removeWidgetFromTree(node: LayoutNode | null, id: string): LayoutNode | null {
  if (!node) return null;
  if (node.type === 'widget') {
    return node.id === id ? null : node;
  }

  const newFirst = removeWidgetFromTree(node.first, id);
  const newSecond = removeWidgetFromTree(node.second, id);

  if (!newFirst && !newSecond) return null;
  if (!newFirst) return newSecond;
  if (!newSecond) return newFirst;

  return { ...node, first: newFirst, second: newSecond };
}

function swapWidgetsInTree(node: LayoutNode | null, id1: string, id2: string): LayoutNode | null {
  if (!node) return null;
  if (node.type === 'widget') {
    if (node.id === id1) return { type: 'widget', id: id2 };
    if (node.id === id2) return { type: 'widget', id: id1 };
    return node;
  }
  return {
    ...node,
    first: swapWidgetsInTree(node.first, id1, id2) as LayoutNode,
    second: swapWidgetsInTree(node.second, id1, id2) as LayoutNode
  };
}

interface WidgetState {
  widgets: WidgetConfig[];
  workspaces: Record<number, LayoutNode | null>;
  activeWorkspaceId: number;
  activeWidgetId: string | null;
  addWidget: (id: string) => void;
  removeWidget: (id: string) => void;
  setActiveWidget: (id: string | null) => void;
  resetLayout: () => void;
  switchWorkspace: (id: number) => void;
  moveToWorkspace: (widgetId: string, targetWorkspaceId: number) => void;
  swapWidgets: (id1: string, id2: string) => void;
}

export const useWidgetStore = create<WidgetState>()(
  persist(
    (set) => ({
      widgets: ALL_WIDGETS,
      workspaces: {
        1: DEFAULT_LAYOUT,
        2: null,
        3: null,
        4: null,
        5: null,
      },
      activeWorkspaceId: 1,
      activeWidgetId: 'quick-stats',
      
      addWidget: (id) => set((state) => {
        let newWorkspaces = { ...state.workspaces };
        
        const targetLayout = newWorkspaces[state.activeWorkspaceId];
        
        if (getAllWidgets(targetLayout).includes(id)) {
          return { activeWidgetId: id };
        }
        
        newWorkspaces[state.activeWorkspaceId] = addWidgetToTree(targetLayout, id, state.activeWidgetId);
        
        return {
          workspaces: newWorkspaces,
          activeWidgetId: id,
        };
      }),
      
      removeWidget: (id) => set((state) => {
        let newWorkspaces = { ...state.workspaces };
        let newActive = state.activeWidgetId;
        
        const currentLayout = newWorkspaces[state.activeWorkspaceId];
        if (getAllWidgets(currentLayout).includes(id)) {
          const newLayout = removeWidgetFromTree(currentLayout, id);
          newWorkspaces[state.activeWorkspaceId] = newLayout;
          
          if (state.activeWidgetId === id) {
            const remaining = getAllWidgets(newLayout);
            newActive = remaining.length > 0 ? remaining[remaining.length - 1] : null;
          }
        }
        
        return {
          workspaces: newWorkspaces,
          activeWidgetId: newActive,
        };
      }),
      
      setActiveWidget: (id) => set({ activeWidgetId: id }),
      
      resetLayout: () => set({ 
        workspaces: { 1: DEFAULT_LAYOUT, 2: null, 3: null, 4: null, 5: null },
        activeWorkspaceId: 1,
        activeWidgetId: 'quick-stats' 
      }),

      switchWorkspace: (id) => set((state) => {
        if (id === state.activeWorkspaceId) return state;
        const layout = state.workspaces[id];
        const remaining = getAllWidgets(layout);
        return {
          activeWorkspaceId: id,
          activeWidgetId: remaining.length > 0 ? remaining[remaining.length - 1] : null,
        };
      }),

      moveToWorkspace: (widgetId, targetWorkspaceId) => set((state) => {
        if (state.activeWorkspaceId === targetWorkspaceId) return state;

        let newWorkspaces = { ...state.workspaces };

        const currentLayout = newWorkspaces[state.activeWorkspaceId];
        const newLayout = removeWidgetFromTree(currentLayout, widgetId);
        newWorkspaces[state.activeWorkspaceId] = newLayout;

        const targetLayout = newWorkspaces[targetWorkspaceId];
        if (!getAllWidgets(targetLayout).includes(widgetId)) {
          newWorkspaces[targetWorkspaceId] = addWidgetToTree(targetLayout, widgetId, null);
        }

        const remaining = getAllWidgets(newWorkspaces[state.activeWorkspaceId]);
        const newActive = remaining.length > 0 ? remaining[remaining.length - 1] : null;

        return {
          workspaces: newWorkspaces,
          activeWidgetId: newActive,
        };
      }),

      swapWidgets: (id1, id2) => set((state) => {
        let newWorkspaces = { ...state.workspaces };
        const currentLayout = newWorkspaces[state.activeWorkspaceId];
        newWorkspaces[state.activeWorkspaceId] = swapWidgetsInTree(currentLayout, id1, id2);
        return { workspaces: newWorkspaces };
      }),
    }),
    { 
      name: "pipelineiq-layout-v4",
      partialize: (state) => ({
        workspaces: state.workspaces,
        activeWorkspaceId: state.activeWorkspaceId,
        activeWidgetId: state.activeWidgetId,
      }),
    }
  )
);
