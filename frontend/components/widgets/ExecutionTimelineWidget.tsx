"use client";

import React, { useEffect, useState, useRef } from "react";
import { usePipelineStore } from "@/store/pipelineStore";
import { usePipelineRun } from "@/hooks/usePipelineRun";
import { Clock, CheckCircle, XCircle, Loader2, BarChart3, GanttChart } from "lucide-react";
import { motion } from "motion/react";
import type { RunTimingResponse, TimelineStep } from "@/lib/types";
import { fetchApi } from "@/lib/api";

const STATUS_ICONS: Record<string, React.ReactNode> = {
  COMPLETED: <CheckCircle className="w-4 h-4 text-green-500" />,
  FAILED: <XCircle className="w-4 h-4 text-red-500" />,
  RUNNING: <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />,
  PENDING: <Clock className="w-4 h-4 text-gray-400" />,
};

const STEP_COLORS: Record<string, string> = {
  load: "bg-blue-500",
  filter: "bg-cyan-500",
  select: "bg-teal-500",
  rename: "bg-indigo-500",
  join: "bg-violet-500",
  aggregate: "bg-purple-500",
  sort: "bg-fuchsia-500",
  validate: "bg-amber-500",
  save: "bg-emerald-500",
  pivot: "bg-pink-500",
  unpivot: "bg-rose-500",
  deduplicate: "bg-orange-500",
  fill_nulls: "bg-lime-500",
  sample: "bg-yellow-500",
  sql: "bg-sky-500",
  wasm_compute: "bg-gray-500",
};

function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function StepBar({
  step,
  maxDuration,
  index,
}: {
  step: TimelineStep;
  maxDuration: number;
  index: number;
}) {
  const pct = maxDuration > 0 ? ((step.duration_ms ?? 0) / maxDuration) * 100 : 0;
  const barColor = STEP_COLORS[step.step_type] ?? "bg-gray-400";

  return (
    <div className="flex items-center gap-3 py-2 text-xs">
      <span className="w-5 text-right text-[var(--text-secondary)] font-mono shrink-0">
        {index + 1}
      </span>
      {STATUS_ICONS[step.status] ?? <Clock className="w-4 h-4 text-gray-400" />}
      <span className="w-28 truncate text-[var(--text-primary)] font-medium shrink-0">
        {step.step_name}
      </span>
      <span className="w-16 text-[var(--text-secondary)] shrink-0">
        {step.step_type}
      </span>
      <div className="flex-1 h-4 rounded-full overflow-hidden bg-[var(--bg-elevated)] min-w-[60px]">
        <motion.div
          className={`h-full rounded-full ${barColor}`}
          initial={{ width: 0 }}
          animate={{ width: `${Math.max(pct, 2)}%` }}
          transition={{ duration: 0.4, ease: "easeOut" }}
        />
      </div>
      <span className="w-16 text-right text-[var(--text-secondary)] font-mono shrink-0">
        {formatMs(step.duration_ms)}
      </span>
      <span className="w-24 text-right text-[var(--text-secondary)] font-mono text-[10px] truncate shrink-0">
        {step.trace_id ? step.trace_id.slice(0, 12) + "…" : ""}
      </span>
    </div>
  );
}

export function ExecutionTimelineWidget() {
  const { activeRunId, activeRun } = usePipelineStore();
  usePipelineRun(activeRunId);

  // SSE-driven re-fetch: usePipelineRun patches activeRun.step_results on every
  // step_completed / step_failed SSE event. Counting non-pending steps gives a
  // stable integer that increments with each push — making this effect re-fire
  // automatically without any polling timer.
  const stepsDone =
    activeRun?.step_results?.filter(
      (s) => s.status !== "PENDING" && s.status !== "RUNNING",
    ).length ?? 0;
  const runStatus = activeRun?.status ?? null;

  const [timing, setTiming] = useState<RunTimingResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prevRunId = useRef(activeRunId);

  useEffect(() => {
    if (activeRunId !== prevRunId.current) {
      prevRunId.current = activeRunId;
    }
    if (!activeRunId) return;
    let cancelled = false;

    Promise.resolve().then(() => {
      if (!cancelled) { setLoading(true); setError(null); }
    });

    fetchApi<RunTimingResponse>(`/pipelines/${activeRunId}/timing`)
      .then((data) => {
        if (!cancelled) { setTiming(data); setLoading(false); }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load timing data");
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [activeRunId, stepsDone, runStatus]);

  if (!activeRunId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center p-6 border-2 border-dashed rounded-lg m-2" style={{ borderColor: "var(--widget-border)" }}>
        <GanttChart className="w-12 h-12 text-[var(--text-secondary)] mb-3 opacity-50" />
        <h3 className="text-lg font-medium text-[var(--text-primary)] mb-1">No Run Selected</h3>
        <p className="text-sm text-[var(--text-secondary)] max-w-xs">
          Select a pipeline run to view its execution timeline.
        </p>
      </div>
    );
  }

  if (loading && !timing) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 text-[var(--text-secondary)] animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red-500 text-sm p-4">
        {error}
      </div>
    );
  }

  if (!timing || timing.steps.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-[var(--text-secondary)]">
        No timing data available yet.
      </div>
    );
  }

  const maxDuration = Math.max(...timing.steps.map((s) => s.duration_ms ?? 0), 1);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between p-3 border-b" style={{ borderColor: "var(--widget-border)" }}>
        <div className="flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-[var(--text-secondary)]" />
          <span className="text-sm font-medium text-[var(--text-primary)]">
            Execution Timeline
          </span>
        </div>
        <span className="text-xs text-[var(--text-secondary)] font-mono">
          {formatMs(timing.total_duration_ms)} total
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        <div className="flex items-center gap-3 px-6 pb-1 text-[10px] text-[var(--text-secondary)] font-medium uppercase tracking-wider">
          <span className="w-5 shrink-0" />
          <span className="w-4 shrink-0" />
          <span className="w-28 shrink-0">Step</span>
          <span className="w-16 shrink-0">Type</span>
          <span className="flex-1">Duration</span>
          <span className="w-16 shrink-0 text-right">Time</span>
          <span className="w-24 shrink-0 text-right">Trace</span>
        </div>
        {timing.steps.map((step, i) => (
          <StepBar
            key={step.step_index}
            step={step}
            maxDuration={maxDuration}
            index={i}
          />
        ))}
      </div>
    </div>
  );
}
