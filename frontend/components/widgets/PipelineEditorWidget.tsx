"use client";

import React, { useState, useEffect, useCallback } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { validatePipeline, runPipeline, getFiles } from "@/lib/api";
import { usePipelineStore } from "@/store/pipelineStore";
import { CheckCircle, XCircle, Play, RefreshCw, FileText, Plus } from "lucide-react";
import { ValidationResult } from "@/lib/types";

export function PipelineEditorWidget() {
  const queryClient = useQueryClient();
  const { lastYamlConfig, setLastYamlConfig, setActiveRunId, setActiveRun } = usePipelineStore();
  const [code, setCode] = useState(lastYamlConfig);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [isValidating, setIsValidating] = useState(false);

  const { data: files } = useQuery({ queryKey: ["files"], queryFn: getFiles });

  const validateMutation = useMutation({
    mutationFn: validatePipeline,
    onSuccess: (data) => setValidation(data),
    onSettled: () => setIsValidating(false),
  });

  const runMutation = useMutation({
    mutationFn: (yamlConfig: string) => runPipeline(yamlConfig, "My Pipeline"),
    onSuccess: (data) => {
      // Set initial activeRun so RunMonitor renders immediately
      setActiveRun({
        id: data.run_id,
        name: "My Pipeline",
        status: "PENDING",
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
        total_rows_in: null,
        total_rows_out: null,
        error_message: null,
        duration_ms: null,
        step_results: [],
      });
      setActiveRunId(data.run_id);
      // Immediately refresh run history so the new run appears
      queryClient.invalidateQueries({ queryKey: ["pipelineRuns"] });
    },
  });

  useEffect(() => {
    setIsValidating(true);
    const timer = setTimeout(() => {
      validateMutation.mutate(code);
      setLastYamlConfig(code);
    }, 800);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  // Handle Ctrl+Enter to trigger pipeline:run event
  useEffect(() => {
    const handleRun = () => {
      if (validation?.is_valid) {
        runMutation.mutate(code);
      }
    };
    window.addEventListener("pipeline:run", handleRun);
    return () => window.removeEventListener("pipeline:run", handleRun);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, validation]);

  const insertFileId = useCallback((id: string) => {
    setCode((prev) => {
      const placeholder = 'file_id: ""';
      const idx = prev.lastIndexOf(placeholder);
      if (idx !== -1) {
        return prev.substring(0, idx) + `file_id: "${id}"` + prev.substring(idx + placeholder.length);
      }
      return prev.replace(/file_id:\s*"[^"]*"/, `file_id: "${id}"`);
    });
  }, []);

  return (
    <div className="flex h-full overflow-hidden">
      <div className="w-[60%] flex flex-col border-r" style={{ borderColor: "var(--widget-border)" }}>
        <div className="flex-1 overflow-auto bg-[var(--bg-base)]">
          <CodeMirror
            value={code}
            height="100%"
            extensions={[yaml()]}
            theme="dark"
            onChange={(value) => setCode(value)}
            className="text-sm font-mono"
            style={{ backgroundColor: "var(--bg-base)" }}
          />
        </div>
        <div className="p-2 border-t flex items-center justify-between bg-[var(--bg-surface)]" style={{ borderColor: "var(--widget-border)" }}>
          <div className="flex items-center gap-2">
            <button 
              onClick={() => validateMutation.mutate(code)}
              className="px-3 py-1.5 rounded text-xs font-medium border hover:bg-[var(--interactive-hover)] text-[var(--text-primary)] transition-colors"
              style={{ borderColor: "var(--widget-border)" }}
            >
              Validate
            </button>
            <button 
              onClick={() => setCode("pipeline:\n  name: example\n  steps:\n    - name: load_data\n      type: load\n      file_id: \"\"\n")}
              className="px-3 py-1.5 rounded text-xs font-medium border hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] transition-colors"
              style={{ borderColor: "var(--widget-border)" }}
            >
              Clear
            </button>
          </div>
          <button 
            onClick={() => runMutation.mutate(code)}
            disabled={!validation?.is_valid || runMutation.isPending}
            className="flex items-center gap-2 px-4 py-1.5 rounded text-xs font-medium bg-[var(--accent-primary)] text-[var(--bg-base)] hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {runMutation.isPending ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
            Run Pipeline
          </button>
        </div>
      </div>

      <div className="w-[40%] flex flex-col bg-[var(--bg-surface)] overflow-hidden">
        <div className="p-3 border-b" style={{ borderColor: "var(--widget-border)" }}>
          <h3 className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-2">Validation</h3>
          {isValidating ? (
            <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <RefreshCw className="w-4 h-4 animate-spin" /> Validating...
            </div>
          ) : validation?.is_valid ? (
            <div className="flex items-center gap-2 text-sm text-[var(--accent-success)] font-medium">
              <CheckCircle className="w-4 h-4" /> Valid Pipeline
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2 text-sm text-[var(--accent-error)] font-medium">
                <XCircle className="w-4 h-4" /> {validation?.errors.length || 0} Errors
              </div>
              <div className="max-h-32 overflow-y-auto space-y-2 pr-1">
                {validation?.errors.map((err, i) => (
                  <div key={i} className="p-2 rounded bg-[var(--bg-elevated)] border border-[var(--accent-error)]/30">
                    <div className="text-xs font-medium text-[var(--accent-error)] mb-1">
                      {err.step_name ? `Step: ${err.step_name}` : "Global"} - {err.field}
                    </div>
                    <div className="text-xs text-[var(--text-secondary)]">{err.message}</div>
                    {err.suggestion && <div className="text-[10px] text-[var(--accent-warning)] mt-1">Tip: {err.suggestion}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="flex-1 p-3 overflow-y-auto">
          <h3 className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-2">Available Files</h3>
          {files?.length === 0 ? (
            <div className="text-xs text-[var(--text-secondary)] italic">No files uploaded yet.</div>
          ) : (
            <div className="space-y-2">
              {files?.map((file) => (
                <div key={file.id} className="p-2 rounded border bg-[var(--bg-elevated)] group" style={{ borderColor: "var(--widget-border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-1.5 text-sm font-medium text-[var(--text-primary)] truncate">
                      <FileText className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                      <span className="truncate" title={file.original_filename}>{file.original_filename}</span>
                    </div>
                    <button 
                      onClick={() => insertFileId(file.id)}
                      className="p-1 rounded bg-[var(--interactive-hover)] text-[var(--accent-primary)] hover:bg-[var(--accent-primary)] hover:text-[var(--bg-base)] transition-colors opacity-0 group-hover:opacity-100"
                      title="Insert ID"
                    >
                      <Plus className="w-3 h-3" />
                    </button>
                  </div>
                  <div className="text-[10px] font-mono text-[var(--text-secondary)] truncate">
                    ID: {file.id}
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {file.columns?.slice(0, 3).map((col) => (
                      <span key={col} className="px-1.5 py-0.5 rounded bg-[var(--bg-surface)] border text-[9px] font-mono text-[var(--text-secondary)]" style={{ borderColor: "var(--widget-border)" }}>
                        {col}
                      </span>
                    ))}
                    {file.columns && file.columns.length > 3 && (
                      <span className="px-1.5 py-0.5 rounded bg-[var(--bg-surface)] border text-[9px] font-mono text-[var(--text-secondary)]" style={{ borderColor: "var(--widget-border)" }}>
                        +{file.columns.length - 3} more
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
