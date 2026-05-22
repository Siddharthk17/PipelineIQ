import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getPipelineRuns } from "@/lib/api";
import { PIPELINE_RUNS_PAGE_LIMIT, PIPELINE_RUNS_QUERY_KEY } from "@/lib/constants";

export function usePipelineRuns() {
  return useQuery({
    queryKey: PIPELINE_RUNS_QUERY_KEY,
    queryFn: () => getPipelineRuns(1, PIPELINE_RUNS_PAGE_LIMIT),
    staleTime: 30000,
    gcTime: 60000,
  });
}

export function useInvalidatePipelineRuns() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: PIPELINE_RUNS_QUERY_KEY });
}
