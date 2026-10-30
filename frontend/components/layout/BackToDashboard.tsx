"use client";

import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

/** Single shared "back to dashboard" affordance (matches the catalog page). */
export function BackToDashboard() {
  const router = useRouter();
  return (
    <button
      onClick={() => router.push("/")}
      className="flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--interactive-hover)] transition-colors"
    >
      <ArrowLeft className="w-3.5 h-3.5" />
      Dashboard
    </button>
  );
}
