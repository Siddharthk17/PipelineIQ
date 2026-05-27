"""End-to-end integration tests for Week 10: Global Data Catalog + OpenLineage.

Tests the full flow: pipeline run -> asset registration -> blast radius query ->
catalog search -> OpenLineage export. Uses the FastAPI TestClient with in-memory
SQLite to verify the entire stack works together.
"""
import json
import uuid
from datetime import datetime, timezone

import networkx as nx
import pytest
from fastapi.testclient import TestClient

from backend.models import DataAsset, AssetRelationship, PipelineRun, PipelineStatus
from backend.repositories.catalog import (
    register_run_assets,
    upsert_data_asset,
    upsert_asset_relationship,
    get_blast_radius,
    get_upstream_lineage,
    search_assets,
    list_orphan_assets,
    NS_MINIO_UPLOADS,
    NS_PIPELINE,
)


class TestCatalogRepository:
    """Test the catalog repository layer with real database operations."""

    def test_upsert_data_asset_creates_new_record(self, test_db):
        asset_id = upsert_data_asset(
            db=test_db,
            asset_type="file",
            name="sales.csv",
            namespace=NS_MINIO_UPLOADS,
        )
        assert uuid.UUID(asset_id)

        asset = test_db.query(DataAsset).filter(DataAsset.id == uuid.UUID(asset_id)).first()
        assert asset is not None
        assert asset.asset_type == "file"
        assert asset.name == "sales.csv"
        assert asset.namespace == NS_MINIO_UPLOADS

    def test_upsert_data_asset_updates_existing_record(self, test_db):
        asset_id = upsert_data_asset(
            db=test_db,
            asset_type="column",
            name="customer_id",
            namespace=NS_PIPELINE,
        )

        asset_id2 = upsert_data_asset(
            db=test_db,
            asset_type="column",
            name="customer_id",
            namespace=NS_PIPELINE,
            metadata={"pipeline": "etl_v2"},
        )

        assert asset_id == asset_id2
        count = test_db.query(DataAsset).filter(
            DataAsset.name == "customer_id"
        ).count()
        assert count == 1

    def test_upsert_asset_relationship_creates_edge(self, test_db):
        src_id = upsert_data_asset(
            db=test_db, asset_type="column", name="amount", namespace=NS_PIPELINE
        )
        tgt_id = upsert_data_asset(
            db=test_db, asset_type="column", name="amount_sum", namespace=NS_PIPELINE
        )

        upsert_asset_relationship(
            db=test_db,
            source_id=src_id,
            target_id=tgt_id,
            relation="transforms",
            pipeline_name="sales_report",
        )
        test_db.commit()

        rel = test_db.query(AssetRelationship).filter(
            AssetRelationship.source_id == uuid.UUID(src_id)
        ).first()
        assert rel is not None
        assert rel.relation == "transforms"
        assert rel.pipeline_name == "sales_report"

    def test_register_run_assets_registers_pipeline_and_columns(self, test_db):
        G = nx.DiGraph()
        G.add_node("orders.csv")
        G.add_node("step_load")
        G.add_node("step_agg")
        G.add_node("revenue_sum")
        G.add_edge("orders.csv", "step_load", step_type="load")
        G.add_edge("step_load", "revenue_sum", step_type="aggregate")
        G.add_edge("revenue_sum", "step_agg", step_type="save")

        run_id = str(uuid.uuid4())
        count = register_run_assets(
            db=test_db,
            run_id=run_id,
            pipeline_name="test_pipeline",
            pipeline_yaml="pipeline:\n  name: test",
            lineage_graph=G,
        )

        assert count >= 3

        pipeline_asset = test_db.query(DataAsset).filter(
            DataAsset.asset_type == "pipeline",
            DataAsset.name == "test_pipeline",
        ).first()
        assert pipeline_asset is not None

        file_asset = test_db.query(DataAsset).filter(
            DataAsset.asset_type == "file",
            DataAsset.name == "orders.csv",
        ).first()
        assert file_asset is not None

    def test_register_run_assets_empty_graph_skips(self, test_db):
        count = register_run_assets(
            db=test_db,
            run_id=str(uuid.uuid4()),
            pipeline_name="empty_pipeline",
            pipeline_yaml="pipeline:\n  name: empty",
            lineage_graph=nx.DiGraph(),
        )
        assert count == 0

    def test_get_blast_radius_finds_downstream(self, test_db):
        src_id = upsert_data_asset(
            db=test_db, asset_type="column", name="customer_id", namespace=NS_PIPELINE
        )
        mid_id = upsert_data_asset(
            db=test_db, asset_type="column", name="customer_name", namespace=NS_PIPELINE
        )
        tgt_id = upsert_data_asset(
            db=test_db, asset_type="column", name="report_output", namespace=NS_PIPELINE
        )

        upsert_asset_relationship(db=test_db, source_id=src_id, target_id=mid_id, relation="transforms")
        upsert_asset_relationship(db=test_db, source_id=mid_id, target_id=tgt_id, relation="transforms")
        test_db.commit()

        results = get_blast_radius(test_db, asset_name="customer_id")

        names = [r["name"] for r in results]
        assert "customer_id" in names
        assert "customer_name" in names
        assert "report_output" in names

        depth_map = {r["name"]: r["depth"] for r in results}
        assert depth_map["customer_id"] == 0
        assert depth_map["customer_name"] == 1
        assert depth_map["report_output"] == 2

    def test_get_blast_radius_filters_by_type(self, test_db):
        upsert_data_asset(db=test_db, asset_type="column", name="col_a", namespace=NS_PIPELINE)
        upsert_data_asset(db=test_db, asset_type="file", name="file_a.csv", namespace=NS_MINIO_UPLOADS)
        test_db.commit()

        col_results = get_blast_radius(test_db, asset_name="col_a", asset_type="column")
        assert len(col_results) >= 1
        assert all(r["asset_type"] == "column" for r in col_results)

    def test_get_upstream_lineage_finds_ancestors(self, test_db):
        src_id = upsert_data_asset(
            db=test_db, asset_type="column", name="raw_amount", namespace=NS_PIPELINE
        )
        tgt_id = upsert_data_asset(
            db=test_db, asset_type="column", name="computed_total", namespace=NS_PIPELINE
        )

        upsert_asset_relationship(db=test_db, source_id=src_id, target_id=tgt_id, relation="transforms")
        test_db.commit()

        results = get_upstream_lineage(test_db, asset_name="computed_total")

        names = [r["name"] for r in results]
        assert "computed_total" in names
        assert "raw_amount" in names

    def test_search_assets_finds_by_name(self, test_db):
        upsert_data_asset(db=test_db, asset_type="column", name="customer_id", namespace=NS_PIPELINE)
        upsert_data_asset(db=test_db, asset_type="column", name="customer_name", namespace=NS_PIPELINE)
        upsert_data_asset(db=test_db, asset_type="column", name="order_total", namespace=NS_PIPELINE)
        test_db.commit()

        results = search_assets(test_db, query="customer")
        names = [r["name"] for r in results]
        assert "customer_id" in names
        assert "customer_name" in names
        assert "order_total" not in names

    def test_search_assets_returns_empty_for_short_query(self, test_db):
        results = search_assets(test_db, query="a")
        assert results == []

    def test_search_assets_filters_by_type(self, test_db):
        upsert_data_asset(db=test_db, asset_type="column", name="col_x", namespace=NS_PIPELINE)
        upsert_data_asset(db=test_db, asset_type="file", name="file_x.csv", namespace=NS_MINIO_UPLOADS)
        test_db.commit()

        col_results = search_assets(test_db, query="x", asset_type="column")
        assert all(r["asset_type"] == "column" for r in col_results)

    def test_list_orphan_assets_finds_stale(self, test_db):
        from datetime import timedelta

        upsert_data_asset(db=test_db, asset_type="column", name="fresh_col", namespace=NS_PIPELINE)

        stale_date = datetime.now(timezone.utc) - timedelta(days=100)
        stale_asset = DataAsset(
            asset_type="column",
            name="stale_col",
            namespace="pipeline://",
            last_seen_at=stale_date,
            created_at=stale_date,
        )
        test_db.add(stale_asset)
        test_db.commit()

        orphans = list_orphan_assets(test_db, days_inactive=90)
        names = [o["name"] for o in orphans]
        assert "stale_col" in names
        assert "fresh_col" not in names


class TestCatalogAPI:
    """Test the catalog API endpoints through the FastAPI TestClient."""

    def test_search_endpoint_returns_results(self, client: TestClient, test_db):
        upsert_data_asset(
            db=test_db, asset_type="column", name="test_column", namespace=NS_PIPELINE
        )
        test_db.commit()

        resp = client.get("/api/catalog/search?q=test_column")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert any(r["name"] == "test_column" for r in data["results"])

    def test_search_endpoint_rejects_short_query(self, client: TestClient):
        resp = client.get("/api/catalog/search?q=a")
        assert resp.status_code == 422

    def test_impact_endpoint_returns_downstream(self, client: TestClient, test_db):
        src_id = upsert_data_asset(
            db=test_db, asset_type="column", name="blast_source", namespace=NS_PIPELINE
        )
        tgt_id = upsert_data_asset(
            db=test_db, asset_type="column", name="blast_target", namespace=NS_PIPELINE
        )
        upsert_asset_relationship(
            db=test_db, source_id=src_id, target_id=tgt_id, relation="transforms"
        )
        test_db.commit()

        resp = client.get("/api/catalog/assets/blast_source/impact")
        assert resp.status_code == 200
        data = resp.json()
        assert data["asset_name"] == "blast_source"
        assert data["total_assets"] >= 1

    def test_impact_endpoint_returns_empty_for_unknown(self, client: TestClient):
        resp = client.get("/api/catalog/assets/nonexistent_column/impact")
        assert resp.status_code == 200
        data = resp.json()
        assert data["downstream"] == []
        assert "message" in data

    def test_lineage_endpoint_returns_upstream(self, client: TestClient, test_db):
        src_id = upsert_data_asset(
            db=test_db, asset_type="column", name="upstream_src", namespace=NS_PIPELINE
        )
        tgt_id = upsert_data_asset(
            db=test_db, asset_type="column", name="upstream_tgt", namespace=NS_PIPELINE
        )
        upsert_asset_relationship(
            db=test_db, source_id=src_id, target_id=tgt_id, relation="transforms"
        )
        test_db.commit()

        resp = client.get("/api/catalog/assets/upstream_tgt/lineage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["asset_name"] == "upstream_tgt"
        assert data["total"] >= 1

    def test_orphans_endpoint_returns_list(self, client: TestClient):
        resp = client.get("/api/catalog/orphans")
        assert resp.status_code == 200
        data = resp.json()
        assert "orphans" in data
        assert "count" in data
        assert "days_inactive" in data

    def test_stats_endpoint_returns_counts(self, client: TestClient, test_db):
        upsert_data_asset(
            db=test_db, asset_type="pipeline", name="stats_test_pipeline", namespace=NS_PIPELINE
        )
        test_db.commit()

        resp = client.get("/api/catalog/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "assets_by_type" in data
        assert "total_relationships" in data


class TestOpenLineageAPI:
    """Test the OpenLineage export API endpoints."""

    @pytest.fixture(autouse=True)
    def _patch_redis(self):
        """Prevent Redis connection attempts (~10s delay per test)."""
        from unittest.mock import MagicMock, patch
        with patch("backend.repositories.catalog.get_cache_redis_binary", return_value=MagicMock()):
            yield

    # Must match MOCK_USER_ID in conftest.py
    MOCK_USER_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    def test_openlineage_single_run_returns_event(self, client: TestClient, test_db):
        from backend.models import User
        mock_user = test_db.query(User).filter(User.id == self.MOCK_USER_ID).first()
        if not mock_user:
            mock_user = User(
                id=self.MOCK_USER_ID,
                email="testadmin@test.com",
                username="testadmin",
                hashed_password="hashed",
                role="admin",
                is_active=True,
            )
            test_db.add(mock_user)
            test_db.commit()

        run_id = uuid.uuid4()
        run = PipelineRun(
            id=run_id,
            name="ol_test_run",
            status=PipelineStatus.COMPLETED,
            yaml_config="pipeline:\n  name: ol_test",
            user_id=self.MOCK_USER_ID,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            total_rows_out=100,
        )
        test_db.add(run)
        test_db.commit()

        resp = client.get(f"/api/runs/{run_id}/openlineage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["schemaURL"] == "https://openlineage.io/spec/1-0-5/OpenLineage.json"
        assert data["eventType"] == "COMPLETE"
        assert data["run"]["runId"] == str(run_id)
        assert data["job"]["name"] == "ol_test_run"
        assert data["job"]["namespace"] == "pipelineiq"

    def test_openlineage_single_run_not_found(self, client: TestClient):
        resp = client.get("/api/runs/00000000-0000-0000-0000-000000000000/openlineage")
        assert resp.status_code == 404

    def test_openlineage_bulk_export_returns_ndjson(self, client: TestClient, test_db):
        from backend.models import User
        mock_user = test_db.query(User).filter(User.id == self.MOCK_USER_ID).first()
        if not mock_user:
            mock_user = User(
                id=self.MOCK_USER_ID,
                email="testadmin@test.com",
                username="testadmin",
                hashed_password="hashed",
                role="admin",
                is_active=True,
            )
            test_db.add(mock_user)
            test_db.commit()

        run_id = uuid.uuid4()
        run = PipelineRun(
            id=run_id,
            name="bulk_export_run",
            status=PipelineStatus.COMPLETED,
            yaml_config="pipeline:\n  name: bulk",
            user_id=self.MOCK_USER_ID,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        test_db.add(run)
        test_db.commit()

        resp = client.get("/api/lineage/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-ndjson"

        content = resp.text.strip()
        if content:
            lines = content.split("\n")
            for line in lines:
                event = json.loads(line)
                assert "eventType" in event
                assert "schemaURL" in event
                assert "run" in event
                assert "job" in event

    def test_openlineage_event_for_failed_run(self, client: TestClient, test_db):
        from backend.models import User
        mock_user = test_db.query(User).filter(User.id == self.MOCK_USER_ID).first()
        if not mock_user:
            mock_user = User(
                id=self.MOCK_USER_ID,
                email="testadmin@test.com",
                username="testadmin",
                hashed_password="hashed",
                role="admin",
                is_active=True,
            )
            test_db.add(mock_user)
            test_db.commit()

        run_id = uuid.uuid4()
        run = PipelineRun(
            id=run_id,
            name="failed_run",
            status=PipelineStatus.FAILED,
            yaml_config="pipeline:\n  name: failed",
            user_id=self.MOCK_USER_ID,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            error_message="Something broke",
        )
        test_db.add(run)
        test_db.commit()

        resp = client.get(f"/api/runs/{run_id}/openlineage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["eventType"] == "FAIL"


class TestAssetRegistrationIntegration:
    """Test the full pipeline -> asset registration flow."""

    def test_register_run_assets_creates_complete_graph(self, test_db):
        G = nx.DiGraph()
        G.add_node("sales_data.csv")
        G.add_node("load_sales")
        G.add_node("filter_active")
        G.add_node("aggregate_region")
        G.add_node("save_report")
        G.add_node("region")
        G.add_node("amount")
        G.add_node("amount_sum")
        G.add_node("region_report.csv")

        G.add_edge("sales_data.csv", "load_sales", step_type="load")
        G.add_edge("load_sales", "amount", step_type="load")
        G.add_edge("load_sales", "region", step_type="load")
        G.add_edge("amount", "filter_active", step_type="filter")
        G.add_edge("filter_active", "amount", step_type="filter")
        G.add_edge("amount", "aggregate_region", step_type="aggregate")
        G.add_edge("region", "aggregate_region", step_type="aggregate")
        G.add_edge("aggregate_region", "amount_sum", step_type="aggregate")
        G.add_edge("amount_sum", "save_report", step_type="save")
        G.add_edge("save_report", "region_report.csv", step_type="save")

        run_id = str(uuid.uuid4())
        count = register_run_assets(
            db=test_db,
            run_id=run_id,
            pipeline_name="sales_report",
            pipeline_yaml="pipeline:\n  name: sales_report\n  steps:\n    - name: load",
            lineage_graph=G,
        )

        assert count >= 5

        pipeline_assets = test_db.query(DataAsset).filter(
            DataAsset.asset_type == "pipeline"
        ).all()
        assert len(pipeline_assets) >= 1

        file_assets = test_db.query(DataAsset).filter(
            DataAsset.asset_type == "file"
        ).all()
        assert len(file_assets) >= 1

        relationships = test_db.query(AssetRelationship).all()
        assert len(relationships) >= 5

    def test_blast_radius_across_registered_assets(self, test_db):
        G = nx.DiGraph()
        G.add_node("source.csv")
        G.add_node("step_a")
        G.add_node("step_b")
        G.add_node("output_col")
        G.add_edge("source.csv", "step_a", step_type="load")
        G.add_edge("step_a", "step_b", step_type="filter")
        G.add_edge("step_b", "output_col", step_type="save")

        run_id = str(uuid.uuid4())
        register_run_assets(
            db=test_db,
            run_id=run_id,
            pipeline_name="blast_test",
            pipeline_yaml="pipeline:\n  name: blast_test",
            lineage_graph=G,
        )

        results = get_blast_radius(test_db, asset_name="source.csv")
        assert len(results) >= 1

        results2 = get_blast_radius(test_db, asset_name="output_col")
        assert len(results2) >= 1

    def test_multiple_runs_update_last_seen_at(self, test_db):
        run_id_1 = str(uuid.uuid4())
        run_id_2 = str(uuid.uuid4())

        G1 = nx.DiGraph()
        G1.add_node("shared_file.csv")
        G1.add_edge("shared_file.csv", "step1", step_type="load")

        G2 = nx.DiGraph()
        G2.add_node("shared_file.csv")
        G2.add_edge("shared_file.csv", "step2", step_type="load")

        register_run_assets(
            db=test_db, run_id=run_id_1, pipeline_name="run_1",
            pipeline_yaml="pipeline:\n  name: run_1", lineage_graph=G1,
        )

        import time
        time.sleep(0.01)

        register_run_assets(
            db=test_db, run_id=run_id_2, pipeline_name="run_2",
            pipeline_yaml="pipeline:\n  name: run_2", lineage_graph=G2,
        )

        asset = test_db.query(DataAsset).filter(
            DataAsset.name == "shared_file.csv"
        ).first()
        assert asset is not None

        count = test_db.query(DataAsset).filter(
            DataAsset.name == "shared_file.csv"
        ).count()
        assert count == 1


class TestCatalogAPIGaps:
    """Edge cases and validation for existing catalog endpoints."""

    def test_search_endpoint_filters_by_asset_type(self, client: TestClient, test_db):
        upsert_data_asset(db=test_db, asset_type="column", name="col_a", namespace=NS_PIPELINE)
        upsert_data_asset(db=test_db, asset_type="file", name="col_a.csv", namespace=NS_MINIO_UPLOADS)
        test_db.commit()

        resp = client.get("/api/catalog/search?q=col_a&asset_type=column")
        assert resp.status_code == 200
        data = resp.json()
        assert all(r["asset_type"] == "column" for r in data["results"])
        assert data["count"] == 1

    def test_search_endpoint_empty_results(self, client: TestClient):
        resp = client.get("/api/catalog/search?q=zzzzzznonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["count"] == 0

    def test_search_endpoint_handles_special_characters(self, client: TestClient, test_db):
        upsert_data_asset(db=test_db, asset_type="column", name="order-total_2024", namespace=NS_PIPELINE)
        test_db.commit()

        resp = client.get("/api/catalog/search?q=order-total")
        assert resp.status_code == 200
        data = resp.json()
        names = [r["name"] for r in data["results"]]
        assert "order-total_2024" in names

    def test_impact_endpoint_unknown_asset(self, client: TestClient):
        resp = client.get("/api/catalog/assets/does_not_exist/impact")
        assert resp.status_code == 200
        data = resp.json()
        assert data["downstream"] == []
        assert data["depth_reached"] == 0

    def test_impact_endpoint_with_max_depth(self, client: TestClient, test_db):
        src_id = upsert_data_asset(db=test_db, asset_type="column", name="depth_src", namespace=NS_PIPELINE)
        mid_id = upsert_data_asset(db=test_db, asset_type="column", name="depth_mid", namespace=NS_PIPELINE)
        tgt_id = upsert_data_asset(db=test_db, asset_type="column", name="depth_tgt", namespace=NS_PIPELINE)
        upsert_asset_relationship(db=test_db, source_id=src_id, target_id=mid_id, relation="transforms")
        upsert_asset_relationship(db=test_db, source_id=mid_id, target_id=tgt_id, relation="transforms")
        test_db.commit()

        resp = client.get("/api/catalog/assets/depth_src/impact?max_depth=1")
        assert resp.status_code == 200
        data = resp.json()
        names = [r["name"] for r in data["downstream"]]
        assert "depth_src" in names
        assert "depth_mid" in names
        assert "depth_tgt" not in names
        assert data["depth_reached"] == 1

    def test_impact_endpoint_with_asset_type_filter(self, client: TestClient, test_db):
        src_id = upsert_data_asset(db=test_db, asset_type="column", name="type_filtered", namespace=NS_PIPELINE)
        tgt_id = upsert_data_asset(db=test_db, asset_type="file", name="type_filtered.csv", namespace=NS_MINIO_UPLOADS)
        upsert_asset_relationship(db=test_db, source_id=src_id, target_id=tgt_id, relation="transforms")
        test_db.commit()

        resp = client.get("/api/catalog/assets/type_filtered/impact?asset_type=file")
        assert resp.status_code == 200
        data = resp.json()
        assert all(r["asset_type"] == "file" for r in data["downstream"])

    def test_lineage_endpoint_unknown_asset(self, client: TestClient):
        resp = client.get("/api/catalog/assets/does_not_exist/lineage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["upstream"] == []
        assert data["total"] == 0

    def test_lineage_endpoint_with_max_depth(self, client: TestClient, test_db):
        src_id = upsert_data_asset(db=test_db, asset_type="column", name="up_depth_src", namespace=NS_PIPELINE)
        mid_id = upsert_data_asset(db=test_db, asset_type="column", name="up_depth_mid", namespace=NS_PIPELINE)
        tgt_id = upsert_data_asset(db=test_db, asset_type="column", name="up_depth_tgt", namespace=NS_PIPELINE)
        upsert_asset_relationship(db=test_db, source_id=src_id, target_id=mid_id, relation="transforms")
        upsert_asset_relationship(db=test_db, source_id=mid_id, target_id=tgt_id, relation="transforms")
        test_db.commit()

        resp = client.get("/api/catalog/assets/up_depth_tgt/lineage?max_depth=1")
        assert resp.status_code == 200
        data = resp.json()
        names = [r["name"] for r in data["upstream"]]
        assert "up_depth_tgt" in names
        assert "up_depth_mid" in names
        assert "up_depth_src" not in names

    def test_orphans_endpoint_no_orphans(self, client: TestClient, test_db):
        upsert_data_asset(db=test_db, asset_type="column", name="recent_col", namespace=NS_PIPELINE)
        test_db.commit()

        resp = client.get("/api/catalog/orphans?days_inactive=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["orphans"] == []

    def test_stats_endpoint_empty_catalog(self, client: TestClient):
        resp = client.get("/api/catalog/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["assets_by_type"] == {}
        assert data["total_relationships"] == 0


class TestOpenLineageAPIGaps:
    """OpenLineage edge cases."""

    @pytest.fixture(autouse=True)
    def _patch_redis(self):
        """Prevent Redis connection attempts (~10s delay per test)."""
        from unittest.mock import MagicMock, patch
        with patch("backend.repositories.catalog.get_cache_redis_binary", return_value=MagicMock()):
            yield

    def test_openlineage_invalid_uuid_format(self, client: TestClient):
        resp = client.get("/api/runs/not-a-uuid/openlineage")
        assert resp.status_code in (422, 500)

    def test_openlineage_bulk_export_empty(self, client: TestClient):
        resp = client.get("/api/lineage/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-ndjson"
        assert resp.text.strip() == ""

    def test_openlineage_event_with_lineage_inputs(self, client: TestClient, test_db):
        from backend.models import User, LineageGraph
        mock_user = test_db.query(User).filter(User.id == uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")).first()
        if not mock_user:
            mock_user = User(
                id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                email="testadmin@test.com",
                username="testadmin",
                hashed_password="hashed",
                role="admin",
                is_active=True,
            )
            test_db.add(mock_user)
            test_db.commit()

        run_id = uuid.uuid4()
        run = PipelineRun(
            id=run_id,
            name="lineage_io_run",
            status=PipelineStatus.COMPLETED,
            yaml_config="pipeline:\n  name: lineage_io",
            user_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            total_rows_out=100,
        )
        test_db.add(run)

        import networkx as nx
        import json
        G = nx.DiGraph()
        G.add_node("source_orders.csv")
        G.add_node("final_report.csv")
        G.add_edge("source_orders.csv", "filter_step", step_type="load")
        G.add_edge("filter_step", "final_report.csv", step_type="save")

        lg = LineageGraph(
            pipeline_run_id=run_id,
            graph_data=nx.node_link_data(G),
            react_flow_data={"nodes": [], "edges": []},
        )
        test_db.add(lg)
        test_db.commit()

        resp = client.get(f"/api/runs/{run_id}/openlineage")
        assert resp.status_code == 200
        data = resp.json()
        input_names = [i["name"] for i in data.get("inputs", [])]
        output_names = [o["name"] for o in data.get("outputs", [])]
        assert "source_orders.csv" in input_names
        assert "final_report.csv" in output_names
