"""Tests for pipeline template loading and forking."""
import pytest
from backend.templates.loader import (
    get_all_templates, fork_template, get_pipeline_yaml_from_template
)


class TestGetAllTemplates:
    def test_returns_list(self):
        templates = get_all_templates()
        assert isinstance(templates, list)

    def test_has_exactly_five_templates(self):
        templates = get_all_templates()
        assert len(templates) == 5, \
            f"Expected 5 templates, got {len(templates)}: {[t.get('id') for t in templates]}"

    def test_each_template_has_required_fields(self):
        templates = get_all_templates()
        required_fields = {"id", "name", "description", "category", "required_files"}
        for t in templates:
            missing = required_fields - set(t.keys())
            assert not missing, f"Template {t.get('id')} missing fields: {missing}"

    def test_all_template_ids_are_unique(self):
        templates = get_all_templates()
        ids = [t["id"] for t in templates]
        assert len(ids) == len(set(ids)), f"Duplicate template IDs: {ids}"

    def test_expected_template_ids_present(self):
        templates = get_all_templates()
        ids = {t["id"] for t in templates}
        expected = {
            "sales_revenue_report",
            "data_quality_audit",
            "weekly_rollup",
            "multi_source_merge",
            "customer_segmentation",
        }
        assert ids == expected, f"Template IDs mismatch. Got: {ids}"


class TestForkTemplate:
    def test_fork_replaces_placeholder_with_file_id(self):
        yaml_result = fork_template(
            "data_quality_audit",
            pipeline_name="my_audit",
            file_mappings={"source_file_id": "abc-123-def"},
        )
        assert "abc-123-def" in yaml_result
        assert "{{source_file_id}}" not in yaml_result

    def test_fork_updates_pipeline_name(self):
        import yaml
        yaml_result = fork_template(
            "weekly_rollup",
            pipeline_name="my_weekly_pipeline",
            file_mappings={"daily_data_file_id": "uuid-here"},
        )
        parsed = yaml.safe_load(yaml_result)
        assert parsed["pipeline"]["name"] == "my_weekly_pipeline"

    def test_fork_missing_placeholder_raises(self):
        with pytest.raises(ValueError, match="placeholder"):
            fork_template(
                "sales_revenue_report",
                pipeline_name="test",
                file_mappings={"orders_file_id": "abc"},
            )

    def test_fork_produces_valid_yaml(self):
        import yaml
        yaml_result = fork_template(
            "customer_segmentation",
            pipeline_name="test_segmentation",
            file_mappings={"orders_file_id": "test-uuid-123"},
        )
        parsed = yaml.safe_load(yaml_result)
        assert "pipeline" in parsed
        assert "steps" in parsed["pipeline"]
        assert len(parsed["pipeline"]["steps"]) > 0

    def test_fork_unknown_template_raises(self):
        with pytest.raises(FileNotFoundError):
            fork_template("nonexistent_template", "name", {})

    def test_all_templates_fork_correctly(self):
        templates = get_all_templates()
        for tmpl in templates:
            template_id = tmpl["id"]
            required_files = tmpl["required_files"]
            file_mappings = {
                rf["placeholder"]: f"fake-uuid-{i:04d}"
                for i, rf in enumerate(required_files)
            }
            result = fork_template(template_id, "test_pipeline", file_mappings)
            assert "pipeline:" in result, \
                f"Template {template_id} fork produced invalid YAML"
