"""Pipeline template API endpoints.

Provides a curated library of ready-to-use pipeline templates
that users can browse, preview, and import into the pipeline editor.
"""

import logging

from fastapi import APIRouter, HTTPException, status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])

PIPELINE_TEMPLATES = [
    {
        "id": "etl-basic",
        "name": "Basic ETL Pipeline",
        "description": "Extract data from a CSV file, apply transformations (rename, filter, compute), and save the result.",
        "category": "ETL",
        "yaml_config": (
            "pipeline:\n"
            "  name: basic_etl\n"
            "  steps:\n"
            "    - name: load_data\n"
            "      type: load\n"
            "      file_id: <YOUR_FILE_ID>\n"
            "    - name: rename_columns\n"
            "      type: rename\n"
            "      input: load_data\n"
            "      mapping:\n"
            "        old_name: new_name\n"
            "    - name: filter_rows\n"
            "      type: filter\n"
            "      input: rename_columns\n"
            "      condition: \"new_name != ''\"\n"
            "    - name: save_output\n"
            "      type: save\n"
            "      input: filter_rows\n"
            "      filename: etl_output.csv\n"
        ),
    },
    {
        "id": "data-cleaning",
        "name": "Data Cleaning Pipeline",
        "description": "Load a dataset, drop duplicates, fill missing values, and standardize column types.",
        "category": "Data Cleaning",
        "yaml_config": (
            "pipeline:\n"
            "  name: data_cleaning\n"
            "  steps:\n"
            "    - name: load_data\n"
            "      type: load\n"
            "      file_id: <YOUR_FILE_ID>\n"
            "    - name: deduplicate\n"
            "      type: deduplicate\n"
            "      input: load_data\n"
            "    - name: fill_missing\n"
            "      type: fill_missing\n"
            "      input: deduplicate\n"
            "      strategy: mean\n"
            "    - name: save_clean\n"
            "      type: save\n"
            "      input: fill_missing\n"
            "      filename: cleaned_data.csv\n"
        ),
    },
    {
        "id": "data-validation",
        "name": "Data Validation Pipeline",
        "description": "Load data and run validation checks (null checks, range checks, type checks) before saving.",
        "category": "Data Validation",
        "yaml_config": (
            "pipeline:\n"
            "  name: data_validation\n"
            "  steps:\n"
            "    - name: load_data\n"
            "      type: load\n"
            "      file_id: <YOUR_FILE_ID>\n"
            "    - name: validate\n"
            "      type: filter\n"
            "      input: load_data\n"
            "      condition: \"amount > 0\"\n"
            "    - name: save_valid\n"
            "      type: save\n"
            "      input: validate\n"
            "      filename: validated_data.csv\n"
        ),
    },
    {
        "id": "aggregation",
        "name": "Aggregation Pipeline",
        "description": "Load data, group by a key column, compute aggregate metrics, and save the summary.",
        "category": "Aggregation",
        "yaml_config": (
            "pipeline:\n"
            "  name: aggregation\n"
            "  steps:\n"
            "    - name: load_data\n"
            "      type: load\n"
            "      file_id: <YOUR_FILE_ID>\n"
            "    - name: group_and_aggregate\n"
            "      type: aggregate\n"
            "      input: load_data\n"
            "      group_by:\n"
            "        - category\n"
            "      aggregations:\n"
            "        amount: sum\n"
            "        id: count\n"
            "    - name: save_summary\n"
            "      type: save\n"
            "      input: group_and_aggregate\n"
            "      filename: aggregated_output.csv\n"
        ),
    },
    {
        "id": "merge-join",
        "name": "Merge / Join Pipeline",
        "description": "Load two datasets and join them on a common key column, then save the merged result.",
        "category": "Merge/Join",
        "yaml_config": (
            "pipeline:\n"
            "  name: merge_join\n"
            "  steps:\n"
            "    - name: load_left\n"
            "      type: load\n"
            "      file_id: <LEFT_FILE_ID>\n"
            "    - name: load_right\n"
            "      type: load\n"
            "      file_id: <RIGHT_FILE_ID>\n"
            "    - name: join_data\n"
            "      type: join\n"
            "      left_input: load_left\n"
            "      right_input: load_right\n"
            "      join_key: id\n"
            "      join_type: inner\n"
            "    - name: save_merged\n"
            "      type: save\n"
            "      input: join_data\n"
            "      filename: merged_output.csv\n"
        ),
    },
]

_TEMPLATES_BY_ID = {t["id"]: t for t in PIPELINE_TEMPLATES}

@router.get(
    "/",
    summary="List all pipeline templates",
    description="Returns a curated list of pipeline templates for common data workflows.",
)
def list_templates() -> dict:
    """List all available pipeline templates."""
    return {
        "templates": [
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "category": t["category"],
            }
            for t in PIPELINE_TEMPLATES
        ],
        "total": len(PIPELINE_TEMPLATES),
    }

@router.get(
    "/{template_id}",
    summary="Get a pipeline template",
    description="Returns the full template including YAML configuration.",
)
def get_template(template_id: str) -> dict:
    """Get a specific pipeline template by ID."""
    template = _TEMPLATES_BY_ID.get(template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found",
        )
    return template
