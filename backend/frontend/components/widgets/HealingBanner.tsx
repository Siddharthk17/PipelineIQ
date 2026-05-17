"use client";

import React from "react";
import { AlertTriangle, CheckCircle2, Sparkles } from "lucide-react";
import type { HealingAttempt, PipelineRun } from "@/lib/types";

interface HealingBannerProps {
  status: PipelineRun["status"];
  attempts: HealingAttempt[];
}

function getLatestAttempt(attempts: HealingAttempt[]): HealingAttempt | null {
  if (!attempts.length) {
    return null;
  }
  return [...attempts].sort((left, right) => right.attempt_number - left.attempt_number)[0];
}

export function HealingBanner({ status, attempts }: HealingBannerProps) {
  const latestAttempt = getLatestAttempt(attempts);
  const confidence =
    latestAttempt?.confidence != null ? Math.round(latestAttempt.confidence * 100) : null;
  const patchDescription =
    typeof latestAttempt?.gemini_patch?.change_description === "string"
      ? latestAttempt.gemini_patch.change_description
      : latestAttempt?.classification_reason;
  const sandboxRows =
    typeof latestAttempt?.sandbox_result?.output_rows === "number"
      ? latestAttempt.sandbox_result.output_rows
      : null;

  if (status === "HEALING") {
    return (
      <div
        className="flex items-start gap-3 border-b px-4 py-3"
        style={{ borderColor: "var(--widget-border)", backgroundColor: "color-mix(in srgb, var(--accent-warning) 10%, transparent)" }}
        data-testid="healing-banner-active"
      >
        <Sparkles className="mt-0.5 h-4 w-4 text-[var(--accent-warning)]" />
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--text-primary)]">Auto-healing in progress</div>
          <div className="text-xs text-[var(--text-secondary)]">
            {latestAttempt?.failed_step
              ? `Repairing step "${latestAttempt.failed_step}" in an isolated DuckDB sandbox.`
              : "Repairing the failed pipeline in an isolated DuckDB sandbox."}
          </div>
        </div>
      </div>
    );
  }

  if (status === "HEALED") {
    return (
      <div
        className="flex items-start gap-3 border-b px-4 py-3"
        style={{ borderColor: "var(--widget-border)", backgroundColor: "color-mix(in srgb, var(--accent-success) 10%, transparent)" }}
        data-testid="healing-banner-healed"
      >
        <CheckCircle2 className="mt-0.5 h-4 w-4 text-[var(--accent-success)]" />
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--text-primary)]">Auto-healed successfully</div>
          {patchDescription && (
            <div className="text-xs text-[var(--text-secondary)]">{patchDescription}</div>
          )}
          <div className="text-xs text-[var(--text-secondary)]">
            {confidence != null ? `Confidence ${confidence}%` : "Patch validated"}
            {sandboxRows != null ? ` • Sandbox rows ${sandboxRows}` : ""}
          </div>
        </div>
      </div>
    );
  }

  if (status === "FAILED" && attempts.length > 0) {
    return (
      <div
        className="flex items-start gap-3 border-b px-4 py-3"
        style={{ borderColor: "var(--widget-border)", backgroundColor: "color-mix(in srgb, var(--accent-error) 10%, transparent)" }}
        data-testid="healing-banner-failed"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 text-[var(--accent-error)]" />
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--text-primary)]">
            Auto-healing failed after {attempts.length} attempt{attempts.length === 1 ? "" : "s"}
          </div>
          <div className="text-xs text-[var(--text-secondary)]">
            {latestAttempt?.ai_error || latestAttempt?.error_message || "The repair agent could not produce a safe patch."}
          </div>
        </div>
      </div>
    );
  }

  return null;
}
