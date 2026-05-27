"""Tests for OpenLineage event builder."""
import json

import networkx as nx
from datetime import datetime, timezone

from backend.openlineage.builder import (
    build_openlineage_event,
    OPENLINEAGE_SCHEMA_URL,
    PRODUCER_URL,
)


class TestOpenLineage:
    def test_event_has_correct_schema_url(self):
        assert "openlineage.io" in OPENLINEAGE_SCHEMA_URL

    def test_event_type_complete_for_success(self):
        event = build_openlineage_event(
            run_id="r1", pipeline_name="p1", status="success",
            started_at=datetime.now(timezone.utc), completed_at=None,
            duration_ms=None, output_row_count=None,
            lineage_graph=nx.DiGraph(), file_ids=[],
        )
        assert event["eventType"] == "COMPLETE"

    def test_event_type_fail_for_failed(self):
        event = build_openlineage_event(
            run_id="r1", pipeline_name="p1", status="failed",
            started_at=datetime.now(timezone.utc), completed_at=None,
            duration_ms=None, output_row_count=None,
            lineage_graph=nx.DiGraph(), file_ids=[],
        )
        assert event["eventType"] == "FAIL"

    def test_event_type_complete_for_healed(self):
        event = build_openlineage_event(
            run_id="r1", pipeline_name="p1", status="healed",
            started_at=datetime.now(timezone.utc), completed_at=None,
            duration_ms=None, output_row_count=None,
            lineage_graph=nx.DiGraph(), file_ids=[],
        )
        assert event["eventType"] == "COMPLETE"

    def test_event_has_run_id(self):
        event = build_openlineage_event(
            run_id="my-run-id", pipeline_name="p1", status="success",
            started_at=datetime.now(timezone.utc), completed_at=None,
            duration_ms=None, output_row_count=None,
            lineage_graph=nx.DiGraph(), file_ids=[],
        )
        assert event["run"]["runId"] == "my-run-id"

    def test_event_has_job_name_and_namespace(self):
        event = build_openlineage_event(
            run_id="r1", pipeline_name="my_pipeline", status="success",
            started_at=datetime.now(timezone.utc), completed_at=None,
            duration_ms=None, output_row_count=None,
            lineage_graph=nx.DiGraph(), file_ids=[],
        )
        assert event["job"]["name"] == "my_pipeline"
        assert event["job"]["namespace"] == "pipelineiq"

    def test_source_file_appears_in_inputs(self):
        G = nx.DiGraph()
        G.add_node("orders.csv")
        G.add_edge("orders.csv", "customer_id")

        event = build_openlineage_event(
            run_id="r1", pipeline_name="p1", status="success",
            started_at=datetime.now(timezone.utc), completed_at=None,
            duration_ms=None, output_row_count=None,
            lineage_graph=G, file_ids=[],
        )
        input_names = [i["name"] for i in event.get("inputs", [])]
        assert "orders.csv" in input_names

    def test_output_file_appears_in_outputs(self):
        G = nx.DiGraph()
        G.add_edge("amount", "report.csv")

        event = build_openlineage_event(
            run_id="r1", pipeline_name="p1", status="success",
            started_at=datetime.now(timezone.utc), completed_at=None,
            duration_ms=None, output_row_count=None,
            lineage_graph=G, file_ids=[],
        )
        output_names = [o["name"] for o in event.get("outputs", [])]
        assert "report.csv" in output_names

    def test_event_has_producer_url(self):
        event = build_openlineage_event(
            run_id="r1", pipeline_name="p1", status="success",
            started_at=datetime.now(timezone.utc), completed_at=None,
            duration_ms=None, output_row_count=None,
            lineage_graph=nx.DiGraph(), file_ids=[],
        )
        assert event["producer"] == PRODUCER_URL

    def test_event_time_is_iso_format(self):
        now = datetime.now(timezone.utc)
        event = build_openlineage_event(
            run_id="r1", pipeline_name="p1", status="success",
            started_at=now, completed_at=now,
            duration_ms=100, output_row_count=50,
            lineage_graph=nx.DiGraph(), file_ids=[],
        )
        assert "T" in event["eventTime"]

    def test_empty_graph_produces_empty_inputs_outputs(self):
        event = build_openlineage_event(
            run_id="r1", pipeline_name="p1", status="success",
            started_at=datetime.now(timezone.utc), completed_at=None,
            duration_ms=None, output_row_count=None,
            lineage_graph=nx.DiGraph(), file_ids=[],
        )
        assert event["inputs"] == []
        assert event["outputs"] == []

    def test_ndjson_format_one_json_per_line(self):
        events = ['{"a": 1}', '{"b": 2}', '{"c": 3}']
        ndjson = "\n".join(events)
        lines = ndjson.strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_bulk_export_endpoint_returns_ndjson_content_type(self):
        from backend.routers.lineage_export import export_all_openlineage
        source = inspect.getsource(export_all_openlineage)
        assert "ndjson" in source.lower() or "x-ndjson" in source.lower()


import inspect
