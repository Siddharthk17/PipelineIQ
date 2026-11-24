"""Regression tests for readv3-correctness N3.

N3 (Low): legacy ``method``/``value`` (fill_nulls) and ``frac`` (sample)
were tolerated on the DuckDB path (``build_fill_nulls_sql`` /
``build_sample_sql`` fall back to them) but rejected by the parser — no
migration for old builder YAML. The parser now normalizes to canonical
keys with a warn-level log.
"""

import logging

from backend.pipeline.parser import PipelineParser


def _validate(yaml_text):
    parser = PipelineParser()
    config = parser.parse(yaml_text)
    return parser, parser.validate(config, {"f1"})


def _steps(yaml_text):
    parser = PipelineParser()
    return parser.parse(yaml_text).steps


def test_n3_legacy_fill_nulls_method_value_normalized(caplog):
    yaml_text = (
        "pipeline:\n  name: p\n  steps:\n"
        "    - name: l\n      type: load\n      file_id: f1\n"
        "    - name: f\n      type: fill_nulls\n      input: l\n"
        "      method: constant\n      value: 0\n      columns: [a]\n"
        "    - name: out\n      type: save\n      input: f\n      filename: o.csv\n"
    )
    with caplog.at_level(logging.WARNING, logger="backend.pipeline.parser"):
        _, result = _validate(yaml_text)
    assert result.is_valid is True
    step = next(s for s in _steps(yaml_text) if s.name == "f")
    assert step.strategy == "constant"
    assert step.constant_value == 0
    assert "method" in caplog.text


def test_n3_legacy_sample_frac_normalized(caplog):
    yaml_text = (
        "pipeline:\n  name: p\n  steps:\n"
        "    - name: l\n      type: load\n      file_id: f1\n"
        "    - name: sm\n      type: sample\n      input: l\n"
        "      frac: 0.5\n"
        "    - name: out\n      type: save\n      input: sm\n      filename: o.csv\n"
    )
    with caplog.at_level(logging.WARNING, logger="backend.pipeline.parser"):
        _, result = _validate(yaml_text)
    assert result.is_valid is True
    step = next(s for s in _steps(yaml_text) if s.name == "sm")
    assert step.fraction == 0.5
    assert "frac" in caplog.text


def test_n3_canonical_keys_win_silently(caplog):
    yaml_text = (
        "pipeline:\n  name: p\n  steps:\n"
        "    - name: l\n      type: load\n      file_id: f1\n"
        "    - name: f\n      type: fill_nulls\n      input: l\n"
        "      strategy: constant\n      constant_value: 7\n"
        "      method: mean\n      value: 0\n      columns: [a]\n"
        "    - name: out\n      type: save\n      input: f\n      filename: o.csv\n"
    )
    with caplog.at_level(logging.WARNING, logger="backend.pipeline.parser"):
        _, result = _validate(yaml_text)
    assert result.is_valid is True
    step = next(s for s in _steps(yaml_text) if s.name == "f")
    assert step.strategy == "constant"
    assert step.constant_value == 7
    assert "legacy" not in caplog.text
