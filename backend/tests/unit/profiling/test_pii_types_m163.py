"""Regression tests for M-163: PII auto-detection coverage.

``detect_pii_columns`` keys off ``semantic_type`` values in
``PII_SEMANTIC_TYPES`` (email, phone, ssn, credit_card, ip_address,
person_name, address), but ``infer_semantic_type`` only ever produced
``email`` and ``phone`` — the other five types could never fire, so
PII columns of those kinds were never suggested for protection.
These tests pin that every PII type is inferable from realistic data
and that the end-to-end path (profile -> suggestions) fires.
"""

import pandas as pd

from backend.profiling.analyzer import infer_semantic_type, profile_dataframe
from backend.security.column_security import PII_SEMANTIC_TYPES, detect_pii_columns


def _s(values):
    return pd.Series(values, dtype=object)


class TestM163PIITypeInference:
    def test_ssn_detected_by_name(self):
        s = _s(["123-45-6789", "987-65-4321", "555-12-3456", "111-22-3333"])
        assert infer_semantic_type(s, "ssn") == "ssn"

    def test_ssn_detected_by_content(self):
        s = _s(["123-45-6789", "987-65-4321", "555-12-3456", "111-22-3333"])
        assert infer_semantic_type(s, "tax_id") == "ssn"

    def test_credit_card_detected_by_name(self):
        s = _s(["4111111111111111", "5500005555555559", "378282246310005"])
        assert infer_semantic_type(s, "card_number") == "credit_card"

    def test_credit_card_detected_by_spaced_content(self):
        s = _s(["4111 1111 1111 1111", "5500-0055-5555-5559"])
        assert infer_semantic_type(s, "payment_ref") == "credit_card"

    def test_ip_address_detected_by_name(self):
        s = _s(["192.168.1.1", "10.0.0.5", "172.16.0.9", "8.8.8.8"])
        assert infer_semantic_type(s, "client_ip") == "ip_address"

    def test_ip_address_detected_by_content(self):
        s = _s(["192.168.1.1", "10.0.0.5", "172.16.0.9", "8.8.8.8"])
        assert infer_semantic_type(s, "remote_host") == "ip_address"

    def test_person_name_detected_by_name(self):
        s = _s(["Alice Smith", "Bob Jones", "Carol White", "Dan Brown"])
        assert infer_semantic_type(s, "full_name") == "person_name"

    def test_address_detected_by_name(self):
        s = _s(["12 Main St", "48 Oak Ave", "7 Pine Rd", "99 Elm Blvd"])
        assert infer_semantic_type(s, "home_address") == "address"

    def test_address_detected_by_zip_content(self):
        s = _s(["90210", "10001", "30301-1234", "60601"])
        assert infer_semantic_type(s, "postal") == "address"

    def test_all_pii_types_covered(self):
        assert PII_SEMANTIC_TYPES == frozenset(
            {
                "email",
                "phone",
                "ssn",
                "credit_card",
                "ip_address",
                "person_name",
                "address",
            }
        )


class TestM163EndToEndSuggestions:
    def test_every_pii_type_fires_detect_pii_columns(self):
        df = pd.DataFrame(
            {
                "email": _s(["a@b.com", "c@d.org", "e@f.net", "g@h.io"]),
                "phone": _s(["+15551234567"] * 4),
                "ssn": _s(["123-45-6789"] * 4),
                "cc": _s(["4111111111111111"] * 4),
                "ip": _s(["192.168.1.1"] * 4),
                "name": _s(["Alice", "Bob", "Carol", "Dan"]),
                "addr": _s(["12 Main St"] * 4),
            }
        ).rename(
            columns={
                "cc": "card_number",
                "ip": "client_ip",
                "name": "full_name",
                "addr": "home_address",
            }
        )
        profile = profile_dataframe(df)
        assert profile["ssn"]["semantic_type"] == "ssn"
        assert profile["card_number"]["semantic_type"] == "credit_card"
        assert profile["client_ip"]["semantic_type"] == "ip_address"
        assert profile["full_name"]["semantic_type"] == "person_name"
        assert profile["home_address"]["semantic_type"] == "address"
        found = set(detect_pii_columns(profile))
        assert {
            "ssn",
            "card_number",
            "client_ip",
            "full_name",
            "home_address",
        } <= found

    def test_non_pii_columns_still_not_detected(self):
        df = pd.DataFrame(
            {
                "region": _s(["North", "South", "North", "West"]),
                "revenue": pd.Series([10.0, 20.0, 30.0, 40.0]),
            }
        )
        profile = profile_dataframe(df)
        assert detect_pii_columns(profile) == []

    def test_uuid_id_columns_still_identifier_not_pii(self):
        import uuid

        s = _s([str(uuid.uuid4()) for _ in range(6)])
        assert infer_semantic_type(s, "record_uuid") == "identifier"
        profile = profile_dataframe(pd.DataFrame({"record_uuid": s}))
        assert detect_pii_columns(profile) == []
