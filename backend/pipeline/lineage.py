"""Column-level lineage tracking using a directed acyclic graph (DAG).

Records every column's journey through a pipeline as a directed graph
using NetworkX. Supports backward ancestry tracing, forward impact
analysis, and React Flow visualization layout.

Node naming convention (consistent everywhere):
    Source file:     "file::{file_id}"
    Column node:     "col::{step_name}::{column_name}"
    Step node:       "step::{step_name}"
    Output file:     "output::{step_name}::{filename}"
"""

# Standard library
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

# Third-party packages
import networkx as nx

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# NODE ID BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════


def _file_node_id(file_id: str) -> str:
    """Build a source file node ID."""
    return f"file::{file_id}"


def _col_node_id(step_name: str, column_name: str) -> str:
    """Build a column node ID scoped to a step."""
    return f"col::{step_name}::{column_name}"


def _step_node_id(step_name: str) -> str:
    """Build a step node ID."""
    return f"step::{step_name}"


def _output_node_id(step_name: str, filename: str) -> str:
    """Build an output file node ID."""
    return f"output::{step_name}::{filename}"


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TransformationStep:
    """A single step in a column's transformation history."""

    step_name: str
    step_type: str
    detail: str


@dataclass
class ColumnLineage:
    """Complete ancestry trace for a single output column."""

    column_name: str
    source_file: str
    source_column: str
    transformation_chain: List[TransformationStep]
    total_steps: int


@dataclass
class ImpactAnalysis:
    """Forward impact analysis for a column."""

    source_step: str
    source_column: str
    affected_steps: List[str]
    affected_output_columns: List[str]


@dataclass
class ReactFlowNode:
    """A node in the React Flow visualization."""

    id: str
    type: str
    data: Dict[str, Any]  # Any needed: React Flow data payload is polymorphic
    position: Dict[str, int]


@dataclass
class ReactFlowEdge:
    """An edge in the React Flow visualization."""

    id: str
    source: str
    target: str
    animated: bool = False
    style: Optional[Dict[str, str]] = None


@dataclass
class ReactFlowGraph:
    """Complete React Flow graph for visualization."""

    nodes: List[ReactFlowNode]
    edges: List[ReactFlowEdge]


# ═══════════════════════════════════════════════════════════════════════════════
# LAYOUT CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

LAYER_X_SPACING: int = 300
NODE_Y_SPACING: int = 80

# Node type → React Flow type mapping
_NODE_TYPE_MAP: Dict[str, str] = {
    "source_file": "sourceFile",
    "source_column": "columnNode",
    "step": "stepNode",
    "output_column": "columnNode",
    "output_file": "outputFile",
}


# ═══════════════════════════════════════════════════════════════════════════════
# LINEAGE RECORDER
# ═══════════════════════════════════════════════════════════════════════════════


class LineageRecorder:
    """Records column-level lineage as a directed acyclic graph.

    Maintains an internal NetworkX DiGraph where nodes represent files,
    columns, and pipeline steps, and edges represent data flow between
    them. Provides methods for recording each step type, querying
    column ancestry, performing impact analysis, and exporting to
    React Flow format.
    """

    def __init__(self) -> None:
        self.graph: nx.DiGraph = nx.DiGraph()

    # ── Recording Methods ─────────────────────────────────────────────────────

    def record_load(
        self,
        file_id: str,
        file_name: str,
        step_name: str,
        columns: List[str],
        dtypes: Dict[str, str],
    ) -> None:
        """Record a file load step in the lineage graph.

        Creates a file node and one column node per column, with edges
        from the file to each column.

        Args:
            file_id: Unique identifier of the uploaded file.
            file_name: Original filename for display purposes.
            step_name: Name of the load step.
            columns: List of column names loaded from the file.
            dtypes: Mapping of column name to pandas dtype string.
        """
        file_node = _file_node_id(file_id)
        step_node = _step_node_id(step_name)

        self._add_node(file_node, {
            "node_type": "source_file",
            "label": file_name,
            "step_name": step_name,
            "file_name": file_name,
        })
        self._add_node(step_node, {
            "node_type": "step",
            "label": f"Load: {file_name}",
            "step_name": step_name,
            "step_type": "load",
        })
        self.graph.add_edge(file_node, step_node)

        for col in columns:
            col_node = _col_node_id(step_name, col)
            self._add_node(col_node, {
                "node_type": "source_column",
                "label": col,
                "step_name": step_name,
                "column_name": col,
                "data_type": dtypes.get(col, "unknown"),
            })
            self.graph.add_edge(step_node, col_node)

        logger.debug(
            "Recorded load: file=%s, step=%s, columns=%d",
            file_name, step_name, len(columns),
        )

    def record_passthrough(
        self,
        step_name: str,
        step_type: str,
        input_step: str,
        columns: List[str],
    ) -> None:
        """Record a step that passes all columns through unchanged.

        Used for filter and sort steps that change rows but not columns.

        Args:
            step_name: Name of this step.
            step_type: Type of this step (e.g., "filter", "sort").
            input_step: Name of the step providing input data.
            columns: List of column names passing through.
        """
        step_node = _step_node_id(step_name)
        self._add_node(step_node, {
            "node_type": "step",
            "label": f"{step_type.title()}: {step_name}",
            "step_name": step_name,
            "step_type": step_type,
        })

        for col in columns:
            input_col = _col_node_id(input_step, col)
            output_col = _col_node_id(step_name, col)
            self._add_node(output_col, {
                "node_type": "output_column",
                "label": col,
                "step_name": step_name,
                "column_name": col,
            })
            self.graph.add_edge(input_col, step_node)
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded passthrough: step=%s, type=%s, columns=%d",
            step_name, step_type, len(columns),
        )

    def record_projection(
        self,
        step_name: str,
        input_step: str,
        kept_columns: List[str],
        dropped_columns: List[str],
    ) -> None:
        """Record a select/projection step that keeps only specified columns.

        Dropped columns get no output edge, effectively pruning them
        from the lineage graph.

        Args:
            step_name: Name of this select step.
            input_step: Name of the step providing input data.
            kept_columns: Columns that are kept in the output.
            dropped_columns: Columns that are removed from the output.
        """
        step_node = _step_node_id(step_name)
        self._add_node(step_node, {
            "node_type": "step",
            "label": f"Select: {step_name}",
            "step_name": step_name,
            "step_type": "select",
        })

        for col in kept_columns:
            input_col = _col_node_id(input_step, col)
            output_col = _col_node_id(step_name, col)
            self._add_node(output_col, {
                "node_type": "output_column",
                "label": col,
                "step_name": step_name,
                "column_name": col,
            })
            self.graph.add_edge(input_col, step_node)
            self.graph.add_edge(step_node, output_col)

        # Record dropped columns as edges to step only (no output)
        for col in dropped_columns:
            input_col = _col_node_id(input_step, col)
            self.graph.add_edge(input_col, step_node)

        logger.debug(
            "Recorded projection: step=%s, kept=%d, dropped=%d",
            step_name, len(kept_columns), len(dropped_columns),
        )

    def record_rename(
        self,
        step_name: str,
        input_step: str,
        rename_mapping: Dict[str, str],
        all_columns: List[str],
    ) -> None:
        """Record a column rename step.

        Renamed columns get edges from old name to new name.
        Unchanged columns pass through with the same name.

        Args:
            step_name: Name of this rename step.
            input_step: Name of the step providing input data.
            rename_mapping: Mapping of old column name to new column name.
            all_columns: All input columns (both renamed and unchanged).
        """
        step_node = _step_node_id(step_name)
        self._add_node(step_node, {
            "node_type": "step",
            "label": f"Rename: {step_name}",
            "step_name": step_name,
            "step_type": "rename",
        })

        for col in all_columns:
            input_col = _col_node_id(input_step, col)
            new_name = rename_mapping.get(col, col)
            output_col = _col_node_id(step_name, new_name)
            self._add_node(output_col, {
                "node_type": "output_column",
                "label": new_name,
                "step_name": step_name,
                "column_name": new_name,
            })
            self.graph.add_edge(input_col, step_node)
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded rename: step=%s, renamed=%d, total=%d",
            step_name, len(rename_mapping), len(all_columns),
        )

    def record_join(
        self,
        step_name: str,
        left_step: str,
        right_step: str,
        left_cols: List[str],
        right_cols: List[str],
        output_cols: List[str],
        join_key: str,
        how: str,
    ) -> None:
        """Record a join step combining two DataFrames.

        Both left and right columns flow into the step node. Join key
        edges are marked with an is_join_key attribute.

        Args:
            step_name: Name of this join step.
            left_step: Name of the left input step.
            right_step: Name of the right input step.
            left_cols: Column names from the left DataFrame.
            right_cols: Column names from the right DataFrame.
            output_cols: Column names in the joined output.
            join_key: Column name used as the join key.
            how: Join method (inner, left, right, outer).
        """
        step_node = _step_node_id(step_name)
        self._add_node(step_node, {
            "node_type": "step",
            "label": f"Join ({how}): {step_name}",
            "step_name": step_name,
            "step_type": "join",
        })

        self._connect_join_side(left_step, left_cols, step_node, join_key)
        self._connect_join_side(right_step, right_cols, step_node, join_key)

        for col in output_cols:
            output_col = _col_node_id(step_name, col)
            self._add_node(output_col, {
                "node_type": "output_column",
                "label": col,
                "step_name": step_name,
                "column_name": col,
            })
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded join: step=%s, how=%s, key=%s, output_cols=%d",
            step_name, how, join_key, len(output_cols),
        )

    def record_aggregate(
        self,
        step_name: str,
        input_step: str,
        group_by_cols: List[str],
        aggregations: List[Dict[str, str]],
        output_cols: List[str],
    ) -> None:
        """Record a group-by aggregation step.

        Group-by columns pass through. Aggregated columns create new
        output column nodes with computed names.

        Args:
            step_name: Name of this aggregate step.
            input_step: Name of the step providing input data.
            group_by_cols: Columns used for grouping.
            aggregations: List of {"column": ..., "function": ..., "alias": ...}.
            output_cols: All output column names after aggregation.
        """
        step_node = _step_node_id(step_name)
        self._add_node(step_node, {
            "node_type": "step",
            "label": f"Aggregate: {step_name}",
            "step_name": step_name,
            "step_type": "aggregate",
        })

        # Group-by columns pass through
        for col in group_by_cols:
            input_col = _col_node_id(input_step, col)
            self.graph.add_edge(input_col, step_node)

        # Aggregated columns
        for agg in aggregations:
            input_col = _col_node_id(input_step, agg["column"])
            self.graph.add_edge(input_col, step_node)

        # Output columns
        for col in output_cols:
            output_col = _col_node_id(step_name, col)
            self._add_node(output_col, {
                "node_type": "output_column",
                "label": col,
                "step_name": step_name,
                "column_name": col,
            })
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded aggregate: step=%s, group_by=%d, aggs=%d, output=%d",
            step_name, len(group_by_cols), len(aggregations), len(output_cols),
        )

    def record_save(
        self,
        step_name: str,
        input_step: str,
        filename: str,
        columns: List[str],
    ) -> None:
        """Record a save step writing output to a file.

        Creates an output file node with edges from the last column
        nodes through the step node.

        Args:
            step_name: Name of this save step.
            input_step: Name of the step providing input data.
            filename: Output filename.
            columns: Column names being saved.
        """
        step_node = _step_node_id(step_name)
        output_file = _output_node_id(step_name, filename)

        self._add_node(step_node, {
            "node_type": "step",
            "label": f"Save: {filename}",
            "step_name": step_name,
            "step_type": "save",
        })
        self._add_node(output_file, {
            "node_type": "output_file",
            "label": filename,
            "step_name": step_name,
            "file_name": filename,
        })

        for col in columns:
            input_col = _col_node_id(input_step, col)
            self.graph.add_edge(input_col, step_node)

        self.graph.add_edge(step_node, output_file)

        logger.debug(
            "Recorded save: step=%s, filename=%s, columns=%d",
            step_name, filename, len(columns),
        )

    # ── Query Methods ─────────────────────────────────────────────────────────

    def get_column_ancestry(
        self, output_step_name: str, column_name: str
    ) -> ColumnLineage:
        """Trace a column backward to its source file.

        Traverses the graph from the output column node backward
        through all ancestors, collecting transformation steps and
        identifying the original source file and column.

        Args:
            output_step_name: Name of the step containing the column.
            column_name: Name of the column to trace.

        Returns:
            ColumnLineage with source file, source column, and the
            complete transformation chain.
        """
        target_node = _col_node_id(output_step_name, column_name)
        ancestors = nx.ancestors(self.graph, target_node)

        source_file = "unknown"
        source_column = column_name
        transformation_chain: List[TransformationStep] = []

        # Collect step nodes from ancestors in topological order
        step_ancestors = [
            node for node in ancestors
            if self.graph.nodes[node].get("node_type") == "step"
        ]

        # Sort by topological order
        topo_order = list(nx.topological_sort(self.graph))
        topo_index = {node: idx for idx, node in enumerate(topo_order)}
        step_ancestors.sort(key=lambda n: topo_index.get(n, 0))

        for step_node in step_ancestors:
            attrs = self.graph.nodes[step_node]
            transformation_chain.append(TransformationStep(
                step_name=attrs.get("step_name", ""),
                step_type=attrs.get("step_type", ""),
                detail=attrs.get("label", ""),
            ))

        # Find source file
        file_ancestors = [
            node for node in ancestors
            if self.graph.nodes[node].get("node_type") == "source_file"
        ]
        if file_ancestors:
            source_file = self.graph.nodes[file_ancestors[0]].get("label", "unknown")

        # Find source column (first column node in ancestry)
        col_ancestors = [
            node for node in ancestors
            if self.graph.nodes[node].get("node_type") == "source_column"
        ]
        if col_ancestors:
            col_ancestors.sort(key=lambda n: topo_index.get(n, 0))
            source_column = self.graph.nodes[col_ancestors[0]].get("column_name", column_name)

        return ColumnLineage(
            column_name=column_name,
            source_file=source_file,
            source_column=source_column,
            transformation_chain=transformation_chain,
            total_steps=len(transformation_chain),
        )

    def get_impact_analysis(
        self, step_name: str, column_name: str
    ) -> ImpactAnalysis:
        """Analyze the downstream impact of a column.

        Traverses the graph forward from a column node to find all
        steps and output columns that depend on it.

        Args:
            step_name: Name of the step containing the source column.
            column_name: Name of the column to analyze.

        Returns:
            ImpactAnalysis with affected steps and output columns.
        """
        source_node = _col_node_id(step_name, column_name)
        descendants = nx.descendants(self.graph, source_node)

        affected_steps: List[str] = []
        affected_output_columns: List[str] = []

        for node in descendants:
            attrs = self.graph.nodes[node]
            node_type = attrs.get("node_type", "")

            if node_type == "step":
                affected_steps.append(attrs.get("step_name", ""))
            elif node_type in ("output_column", "output_file"):
                label = attrs.get("label", "")
                if label:
                    affected_output_columns.append(label)

        return ImpactAnalysis(
            source_step=step_name,
            source_column=column_name,
            affected_steps=affected_steps,
            affected_output_columns=affected_output_columns,
        )

    # ── React Flow Export ─────────────────────────────────────────────────────

    def to_react_flow_format(self) -> ReactFlowGraph:
        """Convert the lineage graph to React Flow visualization format.

        Uses a Sugiyama-inspired layered layout based on topological sort
        position. Nodes are assigned layers by their position in the
        topological ordering, and distributed evenly on the Y axis
        within each layer.

        Returns:
            ReactFlowGraph with positioned nodes and styled edges.
        """
        nodes = self._build_react_flow_nodes()
        edges = self._build_react_flow_edges()
        return ReactFlowGraph(nodes=nodes, edges=edges)

    def _build_react_flow_nodes(self) -> List[ReactFlowNode]:
        """Build positioned React Flow nodes from the graph."""
        if not self.graph.nodes:
            return []

        topo_order = list(nx.topological_sort(self.graph))
        layers = self._assign_layers(topo_order)
        return self._position_nodes(layers)

    def _assign_layers(
        self, topo_order: List[str]
    ) -> Dict[int, List[str]]:
        """Assign nodes to layers based on longest path from source."""
        layer_map: Dict[str, int] = {}
        for node in topo_order:
            predecessors = list(self.graph.predecessors(node))
            if not predecessors:
                layer_map[node] = 0
            else:
                layer_map[node] = max(
                    layer_map.get(pred, 0) for pred in predecessors
                ) + 1

        layers: Dict[int, List[str]] = {}
        for node, layer in layer_map.items():
            layers.setdefault(layer, []).append(node)

        return layers

    def _position_nodes(
        self, layers: Dict[int, List[str]]
    ) -> List[ReactFlowNode]:
        """Position nodes in a layered layout."""
        nodes: List[ReactFlowNode] = []

        for layer_idx in sorted(layers.keys()):
            layer_nodes = layers[layer_idx]
            x_pos = layer_idx * LAYER_X_SPACING

            for y_idx, node_id in enumerate(layer_nodes):
                y_pos = y_idx * NODE_Y_SPACING
                attrs = self.graph.nodes[node_id]
                node_type = attrs.get("node_type", "step")

                nodes.append(ReactFlowNode(
                    id=node_id,
                    type=_NODE_TYPE_MAP.get(node_type, "stepNode"),
                    data={
                        "label": attrs.get("label", node_id),
                        "nodeType": node_type,
                        "stepName": attrs.get("step_name"),
                        "columnName": attrs.get("column_name"),
                        "dataType": attrs.get("data_type"),
                        "fileName": attrs.get("file_name"),
                    },
                    position={"x": x_pos, "y": y_pos},
                ))

        return nodes

    def _build_react_flow_edges(self) -> List[ReactFlowEdge]:
        """Build React Flow edges with join key styling."""
        edges: List[ReactFlowEdge] = []

        for idx, (source, target, data) in enumerate(self.graph.edges(data=True)):
            is_join_key = data.get("is_join_key", False)
            edge = ReactFlowEdge(
                id=f"edge-{idx}",
                source=source,
                target=target,
                animated=is_join_key,
                style={"stroke": "#ff6b6b"} if is_join_key else None,
            )
            edges.append(edge)

        return edges

    # ── Serialization ─────────────────────────────────────────────────────────

    def serialize(self) -> Dict[str, Any]:
        """Serialize the lineage graph for database storage.

        Returns:
            Dictionary containing both raw graph data (node-link format)
            and pre-computed React Flow layout for fast API responses.
        """
        react_flow = self.to_react_flow_format()
        return {
            "graph_data": nx.node_link_data(self.graph),
            "react_flow_data": {
                "nodes": [
                    {
                        "id": n.id,
                        "type": n.type,
                        "data": n.data,
                        "position": n.position,
                    }
                    for n in react_flow.nodes
                ],
                "edges": [
                    {
                        "id": e.id,
                        "source": e.source,
                        "target": e.target,
                        "animated": e.animated,
                        "style": e.style,
                    }
                    for e in react_flow.edges
                ],
            },
        }

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _add_node(self, node_id: str, attrs: Dict[str, Any]) -> None:
        """Add a node to the graph with the given attributes."""
        self.graph.add_node(node_id, **attrs)

    def _connect_join_side(
        self,
        input_step: str,
        columns: List[str],
        step_node: str,
        join_key: str,
    ) -> None:
        """Connect one side of a join to the step node."""
        for col in columns:
            input_col = _col_node_id(input_step, col)
            is_key = col == join_key
            self.graph.add_edge(
                input_col, step_node, is_join_key=is_key
            )
