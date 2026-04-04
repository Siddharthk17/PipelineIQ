"""Integration checks for infrastructure shape and runtime wiring.

These tests are gated behind RUN_INTEGRATION_TESTS=1. They validate container
service wiring and key runtime assumptions using existing project artifacts.
"""

from pathlib import Path
import os

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Set RUN_INTEGRATION_TESTS=1 to run infrastructure integration checks",
)


def _compose_text() -> str:
    return Path(__file__).resolve().parents[3].joinpath("docker-compose.yml").read_text()


def _nginx_text() -> str:
    return Path(__file__).resolve().parents[3].joinpath("nginx/conf.d/pipelineiq.conf").read_text()


def test_compose_includes_dedicated_sse_service():
    compose = _compose_text()
    assert "sse-service:" in compose
    assert "backend.sse_app:sse_app" in compose


def test_compose_includes_worker_queue_specialization():
    compose = _compose_text()
    assert "worker-critical:" in compose
    assert "--queues=critical" in compose
    assert "worker-default:" in compose
    assert "--queues=critical,default" in compose
    assert "worker-bulk:" in compose
    assert "--queues=bulk" in compose


def test_compose_includes_four_redis_roles():
    compose = _compose_text()
    for service_name in ("redis-broker:", "redis-pubsub:", "redis-cache:", "redis-yjs:"):
        assert service_name in compose


def test_nginx_routes_run_stream_endpoint_to_sse_service():
    nginx = _nginx_text()
    assert "location ~ ^/api/v1/pipelines/[^/]+/stream$" in nginx
    assert "proxy_pass $sse_backend;" in nginx
    assert "proxy_buffering off;" in nginx


def test_main_app_does_not_mount_sse_router_directly():
    from backend.main import app

    paths = {route.path for route in app.routes}
    assert "/api/v1/pipelines/{run_id}/stream" not in paths


def test_sse_app_mounts_stream_router():
    from backend.sse_app import sse_app

    paths = {route.path for route in sse_app.routes}
    assert "/api/v1/pipelines/{run_id}/stream" in paths
