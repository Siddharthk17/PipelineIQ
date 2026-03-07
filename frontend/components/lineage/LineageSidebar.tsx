import React from "react";
import { motion, AnimatePresence } from "motion/react";
import { X, GitMerge, ArrowRight, Database, AlertTriangle, Activity } from "lucide-react";
import { useColumnLineage } from "@/hooks/useLineage";
import { ImpactAnalysis } from "@/lib/types";

interface LineageSidebarProps {
  runId: string | null;
  mode: "ancestry" | "impact";
  step: string | null;
  column: string | null;
  impactData?: ImpactAnalysis | null;
  isOpen: boolean;
  onClose: () => void;
}

export function LineageSidebar({ runId, mode, step, column, impactData, isOpen, onClose }: LineageSidebarProps) {
  const { data: lineage, isLoading } = useColumnLineage(
    mode === "ancestry" ? runId : null,
    mode === "ancestry" ? step : null,
    mode === "ancestry" ? column : null,
  );

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, x: "100%" }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: "100%" }}
          transition={{ type: "spring", damping: 25, stiffness: 200 }}
          className="absolute inset-y-0 right-0 z-10 w-80 shadow-2xl flex flex-col"
          style={{ backgroundColor: "var(--bg-elevated)", borderLeft: "1px solid var(--widget-border)" }}
        >
          <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "var(--widget-border)" }}>
            <div className="flex items-center gap-2 text-[var(--text-primary)]">
              {mode === "ancestry" ? (
                <>
                  <GitMerge className="w-4 h-4 text-[var(--accent-primary)]" />
                  <h3 className="font-medium text-sm">Column Ancestry</h3>
                </>
              ) : (
                <>
                  <Activity className="w-4 h-4 text-[var(--accent-error)]" />
                  <h3 className="font-medium text-sm">Impact Analysis</h3>
                </>
              )}
            </div>
            <button onClick={onClose} className="p-1 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]">
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-4">
            {mode === "ancestry" ? (
              <AncestryContent lineage={lineage} isLoading={isLoading} />
            ) : (
              <ImpactContent step={step} column={column} impactData={impactData} />
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function AncestryContent({ lineage, isLoading }: { lineage: ReturnType<typeof useColumnLineage>["data"]; isLoading: boolean }) {
  if (isLoading) {
    return <div className="text-sm text-[var(--text-secondary)] animate-pulse">Loading ancestry...</div>;
  }
  if (!lineage) {
    return <div className="text-sm text-[var(--text-secondary)]">Select a column to view its ancestry.</div>;
  }

  return (
    <div className="space-y-6">
      <div className="p-3 rounded-lg bg-[var(--bg-surface)] border" style={{ borderColor: "var(--widget-border)" }}>
        <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-1">Target Column</div>
        <div className="font-mono text-sm text-[var(--accent-primary)] font-bold mb-3">{lineage.column_name}</div>
        <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-1">Source</div>
        <div className="flex items-center gap-2 text-sm text-[var(--text-primary)]">
          <Database className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
          <span>{lineage.source_file}</span>
          <ArrowRight className="w-3 h-3 text-[var(--text-secondary)]" />
          <span className="font-mono">{lineage.source_column}</span>
        </div>
      </div>

      <div>
        <h4 className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-3">Transformation Chain</h4>
        <div className="relative pl-4 border-l-2 space-y-4" style={{ borderColor: "var(--widget-border)" }}>
          {lineage.transformation_chain.map((tStep, idx) => (
            <div key={idx} className="relative">
              <div className="absolute -left-[21px] top-1 w-2.5 h-2.5 rounded-full bg-[var(--bg-elevated)] border-2" style={{ borderColor: "var(--accent-secondary)" }} />
              <div className="flex items-center gap-2 mb-1">
                <span className="text-sm font-bold text-[var(--text-primary)]">{tStep.step_name}</span>
                <span className="px-1.5 py-0.5 rounded bg-[var(--bg-surface)] text-[10px] text-[var(--text-secondary)] uppercase border" style={{ borderColor: "var(--widget-border)" }}>
                  {tStep.step_type}
                </span>
              </div>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed">{tStep.detail}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="pt-4 border-t text-xs text-[var(--text-secondary)] text-center" style={{ borderColor: "var(--widget-border)" }}>
        Total: {lineage.total_steps} transformations
      </div>
    </div>
  );
}

function ImpactContent({ step, column, impactData }: { step: string | null; column: string | null; impactData?: ImpactAnalysis | null }) {
  if (!impactData) {
    return <div className="text-sm text-[var(--text-secondary)]">Click a column node to analyze its downstream impact.</div>;
  }

  const affectedSteps = impactData.affected_steps ?? [];
  const affectedColumns = impactData.affected_output_columns ?? [];

  return (
    <div className="space-y-5">
      {/* Subtitle */}
      <div className="p-3 rounded-lg bg-[var(--bg-surface)] border" style={{ borderColor: "var(--widget-border)" }}>
        <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-1">Analyzing</div>
        <div className="font-mono text-sm text-[var(--accent-error)] font-bold">
          {column} <span className="text-[var(--text-secondary)] font-normal">in</span> {step}
        </div>
      </div>

      {/* Warning Summary */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-xs text-[var(--accent-warning)]">
          <AlertTriangle className="w-3.5 h-3.5" />
          <span className="font-medium">⚠ {affectedSteps.length} steps affected</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-[var(--accent-warning)]">
          <AlertTriangle className="w-3.5 h-3.5" />
          <span className="font-medium">⚠ {affectedColumns.length} output columns affected</span>
        </div>
      </div>

      {/* Affected Steps */}
      {affectedSteps.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-2">Affected Steps</h4>
          <div className="space-y-1.5">
            {affectedSteps.map((s) => (
              <div key={s} className="flex items-center gap-2 px-3 py-1.5 rounded text-sm" style={{ background: 'var(--bg-surface)', border: '1px solid var(--widget-border)' }}>
                <div className="w-2 h-2 rounded-full" style={{ background: 'var(--accent-warning)' }} />
                <span className="text-[var(--text-primary)] font-medium">{s}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Affected Output Columns */}
      {affectedColumns.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-2">Affected Output Columns</h4>
          <div className="flex flex-wrap gap-1.5">
            {affectedColumns.map((col, i) => (
              <span key={`${col}-${i}`} className="px-2 py-1 rounded font-mono text-xs" style={{ background: 'var(--bg-surface)', border: '1px solid var(--widget-border)', color: 'var(--text-primary)' }}>
                {col}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Bottom warning box */}
      <div className="p-3 rounded-lg text-xs leading-relaxed" style={{ background: 'color-mix(in srgb, var(--accent-error) 15%, var(--bg-surface))', border: '1px solid var(--accent-error)', color: 'var(--accent-error)' }}>
        <div className="flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>
            Changing <strong>&apos;{column}&apos;</strong> in <strong>{step}</strong> will
            break {affectedSteps.length} downstream step{affectedSteps.length !== 1 ? 's' : ''} and
            affect {affectedColumns.length} output column{affectedColumns.length !== 1 ? 's' : ''}.
          </span>
        </div>
      </div>
    </div>
  );
}
