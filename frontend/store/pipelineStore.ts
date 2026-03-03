import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { PipelineRun } from "@/lib/types";

interface PipelineState {
  activeRunId: string | null;
  activeRun: PipelineRun | null;
  lastYamlConfig: string;
  setActiveRunId: (id: string | null) => void;
  setActiveRun: (run: PipelineRun | null) => void;
  setLastYamlConfig: (yaml: string) => void;
}

export const usePipelineStore = create<PipelineState>()(
  persist(
    (set) => ({
      activeRunId: null,
      activeRun: null,
      lastYamlConfig: "pipeline:\n  name: my_pipeline\n  steps:\n    - name: load_step\n      type: load\n      file_id: \"\"\n",
      setActiveRunId: (id) => set({ activeRunId: id }),
      setActiveRun: (run) => set({ activeRun: run }),
      setLastYamlConfig: (yaml) => set({ lastYamlConfig: yaml }),
    }),
    { name: "pipelineiq-pipeline" }
  )
);
