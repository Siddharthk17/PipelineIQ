"use client";

import { useState, useEffect, useCallback } from "react";
import { Shield, Lock, Eye, Trash2, Plus, X } from "lucide-react";
import {
  listColumnPolicies,
  createColumnPolicy,
  deleteColumnPolicy,
} from "@/lib/api";
import type { ColumnPolicyRecord } from "@/lib/types";
import { ApiError } from "@/lib/api";

interface ColumnPolicyManagerProps {
  fileId: string;
  fileColumns: string[];
  initialPiiSuggestions?: string[];
}

const MASK_PATTERNS = [
  { value: "email", label: "Email" },
  { value: "phone", label: "Phone" },
  { value: "credit_card", label: "Credit Card" },
  { value: "ssn", label: "SSN" },
  { value: "name", label: "Name" },
  { value: "default", label: "Default" },
];

export function ColumnPolicyManager({
  fileId,
  fileColumns,
  initialPiiSuggestions,
}: ColumnPolicyManagerProps) {
  const [policies, setPolicies] = useState<ColumnPolicyRecord[]>([]);
  const [piiSuggestions, setPiiSuggestions] = useState<string[]>(initialPiiSuggestions || []);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [selectedColumn, setSelectedColumn] = useState("");
  const [policyType, setPolicyType] = useState<"redacted" | "masked">("redacted");
  const [maskPattern, setMaskPattern] = useState("");
  const [allowedRoles, setAllowedRoles] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const loadPolicies = useCallback(async () => {
    try {
      setError(null);
      const data = await listColumnPolicies(fileId);
      setPolicies(data.policies);
      if (data.pii_suggestions) {
        setPiiSuggestions(data.pii_suggestions);
      }
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.message : "Failed to load policies";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [fileId]);

  useEffect(() => {
    loadPolicies();
  }, [loadPolicies]);

  const handleCreate = async () => {
    if (!selectedColumn) {
      setFormError("Select a column");
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await createColumnPolicy({
        file_id: fileId,
        column_name: selectedColumn,
        policy: policyType,
        mask_pattern: policyType === "masked" ? maskPattern || "default" : null,
        allowed_roles: allowedRoles
          ? allowedRoles.split(",").map((r) => r.trim()).filter(Boolean)
          : [],
      });
      setShowForm(false);
      setSelectedColumn("");
      setPolicyType("redacted");
      setMaskPattern("");
      setAllowedRoles("");
      await loadPolicies();
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.message : "Failed to create policy";
      setFormError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (policyId: string) => {
    try {
      await deleteColumnPolicy(policyId);
      await loadPolicies();
    } catch (err: unknown) {
      const message = err instanceof ApiError ? err.message : "Failed to delete policy";
      setError(message);
    }
  };

  const unassignedColumns = fileColumns.filter(
    (c) => !policies.find((p) => p.column_name === c) && !piiSuggestions.includes(c)
  );

  const piiColumnsWithoutPolicy = piiSuggestions.filter(
    (c) => !policies.find((p) => p.column_name === c)
  );

  return (
    <div className="rounded-lg border border-[var(--widget-border)] bg-[var(--bg-surface)] mb-6">
      <div className="flex items-center justify-between border-b border-[var(--widget-border)] p-4">
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-[var(--accent-primary)]" />
          <h2 className="text-sm font-medium">Column Security Policies</h2>
          {policies.length > 0 && (
            <span className="rounded-full bg-[var(--accent-primary)]/10 px-2 py-0.5 text-[10px] font-medium text-[var(--accent-primary)]">
              {policies.length} active
            </span>
          )}
        </div>
        <button
          onClick={() => {
            setShowForm(!showForm);
            setFormError(null);
            if (piiColumnsWithoutPolicy.length > 0) {
              setSelectedColumn(piiColumnsWithoutPolicy[0]);
            }
          }}
          className="flex items-center gap-1 rounded border border-[var(--widget-border)] px-2 py-1 text-xs transition-colors hover:bg-[var(--interactive-hover)]"
          data-testid="add-policy-btn"
        >
          <Plus className="h-3 w-3" />
          Add Policy
        </button>
      </div>

      {showForm && (
        <div className="border-b border-[var(--widget-border)] p-4" data-testid="policy-form">
          {formError && (
            <div className="mb-3 rounded border border-[var(--accent-error)]/30 bg-[var(--accent-error)]/5 px-3 py-2 text-xs text-[var(--accent-error)]">
              {formError}
            </div>
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-[10px] font-medium text-[var(--text-secondary)]">
                Column
              </label>
              <select
                value={selectedColumn}
                onChange={(e) => setSelectedColumn(e.target.value)}
                className="w-full rounded border border-[var(--widget-border)] bg-[var(--bg-base)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                data-testid="policy-column-select"
              >
                <option value="">Select column...</option>
                {piiColumnsWithoutPolicy.length > 0 && (
                  <optgroup label="PII Detected (no policy)">
                    {piiColumnsWithoutPolicy.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </optgroup>
                )}
                {fileColumns.filter((c) => !piiColumnsWithoutPolicy.includes(c)).length > 0 && (
                  <optgroup label="All Columns">
                    {fileColumns
                      .filter((c) => !piiColumnsWithoutPolicy.includes(c))
                      .map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                  </optgroup>
                )}
              </select>
            </div>

            <div>
              <label className="mb-1 block text-[10px] font-medium text-[var(--text-secondary)]">
                Policy
              </label>
              <select
                value={policyType}
                onChange={(e) => setPolicyType(e.target.value as "redacted" | "masked")}
                className="w-full rounded border border-[var(--widget-border)] bg-[var(--bg-base)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                data-testid="policy-type-select"
              >
                <option value="redacted">Redact (drop column)</option>
                <option value="masked">Mask (hide value)</option>
              </select>
            </div>

            {policyType === "masked" && (
              <div>
                <label className="mb-1 block text-[10px] font-medium text-[var(--text-secondary)]">
                  Mask Pattern
                </label>
                <select
                  value={maskPattern}
                  onChange={(e) => setMaskPattern(e.target.value)}
                  className="w-full rounded border border-[var(--widget-border)] bg-[var(--bg-base)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
                  data-testid="mask-pattern-select"
                >
                  <option value="">Default</option>
                  {MASK_PATTERNS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div>
              <label className="mb-1 block text-[10px] font-medium text-[var(--text-secondary)]">
                Allowed Roles (comma-separated)
              </label>
              <input
                type="text"
                value={allowedRoles}
                onChange={(e) => setAllowedRoles(e.target.value)}
                placeholder="admin"
                className="w-full rounded border border-[var(--widget-border)] bg-[var(--bg-base)] px-2 py-1.5 text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)]"
                data-testid="allowed-roles-input"
              />
              <p className="mt-0.5 text-[10px] text-[var(--text-muted)]">
                Leave empty to apply to all viewers. Admins always bypass policies.
              </p>
            </div>
          </div>

          <div className="mt-3 flex items-center gap-2">
            <button
              onClick={handleCreate}
              disabled={submitting || !selectedColumn}
              className="rounded bg-[var(--accent-primary)] px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
              data-testid="save-policy-btn"
            >
              {submitting ? "Saving..." : "Save Policy"}
            </button>
            <button
              onClick={() => setShowForm(false)}
              className="rounded border border-[var(--widget-border)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--interactive-hover)]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="mx-4 mt-3 rounded border border-[var(--accent-error)]/30 bg-[var(--accent-error)]/5 px-3 py-2 text-xs text-[var(--accent-error)]">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)]" />
        </div>
      ) : (
        <div className="p-4">
          {policies.length === 0 && !loading ? (
            <p className="text-xs text-[var(--text-muted)] py-4 text-center">
              No column policies configured. Restricted data is visible to all users.
            </p>
          ) : (
            <div className="space-y-2">
              {policies.map((policy) => (
                <div
                  key={policy.id}
                  className="flex items-center justify-between rounded border border-[var(--widget-border)] bg-[var(--bg-elevated)] px-3 py-2"
                  data-testid={`policy-row-${policy.column_name}`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-xs font-mono text-[var(--text-primary)] truncate">
                      {policy.column_name}
                    </span>
                    <span
                      className={`rounded px-2 py-0.5 text-[10px] font-medium ${
                        policy.policy === "redacted"
                          ? "bg-red-500/10 text-red-400"
                          : "bg-orange-500/10 text-orange-400"
                      }`}
                    >
                      {policy.policy === "redacted" ? (
                        <span className="flex items-center gap-1">
                          <Lock className="h-2.5 w-2.5" />
                          Redacted
                        </span>
                      ) : (
                        <span className="flex items-center gap-1">
                          <Eye className="h-2.5 w-2.5" />
                          Masked
                          {policy.mask_pattern && ` (${policy.mask_pattern})`}
                        </span>
                      )}
                    </span>
                    {policy.allowed_roles.length > 0 && (
                      <span className="text-[10px] text-[var(--text-muted)]">
                        Allowed: {policy.allowed_roles.join(", ")}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => handleDelete(policy.id)}
                    className="ml-2 flex-shrink-0 rounded p-1 text-[var(--text-muted)] hover:text-[var(--accent-error)] hover:bg-[var(--accent-error)]/10 transition-colors"
                    title="Remove policy"
                    data-testid={`delete-policy-${policy.column_name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
