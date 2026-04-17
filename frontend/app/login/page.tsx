"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import Link from "next/link";

export default function LoginPage() {
  const router = useRouter();
  const { login, loginAsDemo } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setMounted(true), 50);
    return () => clearTimeout(t);
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("invalid email or password");
      } else if (err instanceof ApiError) {
        const detail = (err.detail as { detail?: string })?.detail;
        setError(detail || "invalid credentials");
      } else {
        setError("something went wrong");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleDemo = async () => {
    setError("");
    setDemoLoading(true);
    try {
      await loginAsDemo();
      router.push("/dashboard");
    } catch {
      setError("demo unavailable — try registering instead");
    } finally {
      setDemoLoading(false);
    }
  };

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

      {/* window */}
      <div
        className="relative w-full max-w-[400px] mx-4"
        style={{
          transition: "opacity 0.4s ease, transform 0.4s ease",
          opacity: mounted ? 1 : 0,
          transform: mounted ? "translateY(0)" : "translateY(12px)",
        }}
      >
        {/* title bar */}
        <div
          className="flex items-center gap-2 px-4 py-2.5"
          style={{
            background: "var(--widget-header-bg)",
            border: "1px solid var(--widget-border)",
            borderBottom: "none",
            borderRadius: "var(--widget-radius) var(--widget-radius) 0 0",
          }}
        >
          <div className="flex gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full" style={{ background: "var(--accent-error)", opacity: 0.8 }} />
            <div className="w-2.5 h-2.5 rounded-full" style={{ background: "var(--accent-warning)", opacity: 0.8 }} />
            <div className="w-2.5 h-2.5 rounded-full" style={{ background: "var(--accent-success)", opacity: 0.8 }} />
          </div>
          <span className="ml-2 text-[11px] text-[var(--text-secondary)]" style={{ fontFamily: "var(--font-mono)" }}>
            pipelineiq · login
          </span>
        </div>

        {/* body */}
        <div
          className="p-7"
          style={{
            background: "var(--widget-bg)",
            backdropFilter: "blur(20px)",
            border: "1px solid var(--widget-border)",
            borderTop: "none",
            borderRadius: "0 0 var(--widget-radius) var(--widget-radius)",
          }}
        >
          {/* brand */}
          <div className="mb-7">
            <h1 className="text-base tracking-tight" style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--text-primary)" }}>
              pipeline<span style={{ color: "var(--accent-primary)" }}>iq</span>
            </h1>
            <p className="text-[11px] mt-1" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
              data pipeline orchestration
            </p>
          </div>

          {error && (
            <div
              className="mb-5 px-3 py-2 text-[12px]"
              style={{
                fontFamily: "var(--font-mono)",
                color: "var(--accent-error)",
                borderLeft: "2px solid var(--accent-error)",
                background: "rgba(0,0,0,0.15)",
                borderRadius: "0 var(--widget-radius) var(--widget-radius) 0",
              }}
            >
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="login-email"
                className="block text-[11px] mb-1.5"
                style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}
              >
                <span style={{ color: "var(--accent-primary)" }}>›</span> email
              </label>
              <input
                id="login-email"
                name="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                className="w-full px-3 py-2.5 text-[13px] outline-none"
                style={{
                  fontFamily: "var(--font-mono)",
                  background: "var(--bg-base)",
                  border: "1px solid var(--widget-border)",
                  borderRadius: "var(--widget-radius)",
                  color: "var(--text-primary)",
                  transition: "border-color 0.2s",
                }}
                onFocus={(e) => (e.target.style.borderColor = "var(--accent-primary)")}
                onBlur={(e) => (e.target.style.borderColor = "var(--widget-border)")}
                placeholder="you@example.com"
                data-testid="email-input"
              />
            </div>

            <div>
              <label
                htmlFor="login-password"
                className="block text-[11px] mb-1.5"
                style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}
              >
                <span style={{ color: "var(--accent-primary)" }}>›</span> password
              </label>
              <div className="relative">
                <input
                  id="login-password"
                  name="password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  className="w-full px-3 py-2.5 pr-14 text-[13px] outline-none"
                  style={{
                    fontFamily: "var(--font-mono)",
                    background: "var(--bg-base)",
                    border: "1px solid var(--widget-border)",
                    borderRadius: "var(--widget-radius)",
                    color: "var(--text-primary)",
                    transition: "border-color 0.2s",
                  }}
                  onFocus={(e) => (e.target.style.borderColor = "var(--accent-primary)")}
                  onBlur={(e) => (e.target.style.borderColor = "var(--widget-border)")}
                  placeholder="••••••••"
                  data-testid="password-input"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 min-h-11 min-w-11 rounded px-2 text-xs"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  style={{
                    fontFamily: "var(--font-mono)",
                    color: "var(--text-secondary)",
                    transition: "color 0.15s",
                    background: "transparent",
                    border: "none",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.color = "var(--text-primary)")}
                  onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-secondary)")}
                >
                  {showPassword ? "hide" : "show"}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading || demoLoading}
              className="w-full py-2.5 text-[13px] font-medium disabled:opacity-40"
              style={{
                fontFamily: "var(--font-mono)",
                background: "var(--accent-primary)",
                color: "var(--bg-base)",
                borderRadius: "var(--widget-radius)",
                border: "none",
                cursor: loading ? "wait" : "pointer",
                transition: "opacity 0.15s",
              }}
              data-testid="login-btn"
            >
                {loading ? "authenticating…" : "sign in"}
              </button>
          </form>

          {/* divider */}
          <div className="flex items-center gap-3 my-5">
            <div className="flex-1 h-px" style={{ background: "var(--widget-border)" }} />
            <span className="text-[10px] uppercase tracking-[0.15em]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
              or
            </span>
            <div className="flex-1 h-px" style={{ background: "var(--widget-border)" }} />
          </div>

          <button
            onClick={handleDemo}
            disabled={loading || demoLoading}
            className="w-full py-2.5 text-[13px] disabled:opacity-40"
            style={{
              fontFamily: "var(--font-mono)",
              background: "transparent",
              border: "1px dashed var(--widget-border)",
              borderRadius: "var(--widget-radius)",
              color: "var(--text-secondary)",
              cursor: demoLoading ? "wait" : "pointer",
              transition: "border-color 0.2s, color 0.2s",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "var(--accent-primary)";
              e.currentTarget.style.color = "var(--accent-primary)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "var(--widget-border)";
              e.currentTarget.style.color = "var(--text-secondary)";
            }}
          >
            {demoLoading ? "loading…" : "try demo →"}
          </button>

          {/* footer */}
          <div className="mt-7 pt-4 flex items-center justify-between" style={{ borderTop: "1px solid var(--widget-border)" }}>
            <p className="text-[11px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
              no account?{" "}
              <Link
                href="/register"
                className="underline-offset-2 hover:underline"
                style={{ color: "var(--accent-primary)" }}
              >
                register
              </Link>
            </p>
            <span className="text-[10px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)", opacity: 0.4 }}>
              v3.6.2
            </span>
          </div>
        </div>
      </div>
    </main>
  );
}
