"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { RunHistoryWidget } from "@/components/widgets/RunHistoryWidget";
import { useAuth } from "@/lib/auth-context";
import { BackToDashboard } from "@/components/layout/BackToDashboard";

export default function RunsPage() {
  const router = useRouter();
  const { user, isLoading } = useAuth();

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
    <main className="flex h-screen w-screen flex-col bg-[var(--bg-base)]" data-testid="runs-page">
      <div className="border-b border-[var(--widget-border)] px-6 py-4">
        <div className="flex items-center gap-3 mb-2">
          <BackToDashboard />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">Pipeline Runs</h1>
          <p className="text-sm text-[var(--text-secondary)]">
            Execution history across all pipelines
          </p>
        </div>
      </div>
      <div className="h-full flex-1 overflow-hidden p-3">
        <div className="h-full overflow-hidden rounded border border-[var(--widget-border)] bg-[var(--widget-bg)]">
          <RunHistoryWidget />
        </div>
      </div>
    </main>
  );
}
