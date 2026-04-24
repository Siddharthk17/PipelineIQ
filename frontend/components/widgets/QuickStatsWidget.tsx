"use client";

import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { getFiles, getPipelineRuns } from "@/lib/api";

function AnimatedNumber({ value }: { value: number }) {
  const [displayValue, setDisplayValue] = useState(0);

  useEffect(() => {
    let start = 0;
    const end = value;
    if (start === end) return;
    
    const duration = 1000;
    const incrementTime = Math.max(16, Math.floor(duration / end));
    
    const timer = setInterval(() => {
      start += Math.ceil(end / (duration / incrementTime));
      if (start >= end) {
        setDisplayValue(end);
        clearInterval(timer);
      } else {
        setDisplayValue(start);
      }
    }, incrementTime);

    return () => clearInterval(timer);
  }, [value]);

  return <span>{displayValue}</span>;
}

export function QuickStatsWidget() {
  const { data: files } = useQuery({ queryKey: ["files"], queryFn: getFiles, refetchInterval: 30000 });
  const { data: runs } = useQuery({ queryKey: ["pipelineRuns", 1, 100], queryFn: () => getPipelineRuns(1, 100), refetchInterval: 30000 });

  const runsArray = Array.isArray(runs) ? runs : [];
  const filesArray = Array.isArray(files) ? files : [];

  const totalRuns = runsArray.length;
  const successfulRuns = runsArray.filter(r => r.status === "COMPLETED" || r.status === "HEALED").length;
  const filesUploaded = filesArray.length;
  const runsWithDuration = runsArray.filter(r => r.duration_ms != null && r.duration_ms > 0);
  const avgDuration = runsWithDuration.length ? Math.round(runsWithDuration.reduce((acc, r) => acc + (r.duration_ms || 0), 0) / runsWithDuration.length) : 0;

  const stats = [
    { label: "Total Pipeline Runs", value: totalRuns, trend: "+12% this week" },
    { label: "Successful Runs", value: successfulRuns, trend: "+5% this week" },
    { label: "Files Uploaded", value: filesUploaded, trend: "+2 this week" },
    { label: "Avg Pipeline Duration", value: avgDuration < 1000 ? `${avgDuration}ms` : `${(avgDuration / 1000).toFixed(1)}s`, trend: "-1.2s this week" },
  ];

  return (
    <div className="grid grid-cols-2 grid-rows-2 gap-2 sm:gap-4 h-full w-full">
      {stats.map((stat, i) => (
        <motion.div
          key={stat.label}
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: i * 0.1 }}
          className="flex flex-col justify-center items-center text-center p-2 rounded-lg min-h-0 min-w-0"
          style={{ backgroundColor: "var(--bg-surface)", border: "1px solid var(--widget-border)" }}
        >
          <span className="text-[10px] font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-1 truncate w-full">{stat.label}</span>
          <div className="text-2xl sm:text-3xl font-bold text-[var(--text-primary)] font-mono truncate w-full">
            {typeof stat.value === "number" ? <AnimatedNumber value={stat.value} /> : stat.value}
          </div>
          <span className="text-[9px] text-[var(--accent-success)] mt-1 truncate w-full">{stat.trend}</span>
        </motion.div>
      ))}
    </div>
  );
}
