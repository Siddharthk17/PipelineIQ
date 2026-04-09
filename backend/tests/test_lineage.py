"""Tests for the lineage recorder."""

import pytest

from backend.pipeline.lineage import LineageRecorder


@pytest.fixture()
def loaded_recorder() -> LineageRecorder:
    """LineageRecorder with a load step pre-recorded."""
    recorder = LineageRecorder()
    recorder.record_load(
        file_id="file-001",
        file_name="sales.csv",
        step_name="load_sales",
        columns=["order_id", "customer_id", "amount", "status"],
        dtypes={
            "order_id": "int64",
            "customer_id": "int64",
            "amount": "float64",
            "status": "object",
        },
    )
    return recorder


class TestRecordLoad:
    """Tests for LineageRecorder.record_load()."""

    def test_record_load_creates_file_node(self, lineage_recorder):
        """Load step creates a file node."""
        lineage_recorder.record_load(
            file_id="f1",
            file_name="data.csv",
            step_name="load_data",
            columns=["col_a", "col_b"],
            dtypes={"col_a": "int64", "col_b": "object"},
        )
        assert "file::f1" in lineage_recorder.graph.nodes

    def test_record_load_creates_column_nodes_for_each_column(self, lineage_recorder):
        """Load step creates column nodes for each column."""
        lineage_recorder.record_load(
            file_id="f1",
            file_name="data.csv",
            step_name="load_data",
            columns=["order_id", "amount", "status"],
            dtypes={},
        )
        assert "col::load_data::order_id" in lineage_recorder.graph.nodes
        assert "col::load_data::amount" in lineage_recorder.graph.nodes
        assert "col::load_data::status" in lineage_recorder.graph.nodes

    def test_record_load_creates_edges_from_file_to_step_to_columns(self, lineage_recorder):
        """Load step creates edges: file → step → column nodes."""
        lineage_recorder.record_load(
            file_id="f1",
            file_name="data.csv",
            step_name="load_data",
            columns=["amount"],
            dtypes={},
        )
        assert lineage_recorder.graph.has_edge("file::f1", "step::load_data")
        assert lineage_recorder.graph.has_edge("step::load_data", "col::load_data::amount")

    def test_record_load_creates_file_and_column_nodes(self, lineage_recorder):
        """Load step creates correct total node count."""
        lineage_recorder.record_load(
            file_id="f1",
            file_name="data.csv",
            step_name="load_data",
            columns=["col_a", "col_b", "col_c"],
            dtypes={"col_a": "int64", "col_b": "object", "col_c": "float64"},
        )
        graph = lineage_recorder.graph
        assert graph.number_of_nodes() == 5
        assert graph.has_node("file::f1")


class TestRecordPassthrough:
    """Tests for LineageRecorder.record_passthrough()."""

    def test_record_passthrough_creates_step_node(self, loaded_recorder):
        """Passthrough step creates a step node."""
        loaded_recorder.record_passthrough(
            step_name="filter_del",
            step_type="filter",
            input_step="load_sales",
            columns=["order_id", "amount"],
        )
        assert "step::filter_del" in loaded_recorder.graph.nodes

    def test_record_passthrough_connects_input_to_step_to_output(self, loaded_recorder):
        """Passthrough creates edges: input_col → step → output_col."""
        loaded_recorder.record_passthrough(
            step_name="filter_del",
            step_type="filter",
            input_step="load_sales",
            columns=["amount"],
        )
        assert loaded_recorder.graph.has_edge("col::load_sales::amount", "step::filter_del")
        assert loaded_recorder.graph.has_edge("step::filter_del", "col::filter_del::amount")

    def test_record_passthrough_preserves_all_columns(self, loaded_recorder):
        """Passthrough step creates output nodes for all input columns."""
        loaded_recorder.record_passthrough(
            step_name="filter_sales",
            step_type="filter",
            input_step="load_sales",
            columns=["order_id", "customer_id", "amount", "status"],
        )
        graph = loaded_recorder.graph
        assert graph.has_node("col::filter_sales::order_id")
        assert graph.has_node("col::filter_sales::amount")
        assert graph.has_node("col::filter_sales::status")


class TestRecordProjection:
    """Tests for LineageRecorder.record_projection()."""

    def test_record_projection_does_not_create_edges_for_dropped_columns(self, loaded_recorder):
        """Projection step only creates output nodes for kept columns."""
        loaded_recorder.record_projection(
            step_name="select_step",
            input_step="load_sales",
            kept_columns=["order_id", "amount"],
            dropped_columns=["customer_id", "status"],
        )
        graph = loaded_recorder.graph
        assert not graph.has_node("col::select_step::customer_id")
        assert not graph.has_node("col::select_step::status")
        assert graph.has_node("col::select_step::order_id")
        assert graph.has_node("col::select_step::amount")


class TestRecordJoin:
    """Tests for LineageRecorder.record_join()."""

    def test_record_join_creates_edges_from_both_inputs(self, loaded_recorder):
        """Join step has edges from both left and right input columns."""
        loaded_recorder.record_load(
            file_id="file-002",
            file_name="customers.csv",
            step_name="load_customers",
            columns=["customer_id", "name"],
            dtypes={"customer_id": "int64", "name": "object"},
        )
        loaded_recorder.record_join(
            step_name="join_data",
            left_step="load_sales",
            right_step="load_customers",
            left_cols=["order_id", "customer_id", "amount", "status"],
            right_cols=["customer_id", "name"],
            output_cols=["order_id", "customer_id", "amount", "status", "name"],
            join_key="customer_id",
            how="inner",
        )
        graph = loaded_recorder.graph
        step_node = "step::join_data"
        assert graph.has_node(step_node)
        assert graph.has_edge("col::load_sales::customer_id", step_node)
        assert graph.has_edge("col::load_customers::customer_id", step_node)


class TestRecordSql:
    """Tests for LineageRecorder.record_sql()."""

    def test_record_sql_connects_all_inputs_to_outputs(self, loaded_recorder):
        """SQL step should connect input columns through the SQL step node."""
        loaded_recorder.record_sql(
            step_name="sql_transform",
            input_step="load_sales",
            input_columns=["order_id", "customer_id", "amount", "status"],
            output_columns=["customer_id", "amount_x2"],
        )

        graph = loaded_recorder.graph
        step_node = "step::sql_transform"
        assert graph.has_node(step_node)
        assert graph.has_edge("col::load_sales::amount", step_node)
        assert graph.has_edge(step_node, "col::sql_transform::amount_x2")


class TestColumnAncestry:
    """Tests for LineageRecorder.get_column_ancestry()."""

    def test_get_column_ancestry_returns_source_file(self, loaded_recorder):
        """Column ancestry traces back to the original source file."""
        loaded_recorder.record_passthrough(
            step_name="filter_sales",
            step_type="filter",
            input_step="load_sales",
            columns=["order_id", "customer_id", "amount", "status"],
        )
        lineage = loaded_recorder.get_column_ancestry("filter_sales", "amount")
        assert lineage.source_file == "sales.csv"

    def test_get_column_ancestry_returns_complete_chain(self, loaded_recorder):
        """Column ancestry includes every transformation step."""
        loaded_recorder.record_passthrough(
            step_name="filter_sales",
            step_type="filter",
            input_step="load_sales",
            columns=["order_id", "customer_id", "amount", "status"],
        )
        loaded_recorder.record_passthrough(
            step_name="sort_sales",
            step_type="sort",
            input_step="filter_sales",
            columns=["order_id", "customer_id", "amount", "status"],
        )
        lineage = loaded_recorder.get_column_ancestry("sort_sales", "amount")
        step_names = [t.step_name for t in lineage.transformation_chain]
        assert "load_sales" in step_names
        assert "filter_sales" in step_names
        assert "sort_sales" in step_names


class TestImpactAnalysis:
    """Tests for LineageRecorder.get_impact_analysis()."""

    def test_get_impact_analysis_identifies_downstream_steps(self, loaded_recorder):
        """Impact analysis finds all steps that use the source column."""
        loaded_recorder.record_passthrough(
            step_name="filter_sales",
            step_type="filter",
            input_step="load_sales",
            columns=["order_id", "customer_id", "amount", "status"],
        )
        impact = loaded_recorder.get_impact_analysis("load_sales", "amount")
        assert "filter_sales" in impact.affected_steps
        assert len(impact.affected_output_columns) > 0


class TestReactFlowExport:
    """Tests for LineageRecorder.to_react_flow_format()."""

    def test_to_react_flow_format_returns_nodes_and_edges(self, loaded_recorder):
        """React Flow export produces nodes and edges."""
        flow = loaded_recorder.to_react_flow_format()
        assert len(flow.nodes) > 0
        assert "nodes" in dir(flow) or hasattr(flow, "nodes")

    def test_to_react_flow_format_all_nodes_have_positions(self, loaded_recorder):
        """React Flow nodes have x/y positions."""
        flow = loaded_recorder.to_react_flow_format()
        for node in flow.nodes:
            assert "x" in node.position
            assert "y" in node.position
            assert node.position["x"] >= 0
            assert node.position["y"] >= 0

    def test_to_react_flow_format_no_overlapping_positions(self, loaded_recorder):
        """No two nodes at exactly the same x,y position."""
        loaded_recorder.record_passthrough(
            step_name="filter_sales",
            step_type="filter",
            input_step="load_sales",
            columns=["order_id", "customer_id", "amount", "status"],
        )
        flow = loaded_recorder.to_react_flow_format()
        positions = [(n.position["x"], n.position["y"]) for n in flow.nodes]
        assert len(positions) == len(set(positions))


class TestSerialization:
    """Tests for LineageRecorder.serialize()."""

    def test_serialize_deserialize_roundtrip_preserves_all_nodes(self, loaded_recorder):
        """Serialized graph preserves all nodes."""
        import networkx as nx
        loaded_recorder.record_passthrough(
            step_name="filter_sales",
            step_type="filter",
            input_step="load_sales",
            columns=["order_id", "customer_id", "amount", "status"],
        )
        serialized = loaded_recorder.serialize()
        reconstructed = nx.node_link_graph(serialized["graph_data"])
        assert set(loaded_recorder.graph.nodes) == set(reconstructed.nodes)

    def test_serialize_deserialize_roundtrip_preserves_all_edges(self, loaded_recorder):
        """Serialized graph preserves all edges."""
        import networkx as nx
        loaded_recorder.record_passthrough(
            step_name="filter_sales",
            step_type="filter",
            input_step="load_sales",
            columns=["order_id", "customer_id", "amount", "status"],
        )
        serialized = loaded_recorder.serialize()
        reconstructed = nx.node_link_graph(serialized["graph_data"])
        assert set(loaded_recorder.graph.edges) == set(reconstructed.edges)

    def test_serialize_includes_react_flow_data(self, loaded_recorder):
        """Serialized data includes react_flow_data with nodes and edges."""
        serialized = loaded_recorder.serialize()
        assert "react_flow_data" in serialized
        assert "nodes" in serialized["react_flow_data"]
        assert "edges" in serialized["react_flow_data"]
