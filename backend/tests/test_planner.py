"""Tests for dry-run execution planner (Deliverable 8).

15 tests covering row estimation, duration, and API endpoints.
"""

import pytest
from backend.pipeline.planner import generate_execution_plan, _estimate_duration
from backend.tests.conftest import upload_file


SIMPLE_YAML_TEMPLATE = """pipeline:
  name: test_plan
  steps:
    - name: load_sales
      type: load
      file_id: "{file_id}"
    - name: filter_rows
      type: filter
      input: load_sales
      column: status
      operator: equals
      value: delivered
    - name: save_output
      type: save
      input: filter_rows
      filename: output.csv
"""


class TestPlannerRowEstimation:
    """Tests for row count estimation heuristics."""

    def test_plan_load_uses_actual_file_row_count(self, client, test_db, sales_csv_bytes):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = SIMPLE_YAML_TEMPLATE.format(file_id=file_id)
        plan = generate_execution_plan(yaml_config, test_db)
        load_step = plan.steps[0]
        assert load_step.estimated_rows_in == 20  # sample_sales_df has 20 rows
        assert load_step.estimated_rows_out == 20

    def test_plan_load_nonexistent_file_marks_will_fail(self, test_db):
        yaml_config = SIMPLE_YAML_TEMPLATE.format(file_id="nonexistent-id")
        plan = generate_execution_plan(yaml_config, test_db)
        assert plan.steps[0].will_fail is True

    def test_plan_load_no_file_id_marks_will_fail(self, test_db):
        yaml_config = """pipeline:
  name: test_plan
  steps:
    - name: load_sales
      type: load
"""
        plan = generate_execution_plan(yaml_config, test_db)
        assert plan.steps[0].will_fail is True

    def test_plan_filter_estimates_70_percent(self, client, test_db, sales_csv_bytes):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = SIMPLE_YAML_TEMPLATE.format(file_id=file_id)
        plan = generate_execution_plan(yaml_config, test_db)
        filter_step = plan.steps[1]
        assert filter_step.estimated_rows_out == int(20 * 0.7)

    def test_plan_join_inner_estimates_min_of_inputs(self, client, test_db, sales_csv_bytes, customers_csv_bytes):
        sales_id = upload_file(client, sales_csv_bytes, "sales.csv")
        customers_id = upload_file(client, customers_csv_bytes, "customers.csv")
        yaml_config = f"""pipeline:
  name: join_test
  steps:
    - name: load_sales
      type: load
      file_id: "{sales_id}"
    - name: load_customers
      type: load
      file_id: "{customers_id}"
    - name: join_data
      type: join
      left: load_sales
      right: load_customers
      on: customer_id
      how: inner
"""
        plan = generate_execution_plan(yaml_config, test_db)
        join_step = plan.steps[2]
        assert join_step.estimated_rows_out == min(20, 10)

    def test_plan_join_left_preserves_left_count(self, client, test_db, sales_csv_bytes, customers_csv_bytes):
        sales_id = upload_file(client, sales_csv_bytes, "sales.csv")
        customers_id = upload_file(client, customers_csv_bytes, "customers.csv")
        yaml_config = f"""pipeline:
  name: join_test
  steps:
    - name: load_sales
      type: load
      file_id: "{sales_id}"
    - name: load_customers
      type: load
      file_id: "{customers_id}"
    - name: join_data
      type: join
      left: load_sales
      right: load_customers
      on: customer_id
      how: left
"""
        plan = generate_execution_plan(yaml_config, test_db)
        join_step = plan.steps[2]
        assert join_step.estimated_rows_out == 20

    def test_plan_aggregate_estimates_fewer_rows(self, client, test_db, sales_csv_bytes):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = f"""pipeline:
  name: agg_test
  steps:
    - name: load_sales
      type: load
      file_id: "{file_id}"
    - name: agg_data
      type: aggregate
      input: load_sales
      group_by: [status]
      aggregations:
        - column: amount
          function: sum
"""
        plan = generate_execution_plan(yaml_config, test_db)
        agg_step = plan.steps[1]
        assert agg_step.estimated_rows_out < 20

    def test_plan_sort_preserves_row_count(self, client, test_db, sales_csv_bytes):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = f"""pipeline:
  name: sort_test
  steps:
    - name: load_sales
      type: load
      file_id: "{file_id}"
    - name: sort_data
      type: sort
      input: load_sales
      by: amount
      order: desc
"""
        plan = generate_execution_plan(yaml_config, test_db)
        sort_step = plan.steps[1]
        assert sort_step.estimated_rows_out == 20


class TestPlannerDuration:
    """Tests for duration estimation."""

    def test_plan_total_duration_sums_steps(self, client, test_db, sales_csv_bytes):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = SIMPLE_YAML_TEMPLATE.format(file_id=file_id)
        plan = generate_execution_plan(yaml_config, test_db)
        total = sum(s.estimated_duration_ms for s in plan.steps)
        assert plan.estimated_total_duration_ms == total


class TestPlannerMetadata:
    """Tests for plan metadata fields."""

    def test_plan_files_read_contains_file_ids(self, client, test_db, sales_csv_bytes):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = SIMPLE_YAML_TEMPLATE.format(file_id=file_id)
        plan = generate_execution_plan(yaml_config, test_db)
        assert file_id in plan.files_read

    def test_plan_files_written_contains_output_names(self, client, test_db, sales_csv_bytes):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = SIMPLE_YAML_TEMPLATE.format(file_id=file_id)
        plan = generate_execution_plan(yaml_config, test_db)
        assert "output.csv" in plan.files_written

    def test_plan_will_succeed_false_when_step_fails(self, test_db):
        yaml_config = SIMPLE_YAML_TEMPLATE.format(file_id="nonexistent")
        plan = generate_execution_plan(yaml_config, test_db)
        assert plan.will_succeed is False

    def test_plan_will_succeed_true_for_valid_pipeline(self, client, test_db, sales_csv_bytes):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = SIMPLE_YAML_TEMPLATE.format(file_id=file_id)
        plan = generate_execution_plan(yaml_config, test_db)
        assert plan.will_succeed is True


class TestPlannerEndpoint:
    """Tests for /pipelines/plan API endpoint."""

    def test_plan_endpoint_returns_200_for_valid_yaml(self, client, sales_csv_bytes):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = SIMPLE_YAML_TEMPLATE.format(file_id=file_id)
        response = client.post(
            "/api/v1/pipelines/plan",
            json={"yaml_config": yaml_config},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pipeline_name"] == "test_plan"
        assert data["total_steps"] == 3
        assert data["will_succeed"] is True

    def test_plan_endpoint_returns_400_for_invalid_yaml(self, client):
        response = client.post(
            "/api/v1/pipelines/plan",
            json={"yaml_config": "not: valid: yaml: ["},
        )
        # Invalid YAML should get a 422 or error
        assert response.status_code in [400, 422]


class TestPlannerSqlStep:
    """Tests for SQL step dry-run planning."""

    def test_plan_sql_step_estimates_rows_and_preserves_columns(
        self, client, test_db, sales_csv_bytes
    ):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = f"""pipeline:
  name: sql_plan
  steps:
    - name: load_sales
      type: load
      file_id: "{file_id}"
    - name: sql_transform
      type: sql
      input: load_sales
      query: "SELECT * FROM {{input}} LIMIT 5"
"""
        plan = generate_execution_plan(yaml_config, test_db)
        sql_step = plan.steps[1]
        assert sql_step.step_type == "sql"
        assert sql_step.estimated_rows_in == 20
        assert sql_step.estimated_rows_out == 5
