import { useEffect, useState } from "react";

interface StreamingStats {
  batches_processed: number;
  messages_processed: number;
  messages_failed: number;
  messages_dlq: number;
  throughput_per_sec: number | null;
  topic: string;
  consumer_group: string;
}

interface Props {
  runId: string;
  status: string;
  pipelineName: string;
}

export function StreamingRunCard({ runId, status, pipelineName }: Props) {
  const [stats, setStats] = useState<StreamingStats | null>(null);

  useEffect(() => {
    if (!["STREAMING_ACTIVE", "STREAMING_PAUSED"].includes(status)) return;
    const token = localStorage.getItem("pipelineiq_token") || "";
    const fetchStats = () =>
      fetch(`/api/streaming/runs/${runId}/stats`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => d && setStats(d));
    fetchStats();
    const id = setInterval(fetchStats, 3000);
    return () => clearInterval(id);
  }, [runId, status]);

  const action = async (a: "pause" | "resume" | "stop" | "restart") => {
    const token = localStorage.getItem("pipelineiq_token") || "";
    await fetch(`/api/streaming/runs/${runId}/${a}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    window.location.reload();
  };

  const isActive = status === "STREAMING_ACTIVE";
  const isPaused = status === "STREAMING_PAUSED";
  const isStopped = status === "STREAMING_STOPPED";

  return (
    <div
      className={`streaming-card streaming-card--${status}`}
      data-testid="streaming-run-card"
    >
      <div className="streaming-card__header">
        <span>{isActive ? "\u26A1" : isPaused ? "\u23F8" : "\u23F9"}</span>
        <div>
          <p className="streaming-card__name">{pipelineName}</p>
          <p className="streaming-card__status">
            {isActive
              ? "STREAMING \u2014 ACTIVE"
              : isPaused
                ? "STREAMING \u2014 PAUSED"
                : "STREAMING \u2014 STOPPED"}
          </p>
        </div>
        <div className="streaming-card__controls">
          {isActive && (
            <button
              onClick={() => action("pause")}
              data-testid="pause-streaming-btn"
            >
              Pause
            </button>
          )}
          {isPaused && (
            <button
              onClick={() => action("resume")}
              data-testid="resume-streaming-btn"
            >
              Resume
            </button>
          )}
          {(isActive || isPaused) && (
            <button
              onClick={() => action("stop")}
              data-testid="stop-streaming-btn"
            >
              Stop
            </button>
          )}
          {isStopped && (
            <button
              onClick={() => action("restart")}
              data-testid="restart-streaming-btn"
            >
              Restart
            </button>
          )}
        </div>
      </div>
      {stats && (
        <div className="streaming-card__stats">
          <span>{stats.messages_processed.toLocaleString()} messages</span>
          <span>{stats.batches_processed} batches</span>
          {stats.throughput_per_sec != null && (
            <span>{Math.round(stats.throughput_per_sec)} msg/s</span>
          )}
          {stats.messages_dlq > 0 && (
            <span className="streaming-stat--warn">{stats.messages_dlq} DLQ</span>
          )}
        </div>
      )}
      {stats?.topic && (
        <p className="streaming-card__meta">
          Topic: <code>{stats.topic}</code>
        </p>
      )}
    </div>
  );
}
