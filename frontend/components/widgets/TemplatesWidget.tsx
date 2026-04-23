"use client";

import React, { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { FileCode, Copy, Check, ChevronRight, Search, Sparkles } from "lucide-react";
import { getTemplates, getTemplateDetail } from "@/lib/api";
import { usePipelineStore } from "@/store/pipelineStore";
import type { PipelineTemplate, PipelineTemplateDetail } from "@/lib/types";

const CATEGORIES = ["All", "ETL", "Data Cleaning", "Data Validation", "Aggregation", "Merge/Join"];

function TemplateCard({
  template,
  onSelect,
}: {
  template: PipelineTemplate;
  onSelect: (t: PipelineTemplateDetail) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(false);

  const handleSelect = useCallback(async () => {
    setLoading(true);
    try {
      const detail = await getTemplateDetail(template.id);
      onSelect(detail);
      setSelected(true);
      setTimeout(() => setSelected(false), 2000);
    } catch (err) {
      console.error("Failed to load template:", err);
    } finally {
      setLoading(false);
    }
  }, [template.id, onSelect]);

  const categoryColors: Record<string, string> = {
    ETL: "var(--accent-primary)",
    "Data Cleaning": "var(--accent-success)",
    "Data Validation": "var(--accent-warning)",
    Aggregation: "var(--accent-error)",
    "Merge/Join": "var(--accent-info)",
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ scale: 1.02 }}
      className="p-4 rounded-lg border cursor-pointer transition-all"
      style={{
        backgroundColor: "var(--bg-surface)",
        borderColor: selected ? "var(--accent-success)" : "var(--widget-border)",
      }}
      onClick={handleSelect}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <div
            className="p-2 rounded-lg"
            style={{ backgroundColor: categoryColors[template.category] || "var(--accent-primary)", opacity: 0.2 }}
          >
            <FileCode size={16} style={{ color: categoryColors[template.category] || "var(--accent-primary)" }} />
          </div>
          <div>
            <h4 className="font-medium text-sm text-[var(--text-primary)]">{template.name}</h4>
            <span
              className="text-xs px-2 py-0.5 rounded-full"
              style={{
                backgroundColor: categoryColors[template.category] || "var(--accent-primary)",
                color: "#fff",
              }}
            >
              {template.category}
            </span>
          </div>
        </div>
        <button
          className="p-2 rounded-lg transition-colors"
          style={{
            backgroundColor: selected ? "var(--accent-success)" : "var(--accent-primary)",
            color: "#fff",
          }}
          onClick={(e) => {
            e.stopPropagation();
            handleSelect();
          }}
          disabled={loading}
        >
          {loading ? (
            <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
          ) : selected ? (
            <Check size={16} />
          ) : (
            <Copy size={16} />
          )}
        </button>
      </div>
      <p className="mt-3 text-xs text-[var(--text-secondary)] line-clamp-2">{template.description}</p>
    </motion.div>
  );
}

export function TemplatesWidget() {
  const [templates, setTemplates] = useState<PipelineTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState("All");
  const [searchQuery, setSearchQuery] = useState("");

  const { setLastYamlConfig } = usePipelineStore();

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getTemplates();
      setTemplates(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load templates");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  const handleSelectTemplate = useCallback(
    (template: PipelineTemplateDetail) => {
      setLastYamlConfig(template.yaml_config);
    },
    [setLastYamlConfig]
  );

  const filteredTemplates = templates.filter((t) => {
    const matchesCategory = activeCategory === "All" || t.category === activeCategory;
    const matchesSearch =
      searchQuery === "" ||
      t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.description.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesCategory && matchesSearch;
  });

  if (error) {
    return (
      <div className="p-4 text-center">
        <p className="text-[var(--accent-error)] text-sm">{error}</p>
        <button
          onClick={loadTemplates}
          className="mt-2 px-4 py-2 rounded-lg text-sm"
          style={{ backgroundColor: "var(--accent-primary)", color: "#fff" }}
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="p-3 border-b" style={{ borderColor: "var(--widget-border)" }}>
        <div className="flex items-center gap-2 mb-3">
          <Sparkles size={16} className="text-[var(--accent-primary)]" />
          <h3 className="font-medium text-sm text-[var(--text-primary)]">Pipeline Templates</h3>
        </div>

        <div className="relative mb-3">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-secondary)]" />
          <input
            type="text"
            placeholder="Search templates..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-2 rounded-lg text-sm border"
            style={{
              backgroundColor: "var(--bg-surface)",
              borderColor: "var(--widget-border)",
              color: "var(--text-primary)",
            }}
          />
        </div>

        <div className="flex gap-1 flex-wrap">
          {CATEGORIES.map((cat) => (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              className="px-3 py-1 rounded-full text-xs transition-colors"
              style={{
                backgroundColor: activeCategory === cat ? "var(--accent-primary)" : "var(--bg-surface)",
                color: activeCategory === cat ? "#fff" : "var(--text-secondary)",
                border: "1px solid var(--widget-border)",
              }}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto p-3 space-y-3">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-8 h-8 border-2 border-[var(--accent-primary)] border-t-transparent rounded-full animate-spin" />
          </div>
        ) : filteredTemplates.length === 0 ? (
          <div className="text-center py-8 text-[var(--text-secondary)] text-sm">
            No templates found
          </div>
        ) : (
          <AnimatePresence>
            {filteredTemplates.map((template) => (
              <TemplateCard
                key={template.id}
                template={template}
                onSelect={handleSelectTemplate}
              />
            ))}
          </AnimatePresence>
        )}
      </div>

      <div
        className="p-3 border-t text-xs text-center"
        style={{ borderColor: "var(--widget-border)", color: "var(--text-secondary)" }}
      >
        Click a template to import YAML into the editor
      </div>
    </div>
  );
}
