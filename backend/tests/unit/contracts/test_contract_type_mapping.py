"""
Roadmap-specified TYPE_CATEGORY_MAP coverage tests.

These tests ensure that every data type the contract layer handles
maps to the correct semantic category, preventing false-positive
type_change breaches from harmless precision differences.
"""

from backend.contracts.validator import TYPE_CATEGORY_MAP


class TestTypeCategoryMapping:
    def test_all_integer_types_same_category(self):
        int_types = [
            "int8", "int16", "int32", "int64",
            "uint8", "uint16", "uint32", "uint64",
        ]
        for t in int_types:
            assert TYPE_CATEGORY_MAP.get(t) == "integer", (
                f"{t} should map to 'integer' category"
            )

    def test_float_types_same_category(self):
        assert TYPE_CATEGORY_MAP.get("float32") == "float"
        assert TYPE_CATEGORY_MAP.get("float64") == "float"
        assert TYPE_CATEGORY_MAP.get("double") == "float"

    def test_object_dtype_maps_to_string(self):
        assert TYPE_CATEGORY_MAP.get("object") == "string"

    def test_all_string_types_map_to_string(self):
        string_types = ["object", "string", "large_string", "utf8", "large_utf8"]
        for t in string_types:
            assert TYPE_CATEGORY_MAP.get(t) == "string", (
                f"{t} should map to 'string' category"
            )

    def test_bool_maps_to_boolean(self):
        assert TYPE_CATEGORY_MAP.get("bool") == "boolean"

    def test_timestamp_types_map_to_datetime(self):
        for t in ("timestamp[ns]", "timestamp[us]", "timestamp[ms]", "timestamp[s]"):
            assert TYPE_CATEGORY_MAP.get(t) == "datetime", (
                f"{t} should map to 'datetime' category"
            )

    def test_date_types_map_to_datetime(self):
        assert TYPE_CATEGORY_MAP.get("date32") == "datetime"
        assert TYPE_CATEGORY_MAP.get("date64") == "datetime"

    def test_float16_maps_to_float(self):
        assert TYPE_CATEGORY_MAP.get("float16") == "float"

    def test_unknown_type_returns_none(self):
        assert TYPE_CATEGORY_MAP.get("nosuchtype") is None
