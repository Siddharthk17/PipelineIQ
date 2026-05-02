"""Integration checks for Week 1 infrastructure.

These tests require the local Docker stack to be running and are intentionally
runtime-oriented instead of static text inspection.
"""

from pathlib import Path
import os
import socket

import psycopg2
import pytest
import redis
import requests
import yaml


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Set RUN_INTEGRATION_TESTS=1 to run infrastructure integration checks",
)


def _compose() -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    return yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))


def _connect_postgres(*, port: int):
    return psycopg2.connect(
        host="127.0.0.1",
        port=port,
        database=os.getenv("POSTGRES_DB", "pipelineiq"),
        user=os.getenv("POSTGRES_USER", "pipelineiq"),
        password=os.getenv("POSTGRES_PASSWORD", "your_password"),
    )


def _assert_tcp_open(port: int) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        return


def test_compose_declares_week1_worker_topology():
    services = _compose()["services"]

    assert "worker-critical" in services
    assert "worker-default" in services
    assert "worker-bulk" in services

    critical_command = services["worker-critical"]["command"]
    default_command = services["worker-default"]["command"]
    bulk_command = services["worker-bulk"]["command"]

    assert "--queues=critical" in critical_command
    assert "--queues=critical,default" in default_command
    assert "--queues=bulk" in bulk_command


def test_compose_declares_four_redis_roles():
    services = _compose()["services"]
    for service_name in ("redis-broker", "redis-pubsub", "redis-cache", "redis-yjs"):
        assert service_name in services


def test_redis_instances_are_reachable_and_distinct():
    for port in (6379, 6380, 6381, 6382):
        _assert_tcp_open(port)

    broker = redis.from_url("redis://127.0.0.1:6379/0")
    pubsub = redis.from_url("redis://127.0.0.1:6380/0")
    cache = redis.from_url("redis://127.0.0.1:6381/0")
    yjs = redis.from_url("redis://127.0.0.1:6382/0")

    assert broker.ping()
    assert pubsub.ping()
    assert cache.ping()
    assert yjs.ping()

    ports = {
        broker.info("server")["tcp_port"],
        pubsub.info("server")["tcp_port"],
        cache.info("server")["tcp_port"],
        yjs.info("server")["tcp_port"],
    }
    assert ports == {6379, 6380, 6381, 6382}


def test_pgbouncer_primary_accepts_connections():
    _assert_tcp_open(5432)
    with _connect_postgres(port=5432) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1


def test_pgbouncer_primary_uses_transaction_pooling():
    # Connect to the pgbouncer virtual database to run SHOW POOLS
    conn = psycopg2.connect(
        host="127.0.0.1",
        port=5432,
        database="pgbouncer",
        user=os.getenv("POSTGRES_USER", "pipelineiq"),
        password=os.getenv("POSTGRES_PASSWORD", "your_password"),
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            cursor.execute("SHOW POOLS")
            pools = cursor.fetchall()
    finally:
        conn.close()
            
    # Find the pipelineiq pool and check its mode (index 15)
    pipelineiq_pool = next((p for p in pools if p[0] == "pipelineiq"), None)
    assert pipelineiq_pool is not None, "pipelineiq pool not found in pgbouncer"
    assert pipelineiq_pool[15] == "transaction"


def test_pgbouncer_replica_is_in_recovery():
    _assert_tcp_open(5433)
    with _connect_postgres(port=5433) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_is_in_recovery()")
            assert cursor.fetchone()[0] is True


def test_performance_indexes_exist():
    required_indexes = {
        "idx_pipeline_runs_user_created",
        "idx_step_results_run_id",
        "idx_schedule_runs_schedule_id",
        "idx_pipeline_runs_pipeline_name",
        "idx_pipeline_schedules_active",
        "idx_lineage_graphs_run_id",
    }
    with _connect_postgres(port=5432) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
            )
            existing_indexes = {row[0] for row in cursor.fetchall()}

    assert required_indexes.issubset(existing_indexes)


def test_api_and_sse_health_endpoints_are_up():
    api_response = requests.get("http://127.0.0.1:8000/health", timeout=5)
    sse_response = requests.get("http://127.0.0.1:8001/health", timeout=5)

    assert api_response.status_code == 200
    assert sse_response.status_code == 200
