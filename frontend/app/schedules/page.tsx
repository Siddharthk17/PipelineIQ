"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSchedules } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { PipelineSchedule } from "@/lib/api";
import { Calendar, Clock, Pause, Play } from "lucide-react";

export default function SchedulesPage() {
  const router = useRouter();
  const { user, isLoading } = useAuth();
  const [schedules, setSchedules] = useState<PipelineSchedule[]>([]);
  const [pageLoading, setPageLoading] = useState(true);

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
      return;
    }
    if (!user) return;
    getSchedules()
      .then(setSchedules)
      .catch(() => setSchedules([]))
      .finally(() => setPageLoading(false));
  }, [isLoading, router, user]);

  if (isLoading || !user || pageLoading) {
    return (
      <main className="flex h-screen items-center justify-center bg-[var(--bg-base)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)]" />
      </main>
    );
  }

  return (
    <main className="flex h-screen w-screen flex-col bg-[var(--bg-base)] text-[var(--text-primary)]">
      <div className="flex items-center justify-between border-b border-[var(--widget-border)] px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">Schedules</h1>
          <p className="text-sm text-[var(--text-secondary)]">
            Recurring pipeline runs configured via cron expressions
          </p>
        </div>
        <button
          onClick={() => router.push("/")}
          className="rounded border border-[var(--widget-border)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--interactive-hover)]"
        >
          Back
        </button>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {schedules.length === 0 ? (
          <div className="flex h-40 items-center justify-center text-sm text-[var(--text-secondary)]">
            No schedules configured
          </div>
        ) : (
          <div className="space-y-3">
            {schedules.map((schedule) => (
              <div
                key={schedule.id}
                data-testid="schedule-card"
                className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] p-4"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium">{schedule.pipeline_name}</p>
                    <div className="mt-1 flex items-center gap-3">
                      <span className="inline-flex items-center gap-1 text-xs text-[var(--text-secondary)]">
                        <Clock className="h-3 w-3" />
                        <span className="font-mono">{schedule.cron_expression}</span>
                      </span>
                      {schedule.next_run_at && (
                        <span className="inline-flex items-center gap-1 text-xs text-[var(--text-secondary)]">
                          <Calendar className="h-3 w-3" />
                          Next: {new Date(schedule.next_run_at).toLocaleDateString()}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${
                        schedule.is_active
                          ? "bg-[var(--accent-success)]/10 text-[var(--accent-success)]"
                          : "bg-[var(--text-secondary)]/10 text-[var(--text-secondary)]"
                      }`}
                    >
                      {schedule.is_active ? (
                        <>
                          <Play className="h-3 w-3" /> Active
                        </>
                      ) : (
                        <>
                          <Pause className="h-3 w-3" /> Paused
                        </>
                      )}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
