"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getFiles, deleteFile } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { UploadedFile } from "@/lib/types";
import { FileText, Trash2, Upload } from "lucide-react";

export default function FilesPage() {
  const router = useRouter();
  const { user, isLoading } = useAuth();
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [pageLoading, setPageLoading] = useState(true);

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
      return;
    }
    if (!user) return;

    getFiles()
      .then(setFiles)
      .catch(() => setFiles([]))
      .finally(() => setPageLoading(false));
  }, [isLoading, router, user]);

  if (isLoading || !user || pageLoading) {
    return (
      <main className="flex h-screen items-center justify-center bg-[var(--bg-base)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)]" />
      </main>
    );
  }

  return (
    <main className="flex h-screen w-screen flex-col bg-[var(--bg-base)] text-[var(--text-primary)]">
      <div className="flex items-center justify-between border-b border-[var(--widget-border)] px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">Files</h1>
          <p className="text-sm text-[var(--text-secondary)]">
            Uploaded data files for pipeline processing
          </p>
        </div>
        <button
          onClick={() => router.push("/")}
          className="rounded border border-[var(--widget-border)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--interactive-hover)]"
        >
          Back
        </button>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div
          data-testid="upload-zone"
          className="mb-6 flex cursor-pointer items-center justify-center rounded-lg border-2 border-dashed border-[var(--widget-border)] p-8 transition-colors hover:border-[var(--accent-primary)] hover:bg-[var(--interactive-hover)]"
          onClick={() => router.push("/pipelines/new")}
          onKeyDown={(e) => {
            if (e.key === "Enter") router.push("/pipelines/new");
          }}
        >
          <div className="flex flex-col items-center gap-2 text-[var(--text-secondary)]">
            <Upload className="h-8 w-8" />
            <span className="text-sm">Upload files via the Pipeline Builder</span>
          </div>
        </div>

        {files.length === 0 ? (
          <div className="flex h-40 items-center justify-center text-sm text-[var(--text-secondary)]">
            No files uploaded yet
          </div>
        ) : (
          <div data-testid="file-list" className="space-y-2">
            {files.map((file) => (
              <div
                key={file.id}
                className="flex items-center gap-3 rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] p-4 transition-colors hover:bg-[var(--interactive-hover)]"
              >
                <FileText className="h-5 w-5 shrink-0 text-[var(--text-secondary)]" />
                <div
                  className="min-w-0 flex-1 cursor-pointer"
                  onClick={() => router.push(`/files/${file.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") router.push(`/files/${file.id}`);
                  }}
                >
                  <p className="truncate text-sm font-medium">{file.original_filename}</p>
                  <p className="text-xs text-[var(--text-secondary)]">
                    {file.row_count?.toLocaleString() ?? "?"} rows ·{" "}
                    {file.column_count} columns ·{" "}
                    {(file.file_size_bytes / 1024).toFixed(1)} KB
                  </p>
                </div>
                <button
                  type="button"
                  onClick={async () => {
                    await deleteFile(file.id);
                    setFiles((prev) => prev.filter((f) => f.id !== file.id));
                  }}
                  className="rounded p-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--accent-error)]/10 hover:text-[var(--accent-error)]"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
