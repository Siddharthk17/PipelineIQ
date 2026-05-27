"""Tests for asset registration and node/edge classification."""
from backend.repositories.catalog import (
    _classify_node,
    _classify_edge,
    NS_MINIO_UPLOADS,
    NS_PIPELINE,
    NS_REDPANDA,
)


class TestAssetRegistration:
    def test_classify_csv_as_file_asset(self):
        asset_type, namespace, name = _classify_node("orders.csv", {})
        assert asset_type == "file"
        assert name == "orders.csv"

    def test_classify_json_as_file_asset(self):
        asset_type, _, name = _classify_node("data.json", {})
        assert asset_type == "file"

    def test_classify_parquet_as_file_asset(self):
        asset_type, _, name = _classify_node("output.parquet", {})
        assert asset_type == "file"

    def test_classify_xlsx_as_file_asset(self):
        asset_type, _, name = _classify_node("report.xlsx", {})
        assert asset_type == "file"

    def test_classify_unknown_as_column(self):
        asset_type, namespace, name = _classify_node("customer_id", {})
        assert asset_type == "column"
        assert name == "customer_id"

    def test_classify_step_column_as_column(self):
        asset_type, namespace, name = _classify_node("aggregate.amount_sum", {})
        assert asset_type == "column"

    def test_classify_minio_path_as_file(self):
        asset_type, namespace, name = _classify_node("minio://bucket/output.csv", {})
        assert asset_type == "file"

    def test_classify_s3_path_as_file(self):
        asset_type, namespace, name = _classify_node("s3://bucket/file.parquet", {})
        assert asset_type == "file"

    def test_classify_redpanda_topic(self):
        asset_type, namespace, name = _classify_node("redpanda://events-topic", {})
        assert asset_type == "topic"

    def test_classify_topic_suffix(self):
        asset_type, _, name = _classify_node("orders-topic", {})
        assert asset_type == "topic"

    def test_classify_edge_join_type(self):
        relation = _classify_edge({"step_type": "join"})
        assert relation == "joins"

    def test_classify_edge_load_type(self):
        relation = _classify_edge({"step_type": "load"})
        assert relation == "reads_from"

    def test_classify_edge_save_type(self):
        relation = _classify_edge({"step_type": "save"})
        assert relation == "writes_to"

    def test_classify_edge_default_transforms(self):
        relation = _classify_edge({})
        assert relation == "transforms"

    def test_classify_edge_explicit_relation(self):
        relation = _classify_edge({"relation": "transforms"})
        assert relation == "transforms"

    def test_classify_edge_type_field(self):
        relation = _classify_edge({"type": "reads_from"})
        assert relation == "reads_from"

    def test_classify_node_with_explicit_asset_type(self):
        asset_type, namespace, name = _classify_node(
            "custom_node", {"asset_type": "pipeline", "namespace": "custom://"}
        )
        assert asset_type == "pipeline"
        assert namespace == "custom://"
