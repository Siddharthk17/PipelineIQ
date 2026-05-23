import { useQuery } from "@tanstack/react-query";
import { ApiError, getLineageGraph, getColumnLineage, getImpactAnalysis } from "@/lib/api";

export function shouldRetryLineageGraphQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status === 404) return failureCount < 5;
  return false;
}

export function useLineageGraph(runId: string | null) {
  return useQuery({
    queryKey: ["lineage", runId],
    queryFn: () => getLineageGraph(runId!),
    enabled: !!runId,
    retry: shouldRetryLineageGraphQuery,
    retryDelay: (attemptIndex) => Math.min(1000 * Math.pow(2, attemptIndex), 4000),
    staleTime: 5000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

export function useColumnLineage(runId: string | null, step: string | null, column: string | null) {
  return useQuery({
    queryKey: ["lineage", runId, "column", step, column],
    queryFn: () => getColumnLineage(runId!, step!, column!),
    enabled: !!runId && !!step && !!column,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

export function useImpactAnalysis(runId: string | null, step: string | null, column: string | null) {
  return useQuery({
    queryKey: ["lineage", runId, "impact", step, column],
    queryFn: () => getImpactAnalysis(runId!, step!, column!),
    enabled: !!runId && !!step && !!column,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}
