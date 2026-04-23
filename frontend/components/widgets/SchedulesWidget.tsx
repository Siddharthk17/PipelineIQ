"use client";

import React, { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Clock, Play, Pause, Trash2, Plus, RefreshCw, AlertCircle } from "lucide-react";
import { getSchedules, deleteSchedule, toggleSchedule, createSchedule, type PipelineSchedule } from "@/lib/api";

function ScheduleCard({
  schedule,
  onToggle,
  onDelete,
}: {
  schedule: PipelineSchedule;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [loading, setLoading] = useState(false);

  const handleToggle = useCallback(async () => {
    setLoading(true);
    try {
      await toggleSchedule(schedule.id);
      onToggle(schedule.id);
    } catch (err) {
      console.error("Failed to toggle schedule:", err);
    } finally {
      setLoading(false);
    }
  }, [schedule.id, onToggle]);

  const handleDelete = useCallback(async () => {
    if (!confirm("Are you sure you want to delete this schedule?")) return;
    setLoading(true);
    try {
      await deleteSchedule(schedule.id);
      onDelete(schedule.id);
    } catch (err) {
      console.error("Failed to delete schedule:", err);
    } finally {
      setLoading(false);
    }
  }, [schedule.id, onDelete]);

  const formatCron = (cron: string) => {
    const parts = cron.split(" ");
    if (parts.length === 5) {
      const [min, hour, day, month, dow] = parts;
      if (hour === "*" && day === "*" && month === "*" && dow === "*") return "Every hour";
      if (min === "0" && day === "*" && month === "*" && dow === "*") return `Daily at ${hour}:00`;
      if (min !== "*" && hour !== "*") return `At ${hour}:${min.padStart(2, "0")}`;
      return cron;
    }
    return cron;
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, x: -20 }}
      className="p-4 rounded-lg border"
      style={{
        backgroundColor: "var(--bg-surface)",
        borderColor: schedule.is_active ? "var(--accent-success)" : "var(--widget-border)",
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div
            className="p-2 rounded-lg"
            style={{
              backgroundColor: schedule.is_active ? "var(--accent-success)" : "var(--bg-muted)",
              opacity: schedule.is_active ? 0.2 : 1,
            }}
          >
            <Clock
              size={18}
              style={{ color: schedule.is_active ? "var(--accent-success)" : "var(--text-secondary)" }}
            />
          </div>
          <div>
            <h4 className="font-medium text-sm text-[var(--text-primary)]">{schedule.pipeline_name}</h4>
            <div className="flex items-center gap-2 mt-1">
              <span
                className="text-xs px-2 py-0.5 rounded-full font-mono"
                style={{
                  backgroundColor: "var(--bg-muted)",
                  color: "var(--text-secondary)",
                }}
              >
                {schedule.cron_expression}
              </span>
              <span className="text-xs text-[var(--text-secondary)]">
                {formatCron(schedule.cron_expression)}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1">
          <button
            onClick={handleToggle}
            disabled={loading}
            className="p-2 rounded-lg transition-colors hover:opacity-80 disabled:opacity-50"
            style={{
              backgroundColor: schedule.is_active ? "var(--accent-warning)" : "var(--accent-success)",
              color: "#fff",
            }}
            title={schedule.is_active ? "Pause" : "Enable"}
          >
            {loading ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : schedule.is_active ? (
              <Pause size={14} />
            ) : (
              <Play size={14} />
            )}
          </button>
          <button
            onClick={handleDelete}
            disabled={loading}
            className="p-2 rounded-lg transition-colors hover:opacity-80 disabled:opacity-50"
            style={{
              backgroundColor: "var(--accent-error)",
              color: "#fff",
            }}
            title="Delete"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-4 text-xs text-[var(--text-secondary)]">
        {schedule.next_run_at && (
          <span>
            Next: {new Date(schedule.next_run_at).toLocaleString()}
          </span>
        )}
        {schedule.last_run_at && (
          <span>
            Last: {new Date(schedule.last_run_at).toLocaleString()}
          </span>
        )}
        {!schedule.next_run_at && !schedule.last_run_at && (
          <span>Never run</span>
        )}
      </div>
    </motion.div>
  );
}

export function SchedulesWidget() {
  const [schedules, setSchedules] = useState<PipelineSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newSchedule, setNewSchedule] = useState({
    pipeline_name: "",
    yaml_config: "",
    cron_expression: "0 * * * *",
  });

  const loadSchedules = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getSchedules();
      setSchedules(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load schedules");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSchedules();
  }, [loadSchedules]);

  const handleToggle = useCallback((id: string) => {
    setSchedules((prev) =>
      prev.map((s) => (s.id === id ? { ...s, is_active: !s.is_active } : s))
    );
  }, []);

  const handleDelete = useCallback((id: string) => {
    setSchedules((prev) => prev.filter((s) => s.id !== id));
  }, []);

  const handleCreate = useCallback(async () => {
    if (!newSchedule.pipeline_name || !newSchedule.yaml_config) return;
    try {
      await createSchedule(newSchedule);
      setShowCreate(false);
      setNewSchedule({ pipeline_name: "", yaml_config: "", cron_expression: "0 * * * *" });
      loadSchedules();
    } catch (err) {
      console.error("Failed to create schedule:", err);
    }
  }, [newSchedule, loadSchedules]);

  const activeSchedules = schedules.filter((s) => s.is_active);
  const inactiveSchedules = schedules.filter((s) => !s.is_active);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="p-3 border-b flex items-center justify-between" style={{ borderColor: "var(--widget-border)" }}>
        <div className="flex items-center gap-2">
          <Clock size={16} className="text-[var(--accent-primary)]" />
          <h3 className="font-medium text-sm text-[var(--text-primary)]">Pipeline Schedules</h3>
          {activeSchedules.length > 0 && (
            <span
              className="text-xs px-2 py-0.5 rounded-full"
              style={{
                backgroundColor: "var(--accent-success)",
                color: "#fff",
              }}
            >
              {activeSchedules.length} active
            </span>
          )}
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="p-1.5 rounded-lg transition-colors"
          style={{
            backgroundColor: "var(--accent-primary)",
            color: "#fff",
          }}
          title="Add Schedule"
        >
          <Plus size={14} />
        </button>
      </div>

      <div className="flex-1 overflow-auto p-3 space-y-4">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-8 h-8 border-2 border-[var(--accent-primary)] border-t-transparent rounded-full animate-spin" />
          </div>
        ) : error ? (
          <div className="text-center py-8">
            <AlertCircle size={24} className="mx-auto mb-2 text-[var(--accent-error)]" />
            <p className="text-sm text-[var(--text-secondary)]">{error}</p>
            <button
              onClick={loadSchedules}
              className="mt-2 px-3 py-1 rounded text-sm"
              style={{ backgroundColor: "var(--accent-primary)", color: "#fff" }}
            >
              Retry
            </button>
          </div>
        ) : schedules.length === 0 ? (
          <div className="text-center py-8">
            <Clock size={32} className="mx-auto mb-2 text-[var(--text-secondary)] opacity-50" />
            <p className="text-sm text-[var(--text-secondary)]">No schedules yet</p>
            <button
              onClick={() => setShowCreate(true)}
              className="mt-2 px-3 py-1.5 rounded text-sm"
              style={{ backgroundColor: "var(--accent-primary)", color: "#fff" }}
            >
              Create Schedule
            </button>
          </div>
        ) : (
          <AnimatePresence>
            {activeSchedules.length > 0 && (
              <div>
                <h4 className="text-xs font-medium text-[var(--text-secondary)] mb-2 uppercase tracking-wider">
                  Active
                </h4>
                <div className="space-y-2">
                  {activeSchedules.map((schedule) => (
                    <ScheduleCard
                      key={schedule.id}
                      schedule={schedule}
                      onToggle={handleToggle}
                      onDelete={handleDelete}
                    />
                  ))}
                </div>
              </div>
            )}
            {inactiveSchedules.length > 0 && (
              <div>
                <h4 className="text-xs font-medium text-[var(--text-secondary)] mb-2 uppercase tracking-wider">
                  Paused
                </h4>
                <div className="space-y-2">
                  {inactiveSchedules.map((schedule) => (
                    <ScheduleCard
                      key={schedule.id}
                      schedule={schedule}
                      onToggle={handleToggle}
                      onDelete={handleDelete}
                    />
                  ))}
                </div>
              </div>
            )}
          </AnimatePresence>
        )}
      </div>

      <AnimatePresence>
        {showCreate && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-black/50 flex items-center justify-center p-4 z-50"
            onClick={() => setShowCreate(false)}
          >
            <motion.div
              initial={{ scale: 0.95 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0.95 }}
              className="w-full max-w-md rounded-lg p-4"
              style={{ backgroundColor: "var(--bg-surface)" }}
              onClick={(e) => e.stopPropagation()}
            >
              <h3 className="font-medium text-lg text-[var(--text-primary)] mb-4">Create Schedule</h3>

              <div className="space-y-3">
                <div>
                  <label className="block text-xs text-[var(--text-secondary)] mb-1">Pipeline Name</label>
                  <input
                    type="text"
                    value={newSchedule.pipeline_name}
                    onChange={(e) => setNewSchedule((s) => ({ ...s, pipeline_name: e.target.value }))}
                    className="w-full px-3 py-2 rounded-lg text-sm border"
                    style={{
                      backgroundColor: "var(--bg-muted)",
                      borderColor: "var(--widget-border)",
                      color: "var(--text-primary)",
                    }}
                    placeholder="my_pipeline"
                  />
                </div>

                <div>
                  <label className="block text-xs text-[var(--text-secondary)] mb-1">Cron Expression</label>
                  <input
                    type="text"
                    value={newSchedule.cron_expression}
                    onChange={(e) => setNewSchedule((s) => ({ ...s, cron_expression: e.target.value }))}
                    className="w-full px-3 py-2 rounded-lg text-sm border font-mono"
                    style={{
                      backgroundColor: "var(--bg-muted)",
                      borderColor: "var(--widget-border)",
                      color: "var(--text-primary)",
                    }}
                    placeholder="0 * * * *"
                  />
                  <p className="text-xs text-[var(--text-secondary)] mt-1">
                    Examples: "0 * * * *" (hourly), "0 0 * * *" (daily), "0 * * * *" (every minute)
                  </p>
                </div>

                <div>
                  <label className="block text-xs text-[var(--text-secondary)] mb-1">YAML Config</label>
                  <textarea
                    value={newSchedule.yaml_config}
                    onChange={(e) => setNewSchedule((s) => ({ ...s, yaml_config: e.target.value }))}
                    className="w-full px-3 py-2 rounded-lg text-sm border font-mono"
                    style={{
                      backgroundColor: "var(--bg-muted)",
                      borderColor: "var(--widget-border)",
                      color: "var(--text-primary)",
                    }}
                    rows={6}
                    placeholder="pipeline:
  name: my_pipeline
  steps:
    - name: load
      type: load
      file_id: ..."
                  />
                </div>
              </div>

              <div className="flex justify-end gap-2 mt-4">
                <button
                  onClick={() => setShowCreate(false)}
                  className="px-3 py-1.5 rounded text-sm"
                  style={{ border: "1px solid var(--widget-border)", color: "var(--text-secondary)" }}
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreate}
                  className="px-3 py-1.5 rounded text-sm"
                  style={{ backgroundColor: "var(--accent-primary)", color: "#fff" }}
                >
                  Create
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
