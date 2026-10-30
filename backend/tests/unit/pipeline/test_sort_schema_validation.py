"""Service-level schema checks for sort steps with list `by` (S-new).

Regression: `collect_schema_validation_errors` stringified a list `by`
into one bogus column name ("['b']"), rejecting valid pipelines with a
confusing 400. It now checks each column individually.
"""

from unittest.mock import MagicMock

from backend.pipeline.parser import PipelineParser
from backend.services.pipeline_validation import collect_schema_validation_errors


def _config():
    yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: 11111111-1111-1111-1111-111111111111
    - name: sort_data
      type: sort
      input: load_data
      by: [b]
      ascending: [false]
    - name: save_output
      type: save
      input: sort_data
      filename: out.csv
"""
    return PipelineParser().parse(yaml_str)


def _db_with_columns(columns):
    file_record = MagicMock()
    file_record.columns = columns
    query = MagicMock()
    query.filter.return_value.first.return_value = file_record
    db = MagicMock()
    db.query.return_value = query
    return db


def test_sort_list_by_validates_per_column():
    errors = collect_schema_validation_errors(_config(), _db_with_columns(["a", "b"]))
    assert errors == []


def test_sort_list_by_flags_missing_column():
    errors = collect_schema_validation_errors(_config(), _db_with_columns(["a"]))
    assert len(errors) == 1
    assert errors[0].field == "by"
    assert "'b'" in errors[0].message
    assert "['b']" not in errors[0].message
