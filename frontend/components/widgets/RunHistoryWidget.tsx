"use client";

import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { CheckCircle, XCircle, Clock, RefreshCw, Sparkles } from "lucide-react";
import { getPipelineRuns, repairPipelineRunWithAI } from "@/lib/api";
import { usePipelineStore } from "@/store/pipelineStore";
import { AIRepairDiffModal } from "@/components/widgets/AIPipelineModals";
import type { AIRepairPipelineResponse } from "@/lib/types";

export function RunHistoryWidget() {
  const queryClient = useQueryClient();
  const { data: runs, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ["pipelineRuns"],
    queryFn: () => getPipelineRuns(1, 50),
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
    staleTime: 0,
  });
  const { setActiveRunId, setLastYamlConfig } = usePipelineStore();
  const [repairResult, setRepairResult] = React.useState<AIRepairPipelineResponse | null>(null);
  const [repairOpen, setRepairOpen] = React.useState(false);

  const repairMutation = useMutation({
    mutationFn: repairPipelineRunWithAI,
    onSuccess: (result) => {
      setRepairResult(result);
      setRepairOpen(true);
    },
  });

  // Force refetch when activeRun status changes to a terminal state
  const activeRunStatus = usePipelineStore(s => s.activeRun?.status);
  React.useEffect(() => {
    if (activeRunStatus === "COMPLETED" || activeRunStatus === "FAILED") {
      queryClient.invalidateQueries({ queryKey: ["pipelineRuns"] });
    }
  }, [activeRunStatus, queryClient]);

  if (isLoading) return <div className="p-4 text-[var(--text-secondary)]">Loading history...</div>;

  if (!runs || runs.length === 0) {
    return <div className="p-4 text-[var(--text-secondary)] text-center">No pipeline runs yet.</div>;
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b" style={{ borderColor: "var(--widget-border)" }}>
        <span className="text-xs text-[var(--text-secondary)]">{runs.length} runs</span>
        <button
          onClick={() => queryClient.invalidateQueries({ queryKey: ["pipelineRuns"] })}
          className="p-1 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]"
          title="Refresh"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto pr-2">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="text-xs text-[var(--text-secondary)] uppercase tracking-wider border-b" style={{ borderColor: "var(--widget-border)" }}>
              <th className="pt-2 pb-3 font-medium pl-3">Status</th>
              <th className="pt-2 pb-3 font-medium">Pipeline</th>
              <th className="pt-2 pb-3 font-medium">Duration</th>
              <th className="pt-2 pb-3 font-medium">Time</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr 
                key={run.id} 
                onClick={() => setActiveRunId(run.id)}
                className="group cursor-pointer border-b last:border-0 hover:bg-[var(--interactive-hover)] transition-colors" 
                style={{ borderColor: "var(--widget-border)" }}
                data-status={run.status.toLowerCase()}
              >
                <td className="py-3 pl-3">
                  <div className="flex items-center gap-2">
                    {run.status === "COMPLETED" && <CheckCircle className="w-4 h-4 text-[var(--accent-success)]" />}
                    {run.status === "FAILED" && <XCircle className="w-4 h-4 text-[var(--accent-error)]" />}
                    {run.status === "RUNNING" && <div className="w-4 h-4 rounded-full bg-[var(--accent-warning)] animate-pulse" />}
                    {run.status === "PENDING" && <Clock className="w-4 h-4 text-[var(--text-secondary)]" />}
                  </div>
                </td>
                <td className="py-3">
                  <span className="text-sm font-medium text-[var(--text-primary)] group-hover:text-[var(--accent-primary)] transition-colors">{run.name}</span>
                  <div className="text-xs text-[var(--text-secondary)]">{run.step_results?.length || 0} steps</div>
                </td>
                <td className="py-3 text-sm text-[var(--text-secondary)] font-mono">
                  {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : "-"}
                </td>
                <td className="py-3 text-xs text-[var(--text-secondary)]">
                  <div className="flex items-center gap-2">
                    <span>{formatDistanceToNow(new Date(run.created_at))} ago</span>
                    {run.status === "FAILED" && (
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          repairMutation.mutate(run.id);
                        }}
                        disabled={repairMutation.isPending}
                        className="rounded border px-2 py-0.5 text-[10px] text-[var(--accent-primary)] hover:bg-[var(--interactive-hover)] disabled:cursor-not-allowed disabled:opacity-50"
                        style={{ borderColor: "var(--widget-border)" }}
                        data-testid="repair-pipeline-btn"
                        data-run-id={run.id}
                      >
                        <span className="inline-flex items-center gap-1">
                          <Sparkles className="h-3 w-3" />
                          Repair
                        </span>
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <AIRepairDiffModal
        isOpen={repairOpen}
        correctedYaml={repairResult?.corrected_yaml ?? ""}
        diffLines={repairResult?.diff_lines ?? []}
        valid={repairResult?.valid ?? false}
        error={repairResult?.error ?? null}
        isApplying={false}
        onClose={() => setRepairOpen(false)}
        onApply={(yaml) => {
          setLastYamlConfig(yaml);
          setRepairOpen(false);
        }}
      />
    </div>
  );
}
