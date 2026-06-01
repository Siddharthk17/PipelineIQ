"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { listCatalogPipelines, getPipelineDescription } from "@/lib/api";
import type { CatalogPipeline, PipelineDescription } from "@/lib/types";
import { ApiError } from "@/lib/api";
import {
  ArrowLeft,
  Zap,
  Search,
  Clock,
  CheckCircle,
  XCircle,
  Circle,
  Play,
  Pause,
  RefreshCw,
  Calendar,
} from "lucide-react";

const STATUS_ICONS: Record<string, React.ReactNode> = {
  COMPLETED: <CheckCircle className="h-3.5 w-3.5 text-green-400" />,
  FAILED: <XCircle className="h-3.5 w-3.5 text-red-400" />,
  RUNNING: <RefreshCw className="h-3.5 w-3.5 animate-spin text-blue-400" />,
  PENDING: <Circle className="h-3.5 w-3.5 text-yellow-400" />,
  HEALING: <RefreshCw className="h-3.5 w-3.5 animate-spin text-purple-400" />,
  HEALED: <CheckCircle className="h-3.5 w-3.5 text-emerald-400" />,
  CANCELLED: <XCircle className="h-3.5 w-3.5 text-gray-400" />,
  TIMEOUT: <XCircle className="h-3.5 w-3.5 text-orange-400" />,
  CONTRACT_VIOLATION: <XCircle className="h-3.5 w-3.5 text-orange-400" />,
};

const STATUS_BG: Record<string, string> = {
  COMPLETED: "bg-green-500/10",
  FAILED: "bg-red-500/10",
  RUNNING: "bg-blue-500/10",
  PENDING: "bg-yellow-500/10",
  HEALING: "bg-purple-500/10",
  HEALED: "bg-emerald-500/10",
  CANCELLED: "bg-gray-500/10",
  TIMEOUT: "bg-orange-500/10",
  CONTRACT_VIOLATION: "bg-orange-500/10",
};

function formatTimeAgo(isoString: string): string {
  const now = Date.now();
  const then = new Date(isoString).getTime();
  const diffMs = now - then;
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

export default function PipelineCatalogPage() {
  const router = useRouter();
  const { user, isLoading } = useAuth();
  const [pipelines, setPipelines] = useState<CatalogPipeline[]>([]);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [pageLoading, setPageLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [descriptions, setDescriptions] = useState<Record<string, string>>({});
  const [loadingDescription, setLoadingDescription] = useState<Record<string, boolean>>({});

  const loadPipelines = useCallback(async (q?: string, status?: string) => {
    try {
      setError(null);
      const data = await listCatalogPipelines(q || undefined, status || undefined);
      setPipelines(data.pipelines);
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.message : "Failed to load pipelines";
      setError(message);
    } finally {
      setPageLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
      return;
    }
    if (!user) return;
    loadPipelines();
  }, [isLoading, user, router, loadPipelines]);

  const handleSearch = useCallback(
    (value: string) => {
      setQuery(value);
      loadPipelines(value || undefined, statusFilter || undefined);
    },
    [statusFilter, loadPipelines]
  );

  const handleStatusFilter = useCallback(
    (status: string | null) => {
      const newStatus = statusFilter === status ? null : status;
      setStatusFilter(newStatus);
      loadPipelines(query || undefined, newStatus || undefined);
    },
    [query, statusFilter, loadPipelines]
  );

  const handleLoadDescription = useCallback(
    async (name: string) => {
      if (descriptions[name] || loadingDescription[name]) return;
      setLoadingDescription((prev) => ({ ...prev, [name]: true }));
      try {
        const result = await getPipelineDescription(name);
        setDescriptions((prev) => ({ ...prev, [name]: result.description }));
      } catch {
        setDescriptions((prev) => ({ ...prev, [name]: "Description unavailable" }));
      } finally {
        setLoadingDescription((prev) => ({ ...prev, [name]: false }));
      }
    },
    [descriptions, loadingDescription]
  );

  useEffect(() => {
    pipelines.forEach((p) => {
      if (!descriptions[p.pipeline_name] && !loadingDescription[p.pipeline_name]) {
        handleLoadDescription(p.pipeline_name);
      }
    });
  }, [pipelines, descriptions, loadingDescription, handleLoadDescription]);

  if (isLoading || !user || pageLoading) {
    return (
      <main className="flex h-screen items-center justify-center bg-[var(--bg-base)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)]" />
      </main>
    );
  }

  const statusOptions = ["COMPLETED", "FAILED", "RUNNING", "PENDING"];

  return (
    <main className="h-screen w-screen overflow-auto bg-[var(--bg-base)] p-4" data-testid="pipeline-catalog-page">
      <div className="mb-4">
        <div className="flex items-center gap-3 mb-2">
          <button
            onClick={() => router.push("/")}
            className="flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--interactive-hover)] transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Dashboard
          </button>
        </div>
        <h1 className="text-2xl font-bold text-[var(--text-primary)] flex items-center gap-2">
          <Zap className="h-6 w-6 text-[var(--accent-primary)]" />
          Pipeline Catalog
        </h1>
        <p className="text-sm text-[var(--text-secondary)] mt-1">
          Every pipeline ever run. Search, filter, and see AI-generated descriptions.
        </p>
      </div>

      <div className="mb-4 flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--text-muted)]" />
          <input
            type="text"
            value={query}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder="Search pipelines by name..."
            className="w-full rounded border border-[var(--widget-border)] bg-[var(--widget-bg)] py-2 pl-9 pr-3 text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)]"
            data-testid="pipeline-catalog-search"
          />
        </div>
        <div className="flex gap-2">
          {statusOptions.map((status) => (
            <button
              key={status}
              onClick={() => handleStatusFilter(status)}
              className={`rounded border px-3 py-1.5 text-[10px] font-medium transition-colors ${
                statusFilter === status
                  ? "border-[var(--accent-primary)] bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]"
                  : "border-[var(--widget-border)] text-[var(--text-secondary)] hover:border-[var(--text-muted)]"
              }`}
              data-testid={`filter-status-${status}`}
            >
              {status}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded border border-[var(--accent-error)]/30 bg-[var(--accent-error)]/5 px-3 py-2 text-xs text-[var(--accent-error)]">
          {error}
        </div>
      )}

      {pipelines.length === 0 && !error ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <Zap className="mb-3 h-8 w-8 text-[var(--text-muted)]" />
          <p className="text-sm font-medium text-[var(--text-secondary)]">No pipelines found</p>
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            Run a pipeline to see it appear here.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3" data-testid="pipeline-catalog-grid">
          {pipelines.map((pipeline) => (
            <div
              key={pipeline.pipeline_name}
              className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] p-4 transition-colors hover:border-[var(--accent-primary)]/50 hover:bg-[var(--bg-elevated)]"
              data-testid={`pipeline-card-${pipeline.pipeline_name}`}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1 min-w-0">
                  <button
                    onClick={() => {
                      if (pipeline.last_run_id) {
                        router.push(`/runs/${pipeline.last_run_id}`);
                      }
                    }}
                    className="text-left"
                  >
                    <h3 className="text-sm font-semibold text-[var(--text-primary)] truncate hover:text-[var(--accent-primary)] transition-colors">
                      {pipeline.pipeline_name}
                    </h3>
                  </button>
                  <div className="mt-1 flex items-center gap-2">
                    <span
                      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-medium ${STATUS_BG[pipeline.last_run_status] ?? "bg-gray-500/10"}`}
                    >
                      {STATUS_ICONS[pipeline.last_run_status] ?? <Circle className="h-3.5 w-3.5" />}
                      {pipeline.last_run_status}
                    </span>
                    <span className="flex items-center gap-1 text-[10px] text-[var(--text-muted)]">
                      <Clock className="h-3 w-3" />
                      {formatTimeAgo(pipeline.last_run_at)}
                    </span>
                  </div>
                </div>

                {pipeline.schedule && (
                  <div className="flex-shrink-0 ml-2">
                    {pipeline.schedule.active ? (
                      <span className="flex items-center gap-1 rounded bg-[var(--accent-success)]/10 px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent-success)]">
                        <Play className="h-2.5 w-2.5" />
                        Active
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 rounded bg-[var(--text-muted)]/20 px-1.5 py-0.5 text-[10px] font-medium text-[var(--text-muted)]">
                        <Pause className="h-2.5 w-2.5" />
                        Paused
                      </span>
                    )}
                  </div>
                )}
              </div>

              {loadingDescription[pipeline.pipeline_name] ? (
                <div className="flex items-center gap-2 py-2">
                  <div className="h-3 w-3 animate-spin rounded-full border-2 border-[var(--text-muted)] border-t-[var(--text-secondary)]" />
                  <span className="text-[10px] text-[var(--text-muted)]">Generating description...</span>
                </div>
              ) : (
                <p className="text-xs text-[var(--text-secondary)] leading-relaxed line-clamp-3">
                  {descriptions[pipeline.pipeline_name] ?? "No description available"}
                </p>
              )}

              {pipeline.schedule?.next_run_at && (
                <div className="mt-3 flex items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
                  <Calendar className="h-3 w-3" />
                  Next run: {new Date(pipeline.schedule.next_run_at).toLocaleString()}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
