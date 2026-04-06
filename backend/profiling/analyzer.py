"""Pure computation module for column-level data profiling.

No Celery, no database, no MinIO. Just DataFrames in, profile dicts out.
This separation makes the core logic fully unit testable.
"""

import math
import re
import pandas as pd
import numpy as np
from typing import Any


def infer_semantic_type(series: pd.Series, column_name: str) -> str:
    """Infer the semantic type of a column."""
    name_lower = column_name.lower()

    if pd.api.types.is_numeric_dtype(series):
        non_null = series.dropna()
        if len(non_null) == 0:
            return "numeric"

        if pd.api.types.is_integer_dtype(series):
            uniqueness_ratio = series.nunique() / len(series)
            if uniqueness_ratio > 0.9 and any(
                x in name_lower for x in ["id", "key", "ref", "code", "num"]
            ):
                return "integer_id"

        if any(
            x in name_lower
            for x in [
                "price",
                "cost",
                "revenue",
                "amount",
                "salary",
                "fee",
                "charge",
                "payment",
            ]
        ):
            return "currency"

        return "numeric"

    if pd.api.types.is_bool_dtype(series):
        return "boolean"

    if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
        non_null = series.dropna().astype(str)
        if len(non_null) == 0:
            return "text"

        sample = non_null.head(1000)

        unique_lower = set(non_null.str.lower().unique())
        if unique_lower <= {
            "true",
            "false",
            "yes",
            "no",
            "1",
            "0",
            "t",
            "f",
            "y",
            "n",
        }:
            return "boolean"

        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if any(x in name_lower for x in ["email", "mail"]) or (
            sample.str.match(email_pattern).mean() > 0.8
        ):
            return "email"

        phone_pattern = r"^[\+\d\-\(\)\s]{7,15}$"
        if any(x in name_lower for x in ["phone", "mobile", "tel", "contact"]) or (
            sample.str.match(phone_pattern).mean() > 0.7
        ):
            return "phone"

        if any(x in name_lower for x in ["url", "link", "href", "website"]) or (
            sample.str.startswith(("http://", "https://")).mean() > 0.5
        ):
            return "url"

        if any(
            x in name_lower
            for x in [
                "date",
                "time",
                "at",
                "on",
                "created",
                "updated",
                "modified",
                "timestamp",
            ]
        ):
            try:
                pd.to_datetime(sample.head(50), format="mixed", errors="raise")
                return "datetime"
            except (ValueError, TypeError):
                pass

        uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        if sample.str.lower().str.match(uuid_pattern).mean() > 0.5 or any(
            x in name_lower
            for x in ["id", "uuid", "guid", "key", "code", "ref", "sku", "hash"]
        ):
            return "identifier"

        uniqueness_ratio = series.nunique() / max(len(series), 1)
        if uniqueness_ratio < 0.05:
            return "categorical"

        return "text"

    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"

    return "text"


def detect_semantic_flags(series: pd.Series, column_name: str) -> list[str]:
    """Detect boolean flags describing the column's characteristics."""
    flags = []
    name_lower = column_name.lower()
    non_null = series.dropna()
    total = len(series)

    if total == 0:
        return flags

    uniqueness = series.nunique() / total

    pii_name_patterns = [
        "name",
        "email",
        "phone",
        "mobile",
        "address",
        "ssn",
        "passport",
        "dob",
        "birthdate",
        "birth_date",
        "gender",
        "sex",
        "age",
        "salary",
        "income",
        "credit",
        "bank",
        "account",
        "ip_address",
        "ip",
    ]
    if any(p in name_lower for p in pii_name_patterns):
        flags.append("likely_pii")

    if any(x in name_lower for x in ["_id", "id_", "_key", "uuid", "guid"]):
        flags.append("likely_id")

    if any(
        x in name_lower
        for x in ["date", "time", "at", "_on", "created", "updated", "timestamp"]
    ):
        flags.append("likely_date")

    if series.nunique(dropna=True) <= 2:
        flags.append("likely_boolean")

    if pd.api.types.is_object_dtype(series):
        sample = non_null.astype(str).head(100)
        email_matches = sample.str.contains(r"@.*\.", regex=True).mean()
        if email_matches > 0.7:
            flags.append("likely_email")
        phone_matches = sample.str.match(r"^[\+\d\-\(\)\s]{7,15}$").mean()
        if phone_matches > 0.7:
            flags.append("likely_phone")

    if uniqueness > 0.9:
        flags.append("high_cardinality")
    if uniqueness < 0.01 and series.nunique() > 1:
        flags.append("low_cardinality")

    null_pct = series.isna().mean()
    if null_pct > 0.20:
        flags.append("high_null_rate")

    if series.nunique(dropna=True) == 1:
        flags.append("constant")

    return flags


def compute_histogram(series: pd.Series, bins: int = 10) -> list[dict]:
    """Compute a histogram for numeric series."""
    non_null = series.dropna()
    if len(non_null) == 0:
        return []

    try:
        counts, bin_edges = np.histogram(non_null, bins=bins)
        return [
            {
                "bin_start": round(float(bin_edges[i]), 6),
                "bin_end": round(float(bin_edges[i + 1]), 6),
                "count": int(counts[i]),
            }
            for i in range(len(counts))
        ]
    except Exception:
        return []


def profile_dataframe(df: pd.DataFrame) -> dict:
    """Compute the complete profile for a DataFrame."""
    profile = {}

    for col in df.columns:
        series = df[col]

        p: dict[str, Any] = {
            "name": col,
            "null_count": int(series.isna().sum()),
            "null_pct": round(float(series.isna().mean() * 100), 2),
            "unique_count": int(series.nunique()),
            "semantic_type": infer_semantic_type(series, col),
            "flags": detect_semantic_flags(series, col),
        }

        if pd.api.types.is_numeric_dtype(series):
            non_null = series.dropna()
            if len(non_null) > 0:
                # Convert to float64 to avoid numpy boolean subtract issues with masked arrays
                numeric_series = pd.to_numeric(series, errors="coerce").dropna()
                if len(numeric_series) > 0:
                    # Convert to regular numpy array to avoid masked array issues with quantile
                    values = numeric_series.values.astype(np.float64)
                    values = values[~np.isnan(values)]
                    q1 = q3 = 0.0  # Default values
                    if len(values) > 0:
                        desc = pd.Series(values).describe()
                        q1 = float(np.percentile(values, 25))
                        q3 = float(np.percentile(values, 75))
                    else:
                        desc = numeric_series.describe()
                    iqr = q3 - q1
                    lower_fence = q1 - 1.5 * iqr
                    upper_fence = q3 + 1.5 * iqr

                    p.update(
                        {
                            "min": round(float(numeric_series.min()), 6),
                            "max": round(float(numeric_series.max()), 6),
                            "mean": round(float(numeric_series.mean()), 6),
                            "median": round(float(numeric_series.median()), 6),
                            "std_dev": round(
                                float(numeric_series.std())
                                if not math.isnan(numeric_series.std())
                                else 0.0,
                                6,
                            ),
                            "p25": round(q1, 6),
                            "p75": round(q3, 6),
                            "outlier_count": int(
                                (
                                    (numeric_series < lower_fence)
                                    | (numeric_series > upper_fence)
                                ).sum()
                            ),
                            "histogram": compute_histogram(numeric_series, bins=10),
                            "is_integer": bool(
                                pd.api.types.is_integer_dtype(series)
                                or (
                                    pd.api.types.is_float_dtype(series)
                                    and numeric_series.apply(
                                        lambda x: float(x).is_integer()
                                    ).all()
                                )
                            ),
                        }
                    )

        elif pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(
            series
        ):
            non_null_str = series.dropna().astype(str)
            if len(non_null_str) > 0:
                top5 = series.value_counts().head(5)
                total = len(series)
                p.update(
                    {
                        "top_values": [
                            {
                                "value": str(k),
                                "count": int(v),
                                "pct": round(float(v) / total * 100, 1),
                            }
                            for k, v in top5.items()
                        ],
                        "avg_length": round(float(non_null_str.str.len().mean()), 1),
                        "max_length": int(non_null_str.str.len().max()),
                    }
                )

        profile[col] = p

    return profile


def compute_completeness(df: pd.DataFrame) -> float:
    """Overall completeness = 100 - avg null percentage across all columns."""
    if df.empty or len(df.columns) == 0:
        return 100.0
    total_cells = len(df) * len(df.columns)
    null_cells = int(df.isna().sum().sum())
    return round(100.0 - (null_cells / total_cells * 100), 2)
