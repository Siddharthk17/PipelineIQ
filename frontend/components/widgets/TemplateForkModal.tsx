"use client";

import React, { useEffect, useState } from "react";
import { X } from "lucide-react";
import {
  getTemplateDetail,
  forkTemplate,
} from "@/lib/api";
import type { UploadedFile } from "@/lib/types";

interface RequiredFile {
  placeholder: string;
  description?: string;
}

interface TemplateForkModalProps {
  templateId: string;
  availableFiles: UploadedFile[];
  onApply: (yaml: string, pipelineName: string) => void;
  onClose: () => void;
}

/** Fill a template's {{file_id}} placeholders by picking real uploaded files. */
export function TemplateForkModal({
  templateId,
  availableFiles,
  onApply,
  onClose,
}: TemplateForkModalProps) {
  const [templateName, setTemplateName] = useState(templateId);
  const [requiredFiles, setRequiredFiles] = useState<RequiredFile[]>([]);
  const [mappings, setMappings] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getTemplateDetail(templateId)
      .then((detail) => {
        if (cancelled) return;
        // Suggest a room/filesystem-safe name; the user can still edit it.
        setTemplateName(
          (detail.name || templateId).trim().replace(/\s+/g, "_"),
        );
        setRequiredFiles(
          ((detail as unknown as { required_files?: RequiredFile[] }).required_files || []),
        );
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load template");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [templateId]);

  const canSubmit =
    !loading &&
    !submitting &&
    templateName.trim().length > 0 &&
    requiredFiles.every((rf) => mappings[rf.placeholder]);

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await forkTemplate(templateId, {
        pipeline_name: templateName.trim(),
        file_mappings: mappings,
      });
      onApply(result.yaml, result.pipeline_name);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to fork template");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-label="Use template"
    >
      <div
        className="w-full max-w-md rounded-lg border bg-[var(--bg-surface)] p-5"
        style={{ borderColor: "var(--widget-border)" }}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">
            Use template
          </h3>
          <button
            onClick={onClose}
            aria-label="Close template dialog"
            className="rounded p-1 text-[var(--text-secondary)] hover:bg-[var(--interactive-hover)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {loading ? (
          <p className="text-xs text-[var(--text-secondary)]">Loading template…</p>
        ) : (
          <div className="space-y-3">
            <label className="block text-xs text-[var(--text-secondary)]">
              Pipeline name
              <input
                value={templateName}
                onChange={(e) => setTemplateName(e.target.value)}
                className="mt-1 w-full rounded border bg-[var(--bg-base)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                style={{ borderColor: "var(--widget-border)" }}
              />
            </label>
            {requiredFiles.map((rf) => (
              <label key={rf.placeholder} className="block text-xs text-[var(--text-secondary)]">
                File for <span className="font-mono">{"{{"}{rf.placeholder}{"}}"}</span>
                {rf.description ? ` — ${rf.description}` : ""}
                <select
                  value={mappings[rf.placeholder] || ""}
                  onChange={(e) =>
                    setMappings((prev) => ({ ...prev, [rf.placeholder]: e.target.value }))
                  }
                  className="mt-1 w-full rounded border bg-[var(--bg-base)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                  style={{ borderColor: "var(--widget-border)" }}
                >
                  <option value="">Select a file…</option>
                  {availableFiles.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.original_filename} ({f.id.slice(0, 8)}…)
                    </option>
                  ))}
                </select>
              </label>
            ))}
            {error && (
              <p className="text-xs text-[var(--accent-error)]" role="alert">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={onClose}
                className="rounded border px-3 py-1.5 text-xs text-[var(--text-primary)] hover:bg-[var(--interactive-hover)]"
                style={{ borderColor: "var(--widget-border)" }}
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={!canSubmit}
                className="rounded border px-3 py-1.5 text-xs transition-colors hover:bg-[var(--interactive-hover)] disabled:opacity-50"
                style={{
                  borderColor: "var(--accent-primary)",
                  color: "var(--accent-primary)",
                }}
              >
                {submitting ? "Loading…" : "Use template"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
