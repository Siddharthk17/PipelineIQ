"use client";

import type { StepResult } from "@/lib/types";
import { CheckCircle, XCircle, PlayCircle, Clock, AlertTriangle, ExternalLink } from "lucide-react";

interface GanttChartProps {
  steps: StepResult[];
  totalDurationMs: number | null;
  violations?: Map<string, number>;
  jaegerUiUrl?: string;
}

const STATUS_COLORS: Record<string, string> = {
  COMPLETED: "var(--accent-success)",
  FAILED: "var(--accent-error)",
  RUNNING: "var(--accent-primary)",
  PENDING: "var(--text-secondary)",
  SKIPPED: "var(--text-muted)",
  CONTRACT_VIOLATION: "var(--accent-warning)",
};

const ENGINE_COLORS: Record<string, string> = {
  duckdb: "#10B981",
  pandas: "#3B82F6",
  wasm: "#F97316",
  io: "#F59E0B",
};

const ENGINE_LABELS: Record<string, string> = {
  duckdb: "DuckDB",
  pandas: "Pandas",
  wasm: "Wasm",
  io: "IO",
};

const STEP_ICONS: Record<string, typeof CheckCircle> = {
  COMPLETED: CheckCircle,
  FAILED: XCircle,
  RUNNING: PlayCircle,
  PENDING: Clock,
  SKIPPED: AlertTriangle,
  CONTRACT_VIOLATION: AlertTriangle,
};

function parseTimestamp(ts: string | null | undefined): number | null {
  if (!ts) return null;
  const d = new Date(ts);
  return isNaN(d.getTime()) ? null : d.getTime();
}

export function GanttChart({ steps, totalDurationMs, violations, jaegerUiUrl }: GanttChartProps) {
  if (steps.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-[var(--text-secondary)]">
        No step data available
      </div>
    );
  }

  // Compute timeline bounds from started_at/completed_at if available
  let timelineStart: number | null = null;
  let timelineEnd: number | null = null;
  const hasTimestamps = steps.some(
    (s) => s.started_at || s.completed_at
  );

  if (hasTimestamps) {
    for (const s of steps) {
      const start = parseTimestamp(s.started_at);
      const end = parseTimestamp(s.completed_at);
      if (start !== null) {
        timelineStart = timelineStart === null ? start : Math.min(timelineStart, start);
      }
      if (end !== null) {
        timelineEnd = timelineEnd === null ? end : Math.max(timelineEnd, end);
      }
    }
  }

  // Fallback: use duration-based timeline
  if (timelineStart === null || timelineEnd === null) {
    const duration = totalDurationMs ?? steps.reduce((max, s) => Math.max(max, s.duration_ms ?? 0), 0);
    timelineStart = 0;
    timelineEnd = duration;
  }

  const totalSpan = (timelineEnd ?? 0) - (timelineStart ?? 0);
  const maxDuration = Math.max(totalSpan, 1);

  function getBarOffset(step: StepResult): number {
    if (hasTimestamps && step.started_at) {
      const start = parseTimestamp(step.started_at);
      if (start !== null && timelineStart !== null) {
        return ((start - timelineStart) / maxDuration) * 100;
      }
    }
    return 0;
  }

  function getBarWidth(step: StepResult): number {
    if (hasTimestamps && step.started_at && step.completed_at) {
      const start = parseTimestamp(step.started_at);
      const end = parseTimestamp(step.completed_at);
      if (start !== null && end !== null) {
        return Math.max(1, ((end - start) / maxDuration) * 100);
      }
    }
    if (step.duration_ms) {
      return Math.max(1, (step.duration_ms / maxDuration) * 100);
    }
    return 0;
  }

  function formatTime(ms: number): string {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 border-b text-xs text-[var(--text-secondary)]" style={{ borderColor: "var(--widget-border)" }}>
        <span className="font-medium">Step Timeline</span>
        {totalDurationMs && (
          <span className="font-mono">{formatTime(totalDurationMs)} total</span>
        )}
      </div>

      {/* Engine legend */}
      <div className="flex items-center gap-3 px-3 py-1 border-b text-[10px]" style={{ borderColor: "var(--widget-border)" }}>
        {Object.entries(ENGINE_COLORS).map(([engine, color]) => (
          <div key={engine} className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 rounded-sm" style={{ backgroundColor: color }} />
            <span className="text-[var(--text-muted)]">{ENGINE_LABELS[engine]}</span>
          </div>
        ))}
      </div>

      {/* Time axis */}
      <div className="relative h-4 px-2 flex items-end border-b" style={{ borderColor: "var(--widget-border)" }}>
        {[0, 0.25, 0.5, 0.75, 1].map((frac) => {
          const ms = (timelineStart ?? 0) + maxDuration * frac;
          return (
            <div
              key={frac}
              className="absolute text-[9px] font-mono text-[var(--text-muted)]"
              style={{ left: `${frac * 100}%`, transform: "translateX(-50%)" }}
            >
              {formatTime(ms)}
            </div>
          );
        })}
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {steps.map((step, idx) => {
          const Icon = STEP_ICONS[step.status] ?? Clock;
          const statusColor = STATUS_COLORS[step.status] ?? "var(--text-secondary)";
          const engineColor = step.engine ? ENGINE_COLORS[step.engine] : undefined;
          const barColor = engineColor ?? statusColor;
          const offset = getBarOffset(step);
          const width = getBarWidth(step);
          const stepViolations = violations?.get(step.step_name);
          const hasViolations = (stepViolations ?? 0) > 0;

          return (
            <div
              key={idx}
              className="group relative flex items-center gap-2 px-2 py-1 rounded hover:bg-[var(--interactive-hover)] transition-colors"
              title={
                step.trace_id
                  ? `${step.step_name} | trace: ${step.trace_id} | engine: ${step.engine ?? "unknown"}`
                  : step.step_name
              }
            >
              <Icon className="w-3 h-3 flex-shrink-0" style={{ color: statusColor }} />
              <span className="text-xs text-[var(--text-primary)] w-28 truncate flex-shrink-0" title={step.step_name}>
                {step.step_name}
              </span>
              {step.engine && (
                <span
                  className="text-[9px] font-mono w-12 flex-shrink-0 hidden sm:block"
                  style={{ color: engineColor ?? "var(--text-muted)" }}
                >
                  {ENGINE_LABELS[step.engine] ?? step.engine}
                </span>
              )}
              {step.trace_id && jaegerUiUrl && (
                <a
                  href={`${jaegerUiUrl}/trace/${step.trace_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex-shrink-0 hidden sm:block opacity-40 hover:opacity-100 transition-opacity"
                  title="View trace in Jaeger"
                  data-testid={`jaeger-link-${step.step_name}`}
                >
                  <ExternalLink className="w-3 h-3" style={{ color: "var(--accent-primary)" }} />
                </a>
              )}
              {hasViolations && (
                <span className="flex items-center gap-0.5 px-1 text-[10px] rounded bg-[var(--accent-error)]/10 text-[var(--accent-error)] flex-shrink-0">
                  <AlertTriangle className="w-2.5 h-2.5" />
                  {stepViolations}
                </span>
              )}
              <div className="flex-1 relative h-3 bg-[var(--bg-elevated)] rounded overflow-hidden min-w-[40px]">
                {width > 0 && (
                  <div
                    className="absolute h-full rounded transition-all duration-300"
                    style={{
                      left: `${offset}%`,
                      width: `${width}%`,
                      backgroundColor: barColor,
                    }}
                  />
                )}
              </div>
              <span className="text-[10px] font-mono text-[var(--text-secondary)] w-12 text-right flex-shrink-0">
                {step.duration_ms ? formatTime(step.duration_ms) : step.status === "PENDING" ? "-" : "..."}
              </span>
              <span className="text-[10px] font-mono text-[var(--text-muted)] w-16 text-right flex-shrink-0 hidden sm:block">
                {step.rows_in ?? "-"}→{step.rows_out ?? "-"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
