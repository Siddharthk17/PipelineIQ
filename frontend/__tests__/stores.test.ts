import { describe, it, expect, beforeEach } from "vitest";
import { usePipelineStore } from "@/store/pipelineStore";
import { useWidgetStore, getAllWidgets, ALL_WIDGETS } from "@/store/widgetStore";
import { useThemeStore } from "@/store/themeStore";
import { useKeybindingStore, DEFAULT_KEYBINDINGS } from "@/store/keybindingStore";

describe("pipelineStore", () => {
  beforeEach(() => {
    usePipelineStore.setState({
      activeRunId: null,
      activeRun: null,
      lastYamlConfig: "pipeline:\n  name: my_pipeline\n  steps:\n    - name: load_step\n      type: load\n      file_id: \"\"\n",
    });
  });

  it("initializes with default values", () => {
    const state = usePipelineStore.getState();
    expect(state.activeRunId).toBeNull();
    expect(state.activeRun).toBeNull();
    expect(state.lastYamlConfig).toContain("my_pipeline");
  });

  it("setActiveRunId updates the active run ID", () => {
    usePipelineStore.getState().setActiveRunId("run-123");
    expect(usePipelineStore.getState().activeRunId).toBe("run-123");
  });

  it("setActiveRun updates the active run", () => {
    const run = {
      id: "r1",
      name: "Test Run",
      status: "COMPLETED" as const,
      created_at: "2024-01-01",
      started_at: null,
      completed_at: null,
      total_rows_in: 100,
      total_rows_out: 50,
      error_message: null,
      duration_ms: 1200,
      step_results: [],
    };
    usePipelineStore.getState().setActiveRun(run);
    expect(usePipelineStore.getState().activeRun).toEqual(run);
  });

  it("setLastYamlConfig stores YAML", () => {
    usePipelineStore.getState().setLastYamlConfig("pipeline:\n  name: updated\n");
    expect(usePipelineStore.getState().lastYamlConfig).toContain("updated");
  });

  it("setActiveRun to null clears it", () => {
    usePipelineStore.getState().setActiveRun({ id: "r1", name: "x", status: "RUNNING", created_at: "", started_at: null, completed_at: null, total_rows_in: null, total_rows_out: null, error_message: null, duration_ms: null, step_results: [] });
    usePipelineStore.getState().setActiveRun(null);
    expect(usePipelineStore.getState().activeRun).toBeNull();
  });
});

describe("widgetStore", () => {
  beforeEach(() => {
    useWidgetStore.getState().resetLayout();
  });

  it("initializes with default widgets", () => {
    const state = useWidgetStore.getState();
    expect(state.widgets).toEqual(ALL_WIDGETS);
    expect(state.activeWorkspaceId).toBe(1);
  });

  it("getAllWidgets returns visible widgets from default layout", () => {
    const visible = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    expect(visible).toContain("quick-stats");
    expect(visible).toContain("pipeline-editor");
    expect(visible).toContain("run-monitor");
  });

  it("addWidget adds a widget to the layout", () => {
    const before = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    expect(before).not.toContain("file-upload");

    useWidgetStore.getState().addWidget("file-upload");
    const after = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    expect(after).toContain("file-upload");
  });

  it("addWidget sets active widget", () => {
    useWidgetStore.getState().addWidget("run-history");
    expect(useWidgetStore.getState().activeWidgetId).toBe("run-history");
  });

  it("addWidget does not duplicate existing widget, just activates", () => {
    const countBefore = getAllWidgets(useWidgetStore.getState().workspaces[1]).length;
    useWidgetStore.getState().addWidget("quick-stats");
    const countAfter = getAllWidgets(useWidgetStore.getState().workspaces[1]).length;
    expect(countAfter).toBe(countBefore);
    expect(useWidgetStore.getState().activeWidgetId).toBe("quick-stats");
  });

  it("removeWidget removes from layout", () => {
    useWidgetStore.getState().removeWidget("quick-stats");
    const after = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    expect(after).not.toContain("quick-stats");
  });

  it("removeWidget updates activeWidgetId if active was removed", () => {
    useWidgetStore.getState().setActiveWidget("quick-stats");
    useWidgetStore.getState().removeWidget("quick-stats");
    expect(useWidgetStore.getState().activeWidgetId).not.toBe("quick-stats");
  });

  it("resetLayout restores defaults", () => {
    useWidgetStore.getState().addWidget("file-upload");
    useWidgetStore.getState().addWidget("run-history");
    useWidgetStore.getState().switchWorkspace(3);
    useWidgetStore.getState().resetLayout();

    expect(useWidgetStore.getState().activeWorkspaceId).toBe(1);
    expect(useWidgetStore.getState().activeWidgetId).toBe("quick-stats");
    const visible = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    expect(visible).toHaveLength(3);
  });

  it("switchWorkspace changes active workspace", () => {
    useWidgetStore.getState().switchWorkspace(3);
    expect(useWidgetStore.getState().activeWorkspaceId).toBe(3);
  });

  it("switchWorkspace to same workspace is no-op", () => {
    useWidgetStore.getState().switchWorkspace(1);
    expect(useWidgetStore.getState().activeWorkspaceId).toBe(1);
  });

  it("moveToWorkspace moves widget between workspaces", () => {
    useWidgetStore.getState().moveToWorkspace("quick-stats", 2);
    const ws1 = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    const ws2 = getAllWidgets(useWidgetStore.getState().workspaces[2]);
    expect(ws1).not.toContain("quick-stats");
    expect(ws2).toContain("quick-stats");
  });

  it("swapWidgets exchanges two widgets", () => {
    const before = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    const idx1 = before.indexOf("quick-stats");
    const idx2 = before.indexOf("pipeline-editor");

    useWidgetStore.getState().swapWidgets("quick-stats", "pipeline-editor");
    const after = getAllWidgets(useWidgetStore.getState().workspaces[1]);
    expect(after[idx1]).toBe("pipeline-editor");
    expect(after[idx2]).toBe("quick-stats");
  });
});

describe("themeStore", () => {
  beforeEach(() => {
    useThemeStore.setState({ activeTheme: "pipelineiq-dark", customThemes: [] });
  });

  it("initializes with pipelineiq-dark", () => {
    expect(useThemeStore.getState().activeTheme).toBe("pipelineiq-dark");
  });

  it("setTheme changes active theme", () => {
    useThemeStore.getState().setTheme("monokai");
    expect(useThemeStore.getState().activeTheme).toBe("monokai");
  });

  it("addCustomTheme stores a new theme", () => {
    useThemeStore.getState().addCustomTheme({
      name: "my-theme",
      author: "tester",
      variables: { "--bg-base": "#000" },
    });
    expect(useThemeStore.getState().customThemes).toHaveLength(1);
    expect(useThemeStore.getState().customThemes[0].name).toBe("my-theme");
  });

  it("addCustomTheme replaces existing by name", () => {
    useThemeStore.getState().addCustomTheme({ name: "t1", author: "a", variables: { "--bg-base": "#111" } });
    useThemeStore.getState().addCustomTheme({ name: "t1", author: "a", variables: { "--bg-base": "#222" } });
    expect(useThemeStore.getState().customThemes).toHaveLength(1);
    expect(useThemeStore.getState().customThemes[0].variables["--bg-base"]).toBe("#222");
  });

  it("removeCustomTheme removes by name", () => {
    useThemeStore.getState().addCustomTheme({ name: "t1", author: "a", variables: {} });
    useThemeStore.getState().addCustomTheme({ name: "t2", author: "a", variables: {} });
    useThemeStore.getState().removeCustomTheme("t1");
    expect(useThemeStore.getState().customThemes).toHaveLength(1);
    expect(useThemeStore.getState().customThemes[0].name).toBe("t2");
  });
});

describe("keybindingStore", () => {
  beforeEach(() => {
    useKeybindingStore.getState().resetKeybindings();
  });

  it("initializes with default keybindings", () => {
    const kbs = useKeybindingStore.getState().keybindings;
    expect(kbs).toHaveLength(DEFAULT_KEYBINDINGS.length);
    expect(kbs.find((k) => k.id === "open-launcher")?.keys).toEqual(["Alt", "Enter"]);
  });

  it("updateKeybinding changes keys for a binding", () => {
    useKeybindingStore.getState().updateKeybinding("open-launcher", ["Ctrl", "Space"]);
    const updated = useKeybindingStore.getState().keybindings.find((k) => k.id === "open-launcher");
    expect(updated?.keys).toEqual(["Ctrl", "Space"]);
  });

  it("updateKeybinding does not affect other bindings", () => {
    useKeybindingStore.getState().updateKeybinding("open-launcher", ["Ctrl", "Space"]);
    const other = useKeybindingStore.getState().keybindings.find((k) => k.id === "close-widget");
    expect(other?.keys).toEqual(["Alt", "Q"]);
  });

  it("resetKeybindings restores defaults", () => {
    useKeybindingStore.getState().updateKeybinding("open-launcher", ["X"]);
    useKeybindingStore.getState().resetKeybindings();
    const kbs = useKeybindingStore.getState().keybindings;
    expect(kbs.find((k) => k.id === "open-launcher")?.keys).toEqual(["Alt", "Enter"]);
  });
});
