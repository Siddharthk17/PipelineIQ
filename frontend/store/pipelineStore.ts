import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { PipelineRun } from "@/lib/types";
import { DEFAULT_PIPELINE_YAML } from "@/lib/pipeline-yaml";

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
      lastYamlConfig: DEFAULT_PIPELINE_YAML,
      setActiveRunId: (id) => set({ activeRunId: id }),
      setActiveRun: (run) => set({ activeRun: run }),
      setLastYamlConfig: (yaml) => set({ lastYamlConfig: yaml }),
    }),
    { name: "pipelineiq-pipeline" }
  )
);
