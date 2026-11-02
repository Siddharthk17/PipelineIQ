"use client";

import Link from "next/link";
import { APP_VERSION } from "@/lib/constants";
import {
  Layers,
  GitBranch,
  Sparkles,
  Users,
  ArrowRight,
  BookOpen,
} from "lucide-react";

const FEATURES = [
  {
    icon: Layers,
    title: "19 pipeline operations",
    body: "Load, filter, join, aggregate, SQL, WASM UDFs, streaming and more — composed in YAML, executed at scale.",
  },
  {
    icon: GitBranch,
    title: "Column-level lineage",
    body: "Trace every output column back to its source file and step. Export OpenLineage for your catalog.",
  },
  {
    icon: Sparkles,
    title: "AI generation & healing",
    body: "Describe a pipeline in plain English to generate it. Failures are classified and auto-healed by agents.",
  },
  {
    icon: Users,
    title: "Live collaboration",
    body: "Edit pipelines together in real time with shared cursors, presence, and conflict-free sync.",
  },
];

export default function LandingPage() {
  return (
    <main className="min-h-screen flex items-center justify-center bg-[var(--grid-bg)] relative overflow-hidden">
      {/* dot grid */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          backgroundImage: "radial-gradient(var(--widget-border) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
          opacity: 0.4,
        }}
      />

      <div className="relative w-full max-w-[640px] mx-4 py-10">
        {/* brand */}
        <div className="text-center mb-8">
          <h1
            className="text-2xl tracking-tight"
            style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--text-primary)" }}
          >
            pipeline<span style={{ color: "var(--accent-primary)" }}>iq</span>
          </h1>
          <p
            className="text-[12px] mt-2"
            style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}
          >
            data pipeline orchestration — define in YAML, run at scale, trace everything
          </p>
        </div>

        {/* features */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-8">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className="p-4"
              style={{
                background: "var(--widget-bg)",
                backdropFilter: "blur(20px)",
                border: "1px solid var(--widget-border)",
                borderRadius: "var(--widget-radius)",
              }}
            >
              <f.icon className="w-4 h-4 mb-2" style={{ color: "var(--accent-primary)" }} />
              <div
                className="text-[12px] mb-1"
                style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--text-primary)" }}
              >
                {f.title}
              </div>
              <div
                className="text-[11px] leading-relaxed"
                style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}
              >
                {f.body}
              </div>
            </div>
          ))}
        </div>

        {/* actions */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-center gap-3 mb-8">
          <Link
            href="/login"
            className="px-5 py-2.5 text-[13px] font-medium text-center inline-flex items-center justify-center gap-2"
            style={{
              fontFamily: "var(--font-mono)",
              background: "var(--accent-primary)",
              color: "var(--bg-base)",
              borderRadius: "var(--widget-radius)",
            }}
            data-testid="landing-login"
          >
            try the live demo <ArrowRight className="w-3.5 h-3.5" />
          </Link>
          <Link
            href="/register"
            className="px-5 py-2.5 text-[13px] text-center"
            style={{
              fontFamily: "var(--font-mono)",
              background: "transparent",
              border: "1px dashed var(--widget-border)",
              borderRadius: "var(--widget-radius)",
              color: "var(--text-secondary)",
            }}
            data-testid="landing-register"
          >
            create account
          </Link>
          <a
            href="/docs"
            className="px-5 py-2.5 text-[13px] text-center inline-flex items-center justify-center gap-2"
            style={{
              fontFamily: "var(--font-mono)",
              background: "transparent",
              border: "1px dashed var(--widget-border)",
              borderRadius: "var(--widget-radius)",
              color: "var(--text-secondary)",
            }}
            data-testid="landing-docs"
          >
            <BookOpen className="w-3.5 h-3.5" /> api docs
          </a>
        </div>

        {/* footer */}
        <div
          className="text-center text-[10px]"
          style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)", opacity: 0.6 }}
        >
          demo login: demo@pipelineiq.app / Demo1234! · v{APP_VERSION}
        </div>
      </div>
    </main>
  );
}
