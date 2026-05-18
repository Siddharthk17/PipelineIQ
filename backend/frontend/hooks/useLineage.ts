import { useQuery } from "@tanstack/react-query";
import { getLineageGraph, getColumnLineage, getImpactAnalysis } from "@/lib/api";

export function useLineageGraph(runId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["lineage", runId],
    queryFn: () => getLineageGraph(runId!),
    enabled: !!runId && enabled,
    retry: false,
  });
}

export function useColumnLineage(runId: string | null, step: string | null, column: string | null) {
  return useQuery({
    queryKey: ["lineage", runId, "column", step, column],
    queryFn: () => getColumnLineage(runId!, step!, column!),
    enabled: !!runId && !!step && !!column,
  });
}

export function useImpactAnalysis(runId: string | null, step: string | null, column: string | null) {
  return useQuery({
    queryKey: ["lineage", runId, "impact", step, column],
    queryFn: () => getImpactAnalysis(runId!, step!, column!),
    enabled: !!runId && !!step && !!column,
  });
}
