"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { PipelineEditorWidget } from "@/components/widgets/PipelineEditorWidget";
import { useAuth } from "@/lib/auth-context";

export default function NewPipelinePage() {
  const router = useRouter();
  const { user, isLoading } = useAuth();
  // Deep-link support: ?pipeline=<name> starts the builder in a pipeline
  // room of that name instead of the default shared one. E2E tests use a
  // unique name per test so concurrent sessions never share canvas state.
  // Read once on mount (client-only; guarded for SSR).
  const [initialPipelineName] = useState<string | undefined>(() => {
    if (typeof window === "undefined") return undefined;
    const match = /[?&]pipeline=([A-Za-z0-9_-]+)/.exec(window.location.search);
    return match ? match[1] : undefined;
  });

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
    }
  }, [isLoading, router, user]);

  if (isLoading || !user) {
    return (
      <main className="flex h-screen items-center justify-center bg-[var(--bg-base)]">
        <div className="w-5 h-5 border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)] rounded-full animate-spin" />
      </main>
    );
  }

  return (
    <main className="h-screen w-screen bg-[var(--bg-base)] p-3" data-testid="new-pipeline-page">
      <div className="h-full overflow-hidden rounded border border-[var(--widget-border)] bg-[var(--widget-bg)]">
        <PipelineEditorWidget
          initialMode="visual"
          initialPipelineName={initialPipelineName}
          key={initialPipelineName ?? "default"}
        />
      </div>
    </main>
  );
}
