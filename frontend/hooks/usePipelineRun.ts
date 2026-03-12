import { useEffect } from "react";
import { usePipelineStore } from "@/store/pipelineStore";
import { useQueryClient } from "@tanstack/react-query";
import { getPipelineRun } from "@/lib/api";
import { API_V1 } from "@/lib/constants";
import type { PipelineRun, StepResult } from "@/lib/types";

/**
 * SSE step events have this shape (not a PipelineRun):
 * { run_id, step_name, step_index, total_steps, status, rows_in, rows_out, duration_ms, error_message }
 */
interface StepEvent {
  run_id: string;
  step_name: string;
  step_index: number;
  total_steps: number;
  status: string;
  rows_in: number | null;
  rows_out: number | null;
  duration_ms: number | null;
  error_message: string | null;
}

function applyStepEvent(run: PipelineRun, evt: StepEvent): PipelineRun {
  const steps = [...(run.step_results ?? [])];
  const idx = steps.findIndex(s => s.step_name === evt.step_name);
  const patch: Partial<StepResult> = {
    step_name: evt.step_name,
    step_index: evt.step_index,
    status: evt.status as StepResult["status"],
    rows_in: evt.rows_in,
    rows_out: evt.rows_out,
    duration_ms: evt.duration_ms,
    error_message: evt.error_message,
  };
  if (idx >= 0) {
    steps[idx] = { ...steps[idx], ...patch };
  } else {
    steps.push({ step_type: "", columns_in: [], columns_out: [], warnings: [], ...patch } as StepResult);
  }
  const runStatus = evt.status === "RUNNING" ? "RUNNING" : run.status;
  return { ...run, step_results: steps, status: runStatus };
}

export function usePipelineRun(runId: string | null) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let reconnectAttempts = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let currentEventSource: EventSource | null = null;

    // Immediately fetch full run data so RunMonitor has something to show
    getPipelineRun(runId)
      .then((run) => {
        if (!cancelled) usePipelineStore.setState({ activeRun: run });
      })
      .catch(() => {
        if (!cancelled) {
          usePipelineStore.setState({
            activeRun: {
              id: runId,
              name: "Pipeline Run",
              status: "PENDING",
              created_at: new Date().toISOString(),
              started_at: null,
              completed_at: null,
              total_rows_in: null,
              total_rows_out: null,
              error_message: null,
              duration_ms: null,
              step_results: [],
            },
          });
        }
      });

    function connectSSE() {
      if (cancelled) return;

      const eventSource = new EventSource(`${API_V1}/pipelines/${runId}/stream`);
      currentEventSource = eventSource;

      eventSource.onopen = () => {
        reconnectAttempts = 0;
      };

      const handleStepEvent = (e: MessageEvent) => {
        const evt: StepEvent = JSON.parse(e.data);
        usePipelineStore.setState((state) => {
          if (!state.activeRun) return state;
          return { activeRun: applyStepEvent(state.activeRun, evt) };
        });
      };

      eventSource.addEventListener("step_started", handleStepEvent);
      eventSource.addEventListener("step_completed", handleStepEvent);
      eventSource.addEventListener("step_failed", handleStepEvent);

      const handleTerminal = (status: PipelineRun["status"]) => (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        getPipelineRun(runId!).then((run) => {
          if (!cancelled) usePipelineStore.setState({ activeRun: run });
        }).catch(() => {
          usePipelineStore.setState((state) => {
            if (!state.activeRun) return state;
            return { activeRun: { ...state.activeRun, status, error_message: data.error_message || null } };
          });
        });
        eventSource.close();
        queryClient.invalidateQueries({ queryKey: ["lineage", runId] });
        queryClient.invalidateQueries({ queryKey: ["pipelineRuns"] });
      };

      eventSource.addEventListener("pipeline_completed", handleTerminal("COMPLETED"));
      eventSource.addEventListener("pipeline_failed", handleTerminal("FAILED"));

      eventSource.onerror = () => {
        eventSource.close();
        if (cancelled) return;

        // Exponential backoff reconnect: 1s, 2s, 4s, 8s, 16s max
        const maxDelay = 16000;
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), maxDelay);
        reconnectAttempts++;

        if (reconnectAttempts <= 5) {
          reconnectTimer = setTimeout(() => {
            if (!cancelled) connectSSE();
          }, delay);
        } else {
          // After max retries, fetch final state
          getPipelineRun(runId!).then((run) => {
            if (!cancelled) usePipelineStore.setState({ activeRun: run });
          }).catch(() => {
            usePipelineStore.setState((state) => {
              if (!state.activeRun) return state;
              return { activeRun: { ...state.activeRun, status: "FAILED", error_message: "Connection lost to stream" } };
            });
          });
        }
      };
    }

    connectSSE();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (currentEventSource) currentEventSource.close();
    };
  }, [runId, queryClient]);
}
