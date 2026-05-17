"use client";

import React from "react";

interface ErrorBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("[ErrorBoundary] Caught error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return (
        <div className="flex flex-col items-center justify-center p-8 text-center min-h-[200px] bg-[var(--bg-surface)] border border-[var(--widget-border)] rounded-lg">
          <div className="text-[var(--accent-error)] text-lg font-semibold mb-2">
            Something went wrong
          </div>
          <p className="text-[var(--text-secondary)] text-sm mb-4 max-w-md">
            {this.state.error?.message || "An unexpected error occurred in this component."}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="px-4 py-2 text-sm rounded bg-[var(--accent-primary)] text-[var(--bg-base)] hover:opacity-90 transition-opacity"
          >
            Try Again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export function WidgetErrorBoundary({ children, widgetName }: { children: React.ReactNode; widgetName?: string }) {
  return (
    <ErrorBoundary
      fallback={
        <div className="flex flex-col items-center justify-center p-6 text-center h-full bg-[var(--bg-surface)] border border-[var(--widget-border)] rounded-lg">
          <div className="text-[var(--accent-error)] text-sm font-semibold mb-1">
            {widgetName || "Widget"} Error
          </div>
          <p className="text-[var(--text-secondary)] text-xs">
            This widget encountered an error. Try refreshing the page.
          </p>
        </div>
      }
    >
      {children}
    </ErrorBoundary>
  );
}
