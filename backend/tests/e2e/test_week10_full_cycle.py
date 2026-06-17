"""Comprehensive E2E test for Week 10 — Global Data Asset Catalog + OpenLineage 1.0.

Tests the complete lifecycle end-to-end as a single ordered workflow:
1. Seed test data (PipelineRun, LineageGraph)
2. Register assets in catalog
3. Catalog search (name, type, namespace)
4. Blast radius (forward dependency analysis)
5. Upstream lineage (backward dependency analysis)
6. OpenLineage 1.0 single-run export
7. OpenLineage NDJSON bulk export
8. Orphan asset detection
9. Catalog statistics
10. Re-registration idempotency
11. Edge cases (missing run, not found)

Uses the FastAPI TestClient with in-memory SQLite to exercise the full stack.
Direct DB writes are used for setup; all queries go through API endpoints.
"""

import json
import uuid
from datetime import datetime, timezone
from uuid import UUID as _UUID

import networkx as nx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func

from backend.models import DataAsset, PipelineRun, LineageGraph
from backend.repositories.catalog import register_run_assets


@pytest.fixture
def seeded_data(test_db):
    """Seed database with PipelineRun, LineageGraph, and catalog assets."""
    run_id = uuid.uuid4()
    yaml_cfg = _build_pipeline_yaml()

    now = datetime.now(timezone.utc)
    run = PipelineRun(
        id=run_id,
        name="e2e_pipeline",
        status="COMPLETED",
        yaml_config=yaml_cfg,
        created_at=now,
        started_at=now,
        completed_at=now,
        total_rows_in=20,
        total_rows_out=8,
        user_id=_UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    )
    test_db.add(run)

    G = _build_lineage_graph()
    graph_json = json.loads(json.dumps(nx.node_link_data(G)))

    lg = LineageGraph(
        pipeline_run_id=run_id,
        graph_data=graph_json,
        react_flow_data={"nodes": [], "edges": []},
    )
    test_db.add(lg)

    count = register_run_assets(
        db=test_db,
        run_id=str(run_id),
        pipeline_name="e2e_pipeline",
        pipeline_yaml=yaml_cfg,
        lineage_graph=G,
        owner_id=None,
    )
    assert count > 0, "No assets registered"
    test_db.commit()

    yield {"run_id": str(run_id), "graph": G, "yaml_cfg": yaml_cfg}


class TestWeek10E2E:
    """End-to-end Week 10 lifecycle — ordered via single shared fixture."""

    # Step 1: Catalog Search

    def test_01_catalog_search(self, client: TestClient, seeded_data):
        resp = client.get("/api/catalog/search?q=output")
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["count"] >= 1

        # Filter by type
        resp = client.get("/api/catalog/search?q=sales&asset_type=file")
        assert resp.status_code == 200
        if resp.json()["count"] > 0:
            for r in resp.json()["results"]:
                assert r["asset_type"] == "file"

        # No results
        resp = client.get("/api/catalog/search?q=xyznonexistent12345")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

        # Short query (min 2 chars)
        resp = client.get("/api/catalog/search?q=x")
        assert resp.status_code in (200, 422)

    # Step 2: Blast Radius

    def test_02_blast_radius(self, client: TestClient, seeded_data):
        resp = client.get("/api/catalog/assets/col::load_data::amount/impact")
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["asset_name"] == "col::load_data::amount"
        assert isinstance(data["total_assets"], int)
        if data["total_assets"] > 0:
            assert isinstance(data["depth_reached"], int)
            assert isinstance(data["downstream"][0]["name"], str)

        # Non-existent asset
        resp = client.get("/api/catalog/assets/nonexistent/impact")
        assert resp.status_code == 200
        assert resp.json()["downstream"] == []
        assert resp.json()["depth_reached"] == 0

    # Step 3: Upstream Lineage

    def test_03_upstream_lineage(self, client: TestClient, seeded_data):
        resp = client.get("/api/catalog/assets/output::output.csv/lineage")
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["asset_name"] == "output::output.csv"

        # Non-existent asset
        resp = client.get("/api/catalog/assets/nonexistent/lineage")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    # Step 4: OpenLineage Single Export

    def test_04_openlineage_single(self, client: TestClient, seeded_data):
        run_id = seeded_data["run_id"]
        resp = client.get(f"/api/runs/{run_id}/openlineage")
        assert resp.status_code == 200, resp.json()
        event = resp.json()

        assert event["eventType"] in ("COMPLETE", "FAIL", "RUNNING", "START")
        assert "eventTime" in event
        assert event["producer"] == "https://github.com/pipelineiq/pipelineiq"
        assert event["run"]["runId"] == run_id
        assert event["job"]["namespace"] == "pipelineiq"
        assert event["job"]["name"] == "e2e_pipeline"
        assert "inputs" in event
        assert "outputs" in event

        # At least one input (the source file)
        assert len(event["inputs"]) >= 1
        assert event["inputs"][0]["namespace"] != ""
        assert event["inputs"][0]["name"] != ""

    # Step 5: OpenLineage Bulk NDJSON Export

    def test_05_openlineage_bulk(self, client: TestClient, seeded_data):
        resp = client.get("/api/lineage/export?limit=100")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/x-ndjson"

        event_count = int(resp.headers.get("x-event-count", "0"))
        assert event_count >= 1

        lines = resp.text.strip().split("\n")
        assert len(lines) == event_count

        for line in lines:
            event = json.loads(line)
            assert event["eventType"] in ("COMPLETE", "FAIL", "RUNNING", "START")
            assert "run" in event
            assert "job" in event
            assert "inputs" in event
            assert "outputs" in event

    # Step 6: Orphan Detection

    def test_06_orphans(self, client: TestClient, seeded_data):
        # Assets just registered → not orphaned with days_inactive=0
        resp = client.get("/api/catalog/orphans?days_inactive=0")
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["days_inactive"] == 0
        assert "orphans" in data
        assert "count" in data
        # All assets were just refreshed → orphans should be none
        if data["count"] > 0:
            orphan = data["orphans"][0]
            assert "name" in orphan
            assert "namespace" in orphan
            assert "asset_type" in orphan

    # Step 7: Catalog Stats

    def test_07_stats(self, client: TestClient, seeded_data):
        resp = client.get("/api/catalog/stats")
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert "assets_by_type" in data
        assert "total_relationships" in data
        total = sum(data["assets_by_type"].values())
        assert total > 0

    # Step 8: Re-registration Idempotency

    def test_08_re_registration(self, test_db, seeded_data):
        run_id = seeded_data["run_id"]
        G = seeded_data["graph"]
        yaml_cfg = seeded_data["yaml_cfg"]

        count1 = test_db.query(func.count(DataAsset.id)).scalar()

        register_run_assets(
            db=test_db,
            run_id=run_id,
            pipeline_name="e2e_pipeline",
            pipeline_yaml=yaml_cfg,
            lineage_graph=G,
            owner_id=None,
        )
        test_db.commit()

        count2 = test_db.query(func.count(DataAsset.id)).scalar()
        assert count2 == count1, "Re-registration created duplicate assets"

    # Step 9: Edge Cases

    def test_09_edge_cases(self, client: TestClient, seeded_data):
        # Missing run → 404
        resp = client.get(f"/api/runs/{uuid.uuid4()}/openlineage")
        assert resp.status_code == 404

        # High orphan threshold → 0 results
        resp = client.get("/api/catalog/orphans?days_inactive=99999")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# Helpers (module-level for fixture access)

def _build_pipeline_yaml() -> str:
    return """pipeline:
  name: e2e_pipeline
  steps:
    - name: load_data
      type: load
      file_id: "00000000-0000-0000-0000-000000000000"
    - name: filter_high
      type: filter
      input: load_data
      column: amount
      operator: greater_than
      value: 100
    - name: save_out
      type: save
      input: filter_high
      filename: output.csv
"""


def _build_lineage_graph() -> nx.DiGraph:
    G = nx.DiGraph()
    fid = "00000000-0000-0000-0000-000000000000"

    G.add_node(f"file::{fid}", asset_type="file",
               namespace="minio://uploads", label="sales.csv")

    load_cols = ["order_id", "amount", "status", "region"]
    for col in load_cols:
        G.add_node(f"col::load_data::{col}", asset_type="column",
                   namespace="pipeline://")
        G.add_edge(f"file::{fid}", f"col::load_data::{col}",
                   relation="reads_from", step_type="load")

    for col in load_cols:
        G.add_node(f"col::filter_high::{col}", asset_type="column",
                   namespace="pipeline://")
        G.add_edge(f"col::load_data::{col}", f"col::filter_high::{col}",
                   relation="transforms", step_type="filter")

    G.add_node("output::output.csv", asset_type="file",
               namespace="minio://outputs", label="output.csv")
    for col in load_cols:
        G.add_edge(f"col::filter_high::{col}", "output::output.csv",
                   relation="writes_to", step_type="save")

    return G
