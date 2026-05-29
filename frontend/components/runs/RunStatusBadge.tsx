"use client";

import React from "react";
import { AlertTriangle, AlertOctagon, CheckCircle, Clock, XCircle } from "lucide-react";
import type { PipelineRun } from "@/lib/types";

interface RunStatusBadgeProps {
  status: PipelineRun["status"];
  className?: string;
}

type StatusConfig = {
  label: string;
  icon: React.ReactNode;
  colorVar: string;
  className: string;
};

function getStatusConfig(status: PipelineRun["status"]): StatusConfig {
  const configs: Record<PipelineRun["status"], StatusConfig> = {
    PENDING: {
      label: "Pending",
      icon: <Clock className="w-4 h-4" />,
      colorVar: "var(--text-secondary)",
      className: "status--pending",
    },
    RUNNING: {
      label: "Running",
      icon: <div className="w-3 h-3 rounded-full bg-[var(--accent-warning)] animate-pulse" />,
      colorVar: "var(--accent-warning)",
      className: "status--running",
    },
    HEALING: {
      label: "Healing",
      icon: <div className="w-3 h-3 rounded-full bg-[var(--accent-primary)] animate-pulse" />,
      colorVar: "var(--accent-primary)",
      className: "status--healing",
    },
    HEALED: {
      label: "Healed",
      icon: <CheckCircle className="w-4 h-4" />,
      colorVar: "var(--accent-success)",
      className: "status--healed",
    },
    COMPLETED: {
      label: "Success",
      icon: <CheckCircle className="w-4 h-4" />,
      colorVar: "var(--accent-success)",
      className: "status--success",
    },
    FAILED: {
      label: "Failed",
      icon: <XCircle className="w-4 h-4" />,
      colorVar: "var(--accent-error)",
      className: "status--failed",
    },
    CANCELLED: {
      label: "Cancelled",
      icon: <XCircle className="w-4 h-4" />,
      colorVar: "var(--text-secondary)",
      className: "status--cancelled",
    },
    TIMEOUT: {
      label: "Timeout",
      icon: <AlertTriangle className="w-4 h-4" />,
      colorVar: "var(--accent-error)",
      className: "status--timeout",
    },
    CONTRACT_VIOLATION: {
      label: "Violation",
      icon: <AlertOctagon className="w-4 h-4" />,
      colorVar: "var(--accent-error)",
      className: "status--contract-violation",
    },
  };

  return (
    configs[status] ?? {
      label: status,
      icon: null,
      colorVar: "var(--text-secondary)",
      className: "status--unknown",
    }
  );
}

export function RunStatusBadge({ status, className = "" }: RunStatusBadgeProps) {
  const config = getStatusConfig(status);

  return (
    <div
      className={`flex items-center gap-2 ${className}`}
      data-testid="run-status-badge"
      data-status={status.toLowerCase()}
    >
      <span style={{ color: config.colorVar }}>{config.icon}</span>
      <span
        className="text-xs font-bold tracking-wider uppercase"
        style={{ color: config.colorVar }}
      >
        {config.label}
      </span>
    </div>
  );
}
