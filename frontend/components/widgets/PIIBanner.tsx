"use client";

import { Shield, AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

interface PIIBannerProps {
  piiSuggestions: string[];
  fileId: string;
  onApplyPolicy?: (column: string) => void;
}

export function PIIBanner({ piiSuggestions, fileId, onApplyPolicy }: PIIBannerProps) {
  const [expanded, setExpanded] = useState(false);

  if (piiSuggestions.length === 0) return null;

  return (
    <div
      className="mb-4 rounded-lg border border-yellow-500/40 bg-yellow-500/5 p-4"
      data-testid="pii-banner"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-yellow-500" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Shield className="h-4 w-4 text-yellow-500" />
            <span className="text-sm font-semibold text-yellow-500">
              Personal Information Detected
            </span>
          </div>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            The data profiler detected personally identifiable information in{" "}
            <span className="font-medium text-yellow-400">{piiSuggestions.length}</span> column
            {piiSuggestions.length !== 1 ? "s" : ""}. Apply column-level security policies to
            control which roles can see this data.
          </p>
          <button
            onClick={() => setExpanded(!expanded)}
            className="mt-2 flex items-center gap-1 text-xs text-yellow-400 hover:text-yellow-300 transition-colors"
            data-testid="pii-banner-toggle"
          >
            {expanded ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
            {expanded ? "Hide" : "View"} PII columns
          </button>
          {expanded && (
            <div className="mt-2 space-y-1">
              {piiSuggestions.map((column) => (
                <div
                  key={column}
                  className="flex items-center justify-between rounded border border-yellow-500/20 bg-yellow-500/5 px-3 py-1.5"
                  data-testid={`pii-column-${column}`}
                >
                  <span className="text-xs font-mono text-yellow-400">{column}</span>
                  <button
                    onClick={() => onApplyPolicy?.(column)}
                    className="rounded border border-yellow-500/30 px-2 py-0.5 text-[10px] font-medium text-yellow-400 hover:bg-yellow-500/10 transition-colors"
                    data-testid={`pii-apply-${column}`}
                  >
                    Add Policy
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
