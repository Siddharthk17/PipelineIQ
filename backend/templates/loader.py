"""Load and manage pipeline templates."""

import pathlib
import re

import yaml

TEMPLATES_DIR = pathlib.Path(__file__).parent


def get_all_templates() -> list[dict]:
    """Return metadata for all available templates."""
    templates = []
    for yaml_file in sorted(TEMPLATES_DIR.glob("*.yaml")):
        try:
            content = yaml.safe_load(yaml_file.read_text())
            if "template" in content:
                meta = dict(content["template"])
                meta["source_file"] = yaml_file.name
                templates.append(meta)
        except Exception:
            continue
    return templates


def get_template_yaml(template_id: str) -> str:
    """Load a template YAML file by its template.id value."""
    for yaml_file in TEMPLATES_DIR.glob("*.yaml"):
        try:
            content = yaml.safe_load(yaml_file.read_text())
            if content.get("template", {}).get("id") == template_id:
                return yaml_file.read_text()
        except Exception:
            continue
    raise FileNotFoundError(f"Template not found: {template_id}")


def get_pipeline_yaml_from_template(template_id: str) -> tuple[str, dict]:
    """Load a template and return only the 'pipeline:' section as YAML
    plus the template metadata dict.

    Returns:
        (pipeline_yaml_text, template_metadata_dict)
    """
    raw = get_template_yaml(template_id)
    content = yaml.safe_load(raw)
    meta = dict(content.get("template", {}))
    pipeline_section = {"pipeline": content["pipeline"]}
    pipeline_yaml = yaml.dump(
        pipeline_section, default_flow_style=False,
        sort_keys=False, allow_unicode=True, width=float("inf"))
    return pipeline_yaml, meta


def fork_template(
    template_id: str,
    pipeline_name: str,
    file_mappings: dict[str, str],
) -> str:
    """Fork a template by replacing {{placeholder}} strings with real file IDs.

    Args:
        template_id:    The template to fork.
        pipeline_name:  The name for the new pipeline.
        file_mappings:  {placeholder_name: actual_file_uuid}
            e.g. {"orders_file_id": "abc-123", "customers_file_id": "def-456"}

    Returns:
        The forked pipeline YAML with all placeholders replaced.
    """
    pipeline_yaml, meta = get_pipeline_yaml_from_template(template_id)

    for placeholder, actual_id in file_mappings.items():
        pipeline_yaml = pipeline_yaml.replace(
            f"{{{{{placeholder}}}}}",
            actual_id,
        )

    content = yaml.safe_load(pipeline_yaml)
    content["pipeline"]["name"] = pipeline_name
    pipeline_yaml = yaml.dump(
        content, default_flow_style=False,
        sort_keys=False, allow_unicode=True, width=float("inf"))

    remaining = re.findall(r'\{\{(\w+)\}\}', pipeline_yaml)
    if remaining:
        raise ValueError(
            "Unfilled template placeholders remain: %s. "
            "Provide file_mappings for these placeholders." % remaining)

    return pipeline_yaml
