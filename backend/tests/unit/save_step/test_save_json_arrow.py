"""M-060 note: measured Arrow to_pylist()+orjson (~200ms/200k rows) against
the pandas path (~130ms) — the pandas path stays because it is faster.
These tests pin the JSON contract: NaN/inf -> null, timestamps (incl.
NaT, which previously crashed orjson with TypeError) serialize cleanly.
"""
import datetime

import orjson
import pandas as pd
import pyarrow as pa

from backend.execution.steps.save_step import _serialize_json


def test_json_datetime_with_nat():
    table = pa.table({
        "ts": pa.array(
            [datetime.datetime(2021, 1, 1, 5, 6, 7), None]),
        "v": pa.array([1, 2]),
    })
    df = table.to_pandas()
    assert df["ts"].iloc[1] is pd.NaT or pd.isna(df["ts"].iloc[1])
    content, _ = _serialize_json(table)
    parsed = orjson.loads(content)
    assert parsed[0]["ts"] == "2021-01-01T05:06:07"
    assert parsed[1]["ts"] is None


def test_json_nan_inf_become_null():
    table = pa.table(
        {"x": pa.array([1.0, float("nan"), float("inf")])})
    content, _ = _serialize_json(table)
    parsed = orjson.loads(content)
    assert parsed[0]["x"] == 1.0
    assert parsed[1]["x"] is None
    assert parsed[2]["x"] is None
