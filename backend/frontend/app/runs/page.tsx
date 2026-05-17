"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { RunHistoryWidget } from "@/components/widgets/RunHistoryWidget";
import { useAuth } from "@/lib/auth-context";

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
    <main className="h-screen w-screen bg-[var(--bg-base)] p-3" data-testid="runs-page">
      <div className="h-full overflow-hidden rounded border border-[var(--widget-border)] bg-[var(--widget-bg)]">
        <RunHistoryWidget />
      </div>
    </main>
  );
}
