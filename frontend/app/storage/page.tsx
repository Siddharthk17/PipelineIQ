"use client";

import React, { useEffect, useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "motion/react";
import {
  HardDrive,
  Database,
  Cpu,
  Cloud,
  Clock,
  Zap,
  Trash2,
  ArrowUpDown,
  Activity,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  RefreshCw,
  ChevronDown,
  ChevronUp,
} from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import {
  getStorageStats,
  getTierHealth,
  triggerEviction,
  cleanupStaleShm,
} from "@/lib/api";
import type {
  TierStats,
  WarmTierStats,
  ColdTierStats,
  GrowthTrendPoint,
  BucketStats,
  LifecyclePolicy,
  TierHealthWarning,
} from "@/lib/types";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i >= 2 ? 1 : 0)} ${units[i]}`;
}

function UtilizationBar({
  value,
  total,
  label,
}: {
  value: number;
  total: number;
  label?: string;
}) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  const color =
    pct > 90 ? "var(--danger)" : pct > 75 ? "var(--warning)" : "var(--accent-primary)";

  return (
    <div className="w-full">
      {(label || total > 0) && (
        <div className="flex justify-between text-xs mb-1">
          <span className="text-[var(--text-secondary)]">
            {label || "Used"}
          </span>
          <span className="text-[var(--text-primary)] font-mono tabular-nums">
            {formatBytes(value)}
            {total > 0 && (
              <span className="text-[var(--text-tertiary)]">
                {" "}
                / {formatBytes(total)}
              </span>
            )}
          </span>
        </div>
      )}
      <div className="w-full h-1.5 bg-[var(--bg-tertiary)] rounded-full overflow-hidden">
        <motion.div
          className="h-full rounded-full"
          initial={{ width: 0 }}
          animate={{ width: `${Math.min(pct, 100)}%` }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          style={{ backgroundColor: color }}
        />
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  accent,
}: {
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>;
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div
      className="rounded-lg border p-4"
      style={{
        backgroundColor: "var(--bg-surface)",
        borderColor: "var(--widget-border)",
      }}
    >
      <div className="flex items-center gap-2 mb-2">
        <Icon
          className="w-4 h-4"
          style={{ color: accent || "var(--text-secondary)" }}
        />
        <span className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wide">
          {label}
        </span>
      </div>
      <div className="text-xl font-semibold text-[var(--text-primary)] tabular-nums">
        {value}
      </div>
      {sub && (
        <div className="text-xs text-[var(--text-tertiary)] mt-1">{sub}</div>
      )}
    </div>
  );
}

function Sparkline({ points }: { points: GrowthTrendPoint[] }) {
  if (points.length < 2) {
    return (
      <div className="h-12 flex items-center justify-center text-xs text-[var(--text-tertiary)]">
        Not enough data for trend
      </div>
    );
  }

  const max = Math.max(...points.map((p) => p.mb), 1);
  const min = Math.min(...points.map((p) => p.mb), 0);
  const range = max - min || 1;
  const w = 100;
  const h = 48;
  const padX = 2;
  const padY = 4;

  const xs = points.map(
    (_, i) => padX + (i / (points.length - 1)) * (w - padX * 2)
  );
  const ys = points.map(
    (p) => h - padY - ((p.mb - min) / range) * (h - padY * 2)
  );

  const pathD = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x},${ys[i]}`).join(" ");
  const areaD = `${pathD} L${xs[xs.length - 1]},${h - padY} L${xs[0]},${h - padY} Z`;

  return (
    <div className="w-full">
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="w-full h-12"
        preserveAspectRatio="none"
      >
        <path
          d={areaD}
          fill="var(--accent-primary)"
          fillOpacity={0.08}
        />
        <motion.path
          d={pathD}
          fill="none"
          stroke="var(--accent-primary)"
          strokeWidth={1.5}
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
      </svg>
      <div className="flex justify-between text-[10px] text-[var(--text-tertiary)] px-0.5">
        <span>{points[0]?.day?.slice(5) || ""}</span>
        <span>{points[points.length - 1]?.day?.slice(5) || ""}</span>
      </div>
    </div>
  );
}

function TierOverview({
  hot,
  warm,
  cold,
}: {
  hot?: TierStats;
  warm?: WarmTierStats;
  cold?: ColdTierStats;
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
      <div
        className="rounded-lg border p-4"
        style={{
          backgroundColor: "var(--bg-surface)",
          borderColor: "var(--widget-border)",
        }}
      >
        <div className="flex items-center gap-2 mb-3">
          <div
            className="w-2 h-2 rounded-full"
            style={{ backgroundColor: "var(--danger)" }}
          />
          <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide">
            Tier 1 — HOT
          </span>
          <span className="text-[10px] text-[var(--text-tertiary)]">Redis</span>
        </div>
        {hot?.error ? (
          <p className="text-xs text-[var(--text-tertiary)]">{hot.error}</p>
        ) : (
          <>
            <UtilizationBar
              value={hot?.used_bytes || 0}
              total={hot?.max_bytes || 0}
            />
            <div className="flex justify-between mt-2 text-xs">
              <span className="text-[var(--text-secondary)]">
                ~{hot?.key_count ?? 0} keys
              </span>
              <span className="text-[var(--text-tertiary)]">
                {hot?.utilization != null
                  ? `${(hot.utilization * 100).toFixed(0)}% used`
                  : "—"}
              </span>
            </div>
            <div className="mt-2 text-[10px] text-[var(--text-tertiary)]">
              Payload &lt; 10MB &middot; TTL 1h &middot; ~0.2ms read
            </div>
          </>
        )}
      </div>

      <div
        className="rounded-lg border p-4"
        style={{
          backgroundColor: "var(--bg-surface)",
          borderColor: "var(--widget-border)",
        }}
      >
        <div className="flex items-center gap-2 mb-3">
          <div
            className="w-2 h-2 rounded-full"
            style={{ backgroundColor: "var(--warning)" }}
          />
          <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide">
            Tier 2 — WARM
          </span>
          <span className="text-[10px] text-[var(--text-tertiary)]">
            /dev/shm
          </span>
        </div>
        {!warm?.available ? (
          <p className="text-xs text-[var(--text-tertiary)]">Unavailable</p>
        ) : (
          <>
            <UtilizationBar
              value={warm?.used_bytes || 0}
              total={warm?.total_bytes || 0}
            />
            <div className="flex justify-between mt-2 text-xs">
              <span className="text-[var(--text-secondary)]">
                {formatBytes(warm?.used_bytes || 0)} used
              </span>
              <span className="text-[var(--text-tertiary)]">
                {warm?.utilization != null
                  ? `${(warm.utilization * 100).toFixed(0)}% used`
                  : "—"}
              </span>
            </div>
            <div className="mt-2 text-[10px] text-[var(--text-tertiary)]">
              10MB–500MB &middot; Cleared on run completion &middot; ~0.5ms read
            </div>
          </>
        )}
      </div>

      <div
        className="rounded-lg border p-4"
        style={{
          backgroundColor: "var(--bg-surface)",
          borderColor: "var(--widget-border)",
        }}
      >
        <div className="flex items-center gap-2 mb-3">
          <div
            className="w-2 h-2 rounded-full"
            style={{ backgroundColor: "var(--accent-secondary)" }}
          />
          <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide">
            Tier 3 — COLD
          </span>
          <span className="text-[10px] text-[var(--text-tertiary)]">MinIO</span>
        </div>
        {cold?.error ? (
          <p className="text-xs text-[var(--text-tertiary)]">{cold.error}</p>
        ) : (
          <>
            <div className="text-lg font-semibold text-[var(--text-primary)] tabular-nums">
              {formatBytes(cold?.total_bytes || 0)}
            </div>
            <div className="text-xs text-[var(--text-tertiary)]">
              {cold?.object_count ?? 0} objects
              {cold?.note && ` · ${cold.note}`}
            </div>
            <div className="mt-2 text-[10px] text-[var(--text-tertiary)]">
              &ge;500MB &middot; TTL 48h &middot; Parquet+Snappy &middot; ~50ms
              read
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function BucketSection({ buckets }: { buckets: Record<string, BucketStats> }) {
  const bucketNames = Object.keys(buckets);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  if (bucketNames.length === 0) return null;

  const totalBytes = bucketNames.reduce(
    (sum, name) => sum + (buckets[name]?.total_bytes || 0),
    0
  );

  return (
    <div
      className="rounded-lg border p-4"
      style={{
        backgroundColor: "var(--bg-surface)",
        borderColor: "var(--widget-border)",
      }}
    >
      <div className="flex items-center gap-2 mb-4">
        <HardDrive size={14} style={{ color: "var(--text-secondary)" }} />
        <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide">
          MinIO Buckets
        </span>
        <span className="text-[10px] text-[var(--text-tertiary)]">
          {bucketNames.length} buckets &middot; {formatBytes(totalBytes)} total
        </span>
      </div>
      <div className="space-y-2">
        {bucketNames.map((name) => {
          const stats = buckets[name];
          if (!stats || stats.error) {
            return (
              <div key={name} className="text-xs text-[var(--danger)]">
                {name}: {stats?.error || "unavailable"}
              </div>
            );
          }
          const isOpen = expanded[name];

          return (
            <div key={name}>
              <button
                onClick={() =>
                  setExpanded((prev) => ({ ...prev, [name]: !prev[name] }))
                }
                className="w-full flex items-center justify-between py-1.5 hover:bg-[var(--bg-tertiary)] rounded px-2 transition-colors text-left"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm font-medium text-[var(--text-primary)]">
                    {name}
                  </span>
                  <span className="text-[10px] text-[var(--text-tertiary)] tabular-nums">
                    {stats.object_count} obj
                  </span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-xs text-[var(--text-secondary)] tabular-nums">
                    {formatBytes(stats.total_bytes)}
                  </span>
                  {isOpen ? (
                    <ChevronUp size={12} style={{ color: "var(--text-tertiary)" }} />
                  ) : (
                    <ChevronDown size={12} style={{ color: "var(--text-tertiary)" }} />
                  )}
                </div>
              </button>
              <AnimatePresence>
                {isOpen && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.15 }}
                    className="overflow-hidden"
                  >
                    <div className="pl-4 pr-2 pb-2 pt-1 space-y-1">
                      {stats.largest_object && (
                        <div className="text-[10px] text-[var(--text-tertiary)] flex justify-between">
                          <span>
                            Largest:{" "}
                            <span className="text-[var(--text-secondary)] truncate max-w-[200px] inline-block align-bottom">
                              {stats.largest_object.name}
                            </span>
                          </span>
                          <span className="tabular-nums">
                            {stats.largest_object.size_mb} MB
                          </span>
                        </div>
                      )}
                      {stats.oldest_object?.last_modified && (
                        <div className="text-[10px] text-[var(--text-tertiary)]">
                          Oldest:{" "}
                          <span className="text-[var(--text-secondary)]">
                            {new Date(
                              stats.oldest_object.last_modified
                            ).toLocaleDateString()}
                          </span>
                        </div>
                      )}
                      {stats.top10_by_size && stats.top10_by_size.length > 0 && (
                        <div className="mt-2">
                          <span className="text-[10px] font-medium text-[var(--text-secondary)]">
                            Top 10 by size
                          </span>
                          <div className="mt-1 space-y-0.5">
                            {stats.top10_by_size.slice(0, 10).map((obj, i) => (
                              <div
                                key={obj.name}
                                className="flex justify-between text-[10px]"
                              >
                                <span className="text-[var(--text-tertiary)] truncate max-w-[70%]">
                                  {i + 1}. {obj.name}
                                </span>
                                <span className="text-[var(--text-secondary)] tabular-nums">
                                  {obj.size_mb} MB
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function LifecycleSection({
  lifecycle,
}: {
  lifecycle: Record<string, LifecyclePolicy>;
}) {
  return (
    <div
      className="rounded-lg border p-4"
      style={{
        backgroundColor: "var(--bg-surface)",
        borderColor: "var(--widget-border)",
      }}
    >
      <div className="flex items-center gap-2 mb-4">
        <Clock size={14} style={{ color: "var(--text-secondary)" }} />
        <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide">
          Lifecycle Policies
        </span>
      </div>
      <div className="space-y-2">
        {Object.entries(lifecycle).map(([bucket, policy]) => (
          <div
            key={bucket}
            className="flex items-center justify-between py-1"
          >
            <span className="text-sm text-[var(--text-primary)]">{bucket}</span>
            <div className="flex items-center gap-1.5">
              {policy.error ? (
                <span className="text-[10px] text-[var(--danger)]">
                  {policy.error}
                </span>
              ) : policy.rules.length > 0 ? (
                policy.rules.map((rule) => (
                  <span
                    key={rule.id}
                    className="text-[10px] px-1.5 py-0.5 rounded"
                    style={{
                      backgroundColor: "var(--bg-tertiary)",
                      color:
                        rule.status === "Enabled"
                          ? "var(--accent-primary)"
                          : "var(--text-tertiary)",
                    }}
                  >
                    {rule.expiry_days != null
                      ? `${rule.expiry_days}d expiry`
                      : rule.status}
                  </span>
                ))
              ) : policy.note ? (
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  {policy.note}
                </span>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function EventStatsSection({ events }: { events: import("@/lib/types").EventStat[] }) {
  if (events.length === 0) {
    return (
      <div
        className="rounded-lg border p-4"
        style={{
          backgroundColor: "var(--bg-surface)",
          borderColor: "var(--widget-border)",
        }}
      >
        <div className="flex items-center gap-2 mb-4">
          <Activity size={14} style={{ color: "var(--text-secondary)" }} />
          <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide">
            Storage Events (7d)
          </span>
        </div>
        <p className="text-xs text-[var(--text-tertiary)]">
          No storage events recorded in the last 7 days.
        </p>
      </div>
    );
  }

  const tierMap: Record<string, { label: string; color: string }> = {
    hot: { label: "HOT (Redis)", color: "var(--danger)" },
    warm: { label: "WARM (/dev/shm)", color: "var(--warning)" },
    cold: { label: "COLD (MinIO)", color: "var(--accent-secondary)" },
  };

  return (
    <div
      className="rounded-lg border p-4"
      style={{
        backgroundColor: "var(--bg-surface)",
        borderColor: "var(--widget-border)",
      }}
    >
      <div className="flex items-center gap-2 mb-4">
        <Activity size={14} style={{ color: "var(--text-secondary)" }} />
        <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide">
          Storage Events (7d)
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {events.map((evt) => {
          const tier = tierMap[evt.tier] || {
            label: evt.tier,
            color: "var(--text-tertiary)",
          };
          return (
            <div
              key={evt.tier}
              className="rounded p-3"
              style={{ backgroundColor: "var(--bg-tertiary)" }}
            >
              <div className="flex items-center gap-1.5 mb-1">
                <div
                  className="w-1.5 h-1.5 rounded-full"
                  style={{ backgroundColor: tier.color }}
                />
                <span className="text-[10px] font-medium text-[var(--text-primary)]">
                  {tier.label}
                </span>
              </div>
              <div className="text-lg font-semibold text-[var(--text-primary)] tabular-nums">
                {evt.event_count.toLocaleString()}
              </div>
              <div className="text-[10px] text-[var(--text-tertiary)] space-y-0.5">
                <div>{formatBytes(evt.total_bytes)} total</div>
                <div>{evt.avg_duration_ms.toFixed(1)}ms avg</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ActionsPanel({
  onEvict,
  onClean,
  isEvicting,
  isCleaning,
  evictResult,
  cleanResult,
}: {
  onEvict: () => void;
  onClean: () => void;
  isEvicting: boolean;
  isCleaning: boolean;
  evictResult: number | null;
  cleanResult: number | null;
}) {
  return (
    <div
      className="rounded-lg border p-4"
      style={{
        backgroundColor: "var(--bg-surface)",
        borderColor: "var(--widget-border)",
      }}
    >
      <div className="flex items-center gap-2 mb-4">
        <Zap size={14} style={{ color: "var(--text-secondary)" }} />
        <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide">
          Actions
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          onClick={onEvict}
          disabled={isEvicting}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors disabled:opacity-50"
          style={{
            backgroundColor: "var(--bg-tertiary)",
            color: "var(--text-primary)",
            border: "1px solid var(--widget-border)",
          }}
        >
          <ArrowUpDown size={12} />
          {isEvicting ? "Evicting..." : "Redis → /dev/shm"}
        </button>
        <button
          onClick={onClean}
          disabled={isCleaning}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors disabled:opacity-50"
          style={{
            backgroundColor: "var(--bg-tertiary)",
            color: "var(--text-primary)",
            border: "1px solid var(--widget-border)",
          }}
        >
          <Trash2 size={12} />
          {isCleaning ? "Cleaning..." : "Clean Stale /dev/shm"}
        </button>
        {evictResult != null && (
          <span className="text-xs text-[var(--accent-primary)] self-center">
            Evicted {evictResult} entries
          </span>
        )}
        {cleanResult != null && (
          <span className="text-xs text-[var(--accent-primary)] self-center">
            Deleted {cleanResult} stale files
          </span>
        )}
      </div>
    </div>
  );
}

export default function StorageAnalyticsPage() {
  const { isLoading: authLoading } = useAuth();
  const queryClient = useQueryClient();

  const {
    data: stats,
    isLoading: statsLoading,
    error: statsError,
    refetch: refetchStats,
  } = useQuery({
    queryKey: ["storageStats"],
    queryFn: getStorageStats,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  const {
    data: health,
    isLoading: healthLoading,
  } = useQuery({
    queryKey: ["tierHealth"],
    queryFn: getTierHealth,
    staleTime: 10_000,
    refetchInterval: 15_000,
  });

  const evictMutation = useMutation({
    mutationFn: triggerEviction,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["storageStats"] }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["tierHealth"] }),
  });

  const cleanMutation = useMutation({
    mutationFn: cleanupStaleShm,
  });

  const [evictResult, setEvictResult] = useState<number | null>(null);
  const [cleanResult, setCleanResult] = useState<number | null>(null);

  const handleEvict = () => {
    evictMutation.mutate(undefined, {
      onSuccess: (data) => setEvictResult(data.evicted),
    });
  };

  const handleClean = () => {
    cleanMutation.mutate(undefined, {
      onSuccess: (data) => setCleanResult(data.deleted),
    });
  };

  const growthPoints = useMemo(
    () => stats?.growth_trend_7d || [],
    [stats]
  );

  if (authLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-sm text-[var(--text-secondary)]">Loading...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen" style={{ backgroundColor: "var(--bg-primary)" }}>
      <div className="max-w-7xl mx-auto px-4 py-6 space-y-4">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-[var(--text-primary)]">
              Storage Analytics
            </h1>
            <p className="text-xs text-[var(--text-tertiary)]">
              Tier health, bucket utilization, lifecycle policies
            </p>
          </div>
          <div className="flex items-center gap-3">
            {health?.warnings && health.warnings.length > 0 ? (
              <div className="flex items-center gap-1.5 text-xs">
                <AlertTriangle size={12} color="var(--warning)" />
                <span style={{ color: "var(--warning)" }}>
                  {health.warnings.length} warning
                  {health.warnings.length > 1 ? "s" : ""}
                </span>
              </div>
            ) : health && !healthLoading ? (
              <div className="flex items-center gap-1.5 text-xs">
                <CheckCircle2 size={12} color="var(--accent-primary)" />
                <span style={{ color: "var(--accent-primary)" }}>All healthy</span>
              </div>
            ) : null}
            <button
              onClick={() => refetchStats()}
              className="p-1.5 rounded hover:bg-[var(--bg-tertiary)] transition-colors"
              title="Refresh"
            >
              <RefreshCw size={14} style={{ color: "var(--text-secondary)" }} />
            </button>
          </div>
        </header>

        {health?.warnings && health.warnings.length > 0 && (
          <div
            className="rounded-lg border px-4 py-3 space-y-1.5"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--widget-border)",
            }}
          >
            {health.warnings.map((w: TierHealthWarning, i: number) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                {w.severity === "critical" ? (
                  <XCircle size={12} color="var(--danger)" />
                ) : (
                  <AlertTriangle size={12} color="var(--warning)" />
                )}
                <span className="text-[var(--text-primary)]">{w.message}</span>
                <span
                  className="text-[10px] uppercase tracking-wide"
                  style={{
                    color:
                      w.severity === "critical"
                        ? "var(--danger)"
                        : "var(--warning)",
                  }}
                >
                  {w.tier} &middot; {w.severity}
                </span>
              </div>
            ))}
          </div>
        )}

        {statsError ? (
          <div
            className="rounded-lg border p-6 text-center"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--widget-border)",
            }}
          >
            <p className="text-sm text-[var(--danger)]">
              Failed to load storage statistics
            </p>
            <button
              onClick={() => refetchStats()}
              className="mt-2 text-xs underline text-[var(--accent-primary)]"
            >
              Retry
            </button>
          </div>
        ) : statsLoading && !stats ? (
          <div className="space-y-4">
            <div
              className="rounded-lg border p-8 animate-pulse"
              style={{
                backgroundColor: "var(--bg-surface)",
                borderColor: "var(--widget-border)",
              }}
            />
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="rounded-lg border p-6 animate-pulse"
                  style={{
                    backgroundColor: "var(--bg-surface)",
                    borderColor: "var(--widget-border)",
                  }}
                />
              ))}
            </div>
          </div>
        ) : (
          <>
            <TierOverview
              hot={stats?.tiers?.hot}
              warm={stats?.tiers?.warm}
              cold={stats?.tiers?.cold}
            />

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {stats?.buckets && <BucketSection buckets={stats.buckets} />}
              {stats?.lifecycle && (
                <LifecycleSection lifecycle={stats.lifecycle} />
              )}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {growthPoints.length > 0 && (
                <div
                  className="rounded-lg border p-4"
                  style={{
                    backgroundColor: "var(--bg-surface)",
                    borderColor: "var(--widget-border)",
                  }}
                >
                  <div className="flex items-center gap-2 mb-4">
                    <Activity
                      size={14}
                      style={{ color: "var(--text-secondary)" }}
                    />
                    <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wide">
                      Data Growth (7d)
                    </span>
                  </div>
                  <Sparkline points={growthPoints} />
                  <div className="mt-2 text-[10px] text-[var(--text-tertiary)]">
                    Total:{" "}
                    {formatBytes(
                      growthPoints.reduce((sum, p) => sum + p.mb * 1_048_576, 0)
                    )}
                  </div>
                </div>
              )}

              {stats?.event_stats && (
                <EventStatsSection events={stats.event_stats} />
              )}
            </div>

            <ActionsPanel
              onEvict={handleEvict}
              onClean={handleClean}
              isEvicting={evictMutation.isPending}
              isCleaning={cleanMutation.isPending}
              evictResult={evictResult}
              cleanResult={cleanResult}
            />
          </>
        )}

        {stats?.generated_at && (
          <div className="text-center text-[10px] text-[var(--text-tertiary)]">
            Generated {new Date(stats.generated_at).toLocaleString()}
          </div>
        )}
      </div>
    </div>
  );
}
