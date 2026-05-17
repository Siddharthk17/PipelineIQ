"use client";

import React from "react";
import type { StepPlan } from "@/lib/types";

export function StepDAG({ steps }: { steps: StepPlan[] }) {
  if (!steps || steps.length === 0) return null;

  return (
    <div className="flex items-center gap-0 overflow-x-auto py-2 px-1">
      {steps.map((step, i) => (
        <React.Fragment key={step.step_name}>
          <div
            className="flex flex-col items-center gap-1 px-3 py-2 rounded border min-w-[90px] text-center shrink-0"
            style={{
              backgroundColor: step.will_fail
                ? "var(--accent-error)"
                : "var(--accent-success)",
              borderColor: step.will_fail
                ? "var(--accent-error)"
                : "var(--accent-success)",
              color: "var(--bg-base)",
            }}
          >
            <span className="text-[10px] font-bold uppercase opacity-80">
              {step.step_type}
            </span>
            <span className="text-xs font-semibold truncate max-w-[80px]" title={step.step_name}>
              {step.step_name}
            </span>
          </div>
          {i < steps.length - 1 && (
            <div className="flex items-center shrink-0 text-[var(--text-secondary)]">
              <div className="w-4 h-px" style={{ backgroundColor: "var(--text-secondary)" }} />
              <span className="text-xs">▸</span>
              <div className="w-4 h-px" style={{ backgroundColor: "var(--text-secondary)" }} />
            </div>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}
