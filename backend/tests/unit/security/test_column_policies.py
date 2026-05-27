"""Column-level security policy tests.

Tests the policy enforcement logic using in-memory DataFrames.
"""

import pandas as pd
import pytest

from backend.security.column_security import (
    ColumnPolicyRecord,
    _apply_mask,
    apply_column_policies,
    detect_pii_columns,
)


def _policy(column, policy, mask_pattern=None, allowed_roles=None):
    return ColumnPolicyRecord(
        column_name=column,
        policy=policy,
        mask_pattern=mask_pattern,
        allowed_roles=allowed_roles or [],
    )


class TestRedactedPolicy:
    def test_redacted_column_removed(self):
        df = pd.DataFrame(
            {"name": ["Alice", "Bob"], "email": ["a@x.com", "b@x.com"], "amount": [100, 200]}
        )
        policies = [_policy("email", "redacted")]
        result = apply_column_policies(df, "viewer", policies)
        assert "email" not in result.columns
        assert "name" in result.columns
        assert "amount" in result.columns

    def test_allowed_role_sees_redacted_column(self):
        df = pd.DataFrame({"email": ["a@x.com"], "amount": [100]})
        policies = [_policy("email", "redacted", allowed_roles=["admin"])]
        result = apply_column_policies(df, "admin", policies)
        assert "email" in result.columns
        assert result["email"].iloc[0] == "a@x.com"

    def test_multiple_redacted_columns(self):
        df = pd.DataFrame(
            {"ssn": ["123-45-6789"], "name": ["Alice"], "card": ["4111111111111111"]}
        )
        policies = [
            _policy("ssn", "redacted"),
            _policy("card", "redacted"),
        ]
        result = apply_column_policies(df, "viewer", policies)
        assert "ssn" not in result.columns
        assert "card" not in result.columns
        assert "name" in result.columns

    def test_policy_for_nonexistent_column_ignored(self):
        df = pd.DataFrame({"a": [1, 2]})
        policies = [_policy("nonexistent_column", "redacted")]
        result = apply_column_policies(df, "viewer", policies)
        assert "a" in result.columns
        assert len(result) == 2

    def test_empty_policies_returns_original(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        result = apply_column_policies(df, "viewer", [])
        pd.testing.assert_frame_equal(result, df)


class TestMaskedPolicy:
    def test_email_masked_first_char_visible(self):
        df = pd.DataFrame({"email": ["alice@example.com", "bob@test.org"]})
        policies = [_policy("email", "masked", mask_pattern="email")]
        result = apply_column_policies(df, "viewer", policies)
        assert "email" in result.columns
        assert result["email"].iloc[0] == "a***@example.com"
        assert result["email"].iloc[1] == "b***@test.org"

    def test_credit_card_masked_last_4_visible(self):
        df = pd.DataFrame({"card": ["4111111111111234"]})
        policies = [_policy("card", "masked", mask_pattern="credit_card")]
        result = apply_column_policies(df, "viewer", policies)
        assert "1234" in result["card"].iloc[0]
        assert result["card"].iloc[0].startswith("****")

    def test_phone_masked_last_4_visible(self):
        df = pd.DataFrame({"phone": ["555-867-5309"]})
        policies = [_policy("phone", "masked", mask_pattern="phone")]
        result = apply_column_policies(df, "viewer", policies)
        assert "5309" in result["phone"].iloc[0]

    def test_ssn_masked(self):
        df = pd.DataFrame({"ssn": ["123-45-6789"]})
        policies = [_policy("ssn", "masked", mask_pattern="ssn")]
        result = apply_column_policies(df, "viewer", policies)
        assert "6789" in result["ssn"].iloc[0]
        assert "123" not in result["ssn"].iloc[0]

    def test_allowed_role_sees_unmasked_value(self):
        df = pd.DataFrame({"email": ["alice@example.com"]})
        policies = [
            _policy("email", "masked", mask_pattern="email", allowed_roles=["admin"])
        ]
        result = apply_column_policies(df, "admin", policies)
        assert result["email"].iloc[0] == "alice@example.com"

    def test_null_values_preserved(self):
        df = pd.DataFrame({"email": ["alice@example.com", None, "bob@test.org"]})
        policies = [_policy("email", "masked", mask_pattern="email")]
        result = apply_column_policies(df, "viewer", policies)
        assert result["email"].iloc[0] == "a***@example.com"
        assert pd.isna(result["email"].iloc[1])


class TestMaskFunctions:
    def test_email_mask(self):
        assert _apply_mask("alice@example.com", "email") == "a***@example.com"

    def test_phone_mask(self):
        result = _apply_mask("555-867-5309", "phone")
        assert "5309" in result

    def test_name_mask(self):
        result = _apply_mask("Alice", "name")
        assert result == "A***"

    def test_default_mask_short_string(self):
        result = _apply_mask("abc", "default")
        assert result == "***"

    def test_custom_mask_n_chars(self):
        result = _apply_mask("ABCDEFGH", "custom:3")
        assert result.startswith("ABC")
        assert "***" in result

    def test_empty_string_no_crash(self):
        result = _apply_mask("", "email")
        assert isinstance(result, str)


class TestPIIDetection:
    def test_email_column_detected(self):
        profile = {
            "email_address": {"semantic_type": "email"},
            "revenue": {"semantic_type": "currency"},
        }
        pii = detect_pii_columns(profile)
        assert "email_address" in pii
        assert "revenue" not in pii

    def test_ssn_column_detected(self):
        profile = {"tax_id": {"semantic_type": "ssn"}}
        pii = detect_pii_columns(profile)
        assert "tax_id" in pii

    def test_non_pii_columns_not_detected(self):
        profile = {
            "amount": {"semantic_type": "currency"},
            "region": {"semantic_type": "categorical"},
        }
        pii = detect_pii_columns(profile)
        assert not pii

    def test_empty_profile_returns_empty(self):
        assert detect_pii_columns({}) == []
