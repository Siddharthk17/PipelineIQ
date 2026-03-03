"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { FileText, FileJson, Trash2, Eye, Copy, UploadCloud, X } from "lucide-react";
import { getFiles, deleteFile, getFilePreview } from "@/lib/api";

export function FileRegistryWidget() {
  const queryClient = useQueryClient();
  const { data: files, isLoading } = useQuery({ queryKey: ["files"], queryFn: getFiles });
  const [previewData, setPreviewData] = useState<{ name: string; data: Record<string, unknown>[] } | null>(null);
  
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
    // In a real app, show a toast here
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
      {files.map((file) => (
        <div key={file.id} className="flex items-center justify-between p-3 mb-2 rounded-lg group" style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--widget-border)" }}>
          <div className="flex items-center gap-3 overflow-hidden">
            {file.original_filename.endsWith(".json") ? (
              <FileJson className="w-5 h-5 text-[var(--accent-warning)] flex-shrink-0" />
            ) : (
              <FileText className="w-5 h-5 text-[var(--accent-primary)] flex-shrink-0" />
            )}
            <div className="flex flex-col overflow-hidden">
              <span className="text-sm font-medium text-[var(--text-primary)] truncate" title={file.original_filename}>{file.original_filename}</span>
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
            <button onClick={() => { if(confirm("Delete file?")) deleteMutation.mutate(file.id); }} className="p-1.5 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--accent-error)]" title="Delete">
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
