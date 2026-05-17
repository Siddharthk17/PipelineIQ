"use client";

import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, Link, Mail, Trash2 } from "lucide-react";
import { createNotificationConfig, deleteNotificationConfig, listNotificationConfigs, testNotificationConfig } from "@/lib/api";
import type { NotificationConfig } from "@/lib/types";

function summarizeSlackConfig(config: NotificationConfig): string {
  const url = config.config?.slack_webhook_url as string | undefined;
  if (!url) return "Webhook URL not set";
  try {
    const parsed = new URL(url);
    return `${parsed.hostname}${parsed.pathname.slice(0, 18)}...`;
  } catch {
    return url.length > 32 ? `${url.slice(0, 32)}...` : url;
  }
}

function summarizeEmailConfig(config: NotificationConfig): string {
  const emailTo = config.config?.email_to;
  if (typeof emailTo === "string") return emailTo;
  if (Array.isArray(emailTo)) return emailTo.filter((v) => typeof v === "string").join(", ");
  return "Email not set";
}

export function ManageConnectionsWidget() {
  const queryClient = useQueryClient();
  const { data: configs, isLoading } = useQuery({
    queryKey: ["notificationConfigs"],
    queryFn: listNotificationConfigs,
  });

  const [slackWebhook, setSlackWebhook] = useState("");
  const [emailAddress, setEmailAddress] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusTone, setStatusTone] = useState<"success" | "error" | "info">("info");

  const createMutation = useMutation({
    mutationFn: ({ type, config }: { type: "slack" | "email"; config: Record<string, unknown> }) =>
      createNotificationConfig(type, config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notificationConfigs"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteNotificationConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notificationConfigs"] });
    },
  });

  const testMutation = useMutation({
    mutationFn: testNotificationConfig,
  });

  const slackConfigs = useMemo(() => (configs || []).filter((c) => c.type === "slack"), [configs]);
  const emailConfigs = useMemo(() => (configs || []).filter((c) => c.type === "email"), [configs]);

  const setStatus = (tone: "success" | "error" | "info", message: string) => {
    setStatusTone(tone);
    setStatusMessage(message);
  };

  const handleSlackConnect = async () => {
    if (!slackWebhook.trim()) {
      setStatus("error", "Slack webhook URL is required.");
      return;
    }
    setStatusMessage(null);
    try {
      await createMutation.mutateAsync({
        type: "slack",
        config: { slack_webhook_url: slackWebhook.trim() },
      });
      setSlackWebhook("");
      setStatus("success", "Slack connection saved. Click Verify to send a test notification.");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to create Slack connection.";
      setStatus("error", message);
    }
  };

  const handleEmailConnect = async () => {
    const email = emailAddress.trim();
    if (!email || !email.includes("@")) {
      setStatus("error", "A valid email address is required.");
      return;
    }
    setStatusMessage(null);
    try {
      await createMutation.mutateAsync({
        type: "email",
        config: { email_to: email },
      });
      setEmailAddress("");
      setStatus("success", "Email connection saved. Click Verify to send a test email.");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to create email connection.";
      setStatus("error", message);
    }
  };

  const handleVerify = async (configId: string) => {
    setStatusMessage(null);
    try {
      const result = await testMutation.mutateAsync(configId);
      setStatus("success", result.detail || "Test notification sent.");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to send test notification.";
      setStatus("error", message);
    }
  };

  const handleDelete = async (configId: string) => {
    setStatusMessage(null);
    try {
      await deleteMutation.mutateAsync(configId);
      setStatus("info", "Connection removed.");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to remove connection.";
      setStatus("error", message);
    }
  };

  if (isLoading) {
    return <div className="p-4 text-[var(--text-secondary)]">Loading connections...</div>;
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto pr-2 space-y-4">
      <div className="text-xs text-[var(--text-secondary)]">
        Connect Slack and email to receive pipeline completion and failure notifications. Use Verify to confirm delivery.
      </div>

      <div className="grid grid-cols-1 gap-3">
        <div className="p-3 rounded-lg" style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--widget-border)" }}>
          <div className="flex items-center gap-2 text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wider mb-2">
            <Link className="w-3.5 h-3.5 text-[var(--accent-primary)]" /> Slack
          </div>
          <div className="flex items-center gap-2">
            <input
              value={slackWebhook}
              onChange={(e) => setSlackWebhook(e.target.value)}
              placeholder="https://hooks.slack.com/services/..."
              className="flex-1 px-2 py-1.5 text-xs rounded bg-[var(--bg-base)] border outline-none"
              style={{ borderColor: "var(--widget-border)", color: "var(--text-primary)" }}
            />
            <button
              onClick={handleSlackConnect}
              disabled={createMutation.isPending}
              className="px-3 py-1.5 text-xs rounded font-medium bg-[var(--accent-primary)] text-[var(--bg-base)] hover:opacity-90 disabled:opacity-60"
            >
              {createMutation.isPending ? "Saving..." : "Connect"}
            </button>
          </div>
        </div>

        <div className="p-3 rounded-lg" style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--widget-border)" }}>
          <div className="flex items-center gap-2 text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wider mb-2">
            <Mail className="w-3.5 h-3.5 text-[var(--accent-primary)]" /> Email
          </div>
          <div className="flex items-center gap-2">
            <input
              value={emailAddress}
              onChange={(e) => setEmailAddress(e.target.value)}
              placeholder="you@company.com"
              className="flex-1 px-2 py-1.5 text-xs rounded bg-[var(--bg-base)] border outline-none"
              style={{ borderColor: "var(--widget-border)", color: "var(--text-primary)" }}
            />
            <button
              onClick={handleEmailConnect}
              disabled={createMutation.isPending}
              className="px-3 py-1.5 text-xs rounded font-medium bg-[var(--accent-primary)] text-[var(--bg-base)] hover:opacity-90 disabled:opacity-60"
            >
              {createMutation.isPending ? "Saving..." : "Connect"}
            </button>
          </div>
        </div>
      </div>

      {statusMessage && (
        <div
          className={`px-3 py-2 text-xs rounded-lg ${
            statusTone === "success"
              ? "bg-emerald-500/20 text-emerald-400"
              : statusTone === "error"
              ? "bg-red-500/20 text-red-400"
              : "bg-[var(--bg-surface)] text-[var(--text-secondary)]"
          }`}
        >
          {statusMessage}
        </div>
      )}

      <div className="space-y-2">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
          Active Connections
        </div>

        {(!configs || configs.length === 0) && (
          <div className="text-xs text-[var(--text-secondary)]">No connections configured yet.</div>
        )}

        {slackConfigs.map((config) => (
          <div key={config.id} className="flex items-center justify-between p-2 rounded-lg" style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--widget-border)" }}>
            <div className="flex flex-col">
              <span className="text-xs font-medium text-[var(--text-primary)]">Slack</span>
              <span className="text-[10px] text-[var(--text-secondary)]">{summarizeSlackConfig(config)}</span>
              <span className="text-[10px] text-[var(--text-secondary)]">Events: {(config.events || []).join(", ") || "pipeline_completed, pipeline_failed"}</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => handleVerify(config.id)}
                className="flex items-center gap-1 px-2 py-1 text-[10px] rounded bg-[var(--interactive-hover)] text-[var(--text-primary)] hover:text-[var(--accent-success)]"
              >
                <CheckCircle className="w-3 h-3" /> Verify
              </button>
              <button
                onClick={() => handleDelete(config.id)}
                className="p-1 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--accent-error)]"
                title="Remove"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        ))}

        {emailConfigs.map((config) => (
          <div key={config.id} className="flex items-center justify-between p-2 rounded-lg" style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--widget-border)" }}>
            <div className="flex flex-col">
              <span className="text-xs font-medium text-[var(--text-primary)]">Email</span>
              <span className="text-[10px] text-[var(--text-secondary)]">{summarizeEmailConfig(config)}</span>
              <span className="text-[10px] text-[var(--text-secondary)]">Events: {(config.events || []).join(", ") || "pipeline_completed, pipeline_failed"}</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => handleVerify(config.id)}
                className="flex items-center gap-1 px-2 py-1 text-[10px] rounded bg-[var(--interactive-hover)] text-[var(--text-primary)] hover:text-[var(--accent-success)]"
              >
                <CheckCircle className="w-3 h-3" /> Verify
              </button>
              <button
                onClick={() => handleDelete(config.id)}
                className="p-1 rounded hover:bg-[var(--interactive-hover)] text-[var(--text-secondary)] hover:text-[var(--accent-error)]"
                title="Remove"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
