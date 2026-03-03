"use client";

import React from "react";
import { usePipelineStore } from "@/store/pipelineStore";
import { usePipelineRun } from "@/hooks/usePipelineRun";
import { CheckCircle, XCircle, PlayCircle, Clock, AlertTriangle } from "lucide-react";
import { motion } from "motion/react";

export function RunMonitorWidget() {
  const { activeRunId, activeRun } = usePipelineStore();
  
  // This hook connects to the SSE stream and updates the activeRun in the store
  usePipelineRun(activeRunId);

  if (!activeRunId || !activeRun) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center p-6 border-2 border-dashed rounded-lg m-2" style={{ borderColor: "var(--widget-border)" }}>
        <PlayCircle className="w-12 h-12 text-[var(--text-secondary)] mb-3 opacity-50" />
        <h3 className="text-lg font-medium text-[var(--text-primary)] mb-1">No Active Pipeline</h3>
        <p className="text-sm text-[var(--text-secondary)] max-w-xs">
          Start a pipeline run from the Pipeline Editor to monitor its execution in real-time.
        </p>
      </div>
    );
  }

  const totalSteps = activeRun.step_results?.length || 0;
  const completedSteps = activeRun.step_results?.filter(s => s.status === "COMPLETED" || s.status === "FAILED").length || 0;
  
  const maxDuration = Math.max(...(activeRun.step_results?.map(s => s.duration_ms || 0) || [1]));

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between p-4 border-b bg-[var(--bg-surface)]" style={{ borderColor: "var(--widget-border)" }}>
        <div>
          <h3 className="text-sm font-bold text-[var(--text-primary)]">{activeRun.name ?? "Unnamed Run"}</h3>
          <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)] mt-1">
            <span className="font-mono">{activeRun.id?.substring(0, 8) ?? "..."}...</span>
            <span>•</span>
            <span>{completedSteps}/{totalSteps} steps</span>
            {activeRun.duration_ms && (
              <>
                <span>•</span>
                <span>{(activeRun.duration_ms / 1000).toFixed(1)}s total</span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full border" style={{ borderColor: "var(--widget-border)", backgroundColor: "var(--bg-elevated)" }}>
          {activeRun.status === "COMPLETED" && <CheckCircle className="w-4 h-4 text-[var(--accent-success)]" />}
          {activeRun.status === "FAILED" && <XCircle className="w-4 h-4 text-[var(--accent-error)]" />}
          {activeRun.status === "RUNNING" && <div className="w-3 h-3 rounded-full bg-[var(--accent-warning)] animate-pulse" />}
          {activeRun.status === "PENDING" && <Clock className="w-4 h-4 text-[var(--text-secondary)]" />}
          <span className="text-xs font-bold tracking-wider uppercase" style={{ 
            color: activeRun.status === "COMPLETED" ? "var(--accent-success)" : 
                   activeRun.status === "FAILED" ? "var(--accent-error)" : 
                   activeRun.status === "RUNNING" ? "var(--accent-warning)" : "var(--text-secondary)" 
          }}>
            {activeRun.status ?? "UNKNOWN"}
          </span>
        </div>
      </div>

      {activeRun.error_message && (
        <div className="p-3 bg-[var(--accent-error)]/10 border-b border-[var(--accent-error)]/20 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-[var(--accent-error)] flex-shrink-0 mt-0.5" />
          <p className="text-xs text-[var(--accent-error)] font-medium">{activeRun.error_message}</p>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {activeRun.step_results?.map((step, idx) => {
          const isRunning = step.status === "RUNNING";
          const isPending = step.status === "PENDING";
          const isFailed = step.status === "FAILED";
          const isSuccess = step.status === "COMPLETED";
          
          const durationPercent = step.duration_ms ? Math.max(5, (step.duration_ms / maxDuration) * 100) : 0;

          return (
            <motion.div 
              key={idx}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              className={`flex items-center p-2 rounded-lg transition-colors ${
                isRunning ? "bg-[var(--interactive-hover)] border border-[var(--accent-primary)]/30" : "hover:bg-[var(--bg-surface)]"
              } ${isPending ? "opacity-50" : "opacity-100"}`}
            >
              <div className="w-6 flex justify-center flex-shrink-0 mr-2">
                {isSuccess && <CheckCircle className="w-4 h-4 text-[var(--accent-success)]" />}
                {isFailed && <XCircle className="w-4 h-4 text-[var(--accent-error)]" />}
                {isRunning && <PlayCircle className="w-4 h-4 text-[var(--accent-primary)] animate-pulse" />}
                {isPending && <div className="w-2 h-2 rounded-full border-2 border-[var(--text-secondary)]" />}
              </div>

              <div className="flex-1 min-w-0 flex items-center justify-between gap-4">
                <div className="flex flex-col min-w-0 w-1/3">
                  <span className={`text-sm font-medium truncate ${isRunning ? "text-[var(--accent-primary)]" : "text-[var(--text-primary)]"}`}>
                    {step.step_name ?? "unknown"}
                  </span>
                  <span className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">
                    {step.step_type ?? "—"}
                  </span>
                </div>

                <div className="flex-1 flex items-center gap-2">
                  {!isPending && (
                    <>
                      <div className="flex-1 h-1.5 rounded-full bg-[var(--bg-elevated)] overflow-hidden">
                        <motion.div 
                          className={`h-full rounded-full ${isFailed ? "bg-[var(--accent-error)]" : isRunning ? "bg-[var(--accent-primary)]" : "bg-[var(--accent-success)]"}`}
                          initial={{ width: 0 }}
                          animate={{ width: `${durationPercent}%` }}
                          transition={{ duration: 0.5, ease: "easeOut" }}
                        />
                      </div>
                      <span className="text-xs font-mono text-[var(--text-secondary)] w-12 text-right">
                        {step.duration_ms ? `${step.duration_ms}ms` : isRunning ? "..." : "-"}
                      </span>
                    </>
                  )}
                </div>

                <div className="w-24 text-right flex flex-col items-end justify-center">
                  {!isPending && (
                    <span className="text-xs font-mono text-[var(--text-secondary)]">
                      {step.rows_in ?? "-"} → {step.rows_out ?? "-"}
                    </span>
                  )}
                </div>
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}
