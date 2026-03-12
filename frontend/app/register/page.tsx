"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { register, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import Link from "next/link";

export default function RegisterPage() {
  const router = useRouter();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setMounted(true), 50);
    return () => clearTimeout(t);
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (password !== confirmPassword) {
      setError("passwords do not match");
      return;
    }
    if (password.length < 8) {
      setError("password must be at least 8 characters");
      return;
    }

    setLoading(true);
    try {
      await register(email, username, password);
      await login(email, password);
      router.push("/");
    } catch (err) {
      if (err instanceof ApiError) {
        const detail = (err.detail as { detail?: string })?.detail;
        setError(detail || "registration failed");
      } else {
        setError("something went wrong");
      }
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    fontFamily: "var(--font-mono)",
    background: "var(--bg-base)",
    border: "1px solid var(--widget-border)",
    borderRadius: "var(--widget-radius)",
    color: "var(--text-primary)",
    transition: "border-color 0.2s",
  };

  const handleFocus = (e: React.FocusEvent<HTMLInputElement>) => {
    e.target.style.borderColor = "var(--accent-primary)";
  };
  const handleBlur = (e: React.FocusEvent<HTMLInputElement>) => {
    e.target.style.borderColor = "var(--widget-border)";
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--grid-bg)] relative overflow-hidden">
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
            pipelineiq · register
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
              create your account
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
              <label className="block text-[11px] mb-1.5" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                <span style={{ color: "var(--accent-primary)" }}>›</span> email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                className="w-full px-3 py-2.5 text-[13px] outline-none"
                style={inputStyle}
                onFocus={handleFocus}
                onBlur={handleBlur}
                placeholder="you@example.com"
              />
            </div>

            <div>
              <label className="block text-[11px] mb-1.5" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                <span style={{ color: "var(--accent-primary)" }}>›</span> username
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                minLength={3}
                maxLength={50}
                pattern="^[a-zA-Z0-9_]+$"
                autoComplete="username"
                className="w-full px-3 py-2.5 text-[13px] outline-none"
                style={inputStyle}
                onFocus={handleFocus}
                onBlur={handleBlur}
                placeholder="your_username"
              />
            </div>

            <div>
              <label className="block text-[11px] mb-1.5" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                <span style={{ color: "var(--accent-primary)" }}>›</span> password
              </label>
              <div className="relative">
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                  autoComplete="new-password"
                  className="w-full px-3 py-2.5 pr-14 text-[13px] outline-none"
                  style={inputStyle}
                  onFocus={handleFocus}
                  onBlur={handleBlur}
                  placeholder="••••••••"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[11px]"
                  style={{
                    fontFamily: "var(--font-mono)",
                    color: "var(--text-secondary)",
                    transition: "color 0.15s",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.color = "var(--text-primary)")}
                  onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-secondary)")}
                >
                  {showPassword ? "hide" : "show"}
                </button>
              </div>
            </div>

            <div>
              <label className="block text-[11px] mb-1.5" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                <span style={{ color: "var(--accent-primary)" }}>›</span> confirm password
              </label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                autoComplete="new-password"
                className="w-full px-3 py-2.5 text-[13px] outline-none"
                style={inputStyle}
                onFocus={handleFocus}
                onBlur={handleBlur}
                placeholder="••••••••"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
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
            >
              {loading ? "creating account…" : "create account"}
            </button>
          </form>

          {/* footer */}
          <div className="mt-7 pt-4 flex items-center justify-between" style={{ borderTop: "1px solid var(--widget-border)" }}>
            <p className="text-[11px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
              have an account?{" "}
              <Link
                href="/login"
                className="underline-offset-2 hover:underline"
                style={{ color: "var(--accent-primary)" }}
              >
                sign in
              </Link>
            </p>
            <span className="text-[10px]" style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)", opacity: 0.4 }}>
              v3.6.2
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
