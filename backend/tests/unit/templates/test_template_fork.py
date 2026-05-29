"""Tests for the template fork and loader API."""
import pytest
from backend.templates.loader import fork_template, get_all_templates


class TestForkTemplateValidation:
    def test_fork_with_extra_mappings_succeeds(self):
        yaml_result = fork_template(
            "data_quality_audit",
            pipeline_name="test",
            file_mappings={
                "source_file_id": "real-uuid",
                "unused_placeholder": "extra-uuid",
            },
        )
        assert "real-uuid" in yaml_result

    def test_fork_preserves_pipeline_structure(self):
        import yaml
        yaml_result = fork_template(
            "customer_segmentation",
            pipeline_name="my_segments",
            file_mappings={"orders_file_id": "test-id"},
        )
        parsed = yaml.safe_load(yaml_result)
        steps = parsed["pipeline"]["steps"]
        step_types = [s.get("type") for s in steps]
        assert "load" in step_types
        assert "aggregate" in step_types
        assert "sort" in step_types
        assert "save" in step_types

    def test_all_templates_reference_valid_step_types(self):
        valid_types = {
            "load", "filter", "join", "aggregate", "sort", "save",
            "deduplicate", "fill_nulls", "sample", "select",
        }
        templates = get_all_templates()
        for tmpl in templates:
            result = fork_template(
                tmpl["id"],
                "test",
                {rf["placeholder"]: f"uuid-{i}"
                 for i, rf in enumerate(tmpl["required_files"])},
            )
            import yaml
            parsed = yaml.safe_load(result)
            for step in parsed["pipeline"]["steps"]:
                assert step.get("type") in valid_types, \
                    f"Template {tmpl['id']}: unknown step type '{step.get('type')}'"

    def test_sales_template_has_multiple_file_placeholders(self):
        templates = get_all_templates()
        sales = next(t for t in templates if t["id"] == "sales_revenue_report")
        assert len(sales["required_files"]) == 2
        placeholders = {rf["placeholder"] for rf in sales["required_files"]}
        assert "orders_file_id" in placeholders
        assert "customers_file_id" in placeholders

    def test_quality_audit_template_has_single_placeholder(self):
        templates = get_all_templates()
        quality = next(t for t in templates if t["id"] == "data_quality_audit")
        assert len(quality["required_files"]) == 1
        assert quality["required_files"][0]["placeholder"] == "source_file_id"
