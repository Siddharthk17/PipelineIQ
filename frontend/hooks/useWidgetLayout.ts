"use client";

import { useWidgetStore, getAllWidgets } from "@/store/widgetStore";

export function useWidgetLayout() {
  const { widgets, workspaces, activeWorkspaceId, addWidget, removeWidget, resetLayout } = useWidgetStore();
  const visibleWidgetIds = getAllWidgets(workspaces[activeWorkspaceId]);

  const toggleWidget = (id: string) => {
    if (visibleWidgetIds.includes(id)) {
      removeWidget(id);
    } else {
      addWidget(id);
    }
  };

  return {
    widgets,
    visibleWidgetIds,
    toggleWidget,
    resetLayout,
  };
}
