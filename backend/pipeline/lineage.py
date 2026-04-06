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

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

logger = logging.getLogger(__name__)


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


LAYER_X_SPACING: int = 300
NODE_Y_SPACING: int = 80

_NODE_TYPE_MAP: Dict[str, str] = {
    "source_file": "sourceFile",
    "source_column": "columnNode",
    "step": "stepNode",
    "output_column": "columnNode",
    "output_file": "outputFile",
}


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

    def record_load(
        self,
        file_id: str,
        file_name: str,
        step_name: str,
        columns: List[str],
        dtypes: Dict[str, str],
    ) -> None:
        """Record a file load step in the lineage graph."""
        file_node = _file_node_id(file_id)
        step_node = _step_node_id(step_name)

        self._add_node(
            file_node,
            {
                "node_type": "source_file",
                "label": file_name,
                "step_name": step_name,
                "file_name": file_name,
            },
        )
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Load: {file_name}",
                "step_name": step_name,
                "step_type": "load",
            },
        )
        self.graph.add_edge(file_node, step_node)

        for col in columns:
            col_node = _col_node_id(step_name, col)
            self._add_node(
                col_node,
                {
                    "node_type": "source_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                    "data_type": dtypes.get(col, "unknown"),
                },
            )
            self.graph.add_edge(step_node, col_node)

        logger.debug(
            "Recorded load: file=%s, step=%s, columns=%d",
            file_name,
            step_name,
            len(columns),
        )

    def record_passthrough(
        self,
        step_name: str,
        step_type: str,
        input_step: str,
        columns: List[str],
    ) -> None:
        """Record a step that passes all columns through unchanged (filter/sort)."""
        step_node = _step_node_id(step_name)
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"{step_type.title()}: {step_name}",
                "step_name": step_name,
                "step_type": step_type,
            },
        )

        for col in columns:
            input_col = _col_node_id(input_step, col)
            output_col = _col_node_id(step_name, col)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                },
            )
            self.graph.add_edge(input_col, step_node)
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded passthrough: step=%s, type=%s, columns=%d",
            step_name,
            step_type,
            len(columns),
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
        """
        step_node = _step_node_id(step_name)
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Select: {step_name}",
                "step_name": step_name,
                "step_type": "select",
            },
        )

        for col in kept_columns:
            input_col = _col_node_id(input_step, col)
            output_col = _col_node_id(step_name, col)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                },
            )
            self.graph.add_edge(input_col, step_node)
            self.graph.add_edge(step_node, output_col)

        # Record dropped columns as edges to step only (no output)
        for col in dropped_columns:
            input_col = _col_node_id(input_step, col)
            self.graph.add_edge(input_col, step_node)

        logger.debug(
            "Recorded projection: step=%s, kept=%d, dropped=%d",
            step_name,
            len(kept_columns),
            len(dropped_columns),
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
        """
        step_node = _step_node_id(step_name)
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Rename: {step_name}",
                "step_name": step_name,
                "step_type": "rename",
            },
        )

        for col in all_columns:
            input_col = _col_node_id(input_step, col)
            new_name = rename_mapping.get(col, col)
            output_col = _col_node_id(step_name, new_name)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": new_name,
                    "step_name": step_name,
                    "column_name": new_name,
                },
            )
            self.graph.add_edge(input_col, step_node)
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded rename: step=%s, renamed=%d, total=%d",
            step_name,
            len(rename_mapping),
            len(all_columns),
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
        """
        step_node = _step_node_id(step_name)
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Join ({how}): {step_name}",
                "step_name": step_name,
                "step_type": "join",
            },
        )

        self._connect_join_side(left_step, left_cols, step_node, join_key)
        self._connect_join_side(right_step, right_cols, step_node, join_key)

        for col in output_cols:
            output_col = _col_node_id(step_name, col)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                },
            )
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded join: step=%s, how=%s, key=%s, output_cols=%d",
            step_name,
            how,
            join_key,
            len(output_cols),
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

        Group-by columns pass through with explicit input→output edges.
        Aggregated columns create new output column nodes with explicit
        edges from the source input column to preserve lineage.
        """
        step_node = _step_node_id(step_name)
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Aggregate: {step_name}",
                "step_name": step_name,
                "step_type": "aggregate",
            },
        )

        # Build a mapping of output column name → input column name
        # for lineage tracing
        output_to_input: Dict[str, str] = {}

        # Group-by columns pass through
        for col in group_by_cols:
            input_col = _col_node_id(input_step, col)
            output_col = _col_node_id(step_name, col)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                },
            )
            self.graph.add_edge(input_col, step_node)
            self.graph.add_edge(step_node, output_col)
            output_to_input[col] = col

        # Aggregated columns: create output nodes and track input mapping
        for agg in aggregations:
            input_col_name = agg["column"]
            func_name = agg["function"]
            input_col = _col_node_id(input_step, input_col_name)
            self.graph.add_edge(input_col, step_node)
            # Output column name is typically {column}_{function}
            output_col_name = f"{input_col_name}_{func_name}"
            output_to_input[output_col_name] = input_col_name

        # Create output column nodes with edges from step
        for col in output_cols:
            output_col = _col_node_id(step_name, col)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                    "source_column": output_to_input.get(col, col),
                },
            )
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded aggregate: step=%s, group_by=%d, aggs=%d, output=%d",
            step_name,
            len(group_by_cols),
            len(aggregations),
            len(output_cols),
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
        """
        step_node = _step_node_id(step_name)
        output_file = _output_node_id(step_name, filename)

        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Save: {filename}",
                "step_name": step_name,
                "step_type": "save",
            },
        )
        self._add_node(
            output_file,
            {
                "node_type": "output_file",
                "label": filename,
                "step_name": step_name,
                "file_name": filename,
            },
        )

        for col in columns:
            input_col = _col_node_id(input_step, col)
            self.graph.add_edge(input_col, step_node)

        self.graph.add_edge(step_node, output_file)

        logger.debug(
            "Recorded save: step=%s, filename=%s, columns=%d",
            step_name,
            filename,
            len(columns),
        )

    def get_column_ancestry(
        self, output_step_name: str, column_name: str
    ) -> ColumnLineage:
        """Trace a column backward to its source file.

        Uses BFS backward from the target column node, following
        predecessor edges through step nodes to find the original
        source file and column.
        """
        target_node = _col_node_id(output_step_name, column_name)
        if target_node not in self.graph:
            return ColumnLineage(
                column_name=column_name,
                source_file="unknown",
                source_column=column_name,
                transformation_chain=[],
                total_steps=0,
            )

        source_file = "unknown"
        source_column = column_name
        transformation_chain: List[TransformationStep] = []
        visited: Set[str] = set()
        visited.add(target_node)

        # BFS: queue contains nodes to explore
        queue: List[str] = [target_node]
        step_nodes_seen: List[str] = []
        source_col_nodes: List[str] = []

        while queue:
            node = queue.pop(0)
            attrs = self.graph.nodes[node]
            node_type = attrs.get("node_type", "")

            if node_type == "source_file":
                if source_file == "unknown":
                    source_file = attrs.get("label", "unknown")
                # Don't continue - there may be no predecessors anyway

            if node_type == "source_column":
                source_col_nodes.append(node)
                # Don't continue - follow predecessors to find the step node

            if node_type == "step":
                if node not in step_nodes_seen:
                    step_nodes_seen.append(node)

            # Follow all predecessor edges
            for pred in self.graph.predecessors(node):
                if pred not in visited:
                    visited.add(pred)
                    queue.append(pred)

        # Build transformation chain in topological order
        try:
            topo_order = list(nx.topological_sort(self.graph))
        except nx.NetworkXUnfeasible:
            logger.warning(
                "Lineage graph contains cycles — ancestry trace may be incomplete"
            )
            topo_order = list(self.graph.nodes())
        topo_index = {node: idx for idx, node in enumerate(topo_order)}
        step_nodes_seen.sort(key=lambda n: topo_index.get(n, 0))

        for step_node in step_nodes_seen:
            attrs = self.graph.nodes[step_node]
            transformation_chain.append(
                TransformationStep(
                    step_name=attrs.get("step_name", ""),
                    step_type=attrs.get("step_type", ""),
                    detail=attrs.get("label", ""),
                )
            )

        # Determine source column: prefer the one with matching column_name,
        # or use the source_column attribute if set (for aggregate steps)
        if source_col_nodes:
            # First try to find a source column with matching name
            matching = [
                n
                for n in source_col_nodes
                if self.graph.nodes[n].get("column_name") == column_name
            ]
            if matching:
                source_column = self.graph.nodes[matching[0]].get(
                    "column_name", column_name
                )
            else:
                # For aggregate steps, check if any output column has source_column attr
                # that matches one of our source columns
                target_attrs = self.graph.nodes.get(target_node, {})
                src_col = target_attrs.get("source_column")
                if src_col:
                    matching_src = [
                        n
                        for n in source_col_nodes
                        if self.graph.nodes[n].get("column_name") == src_col
                    ]
                    if matching_src:
                        source_column = src_col
                    else:
                        source_column = self.graph.nodes[source_col_nodes[0]].get(
                            "column_name", column_name
                        )
                else:
                    source_column = self.graph.nodes[source_col_nodes[0]].get(
                        "column_name", column_name
                    )

        return ColumnLineage(
            column_name=column_name,
            source_file=source_file,
            source_column=source_column,
            transformation_chain=transformation_chain,
            total_steps=len(transformation_chain),
        )

    def get_impact_analysis(self, step_name: str, column_name: str) -> ImpactAnalysis:
        """Analyze the downstream impact of a column.

        Traverses the graph forward from a column node to find all
        steps and output columns that depend on it.
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

    def to_react_flow_format(self) -> ReactFlowGraph:
        """Convert the lineage graph to React Flow visualization format.

        Uses a Sugiyama-inspired layered layout based on topological sort
        position. Nodes are assigned layers by their position in the
        topological ordering, and distributed evenly on the Y axis
        within each layer.
        """
        nodes = self._build_react_flow_nodes()
        edges = self._build_react_flow_edges()
        return ReactFlowGraph(nodes=nodes, edges=edges)

    def _build_react_flow_nodes(self) -> List[ReactFlowNode]:
        """Build positioned React Flow nodes from the graph."""
        if not self.graph.nodes:
            return []

        try:
            topo_order = list(nx.topological_sort(self.graph))
        except nx.NetworkXUnfeasible:
            logger.warning("Lineage graph contains cycles — layout may be suboptimal")
            topo_order = list(self.graph.nodes())
        layers = self._assign_layers(topo_order)
        return self._position_nodes(layers)

    def _assign_layers(self, topo_order: List[str]) -> Dict[int, List[str]]:
        """Assign nodes to layers based on longest path from source."""
        layer_map: Dict[str, int] = {}
        for node in topo_order:
            predecessors = list(self.graph.predecessors(node))
            if not predecessors:
                layer_map[node] = 0
            else:
                layer_map[node] = (
                    max(layer_map.get(pred, 0) for pred in predecessors) + 1
                )

        layers: Dict[int, List[str]] = {}
        for node, layer in layer_map.items():
            layers.setdefault(layer, []).append(node)

        return layers

    def _position_nodes(self, layers: Dict[int, List[str]]) -> List[ReactFlowNode]:
        """Position nodes in a layered layout."""
        nodes: List[ReactFlowNode] = []

        for layer_idx in sorted(layers.keys()):
            layer_nodes = layers[layer_idx]
            x_pos = layer_idx * LAYER_X_SPACING

            for y_idx, node_id in enumerate(layer_nodes):
                y_pos = y_idx * NODE_Y_SPACING
                attrs = self.graph.nodes[node_id]
                node_type = attrs.get("node_type", "step")

                nodes.append(
                    ReactFlowNode(
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
                    )
                )

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

    def record_pivot(
        self,
        step_name: str,
        input_step: str,
        index_col: str,
        columns_col: str,
        values_col: str,
        output_columns: List[str],
    ) -> None:
        """Record a pivot step that reshapes data from long to wide format."""
        step_node = _step_node_id(step_name)
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Pivot: {step_name}",
                "step_name": step_name,
                "step_type": "pivot",
            },
        )

        for col in [index_col, columns_col, values_col]:
            input_col = _col_node_id(input_step, col)
            self.graph.add_edge(input_col, step_node)

        for col in output_columns:
            output_col = _col_node_id(step_name, col)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                },
            )
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded pivot: step=%s, index=%s, columns_to=%s, values=%s, output_cols=%d",
            step_name,
            index_col,
            columns_col,
            values_col,
            len(output_columns),
        )

    def record_unpivot(
        self,
        step_name: str,
        input_step: str,
        id_columns: List[str],
        value_columns: List[str],
        output_columns: List[str],
    ) -> None:
        """Record an unpivot (melt) step that reshapes data from wide to long format."""
        step_node = _step_node_id(step_name)
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Unpivot: {step_name}",
                "step_name": step_name,
                "step_type": "unpivot",
            },
        )

        for col in id_columns + value_columns:
            input_col = _col_node_id(input_step, col)
            self.graph.add_edge(input_col, step_node)

        for col in output_columns:
            output_col = _col_node_id(step_name, col)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                },
            )
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded unpivot: step=%s, id_cols=%d, value_cols=%d, output_cols=%d",
            step_name,
            len(id_columns),
            len(value_columns),
            len(output_columns),
        )

    def record_deduplicate(
        self,
        step_name: str,
        input_step: str,
        columns: List[str],
        subset: Optional[List[str]],
    ) -> None:
        """Record a deduplicate step that removes duplicate rows."""
        step_node = _step_node_id(step_name)
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Deduplicate: {step_name}",
                "step_name": step_name,
                "step_type": "deduplicate",
            },
        )

        for col in columns:
            input_col = _col_node_id(input_step, col)
            output_col = _col_node_id(step_name, col)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                },
            )
            self.graph.add_edge(input_col, step_node)
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded deduplicate: step=%s, columns=%d, subset=%s",
            step_name,
            len(columns),
            subset,
        )

    def record_fill_nulls(
        self,
        step_name: str,
        input_step: str,
        columns: List[str],
        method: str,
    ) -> None:
        """Record a fill_nulls step that fills missing values."""
        step_node = _step_node_id(step_name)
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Fill Nulls: {step_name}",
                "step_name": step_name,
                "step_type": "fill_nulls",
                "method": method,
            },
        )

        for col in columns:
            input_col = _col_node_id(input_step, col)
            output_col = _col_node_id(step_name, col)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                },
            )
            self.graph.add_edge(input_col, step_node)
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded fill_nulls: step=%s, columns=%d, method=%s",
            step_name,
            len(columns),
            method,
        )

    def record_sample(
        self,
        step_name: str,
        input_step: str,
        columns: List[str],
    ) -> None:
        """Record a sample step that randomly samples rows."""
        step_node = _step_node_id(step_name)
        self._add_node(
            step_node,
            {
                "node_type": "step",
                "label": f"Sample: {step_name}",
                "step_name": step_name,
                "step_type": "sample",
            },
        )

        for col in columns:
            input_col = _col_node_id(input_step, col)
            output_col = _col_node_id(step_name, col)
            self._add_node(
                output_col,
                {
                    "node_type": "output_column",
                    "label": col,
                    "step_name": step_name,
                    "column_name": col,
                },
            )
            self.graph.add_edge(input_col, step_node)
            self.graph.add_edge(step_node, output_col)

        logger.debug(
            "Recorded sample: step=%s, columns=%d",
            step_name,
            len(columns),
        )

    def serialize(self) -> Dict[str, Any]:
        """Serialize the lineage graph for database storage.

        Returns both raw graph data (node-link format) and pre-computed
        React Flow layout for fast API responses.
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
            self.graph.add_edge(input_col, step_node, is_join_key=is_key)
