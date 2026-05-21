"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { ArrowLeft, Database } from "lucide-react";

type AssetType = "file" | "column" | "pipeline" | "topic" | null;

interface CatalogAsset {
  id: string;
  name: string;
  namespace: string;
  asset_type: string;
  last_seen_at: string;
  similarity: number;
}

interface BlastRadiusResult {
  name: string;
  namespace: string;
  asset_type: string;
  depth: number;
  pipeline_name: string | null;
  times_used: number;
}

function getAuthToken(): string {
  return localStorage.getItem("pipelineiq_token") || "";
}

const ASSET_ICONS: Record<string, string> = {
  file: "📄",
  column: "⬡",
  pipeline: "⚡",
  topic: "📡",
};

const DEPTH_COLORS: Record<number, string> = {
  0: "border-l-red-500",
  1: "border-l-orange-500",
  2: "border-l-yellow-500",
};

export default function CatalogPage() {
  const router = useRouter();
  const { user, isLoading } = useAuth();
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<AssetType>(null);
  const [searchResults, setResults] = useState<CatalogAsset[]>([]);
  const [searching, setSearching] = useState(false);
  const [selectedAsset, setSelected] = useState<CatalogAsset | null>(null);
  const [blastRadius, setBlastRadius] = useState<BlastRadiusResult[] | null>(null);
  const [loadingImpact, setLoadingImpact] = useState(false);

  const handleSearch = useCallback(
    async (q: string) => {
      if (q.length < 2) {
        setResults([]);
        return;
      }
      setSearching(true);
      try {
        const params = new URLSearchParams({ q, limit: "20" });
        if (typeFilter) params.append("asset_type", typeFilter);
        const resp = await fetch(`/api/catalog/search?${params}`, {
          headers: { Authorization: `Bearer ${getAuthToken()}` },
        });
        const data = await resp.json();
        setResults(data.results || []);
      } finally {
        setSearching(false);
      }
    },
    [typeFilter],
  );

  const handleAssetClick = useCallback(async (asset: CatalogAsset) => {
    setSelected(asset);
    setBlastRadius(null);
    setLoadingImpact(true);
    try {
      const resp = await fetch(
        `/api/catalog/assets/${encodeURIComponent(asset.name)}/impact`,
        { headers: { Authorization: `Bearer ${getAuthToken()}` } },
      );
      const data = await resp.json();
      setBlastRadius(data.downstream || []);
    } finally {
      setLoadingImpact(false);
    }
  }, []);

  if (isLoading || !user) {
    return (
      <main className="flex h-screen items-center justify-center bg-[var(--bg-base)]">
        <div className="w-5 h-5 border-2 border-[var(--text-secondary)] border-t-[var(--accent-primary)] rounded-full animate-spin" />
      </main>
    );
  }

  return (
    <main className="h-screen w-screen bg-[var(--bg-base)] p-3" data-testid="catalog-page">
      <div className="mb-4">
        <div className="flex items-center gap-3 mb-2">
          <button
            onClick={() => router.push("/")}
            className="flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--interactive-hover)] transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Dashboard
          </button>
        </div>
        <h1 className="text-2xl font-bold text-[var(--text-primary)] flex items-center gap-2">
          <Database className="w-6 h-6 text-[var(--accent-primary)]" />
          Data Catalog
        </h1>
        <p className="text-sm text-[var(--text-secondary)] mt-1">
          Search every file, column, pipeline, and topic. Click any asset to see its blast radius.
        </p>
      </div>

      <div className="mb-4">
        <input
          type="text"
          className="w-full px-3 py-2 rounded border border-[var(--widget-border)] bg-[var(--widget-bg)] text-[var(--text-primary)] placeholder-[var(--text-muted)]"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            handleSearch(e.target.value);
          }}
          placeholder="Search: customer_id, orders.csv, revenue_pipeline..."
          data-testid="catalog-search-input"
        />
        <div className="flex gap-2 mt-2">
          {(["file", "column", "pipeline", "topic"] as AssetType[]).map((type) => (
            <button
              key={type}
              onClick={() => setTypeFilter(typeFilter === type ? null : type)}
              className={`px-3 py-1 text-xs rounded border transition-colors ${
                typeFilter === type
                  ? "border-[var(--accent-primary)] bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]"
                  : "border-[var(--widget-border)] text-[var(--text-secondary)] hover:border-[var(--text-muted)]"
              }`}
              data-testid={`filter-${type}`}
            >
              {ASSET_ICONS[type!]} {type}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 h-[calc(100vh-12rem)]">
        <div className="overflow-y-auto rounded border border-[var(--widget-border)] bg-[var(--widget-bg)] p-2" data-testid="catalog-results">
          {searching && <p className="text-sm text-[var(--text-muted)] p-2">Searching...</p>}
          {!searching && searchResults.length === 0 && query.length >= 2 && (
            <p className="text-sm text-[var(--text-muted)] p-2">No assets found for "{query}"</p>
          )}
          {searchResults.map((asset) => (
            <div
              key={asset.id}
              className={`flex items-center gap-2 px-3 py-2 rounded cursor-pointer transition-colors ${
                selectedAsset?.id === asset.id
                  ? "bg-[var(--accent-primary)]/10 border border-[var(--accent-primary)]/30"
                  : "hover:bg-[var(--bg-hover)]"
              }`}
              onClick={() => handleAssetClick(asset)}
              data-testid={`catalog-result-${asset.id}`}
            >
              <span className="text-lg">{ASSET_ICONS[asset.asset_type] ?? "◇"}</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-[var(--text-primary)] truncate">{asset.name}</p>
                <p className="text-xs text-[var(--text-muted)] truncate">{asset.namespace}</p>
              </div>
              <span className="text-xs px-2 py-0.5 rounded bg-[var(--bg-hover)] text-[var(--text-secondary)]">
                {asset.asset_type}
              </span>
            </div>
          ))}
        </div>

        {selectedAsset && (
          <div className="overflow-y-auto rounded border border-[var(--widget-border)] bg-[var(--widget-bg)] p-4" data-testid="catalog-impact-panel">
            <h3 className="text-lg font-semibold text-[var(--text-primary)]">
              Impact of "{selectedAsset.name}"
            </h3>
            <p className="text-xs text-[var(--text-muted)] mt-1">
              What breaks if this {selectedAsset.asset_type} changes?
            </p>

            {loadingImpact && <p className="text-sm text-[var(--text-muted)] mt-3">Analyzing blast radius...</p>}

            {!loadingImpact && blastRadius !== null && (
              <>
                {blastRadius.length === 0 ? (
                  <p className="text-sm text-[var(--text-muted)] mt-3">
                    No downstream dependencies found.
                  </p>
                ) : (
                  <div className="mt-3">
                    <p className="text-xs text-[var(--text-secondary)] mb-2">
                      {blastRadius.length} downstream asset(s) across{" "}
                      {new Set(blastRadius.map((r) => r.pipeline_name).filter(Boolean)).size} pipeline(s)
                    </p>
                    {blastRadius.map((item, i) => (
                      <div
                        key={i}
                        className={`flex items-center justify-between px-3 py-2 mb-1 rounded border-l-4 ${DEPTH_COLORS[item.depth] ?? "border-l-gray-500"} bg-[var(--bg-hover)]`}
                        data-testid={`impact-item-${i}`}
                      >
                        <div className="flex items-center gap-2">
                          <span>{ASSET_ICONS[item.asset_type] ?? "◇"}</span>
                          <div>
                            <p className="text-sm text-[var(--text-primary)]">{item.name}</p>
                            {item.pipeline_name && (
                              <p className="text-xs text-[var(--text-muted)]">via {item.pipeline_name}</p>
                            )}
                          </div>
                        </div>
                        <div className="text-right">
                          <span className="text-xs text-[var(--text-secondary)]">
                            {item.depth === 0 ? "direct" : `${item.depth} hop${item.depth > 1 ? "s" : ""}`}
                          </span>
                          {item.times_used > 0 && (
                            <span className="text-xs text-[var(--text-muted)] ml-2">
                              {item.times_used}x
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
