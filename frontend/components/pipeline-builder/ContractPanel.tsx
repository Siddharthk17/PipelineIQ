"use client";

import { useState, useEffect, useCallback } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { yaml as yamlLang } from "@codemirror/lang-yaml";
import { usePipelineStore } from "@/store/pipelineStore";
import {
  getAllContracts,
  createContract,
  updateContract,
  deleteContract,
  getContractStatus,
} from "@/lib/api";
import type { ContractDef, ContractStatusResponse } from "@/lib/types";
import { CheckCircle, AlertTriangle, Loader2, Save, Trash2, Plus, FileText } from "lucide-react";

export function ContractPanel() {
  const { activeRun } = usePipelineStore();
  const pipelineId = activeRun?.id ?? null;

  const [contracts, setContracts] = useState<ContractDef[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [yaml, setYaml] = useState("");
  const [status, setStatus] = useState<ContractStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const selected = contracts.find((c) => c.id === selectedId);

  const loadContracts = useCallback(async () => {
    if (!pipelineId) return;
    setLoading(true);
    setError(null);
    try {
      const list = await getAllContracts(pipelineId);
      setContracts(list);
      if (list.length > 0 && !selectedId) {
        setSelectedId(list[0].id);
        setYaml(list[0].yaml_content);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load contracts");
    } finally {
      setLoading(false);
    }
  }, [pipelineId, selectedId]);

  const loadStatus = useCallback(async () => {
    if (!pipelineId) return;
    try {
      const s = await getContractStatus(pipelineId);
      setStatus(s);
    } catch {
      // status endpoint may not exist for inactive runs
    }
  }, [pipelineId]);

  useEffect(() => {
    loadContracts();
    loadStatus();
  }, [loadContracts, loadStatus]);

  const handleSave = async () => {
    if (!pipelineId || !yaml.trim()) return;
    setSaving(true);
    setError(null);
    try {
      if (selectedId) {
        const updated = await updateContract(pipelineId, selectedId, yaml);
        setContracts((prev) => prev.map((c) => (c.id === selectedId ? updated : c)));
      } else {
        const created = await createContract(pipelineId, yaml);
        setContracts((prev) => [...prev, created]);
        setSelectedId(created.id);
      }
      setDirty(false);
      loadStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save contract");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!pipelineId) return;
    setError(null);
    try {
      await deleteContract(pipelineId, id);
      setContracts((prev) => prev.filter((c) => c.id !== id));
      if (selectedId === id) {
        const next = contracts.find((c) => c.id !== id);
        setSelectedId(next?.id ?? null);
        setYaml(next?.yaml_content ?? "");
      }
      loadStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete contract");
    }
  };

  const handleNew = () => {
    setSelectedId(null);
    setYaml("columns:\n  revenue:\n    type: float64\n    nullable: false\n  customer_id:\n    type: int64\n    nullable: true\nmin_rows: 100\nmax_rows: 1000000\n");
    setDirty(true);
  };

  if (!pipelineId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center p-6 text-[var(--text-secondary)]">
        <FileText className="w-10 h-10 mb-2 opacity-40" />
        <p className="text-sm">Run a pipeline to define data contracts</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between p-2 border-b gap-2" style={{ borderColor: "var(--widget-border)" }}>
        <div className="flex items-center gap-1">
          <button
            onClick={handleNew}
            className="flex items-center gap-1 px-2 py-1 text-xs rounded hover:bg-[var(--interactive-hover)] text-[var(--text-primary)]"
          >
            <Plus className="w-3 h-3" />
            New
          </button>
          {selectedId && (
            <button
              onClick={() => handleDelete(selectedId)}
              className="flex items-center gap-1 px-2 py-1 text-xs rounded hover:bg-[var(--accent-error)]/10 text-[var(--accent-error)]"
            >
              <Trash2 className="w-3 h-3" />
              Delete
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {dirty && (
            <span className="text-[10px] text-[var(--accent-warning)]">Unsaved</span>
          )}
          <button
            onClick={handleSave}
            disabled={saving || !yaml.trim()}
            className="flex items-center gap-1 px-3 py-1 text-xs rounded bg-[var(--accent-primary)] text-white hover:opacity-90 disabled:opacity-40"
          >
            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            Save
          </button>
        </div>
      </div>

      {contracts.length > 0 && (
        <div className="flex gap-1 p-2 overflow-x-auto border-b" style={{ borderColor: "var(--widget-border)" }}>
          {contracts.map((c) => (
            <button
              key={c.id}
              onClick={() => { setSelectedId(c.id); setYaml(c.yaml_content); setDirty(false); }}
              className={`px-2 py-1 text-xs rounded whitespace-nowrap ${
                c.id === selectedId
                  ? "bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]"
                  : "text-[var(--text-secondary)] hover:bg-[var(--interactive-hover)]"
              }`}
            >
              {c.id.substring(0, 8)}...
            </button>
          ))}
        </div>
      )}

      {loading && (
        <div className="flex items-center justify-center p-4 text-[var(--text-secondary)]">
          <Loader2 className="w-4 h-4 animate-spin mr-2" />
          Loading contracts...
        </div>
      )}

      {error && (
        <div className="p-2 text-xs text-[var(--accent-error)] bg-[var(--accent-error)]/10 border-b" style={{ borderColor: "var(--widget-border)" }}>
          {error}
        </div>
      )}

      {status && (
        <div className="flex items-center gap-3 p-2 border-b text-xs" style={{ borderColor: "var(--widget-border)" }}>
          <span className="text-[var(--text-secondary)]">Status:</span>
          {!status.has_contract ? (
            <span className="text-[var(--text-muted)]">No active contract</span>
          ) : status.total_violations > 0 ? (
            <span className="flex items-center gap-1 text-[var(--accent-error)]">
              <AlertTriangle className="w-3 h-3" />
              {status.total_violations} violation{status.total_violations !== 1 ? "s" : ""}
            </span>
          ) : (
            <span className="flex items-center gap-1 text-[var(--accent-success)]">
              <CheckCircle className="w-3 h-3" />
              Healthy
            </span>
          )}
          {status.active_contract_version != null && (
            <span className="text-[var(--text-tertiary)] ml-auto">v{status.active_contract_version}</span>
          )}
        </div>
      )}

      <div className="flex-1 overflow-hidden">
        <CodeMirror
          value={yaml}
          onChange={(val) => { setYaml(val); setDirty(true); }}
          extensions={[yamlLang()]}
          theme="dark"
          height="100%"
          basicSetup={{ lineNumbers: true, foldGutter: true }}
        />
      </div>
    </div>
  );
}
