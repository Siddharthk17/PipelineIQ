"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  deleteWasmModule,
  listWasmModules,
  uploadWasmModule,
  validateWasmModule,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { WasmModule, WasmModuleExport } from "@/lib/types";
import { Copy, FileCode, Loader2, Trash2, Upload, XCircle, CheckCircle2 } from "lucide-react";

export default function WasmModulesPage() {
  const router = useRouter();
  const { user, isLoading } = useAuth();
  const [modules, setModules] = useState<WasmModule[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [validating, setValidating] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState<{
    isValid: boolean;
    exports: WasmModuleExport[];
    errors: string[];
    warnings: string[];
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isLoading && !user) {
      router.push("/login");
    }
  }, [isLoading, router, user]);

  useEffect(() => {
    if (user) {
      fetchModules();
    }
  }, [user]);

  const fetchModules = async () => {
    setLoading(true);
    try {
      const data = await listWasmModules();
      setModules(data.modules);
    } catch {
      setModules([]);
    } finally {
      setLoading(false);
    }
  };

  const handleFileSelect = useCallback(
    async (file: File) => {
      if (!file.name.endsWith(".wasm")) {
        setUploadError("Only .wasm files are supported");
        return;
      }
      if (file.size > 10 * 1024 * 1024) {
        setUploadError("File exceeds 10 MB limit");
        return;
      }

      setUploading(true);
      setUploadError(null);
      setValidationResult(null);

      try {
        const mod = await uploadWasmModule(file, file.name.replace(".wasm", ""));
        setModules((prev) => [mod, ...prev]);
      } catch (err: unknown) {
        const message =
          err instanceof Error ? err.message : "Upload failed";
        setUploadError(message);
      } finally {
        setUploading(false);
      }
    },
    [],
  );

  const handleValidateFile = useCallback(async (file: File) => {
    if (!file.name.endsWith(".wasm")) return;

    setValidating(file.name);
    setValidationResult(null);
    try {
      const result = await validateWasmModule(file);
      setValidationResult({
        isValid: result.is_valid,
        exports: result.exports,
        errors: result.errors,
        warnings: result.warnings,
      });
    } catch {
      setValidationResult({
        isValid: false,
        exports: [],
        errors: ["Validation request failed"],
        warnings: [],
      });
    } finally {
      setValidating(null);
    }
  }, []);

  const handleDelete = async (moduleId: string, moduleName: string) => {
    if (!confirm(`Delete "${moduleName}"?`)) return;

    try {
      await deleteWasmModule(moduleId);
      setModules((prev) => prev.filter((m) => m.id !== moduleId));
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Delete failed";
      alert(message);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  if (isLoading || !user) {
    return (
      <main className="flex h-screen items-center justify-center bg-[var(--bg-base)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)]" />
      </main>
    );
  }

  return (
    <main className="flex h-screen w-screen flex-col bg-[var(--bg-base)] text-[var(--text-primary)]">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[var(--widget-border)] px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">Wasm Modules</h1>
          <p className="text-sm text-[var(--text-secondary)]">
            Upload custom WebAssembly functions to use in pipeline wasm_compute steps.
          </p>
        </div>
        <button
          onClick={() => router.push("/")}
          className="rounded border border-[var(--widget-border)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--interactive-hover)]"
        >
          Back to Dashboard
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-4xl space-y-6">
          {/* Upload zone */}
          <div
            role="button"
            tabIndex={0}
            className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-[var(--widget-border)] px-6 py-10 transition-colors hover:border-[var(--accent-primary)] hover:bg-[var(--bg-surface)]"
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const file = e.dataTransfer.files[0];
              if (file) handleFileSelect(file);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                fileInputRef.current?.click();
              }
            }}
            data-testid="wasm-upload-zone"
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".wasm"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleFileSelect(file);
              }}
              data-testid="wasm-file-input"
            />
            {uploading ? (
              <div className="flex flex-col items-center gap-3">
                <Loader2 className="h-8 w-8 animate-spin text-[var(--accent-primary)]" />
                <p className="text-sm text-[var(--text-secondary)]">
                  Uploading and validating…
                </p>
              </div>
            ) : (
              <>
                <Upload className="mb-3 h-10 w-10 text-[var(--text-secondary)]" />
                <p className="text-sm font-medium">
                  Drop .wasm file here or click to upload
                </p>
                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                  Max 10 MB — compiled WebAssembly binary only
                </p>
              </>
            )}
          </div>

          {uploadError && (
            <div
              className="flex items-center gap-2 rounded-lg border border-[var(--accent-error)]/30 bg-[var(--accent-error)]/10 px-4 py-3 text-sm text-[var(--accent-error)]"
              data-testid="upload-error"
            >
              <XCircle className="h-4 w-4 shrink-0" />
              {uploadError}
              <button
                className="ml-auto text-xs underline"
                onClick={() => setUploadError(null)}
              >
                Dismiss
              </button>
            </div>
          )}

          {/* Validate before upload */}
          <details className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)]">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
              Validate a .wasm file before uploading
            </summary>
            <div className="border-t border-[var(--widget-border)] px-4 py-4">
              <div className="flex items-center gap-3">
                <input
                  type="file"
                  accept=".wasm"
                  className="text-sm"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleValidateFile(file);
                  }}
                />
                {validating && (
                  <span className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Validating {validating}…
                  </span>
                )}
              </div>
              {validationResult && (
                <div className="mt-4 space-y-3">
                  {validationResult.isValid ? (
                    <div className="flex items-center gap-2 text-sm text-[var(--accent-success)]">
                      <CheckCircle2 className="h-4 w-4" />
                      Valid Wasm module — {validationResult.exports.length} exported function(s)
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 text-sm text-[var(--accent-error)]">
                      <XCircle className="h-4 w-4" />
                      Invalid module
                    </div>
                  )}
                  {validationResult.exports.length > 0 && (
                    <div>
                      <p className="mb-1 text-xs font-medium text-[var(--text-secondary)]">
                        Exported functions:
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {validationResult.exports.map((exp) => (
                          <code
                            key={exp.name}
                            className="rounded bg-[var(--bg-base)] px-2 py-1 text-xs font-mono"
                          >
                            {exp.name}({exp.params.join(", ")}) → {exp.result ?? "void"}
                          </code>
                        ))}
                      </div>
                    </div>
                  )}
                  {validationResult.errors.length > 0 && (
                    <div>
                      <p className="mb-1 text-xs font-medium text-[var(--accent-error)]">
                        Errors:
                      </p>
                      <ul className="list-inside list-disc text-xs text-[var(--accent-error)]">
                        {validationResult.errors.map((err, i) => (
                          <li key={i}>{err}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {validationResult.warnings.length > 0 && (
                    <div>
                      <p className="mb-1 text-xs font-medium text-yellow-500">
                        Warnings:
                      </p>
                      <ul className="list-inside list-disc text-xs text-yellow-500">
                        {validationResult.warnings.map((w, i) => (
                          <li key={i}>{w}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          </details>

          {/* Compile guide */}
          <details className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)]">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
              How to compile Rust to .wasm
            </summary>
            <div className="border-t border-[var(--widget-border)] px-4 py-4">
              <pre className="overflow-x-auto rounded bg-[var(--bg-base)] p-4 text-xs font-mono">
{`# 1. Create a Rust library project
cargo new --lib my_functions

# 2. In Cargo.toml, set crate type:
[lib]
crate-type = ["cdylib"]

# 3. Write your function in src/lib.rs:
#[no_mangle]
pub extern "C" fn my_function(x: f64, y: f64) -> f64 {
    x * y + 1.0
}

# 4. Install the wasm32 target:
rustup target add wasm32-unknown-unknown

# 5. Compile to WebAssembly:
cargo build --target wasm32-unknown-unknown --release

# 6. Find the .wasm file:
ls target/wasm32-unknown-unknown/release/*.wasm`}
              </pre>
              <p className="mt-3 text-xs text-[var(--text-secondary)]">
                Your function receives each input column as an f64 argument and returns an f64.
                All type conversions happen in PipelineIQ.
              </p>
            </div>
          </details>

          {/* Module list */}
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-[var(--accent-primary)]" />
            </div>
          ) : modules.length === 0 ? (
            <div className="py-12 text-center text-sm text-[var(--text-secondary)]">
              No Wasm modules yet. Upload your first .wasm file above.
            </div>
          ) : (
            <div className="space-y-3" data-testid="wasm-module-list">
              {modules.map((mod) => (
                <div
                  key={mod.id}
                  className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] p-4"
                  data-testid={`wasm-module-${mod.id}`}
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-start gap-3">
                      <FileCode className="mt-0.5 h-5 w-5 shrink-0 text-[var(--accent-primary)]" />
                      <div>
                        <h3 className="text-sm font-semibold">{mod.name}</h3>
                        {mod.description && (
                          <p className="text-xs text-[var(--text-secondary)]">
                            {mod.description}
                          </p>
                        )}
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                          {(mod.file_size_bytes / 1024).toFixed(1)} KB · SHA256: {mod.sha256_hash.slice(0, 12)}…
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={() => handleDelete(mod.id, mod.name)}
                      className="rounded p-1.5 text-[var(--accent-error)] transition-colors hover:bg-[var(--accent-error)]/10"
                      title="Delete module"
                      data-testid={`delete-module-${mod.id}`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>

                  {/* Validation status */}
                  <div className="mt-3 flex items-center gap-4">
                    <span className="flex items-center gap-1.5 rounded-full bg-[var(--accent-success)]/10 px-2.5 py-0.5 text-xs font-medium text-[var(--accent-success)]">
                      <CheckCircle2 className="h-3 w-3" />
                      Valid
                    </span>
                    <span className="text-xs text-[var(--text-secondary)]">
                      Fuel budget: {(mod.fuel_budget / 1_000_000).toFixed(0)}M instructions
                    </span>
                  </div>

                  {/* Exported functions */}
                  {mod.exports.length > 0 && (
                    <div className="mt-3">
                      <p className="mb-1.5 text-xs font-medium text-[var(--text-secondary)]">
                        Exported functions:
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {mod.exports.map((exp) => (
                          <code
                            key={exp.name}
                            className="rounded bg-[var(--bg-base)] px-2.5 py-1 text-xs font-mono"
                          >
                            {exp.name}
                          </code>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Module ID */}
                  <div className="mt-3 flex items-center gap-2 rounded bg-[var(--bg-base)] px-3 py-2">
                    <span className="text-xs text-[var(--text-secondary)]">Module ID:</span>
                    <code className="flex-1 truncate text-xs font-mono" data-testid={`module-id-${mod.id}`}>
                      {mod.id}
                    </code>
                    <button
                      onClick={() => copyToClipboard(mod.id)}
                      className="rounded p-1 text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                      title="Copy module ID"
                    >
                      <Copy className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
