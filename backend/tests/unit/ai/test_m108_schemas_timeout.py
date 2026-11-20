"""Regression tests for M-108: AI must see real schemas; calls need timeouts.

Bugs: build_file_schemas_section compared the UUID column against raw
str file_ids, which raises on Postgres (and SQLite) — every section
degraded to "error loading schema", so Gemini never saw column names and
hallucinated them. The router-level generate/repair wait also had no
overall timeout: a hung model call holds the request forever.
Fix: coerce file_ids with as_uuid, and bound the whole AI wait with
AI_REQUEST_TIMEOUT_SECONDS (504 on expiry).
"""

import asyncio
import uuid
from types import SimpleNamespace

from backend.ai.generation import build_file_schemas_section
from backend.models import FileProfile, UploadedFile


def _seed_file(test_db, user_id):
    fid = uuid.uuid4()
    db_file = UploadedFile(
        id=fid,
        original_filename="sales.csv",
        stored_path="uploads/sales.csv",
        file_size_bytes=100,
        row_count=10,
        column_count=2,
        columns=["amount", "region"],
        dtypes={"amount": "float", "region": "string"},
        user_id=user_id,
    )
    test_db.add(db_file)
    test_db.add(
        FileProfile(
            file_id=fid,
            profile={
                "amount": {"semantic_type": "currency", "null_pct": 0.0},
                "region": {"semantic_type": "category", "null_pct": 0.0},
            },
            row_count=10,
            col_count=2,
            completeness_pct=100.0,
            status="complete",
        )
    )
    test_db.commit()
    return str(fid)


class TestSchemasVisibleToAI:
    def test_schema_section_contains_real_columns(self, test_db):
        user_id = uuid.uuid4()
        fid = _seed_file(test_db, user_id)
        section = build_file_schemas_section([fid], test_db)
        assert "amount" in section
        assert "region" in section
        assert "currency" in section
        assert "error loading schema" not in section

    def test_missing_file_degrades_gracefully(self, test_db):
        section = build_file_schemas_section([str(uuid.uuid4())], test_db)
        assert "not found" in section


class TestRouterTimeout:
    def test_hung_generation_returns_504(self, client, sales_csv_bytes, monkeypatch):
        from backend.tests.conftest import upload_file

        file_id = upload_file(client, sales_csv_bytes, "sales.csv")

        async def _hung_generate(**kwargs):
            await asyncio.sleep(30)
            return SimpleNamespace(yaml="", valid=False, attempts=1, error="x")

        monkeypatch.setattr(
            "backend.routers.ai.generate_pipeline_from_description",
            _hung_generate,
        )
        monkeypatch.setattr("backend.config.settings.GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(
            "backend.routers.ai.AI_REQUEST_TIMEOUT_SECONDS",
            1,
            raising=False,
        )
        resp = client.post(
            "/api/ai/generate",
            json={
                "description": "Build a simple ETL pipeline for the file",
                "file_ids": [file_id],
            },
        )
        assert resp.status_code == 504, resp.text
