"use client";

import { useEffect, useState, use } from "react";
import { useRouter } from "next/navigation";
import { getFile, getFilePreview } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { PIIBanner } from "@/components/widgets/PIIBanner";
import { ColumnPolicyManager } from "@/components/widgets/ColumnPolicyManager";
import type { UploadedFile } from "@/lib/types";

export default function FileDetailPage({ params }: { params: Promise<{ fileId: string }> }) {
  const router = useRouter();
  const { user, isLoading } = useAuth();
  const [file, setFile] = useState<UploadedFile | null>(null);
  const [previewRows, setPreviewRows] = useState<Record<string, unknown>[]>([]);
  const [piiColumns, setPiiColumns] = useState<string[]>([]);
  const [pageLoading, setPageLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const resolvedParams = use(params);

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
      return;
    }
    if (!user) return;

    const fileId = resolvedParams.fileId;
    Promise.all([getFile(fileId), getFilePreview(fileId)])
      .then(([fileData, preview]) => {
        setFile(fileData);
        setPreviewRows(preview);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load file"))
      .finally(() => setPageLoading(false));
  }, [isLoading, resolvedParams.fileId, router, user]);

  if (isLoading || !user || pageLoading) {
    return (
      <main className="flex h-screen items-center justify-center bg-[var(--bg-base)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)]" />
      </main>
    );
  }

  if (error || !file) {
    return (
      <main className="flex h-screen items-center justify-center bg-[var(--bg-base)]">
        <div
          data-testid="upload-error"
          className="text-sm text-[var(--accent-error)]"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          {error ?? "File not found"}
        </div>
      </main>
    );
  }

  return (
    <main className="flex h-screen w-screen flex-col bg-[var(--bg-base)] text-[var(--text-primary)]">
      <div className="flex items-center justify-between border-b border-[var(--widget-border)] px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">{file.original_filename}</h1>
          <p className="text-sm text-[var(--text-secondary)]">
            {file.row_count?.toLocaleString() ?? "?"} rows · {file.column_count} columns
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div
            data-testid="profile-status-complete"
            className="rounded-full bg-[var(--accent-success)]/10 px-3 py-1 text-xs font-medium text-[var(--accent-success)]"
          >
            profiled
          </div>
          <button
            onClick={() => router.push("/files")}
            className="rounded border border-[var(--widget-border)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--interactive-hover)]"
          >
            All Files
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <PIIBanner
          piiSuggestions={piiColumns}
          fileId={resolvedParams.fileId}
        />

        <ColumnPolicyManager
          fileId={resolvedParams.fileId}
          fileColumns={file.columns}
        />

        <div className="mb-6 rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] p-4">
          <h2 className="mb-3 text-sm font-medium">Schema</h2>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
            {file.columns.map((col) => (
              <div
                key={col}
                data-testid={`profile-col-${col}`}
                className="rounded border border-[var(--widget-border)] bg-[var(--bg-elevated)] p-2"
              >
                <p className="text-xs font-medium">{col}</p>
                <p className="text-[10px] text-[var(--text-secondary)]">
                  {file.dtypes[col] ?? "unknown"}
                </p>
              </div>
            ))}
          </div>
        </div>

        {previewRows.length > 0 && (
          <div className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)]">
            <div className="border-b border-[var(--widget-border)] p-4">
              <h2 className="text-sm font-medium">Preview ({previewRows.length} rows)</h2>
            </div>
            <div className="overflow-x-auto p-4">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-[var(--widget-border)]">
                    {Object.keys(previewRows[0]).map((key) => (
                      <th key={key} className="px-3 py-2 font-medium text-[var(--text-secondary)]">{key}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {previewRows.map((row, i) => (
                    <tr key={i} className="border-b border-[var(--widget-border)] last:border-0">
                      {Object.values(row).map((val, j) => (
                        <td key={j} className="whitespace-nowrap px-3 py-1.5 font-mono">
                          {String(val)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
