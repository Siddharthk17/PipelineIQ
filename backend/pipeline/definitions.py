"""Pipeline step type definitions for the visual builder and documentation.

Each entry contains metadata used by the frontend visual pipeline builder
(Week 4) to render step nodes with appropriate icons, colors, and labels.
"""

STEP_DEFINITIONS: dict[str, dict] = {
    "load": {
        "icon": "↓",
        "color": "#3B82F6",
        "category": "io",
        "label": "Load",
        "description": "Load a CSV or JSON file into the pipeline",
    },
    "filter": {
        "icon": "⊓",
        "color": "#10B981",
        "category": "transform",
        "label": "Filter",
        "description": "Keep rows matching a condition",
    },
    "select": {
        "icon": "☐",
        "color": "#8B5CF6",
        "category": "transform",
        "label": "Select",
        "description": "Keep only specified columns",
    },
    "rename": {
        "icon": "✎",
        "color": "#A855F7",
        "category": "transform",
        "label": "Rename",
        "description": "Rename columns using a mapping",
    },
    "join": {
        "icon": "⋈",
        "color": "#F59E0B",
        "category": "transform",
        "label": "Join",
        "description": "Merge two DataFrames on a common key",
    },
    "aggregate": {
        "icon": "Σ",
        "color": "#EF4444",
        "category": "transform",
        "label": "Aggregate",
        "description": "Group rows and compute statistics",
    },
    "sort": {
        "icon": "⇅",
        "color": "#6366F1",
        "category": "transform",
        "label": "Sort",
        "description": "Order rows by a column",
    },
    "validate": {
        "icon": "✓",
        "color": "#14B8A6",
        "category": "quality",
        "label": "Validate",
        "description": "Run data quality checks (non-blocking)",
    },
    "save": {
        "icon": "↑",
        "color": "#3B82F6",
        "category": "io",
        "label": "Save",
        "description": "Write the DataFrame to a CSV file",
    },
    "pivot": {
        "icon": "↔",
        "color": "#F97316",
        "category": "reshape",
        "label": "Pivot",
        "description": "Reshape long format to wide format",
    },
    "unpivot": {
        "icon": "↕",
        "color": "#F97316",
        "category": "reshape",
        "label": "Unpivot",
        "description": "Reshape wide format to long format",
    },
    "deduplicate": {
        "icon": "⊘",
        "color": "#84CC16",
        "category": "quality",
        "label": "Deduplicate",
        "description": "Remove duplicate rows",
    },
    "fill_nulls": {
        "icon": "○",
        "color": "#06B6D4",
        "category": "quality",
        "label": "Fill Nulls",
        "description": "Fill missing values with a strategy",
    },
    "sample": {
        "icon": "⚄",
        "color": "#F43F5E",
        "category": "transform",
        "label": "Sample",
        "description": "Take a random sample of rows",
    },
}
