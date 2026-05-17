"use client";

import React, { useMemo, useState } from "react";
import type { UploadedFile, AIRepairDiffLine } from "@/lib/types";
import { Sparkles, Wand2, X } from "lucide-react";

interface AIGeneratePipelineModalProps {
  isOpen: boolean;
  files: UploadedFile[];
  isSubmitting: boolean;
  error: string | null;
  onClose: () => void;
  onGenerate: (description: string, fileIds: string[]) => void;
}

export function AIGeneratePipelineModal({
  isOpen,
  files,
  isSubmitting,
  error,
  onClose,
  onGenerate,
}: AIGeneratePipelineModalProps) {
  const [description, setDescription] = useState("");
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>(() => files.slice(0, 1).map((file) => file.id));

  const handleClose = () => {
    setDescription("");
    setSelectedFileIds(files.slice(0, 1).map((file) => file.id));
    onClose();
  };

  const canSubmit = description.trim().length >= 10 && selectedFileIds.length > 0;

  if (!isOpen) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-[110] flex items-center justify-center bg-black/50 backdrop-blur-sm"
      data-testid="ai-generate-modal"
    >
      <div
        className="w-full max-w-2xl rounded-xl border bg-[var(--bg-surface)] shadow-2xl"
        style={{ borderColor: "var(--widget-border)" }}
      >
        <div
          className="flex items-center justify-between border-b px-4 py-3"
          style={{ borderColor: "var(--widget-border)" }}
        >
          <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
            <Sparkles className="h-4 w-4 text-[var(--accent-primary)]" />
            Generate Pipeline with AI
          </h2>
          <button
            onClick={handleClose}
            className="rounded p-1 text-[var(--text-secondary)] hover:bg-[var(--interactive-hover)]"
            data-testid="ai-modal-close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3 p-4">
          <div className="space-y-1">
            <label className="text-xs font-medium text-[var(--text-secondary)]">Describe what you want</label>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Example: Load sales data, keep delivered orders, aggregate revenue by region, sort descending, then save."
              className="h-28 w-full rounded border bg-[var(--bg-base)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none"
              style={{ borderColor: "var(--widget-border)" }}
              data-testid="ai-description-input"
            />
          </div>

          <div className="space-y-2">
            <div className="text-xs font-medium text-[var(--text-secondary)]">Allow AI to use these files</div>
            <div className="max-h-44 space-y-1 overflow-y-auto rounded border p-2" style={{ borderColor: "var(--widget-border)" }}>
              {files.length === 0 ? (
                <div className="text-xs text-[var(--text-secondary)]">Upload at least one file first.</div>
              ) : (
                files.map((file) => {
                  const checked = selectedFileIds.includes(file.id);
                  return (
                    <label
                      key={file.id}
                      className="flex cursor-pointer items-start gap-2 rounded px-2 py-1 hover:bg-[var(--interactive-hover)]"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          setSelectedFileIds((prev) =>
                            checked ? prev.filter((id) => id !== file.id) : [...prev, file.id]
                          );
                        }}
                        className="mt-0.5"
                      />
                      <div className="min-w-0">
                        <div className="truncate text-xs text-[var(--text-primary)]">{file.original_filename}</div>
                        <div className="text-[10px] text-[var(--text-secondary)]">
                          {file.row_count ?? 0} rows • {file.column_count} cols
                        </div>
                      </div>
                    </label>
                  );
                })
              )}
            </div>
          </div>

          {error && (
            <div
              className="rounded border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-3 py-2 text-xs text-[var(--accent-error)]"
              data-testid="ai-modal-error"
            >
              {error}
            </div>
          )}
        </div>

        <div
          className="flex items-center justify-end gap-2 border-t px-4 py-3"
          style={{ borderColor: "var(--widget-border)" }}
        >
          <button
            onClick={handleClose}
            className="rounded border px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:bg-[var(--interactive-hover)]"
            style={{ borderColor: "var(--widget-border)" }}
          >
            Cancel
          </button>
          <button
            onClick={() => onGenerate(description.trim(), selectedFileIds)}
            disabled={isSubmitting || !canSubmit}
            className="rounded border px-3 py-1.5 text-xs font-medium text-[var(--bg-base)] disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              borderColor: "color-mix(in srgb, var(--accent-primary) 80%, var(--widget-border))",
              backgroundColor: "var(--accent-primary)",
            }}
            data-testid="ai-generate-btn"
          >
            <span className="inline-flex items-center gap-1.5">
              <Wand2 className="h-3.5 w-3.5" />
              {isSubmitting ? "Generating..." : "Generate YAML"}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}

interface AIRepairDiffModalProps {
  isOpen: boolean;
  correctedYaml: string;
  diffLines: AIRepairDiffLine[];
  valid: boolean;
  error: string | null;
  isApplying: boolean;
  onClose: () => void;
  onApply: (yaml: string) => void;
}

export function AIRepairDiffModal({
  isOpen,
  correctedYaml,
  diffLines,
  valid,
  error,
  isApplying,
  onClose,
  onApply,
}: AIRepairDiffModalProps) {
  const rows = useMemo(() => diffLines.slice(0, 400), [diffLines]);
  const statusMessage = error
    ? error
    : valid
      ? "Corrected YAML validated successfully."
      : "AI produced YAML but validation still failed.";

  if (!isOpen) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div
        className="w-full max-w-3xl rounded-xl border bg-[var(--bg-surface)] shadow-2xl"
        style={{ borderColor: "var(--widget-border)" }}
      >
        <div
          className="flex items-center justify-between border-b px-4 py-3"
          style={{ borderColor: "var(--widget-border)" }}
        >
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">AI Repair Diff</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-[var(--text-secondary)] hover:bg-[var(--interactive-hover)]"
            data-testid="ai-repair-close-btn"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-2 p-4">
          <div className="text-xs">
            <span className={error || !valid ? "text-[var(--accent-error)]" : "text-[var(--accent-success)]"}>
              {statusMessage}
            </span>
          </div>
          <div className="max-h-80 overflow-y-auto rounded border bg-[var(--bg-base)]" style={{ borderColor: "var(--widget-border)" }}>
            <pre className="p-3 text-xs">
              {rows.length === 0 ? (
                <span className="text-[var(--text-secondary)]">No diff was generated.</span>
              ) : (
                rows.map((line, index) => {
                  const prefix = line.type === "added" ? "+" : line.type === "removed" ? "-" : " ";
                  const colorClass =
                    line.type === "added"
                      ? "text-[var(--accent-success)]"
                      : line.type === "removed"
                      ? "text-[var(--accent-error)]"
                      : "text-[var(--text-primary)]";
                  return (
                    <div key={`${index}-${line.content}`} className={colorClass}>
                      {prefix} {line.content}
                    </div>
                  );
                })
              )}
            </pre>
          </div>
        </div>

        <div
          className="flex items-center justify-end gap-2 border-t px-4 py-3"
          style={{ borderColor: "var(--widget-border)" }}
        >
          <button
            onClick={onClose}
            className="rounded border px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:bg-[var(--interactive-hover)]"
            style={{ borderColor: "var(--widget-border)" }}
          >
            Cancel
          </button>
          <button
            onClick={() => onApply(correctedYaml)}
            disabled={isApplying || !correctedYaml || !valid}
            className="rounded border px-3 py-1.5 text-xs font-medium text-[var(--bg-base)] disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              borderColor: "color-mix(in srgb, var(--accent-primary) 80%, var(--widget-border))",
              backgroundColor: "var(--accent-primary)",
            }}
            data-testid="ai-repair-apply-btn"
          >
            {isApplying ? "Applying..." : "Apply to Editor"}
          </button>
        </div>
      </div>
    </div>
  );
}
