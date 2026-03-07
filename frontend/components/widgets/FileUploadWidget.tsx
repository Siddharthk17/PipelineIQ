"use client";

import React, { useCallback, useState, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { UploadCloud, CheckCircle, XCircle, Copy } from "lucide-react";
import { uploadFile } from "@/lib/api";
import { motion } from "motion/react";

export function FileUploadWidget() {
  const [dragActive, setDragActive] = useState(false);
  const [uploadState, setUploadState] = useState<"IDLE" | "UPLOADING" | "SUCCESS" | "ERROR">("IDLE");
  const [lastUploaded, setLastUploaded] = useState<{ id: string; name: string; rows: number } | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  
  const queryClient = useQueryClient();

  const uploadMutation = useMutation({
    mutationFn: uploadFile,
    onMutate: () => setUploadState("UPLOADING"),
    onSuccess: (data) => {
      setUploadState("SUCCESS");
      setLastUploaded({ id: data.id, name: data.original_filename, rows: data.row_count || 0 });
      queryClient.invalidateQueries({ queryKey: ["files"] });
    },
    onError: (error: any) => {
      setUploadState("ERROR");
      setErrorMsg(error.message || "Upload failed");
    },
  });

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      uploadMutation.mutate(e.dataTransfer.files[0]);
    }
  }, [uploadMutation]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      uploadMutation.mutate(e.target.files[0]);
    }
  };

  useEffect(() => {
    const handleGlobalUpload = () => {
      document.getElementById("file-upload-input")?.click();
    };
    window.addEventListener("pipeline:upload", handleGlobalUpload);
    return () => window.removeEventListener("pipeline:upload", handleGlobalUpload);
  }, []);

  if (uploadState === "UPLOADING") {
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <div className="w-full max-w-xs bg-[var(--bg-surface)] rounded-full h-2.5 mb-4 border" style={{ borderColor: "var(--widget-border)" }}>
          <motion.div 
            className="bg-[var(--accent-primary)] h-2.5 rounded-full" 
            initial={{ width: "0%" }}
            animate={{ width: "100%" }}
            transition={{ duration: 2, repeat: Infinity }}
          />
        </div>
        <p className="text-sm text-[var(--text-secondary)]">Uploading file...</p>
      </div>
    );
  }

  if (uploadState === "SUCCESS" && lastUploaded) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center p-4">
        <CheckCircle className="w-10 h-10 text-[var(--accent-success)] mb-3" />
        <h3 className="text-[var(--text-primary)] font-medium mb-1">{lastUploaded.name}</h3>
        <p className="text-xs text-[var(--text-secondary)] mb-4">{lastUploaded.rows} rows discovered</p>
        <div className="flex items-center gap-2 bg-[var(--bg-surface)] px-3 py-2 rounded border" style={{ borderColor: "var(--widget-border)" }}>
          <span className="text-xs font-mono text-[var(--text-primary)] truncate w-32">{lastUploaded.id}</span>
          <button 
            onClick={() => navigator.clipboard.writeText(lastUploaded.id)}
            className="text-[var(--text-secondary)] hover:text-[var(--accent-primary)] transition-colors"
          >
            <Copy className="w-4 h-4" />
          </button>
        </div>
        <button 
          onClick={() => setUploadState("IDLE")}
          className="mt-4 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline"
        >
          Upload another file
        </button>
      </div>
    );
  }

  if (uploadState === "ERROR") {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center p-4">
        <XCircle className="w-10 h-10 text-[var(--accent-error)] mb-3" />
        <h3 className="text-[var(--text-primary)] font-medium mb-1">Upload Failed</h3>
        <p className="text-xs text-[var(--accent-error)] mb-4">{errorMsg}</p>
        <button 
          onClick={() => setUploadState("IDLE")}
          className="mt-2 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div 
      className={`relative flex flex-col items-center justify-center h-full border-2 border-dashed rounded-lg transition-all ${
        dragActive ? "border-[var(--accent-primary)] bg-[var(--interactive-hover)] scale-[1.02]" : "border-[var(--widget-border)]"
      }`}
      onDragEnter={handleDrag}
      onDragLeave={handleDrag}
      onDragOver={handleDrag}
      onDrop={handleDrop}
    >
      <input
        id="file-upload-input"
        type="file"
        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
        onChange={handleChange}
        accept=".csv,.json"
      />
      <UploadCloud className={`w-10 h-10 mb-3 transition-colors ${dragActive ? "text-[var(--accent-primary)]" : "text-[var(--text-secondary)]"}`} />
      <p className="text-sm font-medium text-[var(--text-primary)] mb-1">
        {dragActive ? "Release to upload" : "Drop CSV or JSON here"}
      </p>
      <p className="text-xs text-[var(--text-secondary)]">or click to browse</p>
    </div>
  );
}
