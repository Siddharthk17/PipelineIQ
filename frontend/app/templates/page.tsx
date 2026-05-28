"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getTemplates } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { PipelineTemplate } from "@/lib/types";
import { FileCode } from "lucide-react";

export default function TemplatesPage() {
  const router = useRouter();
  const { user, isLoading } = useAuth();
  const [templates, setTemplates] = useState<PipelineTemplate[]>([]);
  const [pageLoading, setPageLoading] = useState(true);

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
      return;
    }
    if (!user) return;
    getTemplates()
      .then(setTemplates)
      .catch(() => setTemplates([]))
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
          <h1 className="text-xl font-semibold">Templates</h1>
          <p className="text-sm text-[var(--text-secondary)]">
            Pre-built pipeline templates for common data workflows
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
        {templates.length === 0 ? (
          <div className="flex h-40 items-center justify-center text-sm text-[var(--text-secondary)]">
            No templates available
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {templates.map((template) => (
              <div
                key={template.id}
                data-testid="template-card"
                className="cursor-pointer rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] p-4 transition-colors hover:border-[var(--accent-primary)] hover:bg-[var(--interactive-hover)]"
                onClick={() => router.push(`/pipelines/new?template=${template.id}`)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") router.push(`/pipelines/new?template=${template.id}`);
                }}
              >
                <div className="mb-2 flex items-center gap-2">
                  <FileCode className="h-4 w-4 text-[var(--accent-primary)]" />
                  <p className="text-sm font-medium">{template.name}</p>
                </div>
                <p className="text-xs text-[var(--text-secondary)]">{template.description}</p>
                <span className="mt-3 inline-block rounded border border-[var(--widget-border)] bg-[var(--bg-elevated)] px-2 py-0.5 text-[10px] text-[var(--text-secondary)]">
                  {template.category}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
