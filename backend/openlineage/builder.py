"""Build OpenLineage 1.0 compliant events from PipelineIQ run data.

OpenLineage is the industry standard for data lineage, used by Apache Airflow,
Apache Spark, dbt, DataHub, Marquez, and OpenMetadata.

Spec: https://openlineage.io/spec/1-0-5/OpenLineage.json
"""
from datetime import datetime, timezone
from typing import Optional

import networkx as nx

OPENLINEAGE_SCHEMA_URL = "https://openlineage.io/spec/1-0-5/OpenLineage.json"
PRODUCER_URL = "https://github.com/pipelineiq/pipelineiq"


def build_openlineage_event(
    run_id: str,
    pipeline_name: str,
    status: str,
    started_at: datetime,
    completed_at: Optional[datetime],
    duration_ms: Optional[int],
    output_row_count: Optional[int],
    lineage_graph: nx.DiGraph,
    file_ids: list[str],
) -> dict:
    """Build a complete OpenLineage RunEvent for a pipeline run."""
    event_type = _map_status_to_event_type(status)
    event_time = (completed_at or started_at or datetime.now(timezone.utc)).isoformat()

    return {
        "eventType": event_type,
        "eventTime": event_time,
        "producer": PRODUCER_URL,
        "schemaURL": OPENLINEAGE_SCHEMA_URL,

        "run": {
            "runId": run_id,
            "facets": {
                "pipelineRunMetrics": {
                    "_producer": PRODUCER_URL,
                    "_schemaURL": f"{OPENLINEAGE_SCHEMA_URL}#/definitions/BaseFacet",
                    "durationMs": duration_ms,
                    "outputRowCount": output_row_count,
                    "startedAt": started_at.isoformat() if started_at else None,
                }
            }
        },

        "job": {
            "namespace": "pipelineiq",
            "name": pipeline_name,
            "facets": {
                "jobType": {
                    "_producer": PRODUCER_URL,
                    "_schemaURL": f"{OPENLINEAGE_SCHEMA_URL}#/definitions/BaseFacet",
                    "processingType": "BATCH",
                    "integration": "PIPELINEIQ",
                    "jobType": "PIPELINE",
                },
            }
        },

        "inputs": _build_inputs(lineage_graph, file_ids),
        "outputs": _build_outputs(lineage_graph),
    }


def _map_status_to_event_type(status: str) -> str:
    mapping = {
        "success": "COMPLETE",
        "healed": "COMPLETE",
        "failed": "FAIL",
        "running": "RUNNING",
        "pending": "START",
    }
    return mapping.get(status, "COMPLETE")


def _build_inputs(lineage_graph: nx.DiGraph, file_ids: list[str]) -> list[dict]:
    """Build OpenLineage input datasets from source nodes (no incoming edges)."""
    inputs = []

    source_nodes = [n for n in lineage_graph.nodes if lineage_graph.in_degree(n) == 0]

    for node_id in source_nodes:
        node_str = str(node_id)
        is_file = node_str.endswith((".csv", ".json", ".parquet"))
        is_topic = node_str.endswith(("-topic", ".topic")) or node_str.startswith("redpanda://")

        if is_file:
            namespace = "minio://pipelineiq-uploads"
            name = node_str
        elif is_topic:
            namespace = "redpanda://localhost:9092"
            name = node_str.replace("redpanda://", "")
        else:
            namespace = "pipeline://"
            name = node_str

        output_columns = [str(tgt) for _, tgt in lineage_graph.out_edges(node_id)]

        input_dataset: dict = {
            "namespace": namespace,
            "name": name,
            "facets": {},
        }

        if output_columns:
            input_dataset["facets"]["columnLineage"] = {
                "_producer": PRODUCER_URL,
                "_schemaURL": f"{OPENLINEAGE_SCHEMA_URL}#/definitions/BaseFacet",
                "fields": {
                    col: {
                        "inputFields": [
                            {"namespace": namespace, "name": name, "field": col}
                        ]
                    }
                    for col in output_columns[:50]
                }
            }

        inputs.append(input_dataset)

    return inputs


def _build_outputs(lineage_graph: nx.DiGraph) -> list[dict]:
    """Build OpenLineage output datasets from sink nodes (no outgoing edges)."""
    outputs = []

    sink_nodes = [
        n for n in lineage_graph.nodes
        if lineage_graph.out_degree(n) == 0 and lineage_graph.in_degree(n) > 0
    ]

    for node_id in sink_nodes:
        node_str = str(node_id)
        is_file = node_str.endswith((".csv", ".json", ".parquet"))

        namespace = "minio://pipelineiq-outputs" if is_file else "pipeline://"
        name = node_str

        input_fields = [str(src) for src, _ in lineage_graph.in_edges(node_id)]

        output_dataset: dict = {
            "namespace": namespace,
            "name": name,
            "facets": {},
        }

        if input_fields:
            output_dataset["facets"]["columnLineage"] = {
                "_producer": PRODUCER_URL,
                "_schemaURL": f"{OPENLINEAGE_SCHEMA_URL}#/definitions/BaseFacet",
                "fields": {
                    name: {
                        "inputFields": [
                            {"namespace": "pipeline://", "name": "pipeline", "field": f}
                            for f in input_fields[:20]
                        ]
                    }
                }
            }

        outputs.append(output_dataset)

    return outputs
