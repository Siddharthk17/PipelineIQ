"""Route-table regression for M-158: /metrics follows the docs gate.

``/metrics`` is registered only when ``ENVIRONMENT != production`` — the
same rule as ``/docs``/``/redoc``/``/openapi.json``. Local Prometheus
scrapes it in dev/compose; production serves no unauthenticated
cardinality data.

The gate is evaluated at import time from the module-level settings
singleton (and the suite pins ``ENVIRONMENT=test`` in conftest), so each
case runs in a subprocess with a clean interpreter.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

_ROUTES_PROBE = (
    "from backend.main import app; "
    "print('ROUTES:' + repr(sorted({getattr(r, 'path', '') "
    "for r in app.routes})))"
)

# Offline-safe: routes resolve without connecting (engines are lazy).
_BASE_ENV = {
    "STORAGE_TYPE": "local",
    "DATABASE_URL": (
        "postgresql://postgres:pipelineiq_dev_password@localhost:5432/pipelineiq"
    ),
    "REDIS_BROKER_URL": "redis://localhost:6379/0",
    "REDIS_BACKEND_URL": "redis://localhost:6379/1",
    "REDIS_PUBSUB_URL": "redis://localhost:6380/0",
    "REDIS_CACHE_URL": "redis://localhost:6381/0",
    "REDIS_YJS_URL": "redis://localhost:6382/0",
}

# Production boots fail closed without strong operational secrets.
_PROD_SECRETS = {
    "SECRET_KEY": "0123456789abcdef0123456789abcdef",
    "FLOWER_PASSWORD": "0123456789abcdef",
    "GRAFANA_PASSWORD": "0123456789abcdef",
}

# backend/tests/unit/test_metrics_exposure.py -> repo root (holds backend/).
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _routes_in(environment: str) -> list[str]:
    env = {**os.environ, **_BASE_ENV, **_PROD_SECRETS, "ENVIRONMENT": environment}
    proc = subprocess.run(
        [sys.executable, "-c", _ROUTES_PROBE],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=env,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"route probe failed under ENVIRONMENT={environment}:\n"
        f"{proc.stderr[-2000:]}"
    )
    marker = next(
        line for line in proc.stdout.splitlines() if line.startswith("ROUTES:")
    )
    return ast.literal_eval(marker.removeprefix("ROUTES:"))


class TestMetricsExposure:
    def test_metrics_exposed_outside_production(self):
        assert "/metrics" in _routes_in("test")

    def test_metrics_hidden_in_production_like_docs(self):
        routes = _routes_in("production")
        assert "/metrics" not in routes
        # Gate parity: the docs surface main.py gates the same way.
        assert "/docs" not in routes
        assert "/redoc" not in routes
