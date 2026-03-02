"""Tests for the lineage recorder."""

# Third-party packages
import pytest

# Internal modules
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

    def test_record_load_creates_file_and_column_nodes(self, lineage_recorder):
        """Load step creates a file node and one column node per column."""
        lineage_recorder.record_load(
            file_id="f1",
            file_name="data.csv",
            step_name="load_data",
            columns=["col_a", "col_b", "col_c"],
            dtypes={"col_a": "int64", "col_b": "object", "col_c": "float64"},
        )

        graph = lineage_recorder.graph
        # File node + step node + 3 column nodes = 5
        assert graph.number_of_nodes() == 5
        assert graph.has_node("file::f1")
        assert graph.has_node("col::load_data::col_a")
        assert graph.has_node("col::load_data::col_b")
        assert graph.has_node("col::load_data::col_c")


class TestRecordPassthrough:
    """Tests for LineageRecorder.record_passthrough()."""

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

    def test_record_projection_drops_excluded_columns(self, loaded_recorder):
        """Projection step only creates output nodes for kept columns."""
        loaded_recorder.record_projection(
            step_name="select_cols",
            input_step="load_sales",
            kept_columns=["order_id", "amount"],
            dropped_columns=["customer_id", "status"],
        )

        graph = loaded_recorder.graph
        assert graph.has_node("col::select_cols::order_id")
        assert graph.has_node("col::select_cols::amount")
        assert not graph.has_node("col::select_cols::customer_id")
        assert not graph.has_node("col::select_cols::status")


class TestRecordJoin:
    """Tests for LineageRecorder.record_join()."""

    def test_record_join_creates_edges_from_both_inputs(self, loaded_recorder):
        """Join step has edges from both left and right input columns."""
        # Add a second load step
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

        # Both left and right columns should have edges to the step
        assert graph.has_edge("col::load_sales::customer_id", step_node)
        assert graph.has_edge("col::load_customers::customer_id", step_node)

        # Join key edges should be marked
        edge_data = graph.edges["col::load_sales::customer_id", step_node]
        assert edge_data.get("is_join_key") is True


class TestColumnAncestry:
    """Tests for LineageRecorder.get_column_ancestry()."""

    def test_column_ancestry_traces_back_to_source_file(self, loaded_recorder):
        """Column ancestry traces back to the original source file."""
        loaded_recorder.record_passthrough(
            step_name="filter_sales",
            step_type="filter",
            input_step="load_sales",
            columns=["order_id", "customer_id", "amount", "status"],
        )

        lineage = loaded_recorder.get_column_ancestry("filter_sales", "amount")

        assert lineage.column_name == "amount"
        assert lineage.source_file == "sales.csv"

    def test_column_ancestry_includes_all_transformations(self, loaded_recorder):
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

    def test_impact_analysis_identifies_downstream_steps(self, loaded_recorder):
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

    def test_react_flow_format_produces_valid_node_list(self, loaded_recorder):
        """React Flow export produces nodes with required fields."""
        flow = loaded_recorder.to_react_flow_format()

        assert len(flow.nodes) > 0
        for node in flow.nodes:
            assert node.id
            assert node.type in ("sourceFile", "stepNode", "columnNode", "outputFile")
            assert "label" in node.data

    def test_react_flow_nodes_have_valid_positions(self, loaded_recorder):
        """React Flow nodes have x/y positions that are non-negative."""
        flow = loaded_recorder.to_react_flow_format()

        for node in flow.nodes:
            assert "x" in node.position
            assert "y" in node.position
            assert node.position["x"] >= 0
            assert node.position["y"] >= 0


class TestSerialization:
    """Tests for LineageRecorder.serialize()."""

    def test_serialize_deserialize_roundtrip(self, loaded_recorder):
        """Serialized graph can be reconstructed via node_link_graph."""
        import networkx as nx

        loaded_recorder.record_passthrough(
            step_name="filter_sales",
            step_type="filter",
            input_step="load_sales",
            columns=["order_id", "customer_id", "amount", "status"],
        )

        serialized = loaded_recorder.serialize()
        reconstructed = nx.node_link_graph(serialized["graph_data"])

        assert reconstructed.number_of_nodes() == loaded_recorder.graph.number_of_nodes()
        assert reconstructed.number_of_edges() == loaded_recorder.graph.number_of_edges()
        assert "react_flow_data" in serialized
        assert "nodes" in serialized["react_flow_data"]
        assert "edges" in serialized["react_flow_data"]
