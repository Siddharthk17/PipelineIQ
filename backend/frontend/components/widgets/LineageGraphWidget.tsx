"use client";

import React, { useState } from "react";
import { usePipelineStore } from "@/store/pipelineStore";
import { useQuery } from "@tanstack/react-query";
import { getPipelineRuns } from "@/lib/api";
import { PIPELINE_RUNS_PAGE_LIMIT, PIPELINE_RUNS_QUERY_KEY } from "@/lib/constants";
import type { PipelineRun } from "@/lib/types";
import { LineageGraph } from "../lineage/LineageGraph";
import { GitMerge, Activity } from "lucide-react";

export function LineageGraphWidget() {
  const { activeRunId, activeRun, setActiveRunId } = usePipelineStore();
  const { data: runs } = useQuery({
    queryKey: PIPELINE_RUNS_QUERY_KEY,
    queryFn: () => getPipelineRuns(1, PIPELINE_RUNS_PAGE_LIMIT),
    staleTime: 15000,
  });
  const [mode, setMode] = useState<"ancestry" | "impact">("ancestry");
  const selectedRun =
    runs?.find((run) => run.id === activeRunId)
    ?? (activeRun?.id === activeRunId ? activeRun : null);
  const lineageReadyStatuses = new Set<PipelineRun["status"]>(["COMPLETED", "HEALED"]);
  const canLoadLineage = selectedRun ? lineageReadyStatuses.has(selectedRun.status) : false;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between p-2 border-b bg-[var(--bg-surface)]" style={{ borderColor: "var(--widget-border)" }}>
        <div className="flex items-center gap-3">
          <label htmlFor="lineage-run-select" className="sr-only">
            Select pipeline run
          </label>
          <select
            id="lineage-run-select"
            name="runId"
            value={activeRunId || ""}
            onChange={(e) => setActiveRunId(e.target.value)}
            className="px-3 py-1.5 rounded text-sm bg-[var(--bg-elevated)] border text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
            style={{ borderColor: "var(--widget-border)" }}
          >
            <option value="" disabled>Select a run...</option>
            {runs?.map(run => (
              <option key={run.id} value={run.id}>{run.name ?? "Unnamed"} ({run.id?.substring(0,8) ?? "..."})</option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-1 bg-[var(--bg-elevated)] p-1 rounded border" style={{ borderColor: "var(--widget-border)" }}>
          <button
            onClick={() => setMode("ancestry")}
            className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-colors ${
              mode === "ancestry" ? "bg-[var(--interactive-active)] text-[var(--accent-primary)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
          >
            <GitMerge className="w-3.5 h-3.5" />
            Ancestry
          </button>
          <button
            onClick={() => setMode("impact")}
            className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-colors ${
              mode === "impact" ? "bg-[var(--interactive-active)] text-[var(--accent-error)]" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
          >
            <Activity className="w-3.5 h-3.5" />
            Impact Analysis
          </button>
        </div>
      </div>

      <div className="flex-1 relative">
        <LineageGraph
          key={`${mode}:${activeRunId ?? "none"}`}
          runId={activeRunId}
          mode={mode}
          canLoad={canLoadLineage}
          runStatus={selectedRun?.status ?? null}
        />
      </div>
    </div>
  );
}
