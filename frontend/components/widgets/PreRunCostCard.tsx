"use client";

import { useState, useCallback } from "react";
import { Clock, Cpu, Lightbulb, TrendingUp, BarChart3, ChevronDown, ChevronUp } from "lucide-react";
import { getCostEstimate } from "@/lib/api";
import type { CostEstimate } from "@/lib/types";
import { ApiError } from "@/lib/api";

interface PreRunCostCardProps {
  pipelineYaml: string;
  fileIds: string[];
  onDismiss: () => void;
}

function formatMs(ms: number): string {
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return `${minutes}m ${remainingSeconds}s`;
}

function formatMemory(mb: number): string {
  if (mb < 1) return "< 1 MB";
  if (mb < 1024) return `${mb.toFixed(0)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

function confidenceColor(confidence: number): string {
  if (confidence >= 80) return "text-green-400";
  if (confidence >= 50) return "text-yellow-400";
  return "text-red-400";
}

export function PreRunCostCard({ pipelineYaml, fileIds, onDismiss }: PreRunCostCardProps) {
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const loadEstimate = useCallback(async () => {
    if (estimate || loading) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getCostEstimate(pipelineYaml, fileIds);
      setEstimate(result);
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.message : "Failed to load cost estimate";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [pipelineYaml, fileIds, estimate, loading]);

  return (
    <div
      className="mb-4 rounded-lg border border-[var(--accent-primary)]/30 bg-[var(--bg-surface)] p-4"
      data-testid="pre-run-cost-card"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-[var(--accent-primary)]" />
          <h3 className="text-sm font-medium">Pre-Run Estimate</h3>
        </div>
        <button
          onClick={onDismiss}
          className="rounded p-1 text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--interactive-hover)] transition-colors"
        >
          &times;
        </button>
      </div>

      {!estimate && !loading && !error && (
        <button
          onClick={loadEstimate}
          className="w-full rounded border border-[var(--widget-border)] bg-[var(--bg-elevated)] px-3 py-2 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--interactive-hover)] transition-colors"
          data-testid="load-estimate-btn"
        >
          Click to estimate cost before running
        </button>
      )}

      {loading && (
        <div className="flex items-center justify-center gap-2 py-4">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)]" />
          <span className="text-xs text-[var(--text-secondary)]">Analyzing pipeline...</span>
        </div>
      )}

      {error && (
        <div className="rounded border border-[var(--accent-error)]/30 bg-[var(--accent-error)]/5 px-3 py-2 text-xs text-[var(--accent-error)]">
          {error}
        </div>
      )}

      {estimate && (
        <div>
          <div className="grid grid-cols-2 gap-3 mb-3 sm:grid-cols-4">
            <div className="rounded border border-[var(--widget-border)] bg-[var(--bg-elevated)] p-3">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-secondary)] mb-1">
                <Clock className="h-3 w-3" />
                Duration
              </div>
              <p className="text-sm font-semibold text-[var(--text-primary)]">
                {estimate.duration_human}
              </p>
            </div>

            <div className="rounded border border-[var(--widget-border)] bg-[var(--bg-elevated)] p-3">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-secondary)] mb-1">
                <Cpu className="h-3 w-3" />
                Peak Memory
              </div>
              <p className="text-sm font-semibold text-[var(--text-primary)]">
                {formatMemory(estimate.peak_memory_mb)}
              </p>
            </div>

            <div className="rounded border border-[var(--widget-border)] bg-[var(--bg-elevated)] p-3">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-secondary)] mb-1">
                <TrendingUp className="h-3 w-3" />
                Confidence
              </div>
              <p className={`text-sm font-semibold ${confidenceColor(estimate.confidence)}`}>
                {estimate.confidence.toFixed(0)}%
              </p>
            </div>

            <div className="rounded border border-[var(--widget-border)] bg-[var(--bg-elevated)] p-3">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-secondary)] mb-1">
                Data Points
              </div>
              <p className="text-sm font-semibold text-[var(--text-primary)]">
                {estimate.data_points_used}
              </p>
            </div>
          </div>

          {estimate.optimization_tip && (
            <div className="flex items-start gap-2 rounded border border-blue-500/20 bg-blue-500/5 px-3 py-2 mb-3">
              <Lightbulb className="mt-0.5 h-4 w-4 flex-shrink-0 text-blue-400" />
              <p className="text-xs text-blue-300">{estimate.optimization_tip}</p>
            </div>
          )}

          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
            data-testid="toggle-step-breakdown"
          >
            {expanded ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
            Per-step breakdown
          </button>

          {expanded && (
            <div className="mt-2 overflow-x-auto rounded border border-[var(--widget-border)]">
              <table className="w-full text-left text-xs" data-testid="step-breakdown-table">
                <thead>
                  <tr className="border-b border-[var(--widget-border)] bg-[var(--bg-elevated)]">
                    <th className="px-3 py-2 font-medium text-[var(--text-secondary)]">Step</th>
                    <th className="px-3 py-2 font-medium text-[var(--text-secondary)]">Type</th>
                    <th className="px-3 py-2 font-medium text-[var(--text-secondary)]">Engine</th>
                    <th className="px-3 py-2 font-medium text-[var(--text-secondary)] text-right">Duration</th>
                    <th className="px-3 py-2 font-medium text-[var(--text-secondary)] text-right">
                      Rows In
                    </th>
                    <th className="px-3 py-2 font-medium text-[var(--text-secondary)] text-right">
                      Rows Out
                    </th>
                    <th className="px-3 py-2 font-medium text-[var(--text-secondary)] text-right">
                      Confidence
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {estimate.steps.map((step, i) => (
                    <tr
                      key={i}
                      className="border-b border-[var(--widget-border)] last:border-0"
                      data-testid={`step-row-${step.step_name}`}
                    >
                      <td className="px-3 py-2 font-mono text-[var(--text-primary)]">
                        {step.step_name}
                      </td>
                      <td className="px-3 py-2 text-[var(--text-secondary)]">{step.step_type}</td>
                      <td className="px-3 py-2">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                            step.engine === "duckdb"
                              ? "bg-emerald-500/10 text-emerald-400"
                              : step.engine === "wasm"
                                ? "bg-purple-500/10 text-purple-400"
                                : step.engine === "io"
                                  ? "bg-blue-500/10 text-blue-400"
                                  : "bg-slate-500/10 text-slate-400"
                          }`}
                        >
                          {step.engine}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-[var(--text-primary)]">
                        {formatMs(step.predicted_ms)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-[var(--text-secondary)]">
                        {step.row_in_est.toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-[var(--text-secondary)]">
                        {step.row_out_est.toLocaleString()}
                      </td>
                      <td className={`px-3 py-2 text-right ${confidenceColor(step.confidence)}`}>
                        {step.confidence.toFixed(0)}%
                      </td>
                    </tr>
                  ))}
                  <tr className="bg-[var(--bg-elevated)]">
                    <td className="px-3 py-2 font-medium text-[var(--text-primary)]" colSpan={3}>
                      Total
                    </td>
                    <td className="px-3 py-2 text-right font-mono font-semibold text-[var(--text-primary)]">
                      {estimate.duration_human}
                    </td>
                    <td className="px-3 py-2" colSpan={3} />
                  </tr>
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
