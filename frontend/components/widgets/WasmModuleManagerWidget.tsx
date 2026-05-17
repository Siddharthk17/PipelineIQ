"use client";

import React, { useState, useRef, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listWasmModules,
  uploadWasmModule,
  deleteWasmModule,
  validateWasmModule,
} from "@/lib/api";
import type { WasmModule, WasmModuleExport } from "@/lib/types";
import {
  Upload,
  Trash2,
  FileCode,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Cpu,
  Hash,
  Clock,
} from "lucide-react";

export function WasmModuleManagerWidget() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [moduleName, setModuleName] = useState("");
  const [moduleDescription, setModuleDescription] = useState("");
  const [fuelBudget, setFuelBudget] = useState(10000000);
  const [validationResult, setValidationResult] = useState<{
    is_valid: boolean;
    errors: string[];
    warnings: string[];
    exports: WasmModuleExport[];
  } | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["wasmModules"],
    queryFn: listWasmModules,
    staleTime: 30000,
  });

  const modules: WasmModule[] = data?.modules ?? [];

  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setSelectedFile(file);
    setUploadError(null);
    setValidationResult(null);

    if (!file.name.endsWith(".wasm")) {
      setUploadError("Please select a .wasm file");
      return;
    }

    try {
      const result = await validateWasmModule(file);
      setValidationResult({
        is_valid: result.is_valid,
        errors: result.errors,
        warnings: result.warnings,
        exports: result.exports,
      });
      if (!moduleName) {
        setModuleName(file.name.replace(/\.wasm$/, ""));
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Validation failed");
    }
  }, [moduleName]);

  const handleUpload = async () => {
    if (!selectedFile || !moduleName) return;
    setUploading(true);
    setUploadError(null);

    try {
      await uploadWasmModule(selectedFile, moduleName, moduleDescription || undefined, fuelBudget);
      queryClient.invalidateQueries({ queryKey: ["wasmModules"] });
      setSelectedFile(null);
      setModuleName("");
      setModuleDescription("");
      setFuelBudget(10000000);
      setValidationResult(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`Delete module "${name}"?`)) return;
    try {
      await deleteWasmModule(id);
      queryClient.invalidateQueries({ queryKey: ["wasmModules"] });
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="p-3 border-b">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <FileCode className="w-4 h-4" />
          Wasm Module Manager
        </h3>
        <p className="text-xs text-muted-foreground mt-1">
          Upload and manage WebAssembly UDF modules for wasm_compute steps
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Upload Section */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".wasm"
              onChange={handleFileSelect}
              className="text-xs flex-1"
            />
          </div>

          {selectedFile && (
            <div className="space-y-2">
              <input
                type="text"
                placeholder="Module name (unique)"
                value={moduleName}
                onChange={(e) => setModuleName(e.target.value)}
                className="w-full px-2 py-1 text-xs border rounded"
              />
              <input
                type="text"
                placeholder="Description (optional)"
                value={moduleDescription}
                onChange={(e) => setModuleDescription(e.target.value)}
                className="w-full px-2 py-1 text-xs border rounded"
              />
              <div className="flex items-center gap-2">
                <Cpu className="w-3 h-3" />
                <label className="text-xs">Fuel budget:</label>
                <input
                  type="number"
                  value={fuelBudget}
                  onChange={(e) => setFuelBudget(Number(e.target.value))}
                  className="w-24 px-2 py-1 text-xs border rounded"
                  min={1000}
                  max={100000000}
                />
              </div>

              {validationResult && (
                <div className={`text-xs p-2 rounded ${validationResult.is_valid ? "bg-green-50 dark:bg-green-950" : "bg-red-50 dark:bg-red-950"}`}>
                  {validationResult.is_valid ? (
                    <div className="flex items-center gap-1 text-green-700 dark:text-green-300">
                      <CheckCircle className="w-3 h-3" />
                      <span>Valid — {validationResult.exports.length} export(s) found</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1 text-red-700 dark:text-red-300">
                      <XCircle className="w-3 h-3" />
                      <span>Invalid</span>
                    </div>
                  )}
                  {validationResult.errors.map((err, i) => (
                    <p key={i} className="text-red-600 dark:text-red-400 mt-1">• {err}</p>
                  ))}
                  {validationResult.warnings.map((warn, i) => (
                    <p key={i} className="text-yellow-600 dark:text-yellow-400 mt-1">• {warn}</p>
                  ))}
                  {validationResult.exports.length > 0 && (
                    <div className="mt-2">
                      <p className="font-medium">Exports:</p>
                      {validationResult.exports.map((exp, i) => (
                        <p key={i} className="font-mono text-[10px] ml-2">
                          {exp.name}({exp.params.join(", ")}) → {exp.result ?? "void"}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <button
                onClick={handleUpload}
                disabled={uploading || !moduleName || !!(validationResult && !validationResult.is_valid)}
                className="w-full px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-1"
              >
                <Upload className="w-3 h-3" />
                {uploading ? "Uploading..." : "Upload Module"}
              </button>
            </div>
          )}

          {uploadError && (
            <div className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400">
              <AlertTriangle className="w-3 h-3" />
              {uploadError}
            </div>
          )}
        </div>

        {/* Module List */}
        <div className="space-y-2">
          <h4 className="text-xs font-semibold flex items-center gap-1">
            <Hash className="w-3 h-3" />
            Registered Modules ({modules.length})
          </h4>

          {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}
          {error && <p className="text-xs text-red-500">Failed to load modules</p>}

          {modules.map((mod) => (
            <div
              key={mod.id}
              className="p-2 border rounded text-xs space-y-1"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">{mod.name}</span>
                <button
                  onClick={() => handleDelete(mod.id, mod.name)}
                  className="text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
              {mod.description && (
                <p className="text-muted-foreground truncate">{mod.description}</p>
              )}
              <div className="flex items-center gap-3 text-muted-foreground">
                <span className="flex items-center gap-1">
                  <FileCode className="w-3 h-3" />
                  {formatSize(mod.file_size_bytes)}
                </span>
                <span className="flex items-center gap-1">
                  <Cpu className="w-3 h-3" />
                  {mod.fuel_budget.toLocaleString()} fuel
                </span>
                <span className="flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {mod.exports.length} export(s)
                </span>
              </div>
              <div className="flex flex-wrap gap-1">
                {mod.exports.map((exp, i) => (
                  <span
                    key={i}
                    className="px-1.5 py-0.5 bg-secondary text-secondary-foreground rounded text-[10px] font-mono"
                  >
                    {exp.name}
                  </span>
                ))}
              </div>
            </div>
          ))}

          {modules.length === 0 && !isLoading && (
            <p className="text-xs text-muted-foreground text-center py-4">
              No Wasm modules registered yet
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
