"use client";

import React, { useState, useEffect, useCallback, useMemo } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";
import { EditorView } from "@codemirror/view";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { validatePipeline, runPipeline, getFiles, getPipelinePlan, previewPipelineStep } from "@/lib/api";
import { usePipelineStore } from "@/store/pipelineStore";
import { useThemeStore } from "@/store/themeStore";
import { CheckCircle, XCircle, Play, RefreshCw, FileText, Plus, X, Eye } from "lucide-react";
import { ValidationResult, ExecutionPlan, PipelinePreview } from "@/lib/types";
import { StepDAG } from "./StepDAG";
import { ConfigPanel, PipelineCanvas, StepPalette } from "@/components/pipeline-builder";
import { usePipelineEditor } from "@/hooks/usePipelineEditor";
import {
  DEFAULT_PIPELINE_YAML,
  extractPipelineName,
  hasNonEmptyFileId,
  removeFileIdLines,
  upsertFileIdInFirstLoadStep,
} from "@/lib/pipeline-yaml";

interface PipelineEditorWidgetProps {
  initialMode?: "yaml" | "visual";
}

export function PipelineEditorWidget({ initialMode = "yaml" }: PipelineEditorWidgetProps) {
  const queryClient = useQueryClient();
  const { lastYamlConfig, setLastYamlConfig, setActiveRunId, setActiveRun } = usePipelineStore();
  const { activeTheme } = useThemeStore();
  const [code, setCode] = useState(lastYamlConfig || DEFAULT_PIPELINE_YAML);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [plan, setPlan] = useState<ExecutionPlan | null>(null);
  const [isPlanLoading, setIsPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PipelinePreview | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [editorMode, setEditorMode] = useState<"yaml" | "visual">(initialMode);

  const { data: files } = useQuery({ queryKey: ["files"], queryFn: getFiles });

  const availableFiles = files ?? [];

  const {
    pipelineName,
    setPipelineName,
    nodes,
    edges,
    configuringNodeId,
    parseError: builderParseError,
    toastMessage: builderToastMessage,
    onNodesChange,
    onEdgesChange,
    handleConnect,
    handleDragStart,
    handleDrop,
    handleAddStep,
    handleConfigure,
    handleConfigClose,
    handleConfigSave,
    handleDeleteNode,
    handleDeleteEdge,
    handleYamlChange,
    getAvailableColumns,
  } = usePipelineEditor({
    yamlText: code,
    onYamlTextChange: setCode,
    availableFiles,
  });

  const configuringNode = useMemo(
    () => nodes.find((node) => node.id === configuringNodeId) ?? null,
    [configuringNodeId, nodes],
  );
  const configColumns = useMemo(
    () => (configuringNodeId ? getAvailableColumns(configuringNodeId) : []),
    [configuringNodeId, getAvailableColumns],
  );

  const validateMutation = useMutation({
    mutationFn: validatePipeline,
    onSuccess: (data) => setValidation(data),
    onSettled: () => setIsValidating(false),
  });

  const runMutation = useMutation({
    mutationFn: (args: { yamlConfig: string; pipelineName?: string }) =>
      runPipeline(args.yamlConfig, args.pipelineName),
    onSuccess: (data, variables) => {
      const runName =
        variables.pipelineName ||
        extractPipelineName(variables.yamlConfig) ||
        "Pipeline Run";
      // Set initial activeRun so RunMonitor renders immediately
      setActiveRun({
        id: data.run_id,
        name: runName,
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

  const latestFileId = files?.[0]?.id ?? null;

  useEffect(() => {
    if (!files || files.length > 0) {
      return;
    }
    setCode((previousCode) => removeFileIdLines(previousCode));
  }, [files]);

  useEffect(() => {
    if (!latestFileId) {
      return;
    }
    setCode((previousCode) => {
      if (hasNonEmptyFileId(previousCode)) {
        return previousCode;
      }
      return upsertFileIdInFirstLoadStep(previousCode, latestFileId);
    });
  }, [latestFileId]);

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
        const pipelineNameValue =
          (editorMode === "visual" ? pipelineName : extractPipelineName(code)) ||
          extractPipelineName(code) ||
          undefined;
        runMutation.mutate({ yamlConfig: code, pipelineName: pipelineNameValue });
      }
    };
    window.addEventListener("pipeline:run", handleRun);
    return () => window.removeEventListener("pipeline:run", handleRun);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, editorMode, pipelineName, validation]);

  const insertFileId = useCallback((id: string) => {
    setCode((prev) => upsertFileIdInFirstLoadStep(prev, id));
  }, []);

  const handlePlan = useCallback(async () => {
    setIsPlanLoading(true);
    setPlanError(null);
    try {
      const result = await getPipelinePlan(code);
      setPlan(result);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to generate plan";
      setPlanError(message);
      setPlan(null);
    } finally {
      setIsPlanLoading(false);
    }
  }, [code]);

  const handlePreview = useCallback(async () => {
    setIsPreviewLoading(true);
    setPreviewError(null);
    try {
      const result = await previewPipelineStep(code, 0);
      setPreview(result);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to load preview";
      setPreviewError(message);
      setPreview(null);
    } finally {
      setIsPreviewLoading(false);
    }
  }, [code]);

  const previewColumns = preview?.columns ?? [];
  const previewRows = preview?.data ?? [];

  const isLightTheme = activeTheme.includes("light");
  const editorExtensions = useMemo(() => {
    const baseExtensions = [
      yaml(),
      EditorView.contentAttributes.of({ "aria-label": "Pipeline YAML editor" }),
    ];

    if (isLightTheme) {
      return baseExtensions;
    }

    // Keep dark mode readable while avoiding low-contrast token styles in the embedded editor.
    return [
      ...baseExtensions,
      EditorView.theme({
        ".cm-content": { color: "var(--text-primary)" },
        ".cm-content span": { color: "var(--text-primary)" },
        ".cm-gutters": {
          color: "var(--text-primary)",
          backgroundColor: "var(--bg-surface)",
          borderRightColor: "var(--widget-border)",
        },
        ".cm-gutterElement": { color: "var(--text-primary)" },
        ".cm-activeLineGutter": { color: "var(--text-primary)" },
      }),
    ];
  }, [isLightTheme]);

  return (
    <div className="flex h-full min-w-0 overflow-hidden" data-testid="pipeline-editor-widget">
      <div
        className="flex min-w-0 basis-[62%] flex-col border-r overflow-hidden"
        style={{ borderColor: "var(--widget-border)" }}
      >
        <div className="flex-1 overflow-hidden bg-[var(--bg-base)]">
          {editorMode === "yaml" ? (
            <div className="h-full overflow-auto" data-testid="yaml-editor">
              <CodeMirror
                value={code}
                height="100%"
                extensions={editorExtensions}
                theme={isLightTheme ? "light" : "dark"}
                onChange={(value) => handleYamlChange(value)}
                className="text-sm font-mono"
                style={{ backgroundColor: "var(--bg-base)" }}
              />
            </div>
          ) : (
            <div className="relative flex h-full min-w-0 overflow-hidden bg-[var(--bg-base)]">
              <StepPalette onDragStart={handleDragStart} onAddStep={handleAddStep} />
              <div className="flex min-w-0 flex-1 flex-col gap-2 p-2">
                <div className="flex items-center gap-2 rounded-md border bg-[var(--bg-surface)] px-2 py-1.5">
                  <label
                    htmlFor="pipeline-name-input"
                    className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]"
                  >
                    Pipeline
                  </label>
                  <input
                    id="pipeline-name-input"
                    name="pipelineName"
                    data-testid="pipeline-name-input"
                    value={pipelineName}
                    onChange={(event) => setPipelineName(event.target.value)}
                    className="min-w-0 flex-1 rounded border bg-[var(--bg-base)] px-2 py-1 text-xs"
                    style={{ borderColor: "var(--widget-border)" }}
                  />
                </div>
                <div className="min-h-0 flex-1">
                  <PipelineCanvas
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onConnect={handleConnect}
                    onDropStep={handleDrop}
                    onConfigureNode={handleConfigure}
                    onDeleteNode={handleDeleteNode}
                    onDeleteEdge={handleDeleteEdge}
                  />
                </div>
                {builderParseError && (
                  <div className="rounded border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-2 py-1 text-[11px] text-[var(--accent-error)]">
                    Builder sync warning: {builderParseError}
                  </div>
                )}
              </div>
              {configuringNode && (
                <ConfigPanel
                  key={configuringNode.id}
                  node={configuringNode}
                  availableFiles={availableFiles}
                  availableColumns={configColumns}
                  onSave={handleConfigSave}
                  onDelete={handleDeleteNode}
                  onClose={handleConfigClose}
                />
              )}
              {builderToastMessage && (
                <div className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded border bg-[var(--bg-elevated)] px-3 py-1.5 text-xs text-[var(--text-primary)]">
                  {builderToastMessage}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="border-t bg-[var(--bg-surface)] p-2" style={{ borderColor: "var(--widget-border)" }}>
          <div className="overflow-x-auto pb-1">
            <div className="flex w-max items-center gap-2 pr-2">
              <div className="mr-2 flex items-center gap-2">
                <button
                  onClick={() => setEditorMode("yaml")}
                  data-testid="mode-yaml-btn"
                  className={`min-h-10 shrink-0 rounded border px-3 py-1.5 text-xs font-medium transition-colors ${
                    editorMode === "yaml"
                      ? "bg-[var(--accent-primary)] text-[var(--bg-base)]"
                      : "text-[var(--text-primary)] hover:bg-[var(--interactive-hover)]"
                  }`}
                  style={{ borderColor: "var(--widget-border)" }}
                >
                  YAML
                </button>
                <button
                  onClick={() => setEditorMode("visual")}
                  data-testid="mode-visual-btn"
                  className={`min-h-10 shrink-0 rounded border px-3 py-1.5 text-xs font-medium transition-colors ${
                    editorMode === "visual"
                      ? "bg-[var(--accent-primary)] text-[var(--bg-base)]"
                      : "text-[var(--text-primary)] hover:bg-[var(--interactive-hover)]"
                  }`}
                  style={{ borderColor: "var(--widget-border)" }}
                >
                  Visual
                </button>
              </div>
              <button
                onClick={() => validateMutation.mutate(code)}
                data-testid="validate-pipeline-btn"
                className="min-h-10 shrink-0 rounded border px-3 py-1.5 text-xs font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--interactive-hover)]"
                style={{ borderColor: "var(--widget-border)" }}
              >
                Validate
              </button>
              <button
                onClick={() =>
                  setCode(
                    latestFileId
                      ? upsertFileIdInFirstLoadStep(DEFAULT_PIPELINE_YAML, latestFileId)
                      : DEFAULT_PIPELINE_YAML
                  )
                }
                data-testid="clear-editor-btn"
                className="min-h-10 shrink-0 rounded border px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--interactive-hover)]"
                style={{ borderColor: "var(--widget-border)" }}
              >
                Clear
              </button>
              <button
                onClick={handlePlan}
                disabled={isPlanLoading}
                data-testid="plan-pipeline-btn"
                className="min-h-10 shrink-0 rounded border px-3 py-1.5 text-xs font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--interactive-hover)] disabled:cursor-not-allowed disabled:opacity-50"
                style={{ borderColor: "var(--widget-border)" }}
              >
                <span className="inline-flex items-center gap-1.5">
                  {isPlanLoading ? <RefreshCw className="h-3 w-3 animate-spin" /> : <span>▷</span>}
                  Plan
                </span>
              </button>
              <button
                onClick={handlePreview}
                disabled={isPreviewLoading}
                data-testid="preview-pipeline-btn"
                className="min-h-10 shrink-0 rounded border px-3 py-1.5 text-xs font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--interactive-hover)] disabled:cursor-not-allowed disabled:opacity-50"
                style={{ borderColor: "var(--widget-border)" }}
              >
                <span className="inline-flex items-center gap-1.5">
                  {isPreviewLoading ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Eye className="h-3 w-3" />}
                  Preview
                </span>
              </button>
            </div>
          </div>

          <div className="mt-1.5 flex justify-end">
            <button
              onClick={() => {
                const pipelineNameValue =
                  (editorMode === "visual" ? pipelineName : extractPipelineName(code)) ||
                  extractPipelineName(code) ||
                  undefined;
                runMutation.mutate({ yamlConfig: code, pipelineName: pipelineNameValue });
              }}
              disabled={!validation?.is_valid || runMutation.isPending}
              data-testid="run-pipeline-btn"
              className="min-h-10 shrink-0 rounded border px-3 py-1.5 text-xs font-medium text-[var(--bg-base)] transition-[filter] hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
              style={{
                backgroundColor: "var(--accent-primary)",
                borderColor: "color-mix(in srgb, var(--accent-primary) 80%, var(--widget-border))",
              }}
            >
              <span className="inline-flex items-center gap-1.5">
                {runMutation.isPending ? (
                  <RefreshCw className="h-3 w-3 animate-spin" />
                ) : (
                  <Play className="h-3 w-3" />
                )}
                Run Pipeline
              </span>
            </button>
          </div>
        </div>
        {planError && (
          <div className="p-2 border-t bg-[var(--bg-surface)] text-xs text-[var(--accent-error)]" style={{ borderColor: "var(--widget-border)" }}>
            Plan error: {planError}
          </div>
        )}
        {plan && (
          <div className="border-t overflow-y-auto max-h-[50%] bg-[var(--bg-surface)]" style={{ borderColor: "var(--widget-border)" }}>
            <div className="p-3 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wider">
                  Execution Plan — {plan.pipeline_name}
                </h3>
                <button onClick={() => setPlan(null)} className="p-0.5 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="flex items-center gap-3 text-xs">
                {plan.will_succeed ? (
                  <span className="flex items-center gap-1 text-[var(--accent-success)] font-medium">
                    <CheckCircle className="w-3.5 h-3.5" /> ✓ Will Succeed
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-[var(--accent-error)] font-medium">
                    <XCircle className="w-3.5 h-3.5" /> ✗ Will Fail
                  </span>
                )}
                <span className="text-[var(--text-secondary)]">
                  {plan.estimated_total_duration_ms > 1000
                    ? `~${(plan.estimated_total_duration_ms / 1000).toFixed(1)}s`
                    : `~${plan.estimated_total_duration_ms}ms`}
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-[var(--text-secondary)] border-b" style={{ borderColor: "var(--widget-border)" }}>
                      <th className="py-1 pr-2 font-medium">Step</th>
                      <th className="py-1 pr-2 font-medium">Type</th>
                      <th className="py-1 pr-2 font-medium">Est. Rows In</th>
                      <th className="py-1 pr-2 font-medium">Est. Rows Out</th>
                      <th className="py-1 font-medium">Warnings</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.steps.map((step) => (
                      <tr
                        key={step.step_name}
                        className={step.will_fail ? "bg-[var(--accent-error)]/10" : ""}
                      >
                        <td className="py-1 pr-2 text-[var(--text-primary)]">{step.step_name}</td>
                        <td className="py-1 pr-2">
                          <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${
                            step.step_type === "load" ? "bg-blue-500/20 text-blue-400"
                            : step.step_type === "transform" ? "bg-purple-500/20 text-purple-400"
                            : step.step_type === "filter" ? "bg-amber-500/20 text-amber-400"
                            : step.step_type === "join" ? "bg-cyan-500/20 text-cyan-400"
                            : step.step_type === "export" ? "bg-green-500/20 text-green-400"
                            : "bg-gray-500/20 text-gray-400"
                          }`}>
                            {step.step_type}
                          </span>
                        </td>
                        <td className="py-1 pr-2 text-[var(--text-secondary)]">{step.estimated_rows_in ?? "—"}</td>
                        <td className="py-1 pr-2 text-[var(--text-secondary)]">{step.estimated_rows_out ?? "—"}</td>
                        <td className="py-1 text-amber-400">
                          {step.warnings.length > 0 ? step.warnings.join("; ") : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {(plan.files_read.length > 0 || plan.files_written.length > 0) && (
                <div className="flex gap-4 text-[10px] text-[var(--text-secondary)]">
                  {plan.files_read.length > 0 && (
                    <div>
                      <span className="font-medium uppercase tracking-wider">Files Read:</span>{" "}
                      {plan.files_read.join(", ")}
                    </div>
                  )}
                  {plan.files_written.length > 0 && (
                    <div>
                      <span className="font-medium uppercase tracking-wider">Files Written:</span>{" "}
                      {plan.files_written.join(", ")}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
        {plan && (
          <div className="border-t bg-[var(--bg-surface)] px-3 py-2" style={{ borderColor: "var(--widget-border)" }}>
            <h4 className="text-[10px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-1">Step Flow</h4>
            <StepDAG steps={plan.steps} />
          </div>
        )}
        {previewError && (
          <div className="p-2 border-t bg-[var(--bg-surface)] text-xs text-[var(--accent-error)]" style={{ borderColor: "var(--widget-border)" }}>
            Preview error: {previewError}
          </div>
        )}
        {preview && (
          <div className="border-t overflow-y-auto max-h-[30%] bg-[var(--bg-surface)]" style={{ borderColor: "var(--widget-border)" }}>
            <div className="p-3 space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wider">
                  Data Preview — {preview.step_name}
                </h3>
                <button onClick={() => setPreview(null)} className="p-0.5 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              {previewColumns.length === 0 ? (
                <div className="text-xs text-[var(--text-primary)]/75">
                  No preview columns available for this step yet.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-[var(--text-secondary)] border-b" style={{ borderColor: "var(--widget-border)" }}>
                        {previewColumns.map((col) => (
                          <th key={col} className="py-1 pr-3 font-medium whitespace-nowrap">{col}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {previewRows.slice(0, 10).map((row, i) => (
                        <tr key={i} className="border-b border-[var(--widget-border)]/30">
                          {previewColumns.map((col) => (
                            <td key={col} className="py-1 pr-3 text-[var(--text-primary)] whitespace-nowrap font-mono">
                              {row[col] != null ? String(row[col]) : "—"}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {previewRows.length > 10 && (
                <div className="text-[10px] text-[var(--text-secondary)]">
                  Showing 10 of {previewRows.length} rows
                </div>
              )}
              {preview.note && (
                <div className="text-[10px] text-[var(--text-secondary)]">
                  {preview.note}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="flex min-w-0 basis-[38%] flex-col bg-[var(--bg-surface)] overflow-hidden">
        <div className="p-3 border-b" style={{ borderColor: "var(--widget-border)" }}>
          <h3 className="text-xs font-medium text-[var(--text-primary)]/80 uppercase tracking-wider mb-2">Validation</h3>
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
                    <div className="text-xs text-[var(--text-primary)]/75">{err.message}</div>
                    {err.suggestion && <div className="text-[10px] text-[var(--accent-warning)] mt-1">Tip: {err.suggestion}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="flex-1 p-3 overflow-y-auto">
          <h3 className="text-xs font-medium text-[var(--text-primary)]/80 uppercase tracking-wider mb-2">Available Files</h3>
          {files?.length === 0 ? (
            <div className="text-xs text-[var(--text-primary)]/75 italic">No files uploaded yet.</div>
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
                  <div className="text-[10px] font-mono text-[var(--text-primary)]/80 truncate">
                    ID: {file.id}
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {file.columns?.slice(0, 3).map((col) => (
                      <span key={col} className="px-1.5 py-0.5 rounded bg-[var(--bg-surface)] border text-[9px] font-mono text-[var(--text-primary)]/80" style={{ borderColor: "var(--widget-border)" }}>
                        {col}
                      </span>
                    ))}
                    {file.columns && file.columns.length > 3 && (
                      <span className="px-1.5 py-0.5 rounded bg-[var(--bg-surface)] border text-[9px] font-mono text-[var(--text-primary)]/80" style={{ borderColor: "var(--widget-border)" }}>
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
