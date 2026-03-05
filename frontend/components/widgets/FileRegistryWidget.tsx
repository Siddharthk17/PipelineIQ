"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { FileText, FileJson, Trash2, Eye, Copy, UploadCloud, X, History } from "lucide-react";
import { getFiles, deleteFile, getFilePreview, getSchemaHistory } from "@/lib/api";
import type { SchemaDrift, ColumnDrift, SchemaSnapshot } from "@/lib/types";

export function FileRegistryWidget() {
  const queryClient = useQueryClient();
  const { data: files, isLoading } = useQuery({ queryKey: ["files"], queryFn: getFiles });
  const [previewData, setPreviewData] = useState<{ name: string; data: Record<string, unknown>[] } | null>(null);
  const [driftDetail, setDriftDetail] = useState<{ name: string; drift: SchemaDrift } | null>(null);
  const [historyData, setHistoryData] = useState<{ name: string; snapshots: SchemaSnapshot[] } | null>(null);
  
  const deleteMutation = useMutation({
    mutationFn: deleteFile,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["files"] }),
  });

  const handlePreview = async (fileId: string, fileName: string) => {
    try {
      const data = await getFilePreview(fileId);
      setPreviewData({ name: fileName, data });
    } catch {
      // Preview unavailable
    }
  };

  const handleCopyId = (id: string) => {
    navigator.clipboard.writeText(id);
  };

  const handleHistory = async (fileId: string, fileName: string) => {
    try {
      const snapshots = await getSchemaHistory(fileId);
      setHistoryData({ name: fileName, snapshots });
    } catch {
      // History unavailable
    }
  };

  if (isLoading) return <div className="p-4 text-[var(--text-secondary)]">Loading files...</div>;

  if (!files || files.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full border-2 border-dashed rounded-lg" style={{ borderColor: "var(--widget-border)" }}>
        <UploadCloud className="w-8 h-8 text-[var(--text-secondary)] mb-2" />
        <p className="text-sm text-[var(--text-secondary)]">No files yet. Upload one!</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto pr-2 relative">
      {previewData && (
        <div className="absolute inset-0 z-10 bg-[var(--bg-base)] flex flex-col overflow-hidden rounded-lg">
          <div className="flex items-center justify-between p-2 border-b" style={{ borderColor: "var(--widget-border)" }}>
            <span className="text-xs font-medium text-[var(--text-primary)] truncate">{previewData.name}</span>
            <button onClick={() => setPreviewData(null)} className="p-1 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]"><X className="w-3.5 h-3.5" /></button>
          </div>
          <div className="flex-1 overflow-auto p-2">
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr>{previewData.data[0] && Object.keys(previewData.data[0]).map(k => <th key={k} className="text-left p-1 border-b font-medium text-[var(--text-secondary)]" style={{ borderColor: "var(--widget-border)" }}>{k}</th>)}</tr>
              </thead>
              <tbody>
                {previewData.data.slice(0, 10).map((row, i) => (
                  <tr key={i}>{Object.values(row).map((v, j) => <td key={j} className="p-1 border-b text-[var(--text-primary)]" style={{ borderColor: "var(--widget-border)" }}>{String(v)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {driftDetail && (
        <div className="absolute inset-0 z-10 bg-[var(--bg-base)] flex flex-col overflow-hidden rounded-lg">
          <div className="flex items-center justify-between p-2 border-b" style={{ borderColor: "var(--widget-border)" }}>
            <span className="text-xs font-medium text-[var(--text-primary)] truncate">Schema Drift — {driftDetail.name}</span>
            <button onClick={() => setDriftDetail(null)} className="p-1 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]"><X className="w-3.5 h-3.5" /></button>
          </div>
          <div className="flex-1 overflow-auto p-2 space-y-2">
            {(["removed", "type_changed", "added"] as const).map((type) => {
              const items = driftDetail.drift.drift_items.filter((d) => d.drift_type === type);
              if (items.length === 0) return null;
              const label = type === "removed" ? "Removed Columns" : type === "type_changed" ? "Type Changed" : "Added Columns";
              return (
                <div key={type}>
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)] mb-1">{label}</p>
                  {items.map((item) => (
                    <div key={item.column} className={`flex items-center justify-between px-2 py-1 rounded text-xs mb-1 ${
                      item.severity === "breaking" ? "bg-red-500/20 text-red-400" : item.severity === "warning" ? "bg-amber-500/20 text-amber-400" : "bg-blue-500/20 text-blue-400"
                    }`}>
                      <span className="font-medium">{item.column}</span>
                      <span className="text-[10px] opacity-75">
                        {item.drift_type === "type_changed" ? `${item.old_value} → ${item.new_value}` : item.drift_type}
                      </span>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {historyData && (
        <div className="absolute inset-0 z-10 bg-[var(--bg-base)] flex flex-col overflow-hidden rounded-lg">
          <div className="flex items-center justify-between p-2 border-b" style={{ borderColor: "var(--widget-border)" }}>
            <span className="text-xs font-medium text-[var(--text-primary)] truncate">Schema History — {historyData.name}</span>
            <button onClick={() => setHistoryData(null)} className="p-1 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)]"><X className="w-3.5 h-3.5" /></button>
          </div>
          <div className="flex-1 overflow-auto p-2 space-y-2">
            {[...historyData.snapshots].reverse().map((snap, idx, arr) => {
              const prev = idx < arr.length - 1 ? arr[idx + 1] : null;
              const added = prev ? snap.columns.filter((c) => !prev.columns.includes(c)) : [];
              const removed = prev ? prev.columns.filter((c) => !snap.columns.includes(c)) : [];
              return (
                <div key={snap.id ?? idx} className="p-2 rounded-lg text-xs" style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--widget-border)" }}>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-medium text-[var(--text-primary)]">{formatDistanceToNow(new Date(snap.captured_at), { addSuffix: true })}</span>
                    <span className="text-[var(--text-secondary)]">{snap.row_count} rows</span>
                  </div>
                  <div className="flex flex-wrap gap-1 mb-1.5">
                    {snap.columns.map((col) => (
                      <span key={col} className="px-1.5 py-0.5 rounded-full text-[10px] bg-[var(--interactive-hover)] text-[var(--text-secondary)]">{col}</span>
                    ))}
                  </div>
                  {prev && (added.length > 0 || removed.length > 0) && (
                    <div className="flex items-center gap-2 text-[10px]">
                      {added.length > 0 && <span className="text-blue-400">+ {added.length} column{added.length > 1 ? "s" : ""} added</span>}
                      {removed.length > 0 && <span className="text-red-400">- {removed.length} column{removed.length > 1 ? "s" : ""} removed</span>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {files.map((file) => (
        <div key={file.id} className="flex items-center justify-between p-3 mb-2 rounded-lg group" style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--widget-border)" }}>
          <div className="flex items-center gap-3 overflow-hidden">
            {file.original_filename.endsWith(".json") ? (
              <FileJson className="w-5 h-5 text-[var(--accent-warning)] flex-shrink-0" />
            ) : (
              <FileText className="w-5 h-5 text-[var(--accent-primary)] flex-shrink-0" />
            )}
            <div className="flex flex-col overflow-hidden">
              <div className="flex items-center gap-1.5">
                <span className="text-sm font-medium text-[var(--text-primary)] truncate" title={file.original_filename}>{file.original_filename}</span>
                {file.schema_drift?.has_drift && (
                  <button
                    onClick={(e) => { e.stopPropagation(); setDriftDetail({ name: file.original_filename, drift: file.schema_drift! }); }}
                    className={`flex-shrink-0 px-1.5 py-0.5 rounded-full text-[10px] font-medium cursor-pointer ${
                      file.schema_drift.breaking_changes > 0
                        ? "bg-red-500/20 text-red-400"
                        : file.schema_drift.warnings > 0
                        ? "bg-amber-500/20 text-amber-400"
                        : "bg-blue-500/20 text-blue-400"
                    }`}
                  >
                    ⚠ {file.schema_drift.breaking_changes > 0 ? `${file.schema_drift.breaking_changes} breaking` : "Schema Drift"}
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                <span>{file.row_count || 0} rows</span>
                <span>•</span>
                <span>{file.column_count || 0} cols</span>
                <span>•</span>
                <span>{(file.file_size_bytes / 1024).toFixed(1)} KB</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <button onClick={() => handleCopyId(file.id)} className="p-1.5 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]" title="Copy ID">
              <Copy className="w-4 h-4" />
            </button>
            <button onClick={() => handlePreview(file.id, file.original_filename)} className="p-1.5 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]" title="Preview">
              <Eye className="w-4 h-4" />
            </button>
            <button onClick={() => handleHistory(file.id, file.original_filename)} className="p-1.5 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]" title="History">
              <History className="w-4 h-4" />
            </button>
            <button onClick={() => { if(confirm("Delete file?")) deleteMutation.mutate(file.id); }} className="p-1.5 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--accent-error)]" title="Delete">
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
