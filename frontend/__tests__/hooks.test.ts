import { describe, it, expect, beforeEach } from "vitest";
import { useWidgetStore, getAllWidgets, ALL_WIDGETS } from "@/store/widgetStore";

describe("useWidgetLayout hook (thin wrapper around widgetStore)", () => {
  beforeEach(() => {
    useWidgetStore.getState().resetLayout();
  });

  it("addWidget + removeWidget toggles widget in layout", () => {
    useWidgetStore.getState().addWidget("file-upload");
    let widgets = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    expect(widgets).toContain("file-upload");

    useWidgetStore.getState().removeWidget("file-upload");
    widgets = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    expect(widgets).not.toContain("file-upload");
  });

  it("workspace switching preserves each workspace state", () => {
    useWidgetStore.getState().addWidget("file-upload");
    useWidgetStore.getState().switchWorkspace(2);
    useWidgetStore.getState().addWidget("run-history");

    const ws1 = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    const ws2 = getAllWidgets(useWidgetStore.getState().workspaces[2]);

    expect(ws1).toContain("file-upload");
    expect(ws1).not.toContain("run-history");
    expect(ws2).toContain("run-history");
    expect(ws2).not.toContain("file-upload");
  });

  it("ALL_WIDGETS contains all available widget configs", () => {
    expect(ALL_WIDGETS.length).toBeGreaterThan(5);
    expect(ALL_WIDGETS.find((w) => w.id === "quick-stats")).toBeTruthy();
    expect(ALL_WIDGETS.find((w) => w.id === "pipeline-editor")).toBeTruthy();
    expect(ALL_WIDGETS.find((w) => w.id === "file-upload")).toBeTruthy();
    expect(ALL_WIDGETS.find((w) => w.id === "run-monitor")).toBeTruthy();
  });
});
