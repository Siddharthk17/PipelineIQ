"""Tests for YAML diff generation used in AI repair responses."""

from backend.ai.generation import compute_yaml_diff


class TestYamlDiff:
    def test_no_change_all_lines_unchanged(self):
        yaml_text = "pipeline:\n  name: test"
        diff = compute_yaml_diff(yaml_text, yaml_text)
        assert diff
        assert all(line["type"] == "unchanged" for line in diff)

    def test_added_line_is_detected(self):
        original = "pipeline:\n  name: test"
        corrected = "pipeline:\n  name: test\n  version: 2"
        diff = compute_yaml_diff(original, corrected)
        assert any(line["type"] == "added" for line in diff)

    def test_removed_line_is_detected(self):
        original = "pipeline:\n  name: test\n  version: 1"
        corrected = "pipeline:\n  name: test"
        diff = compute_yaml_diff(original, corrected)
        assert any(line["type"] == "removed" for line in diff)

    def test_replaced_line_emits_removed_and_added(self):
        original = "  column: reveue"
        corrected = "  column: revenue"
        diff = compute_yaml_diff(original, corrected)
        types = {line["type"] for line in diff}
        assert "removed" in types
        assert "added" in types

