"""Regression test: pipelines can LOAD .parquet uploads (M-111).

Uploads accept .parquet (ALLOWED_EXTENSIONS); the load step must read it.
xlsx stays an honest 415 (no openpyxl dependency).
"""

import io

import pandas as pd


def test_parquet_in_supported_extensions():
    from backend.pipeline.steps._core import SUPPORTED_FILE_EXTENSIONS

    assert ".parquet" in SUPPORTED_FILE_EXTENSIONS


def test_parquet_round_trip_through_load_reader():
    from backend.pipeline.steps._core import SUPPORTED_FILE_EXTENSIONS

    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow")
    buf.seek(0)
    out = SUPPORTED_FILE_EXTENSIONS[".parquet"](buf)
    assert list(out["a"]) == [1, 2, 3]
    assert list(out["b"]) == ["x", "y", "z"]
