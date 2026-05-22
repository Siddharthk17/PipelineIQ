"use client";

import { AlertTriangle, XCircle } from "lucide-react";
import type { ContractViolation } from "@/lib/types";

interface Props {
  violations: ContractViolation[];
}

export function ContractViolationBadge({ violations }: Props) {
  const errors = violations.filter((v) => v.severity === "error").length;
  const warnings = violations.filter((v) => v.severity === "warning").length;

  if (violations.length === 0) return null;

  return (
    <div className="flex items-center gap-1.5">
      {errors > 0 && (
        <span className="flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] rounded-full bg-[var(--accent-error)]/10 text-[var(--accent-error)]">
          <XCircle className="w-2.5 h-2.5" />
          {errors}
        </span>
      )}
      {warnings > 0 && (
        <span className="flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] rounded-full bg-[var(--accent-warning)]/10 text-[var(--accent-warning)]">
          <AlertTriangle className="w-2.5 h-2.5" />
          {warnings}
        </span>
      )}
    </div>
  );
}
