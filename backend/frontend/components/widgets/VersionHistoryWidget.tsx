"use client";

import React, { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { History, Eye, EyeOff, RotateCcw, GitCompare, Search } from "lucide-react";
import { getPipelineVersions, getPipelineDiff, restorePipelineVersion } from "@/lib/api";
import { usePipelineStore } from "@/store/pipelineStore";
import type { PipelineVersion, PipelineDiff } from "@/lib/types";
import { extractPipelineName } from "@/lib/pipeline-yaml";

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function DiffPanel({ diff }: { diff: PipelineDiff }) {
  const added = diff.steps_added.length;
  const removed = diff.steps_removed.length;
  const modified = diff.steps_modified.length;

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      className="border rounded-lg p-3 mt-2 space-y-2"
      style={{ borderColor: "var(--widget-border)", backgroundColor: "var(--bg-surface)" }}
    >
      <div className="text-xs font-medium text-[var(--text-primary)]">
        <span className="text-[var(--accent-success)]">{added} steps added</span>
        {", "}
        <span className="text-[var(--accent-error)]">{removed} steps removed</span>
        {", "}
        <span className="text-[var(--accent-warning)]">{modified} steps modified</span>
      </div>
      {diff.change_summary && (
        <div className="text-xs text-[var(--text-secondary)]">{diff.change_summary}</div>
      )}
      <div>
        <div className="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] mb-1">
          v{diff.version_a} → v{diff.version_b}
        </div>
        <pre className="text-xs font-mono p-2 rounded overflow-auto max-h-48 bg-[var(--widget-bg)] border" style={{ borderColor: "var(--widget-border)" }}>
          <code>{diff.unified_diff}</code>
        </pre>
      </div>
    </motion.div>
  );
}

export function VersionHistoryWidget() {
  const [pipelineName, setPipelineName] = useState("");
  const [versions, setVersions] = useState<PipelineVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedVersion, setExpandedVersion] = useState<number | null>(null);
  const [selectedVersions, setSelectedVersions] = useState<Set<number>>(new Set());
  const [diff, setDiff] = useState<PipelineDiff | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [restoringVersion, setRestoringVersion] = useState<number | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [autoLoadedPipelineName, setAutoLoadedPipelineName] = useState<string | null>(null);

  const { lastYamlConfig, setLastYamlConfig } = usePipelineStore();

  const loadVersions = useCallback(async (name: string) => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      return;
    }
    setLoading(true);
    setHasSearched(true);
    setError(null);
    setVersions([]);
    setDiff(null);
    setSelectedVersions(new Set());
    try {
      const data = await getPipelineVersions(trimmedName);
      setVersions(data);
    } catch (e: any) {
      setError(e.message || "Failed to load versions");
    } finally {
      setLoading(false);
    }
  }, []);

  const handleLoad = async () => {
    await loadVersions(pipelineName);
  };

  useEffect(() => {
    const detectedPipelineName = extractPipelineName(lastYamlConfig);
    if (!detectedPipelineName || detectedPipelineName === autoLoadedPipelineName) {
      return;
    }

    setPipelineName((currentName) => currentName || detectedPipelineName);
    setAutoLoadedPipelineName(detectedPipelineName);
    void loadVersions(detectedPipelineName);
  }, [lastYamlConfig, autoLoadedPipelineName, loadVersions]);

  const toggleSelect = (v: number) => {
    setSelectedVersions((prev) => {
      const next = new Set(prev);
      if (next.has(v)) {
        next.delete(v);
      } else {
        if (next.size >= 2) return prev;
        next.add(v);
      }
      return next;
    });
    setDiff(null);
  };

  const handleCompare = async () => {
    const sorted = Array.from(selectedVersions).sort((a, b) => a - b);
    if (sorted.length !== 2) return;
    setDiffLoading(true);
    try {
      const result = await getPipelineDiff(pipelineName.trim(), sorted[0], sorted[1]);
      setDiff(result);
    } catch (e: any) {
      setError(e.message || "Failed to load diff");
    } finally {
      setDiffLoading(false);
    }
  };

  const handleRestore = async (version: number) => {
    setRestoringVersion(version);
    try {
      const result = await restorePipelineVersion(pipelineName.trim(), version);
      setLastYamlConfig(result.yaml_config);
    } catch (e: any) {
      setError(e.message || "Failed to restore version");
    } finally {
      setRestoringVersion(null);
    }
  };

  const sorted = [...versions].sort((a, b) => b.version_number - a.version_number);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: "var(--widget-border)" }}>
        <History className="w-4 h-4 text-[var(--accent-primary)]" />
        <span className="text-xs font-bold tracking-wide text-[var(--text-primary)]">Pipeline Versions</span>
      </div>

      {/* Search */}
      <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: "var(--widget-border)" }}>
        <label htmlFor="version-history-pipeline-name" className="sr-only">
          Pipeline name
        </label>
        <input
          id="version-history-pipeline-name"
          name="pipelineName"
          type="text"
          value={pipelineName}
          onChange={(e) => setPipelineName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleLoad()}
          placeholder="Pipeline name..."
          className="flex-1 text-sm px-2 py-1 rounded bg-[var(--bg-surface)] border text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] outline-none focus:border-[var(--accent-primary)]"
          style={{ borderColor: "var(--widget-border)" }}
        />
        <button
          onClick={handleLoad}
          disabled={loading || !pipelineName.trim()}
          className="px-3 py-1 text-xs font-medium rounded bg-[var(--accent-primary)] text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {loading ? "..." : "Load"}
        </button>
      </div>
      <div className="px-3 py-1 text-[10px] text-[var(--text-secondary)] border-b" style={{ borderColor: "var(--widget-border)" }}>
        Searching by <span className="font-mono">pipeline.name</span> from your YAML.
      </div>

      {error && (
        <div className="px-3 py-1.5 text-xs text-[var(--accent-error)] bg-[var(--accent-error)]/10">
          {error}
        </div>
      )}

      {/* Compare bar */}
      {selectedVersions.size === 2 && (
        <div className="flex items-center gap-2 px-3 py-1.5 border-b" style={{ borderColor: "var(--widget-border)", backgroundColor: "var(--bg-surface)" }}>
          <GitCompare className="w-3.5 h-3.5 text-[var(--accent-secondary)]" />
          <span className="text-xs text-[var(--text-secondary)]">
            Comparing v{Array.from(selectedVersions).sort((a, b) => a - b).join(" ↔ v")}
          </span>
          <button
            onClick={handleCompare}
            disabled={diffLoading}
            className="ml-auto px-2 py-0.5 text-xs font-medium rounded bg-[var(--accent-secondary)] text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {diffLoading ? "..." : "Compare"}
          </button>
        </div>
      )}

      {/* Version list */}
      <div className="flex-1 overflow-y-auto">
        {sorted.length === 0 && !loading && !error && (
          <div className="flex flex-col items-center justify-center h-full text-[var(--text-secondary)] text-sm gap-1">
            <Search className="w-5 h-5" />
            <span>
              {hasSearched
                ? `No versions found for "${pipelineName.trim()}".`
                : "Enter a pipeline name to view versions"}
            </span>
          </div>
        )}
        <AnimatePresence>
          {sorted.map((v) => (
            <motion.div
              key={v.version_number}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              className="border-b last:border-0 px-3 py-2"
              style={{ borderColor: "var(--widget-border)" }}
            >
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  name={`compare-version-${v.version_number}`}
                  aria-label={`Select version ${v.version_number} for comparison`}
                  checked={selectedVersions.has(v.version_number)}
                  onChange={() => toggleSelect(v.version_number)}
                  className="accent-[var(--accent-primary)] w-3.5 h-3.5"
                />
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-[var(--accent-primary)]/20 text-[var(--accent-primary)]">
                  v{v.version_number}
                </span>
                <span className="text-[10px] text-[var(--text-secondary)] whitespace-nowrap">
                  {relativeTime(v.created_at)}
                </span>
                <span
                  className="flex-1 text-xs text-[var(--text-secondary)] truncate"
                  title={v.change_summary ?? ""}
                >
                  {(v.change_summary ?? "").length > 60 ? (v.change_summary ?? "").slice(0, 60) + "…" : (v.change_summary ?? "")}
                </span>
                <button
                  onClick={() => setExpandedVersion(expandedVersion === v.version_number ? null : v.version_number)}
                  className="p-1 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]"
                  title="View YAML"
                >
                  {expandedVersion === v.version_number ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                </button>
                <button
                  onClick={() => handleRestore(v.version_number)}
                  disabled={restoringVersion === v.version_number}
                  className="p-1 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--accent-success)] disabled:opacity-50 transition-colors"
                  title="Restore this version"
                >
                  <RotateCcw className={`w-3.5 h-3.5 ${restoringVersion === v.version_number ? "animate-spin" : ""}`} />
                </button>
              </div>

              {/* Expanded YAML */}
              <AnimatePresence>
                {expandedVersion === v.version_number && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    className="mt-2"
                  >
                    <pre className="text-xs font-mono p-2 rounded overflow-auto max-h-48 bg-[var(--bg-surface)] border" style={{ borderColor: "var(--widget-border)" }}>
                      <code>{v.yaml_config || "YAML content unavailable for this version."}</code>
                    </pre>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          ))}
        </AnimatePresence>

        {/* Diff panel */}
        <AnimatePresence>
          {diff && (
            <div className="px-3 pb-3">
              <DiffPanel diff={diff} />
            </div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
