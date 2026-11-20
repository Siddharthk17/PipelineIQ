"""Regression tests for M-074: healing must validate patches, cap attempts,
and never mutate identity fields.

Bugs: AI-proposed patches could rewrite a step's type/file_id (identity
mutation), the retry loop ignored the configured max-attempts cap, and
patched YAML was applied after sandbox execution without an explicit
parser (semantic) validation. Fix: reject identity-field mutations and
over-long patch lists in validate_healing_patch (enforced by attempt_heal
before apply), expose the cap + parser-validate helpers in
backend/tasks/healing_tasks.py, and require parser validation of every
patched YAML.
"""

import pytest

from backend.ai.healing_prompts import validate_healing_patch


def _patch_for(field, old="a", new="b"):
    return {
        "confidence": 0.9,
        "change_description": "rename column",
        "patches": [
            {
                "step_name": "filter_data",
                "field": field,
                "old_value": old,
                "new_value": new,
            }
        ],
    }


class TestImmutableIdentityFields:
    @pytest.mark.parametrize("field", ["type", "file_id", "name"])
    def test_identity_field_mutation_rejected(self, field):
        valid, error = validate_healing_patch(_patch_for(field))
        assert valid is False, f"field={field} must be rejected"
        assert "immutable" in error.lower()

    def test_wasm_module_reference_mutation_rejected(self):
        valid, error = validate_healing_patch(_patch_for("wasm_file_id"))
        assert valid is False
        assert "immutable" in error.lower()

    def test_column_patch_still_accepted(self):
        valid, error = validate_healing_patch(_patch_for("column", "ammount", "amount"))
        assert valid is True, error

    def test_patch_list_capped(self):
        from backend.ai.healing_prompts import MAX_HEALING_PATCHES

        patch = {
            "confidence": 0.9,
            "change_description": "many",
            "patches": [
                {
                    "step_name": f"s{i}",
                    "field": "column",
                    "old_value": "a",
                    "new_value": "b",
                }
                for i in range(MAX_HEALING_PATCHES + 1)
            ],
        }
        valid, error = validate_healing_patch(patch)
        assert valid is False
        assert "too many" in error.lower()


class TestHealingTaskGuards:
    def test_attempts_cap_respected(self):
        from backend.tasks.healing_tasks import healing_attempts_allowed

        assert healing_attempts_allowed(0, 3) is True
        assert healing_attempts_allowed(2, 3) is True
        assert healing_attempts_allowed(3, 3) is False
        assert healing_attempts_allowed(4, 3) is False
        assert healing_attempts_allowed(0, 0) is False

    def test_parser_validate_accepts_good_yaml(self, test_db):
        import uuid

        from backend.models import UploadedFile
        from backend.tasks.healing_tasks import parser_validate_patched_yaml

        fid = uuid.uuid4()
        test_db.add(
            UploadedFile(
                id=fid,
                original_filename="sales.csv",
                stored_path="uploads/sales.csv",
                file_size_bytes=100,
                row_count=10,
                column_count=2,
                columns=["amount", "region"],
                dtypes={"amount": "float", "region": "string"},
                user_id=uuid.uuid4(),
            )
        )
        test_db.commit()

        yaml_text = (
            "pipeline:\n"
            "  name: healed\n"
            "  steps:\n"
            "    - name: load_data\n"
            "      type: load\n"
            f'      file_id: "{fid}"\n'
            "    - name: save_out\n"
            "      type: save\n"
            "      input: load_data\n"
            "      filename: out.csv\n"
        )
        assert parser_validate_patched_yaml(yaml_text, [str(fid)], test_db) is None

    def test_parser_validate_rejects_broken_reference(self, test_db):
        from backend.tasks.healing_tasks import parser_validate_patched_yaml

        yaml_text = (
            "pipeline:\n"
            "  name: healed\n"
            "  steps:\n"
            "    - name: bad_filter\n"
            "      type: filter\n"
            "      input: nonexistent_step\n"
            "      column: x\n"
            "      operator: equals\n"
            "      value: 1\n"
        )
        with pytest.raises(ValueError):
            parser_validate_patched_yaml(yaml_text, [], test_db)
