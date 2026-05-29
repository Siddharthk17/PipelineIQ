"use client";

import { useEffect, useState, use } from "react";
import { useRouter } from "next/navigation";
import { getPipelineRun } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { GanttChart } from "@/components/runs/GanttChart";
import { RunStatusBadge } from "@/components/runs/RunStatusBadge";
import type { PipelineRun } from "@/lib/types";
import { Download } from "lucide-react";

export default function RunDetailPage({ params }: { params: Promise<{ runId: string }> }) {
  const router = useRouter();
  const { user, isLoading } = useAuth();
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [pageLoading, setPageLoading] = useState(true);
  const resolvedParams = use(params);

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
      return;
    }
    if (!user) return;
    getPipelineRun(resolvedParams.runId)
      .then(setRun)
      .catch(() => {})
      .finally(() => setPageLoading(false));
  }, [isLoading, resolvedParams.runId, router, user]);

  if (isLoading || !user || pageLoading) {
    return (
      <main className="flex h-screen items-center justify-center bg-[var(--bg-base)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)]" />
      </main>
    );
  }

  if (!run) {
    return (
      <main className="flex h-screen items-center justify-center bg-[var(--bg-base)]">
        <p className="text-sm text-[var(--text-secondary)]">Run not found</p>
      </main>
    );
  }

  return (
    <main className="flex h-screen w-screen flex-col bg-[var(--bg-base)] text-[var(--text-primary)]">
      <div className="flex items-center justify-between border-b border-[var(--widget-border)] px-6 py-4">
        <div className="flex items-center gap-3">
          <RunStatusBadge status={run.status} />
          <div>
            <h1 className="text-lg font-semibold">{run.name}</h1>
            <div data-testid="run-status" className="flex items-center gap-2 text-sm">
              <span
                className="font-mono text-xs"
                style={{
                  color:
                    run.status === "COMPLETED" || run.status === "HEALED"
                      ? "var(--accent-success)"
                      : run.status === "FAILED"
                        ? "var(--accent-error)"
                        : "var(--text-secondary)",
                }}
              >
                {run.status.toLowerCase()}
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {(run.status === "COMPLETED" || run.status === "HEALED") && (
            <button
              data-testid="download-output-btn"
              className="rounded border border-[var(--widget-border)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--interactive-hover)]"
            >
              <span className="inline-flex items-center gap-1">
                <Download className="h-3.5 w-3.5" />
                Download CSV
              </span>
            </button>
          )}
          <button
            onClick={() => router.push("/runs")}
            className="rounded border border-[var(--widget-border)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--interactive-hover)]"
          >
            All Runs
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] p-4">
            <p className="text-xs text-[var(--text-secondary)]">Duration</p>
            <p className="mt-1 font-mono text-sm">
              {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : "-"}
            </p>
          </div>
          <div className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] p-4">
            <p className="text-xs text-[var(--text-secondary)]">Rows In</p>
            <p className="mt-1 font-mono text-sm">{run.total_rows_in ?? "-"}</p>
          </div>
          <div className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] p-4">
            <p className="text-xs text-[var(--text-secondary)]">Rows Out</p>
            <p className="mt-1 font-mono text-sm">{run.total_rows_out ?? "-"}</p>
          </div>
          <div className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] p-4">
            <p className="text-xs text-[var(--text-secondary)]">Steps</p>
            <p className="mt-1 font-mono text-sm">{run.step_results.length}</p>
          </div>
        </div>

        {run.error_message && (
          <div className="mb-6 rounded border-l-2 border-[var(--accent-error)] bg-[var(--accent-error)]/5 p-4">
            <p className="mb-1 text-xs font-medium text-[var(--accent-error)]">Error</p>
            <pre className="whitespace-pre-wrap font-mono text-xs text-[var(--text-secondary)]">
              {run.error_message}
            </pre>
          </div>
        )}

        {run.step_results.length > 0 && (
          <div className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)]">
            <div className="h-64 p-4">
              <GanttChart
                steps={run.step_results}
                totalDurationMs={run.duration_ms}
              />
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
