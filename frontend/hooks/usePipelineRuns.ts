import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getPipelineRuns } from "@/lib/api";
import { PIPELINE_RUNS_PAGE_LIMIT, PIPELINE_RUNS_QUERY_KEY } from "@/lib/constants";
import { useCallback, useRef } from "react";

export function usePipelineRuns() {
  return useQuery({
    queryKey: PIPELINE_RUNS_QUERY_KEY,
    queryFn: () => getPipelineRuns(1, PIPELINE_RUNS_PAGE_LIMIT),
    staleTime: 30_000,
    gcTime: 60_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

let _invalidateTimer: ReturnType<typeof setTimeout> | null = null;

export function useInvalidatePipelineRuns() {
  const queryClient = useQueryClient();
  return useCallback(() => {
    if (_invalidateTimer) return;
    _invalidateTimer = setTimeout(() => {
      _invalidateTimer = null;
      queryClient.invalidateQueries({ queryKey: PIPELINE_RUNS_QUERY_KEY });
    }, 2000);
  }, [queryClient]);
}