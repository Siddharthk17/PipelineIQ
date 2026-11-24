"""Tests for rate limiting (Deliverable 4).

6 tests verifying rate limiting on key endpoints.
"""

from backend.tests.conftest import build_simple_pipeline_yaml, upload_file


class TestRateLimiting:
    """Tests for rate limiting behavior."""

    def test_pipeline_run_rate_limit_blocks_11th_request(
            self, client, sales_csv_bytes):
        """First 10 run requests succeed, 11th returns 429."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = build_simple_pipeline_yaml(file_id)

        for i in range(10):
            response = client.post(
                "/api/v1/pipelines/run",
                json={"yaml_config": yaml_config, "name": "test_pipeline"},
            )
            assert response.status_code in [
                200, 202, 422], f"Request {i + 1} failed: {response.status_code}"

        response = client.post(
            "/api/v1/pipelines/run",
            json={"yaml_config": yaml_config, "name": "test_pipeline"},
        )
        assert response.status_code == 429

    def test_file_upload_rate_limit_exists(self, client, sales_csv_bytes):
        """Upload endpoint has rate limiting."""
        for i in range(30):
            response = client.post(
                "/api/v1/files/upload",
                files={"file": (f"test_{i}.csv", sales_csv_bytes, "text/csv")},
            )
            assert response.status_code in [201, 429]

        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("test_31.csv", sales_csv_bytes, "text/csv")},
        )
        assert response.status_code == 429

    def test_validation_rate_limit_exists(self, client, sales_csv_bytes):
        """Validate endpoint has rate limiting."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = build_simple_pipeline_yaml(file_id)

        for _i in range(60):
            response = client.post(
                "/api/v1/pipelines/validate",
                json={"yaml_config": yaml_config},
            )
            assert response.status_code in [200, 429]

        response = client.post(
            "/api/v1/pipelines/validate",
            json={"yaml_config": yaml_config},
        )
        assert response.status_code == 429

    def test_read_endpoint_rate_limit_exists(self, client):
        """Read endpoints have rate limiting."""
        for _i in range(120):
            response = client.get("/api/v1/pipelines/")
            assert response.status_code in [200, 429]

        response = client.get("/api/v1/pipelines/")
        assert response.status_code == 429

    def test_rate_limit_response_format(self, client, sales_csv_bytes):
        """Rate limit response returns 429 status."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = build_simple_pipeline_yaml(file_id)

        for _ in range(10):
            client.post(
                "/api/v1/pipelines/run",
                json={"yaml_config": yaml_config, "name": "test_pipeline"},
            )

        response = client.post(
            "/api/v1/pipelines/run",
            json={"yaml_config": yaml_config, "name": "test_pipeline"},
        )
        assert response.status_code == 429

    def test_plan_endpoint_rate_limit_exists(self, client, sales_csv_bytes):
        """Plan endpoint uses validation rate limit."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = build_simple_pipeline_yaml(file_id)

        for _i in range(60):
            response = client.post(
                "/api/v1/pipelines/plan",
                json={"yaml_config": yaml_config},
            )
            assert response.status_code in [200, 429]

        response = client.post(
            "/api/v1/pipelines/plan",
            json={"yaml_config": yaml_config},
        )
        assert response.status_code == 429


class TestClientIpKeying:
    """Rate limits key on the client IP via a TRUSTED proxy only (M-047).

    X-Real-IP is honored only when the direct peer is a trusted proxy
    (loopback/RFC1918 by default). TestClient's peer ("testclient") is
    not one, so header-based per-IP isolation is covered at unit level
    with trusted peers below.
    """

    def test_spoofed_client_ip_from_untrusted_peer_shares_one_bucket(
            self, client, sales_csv_bytes):
        from backend.tests.conftest import build_simple_pipeline_yaml, upload_file
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = build_simple_pipeline_yaml(file_id)

        ip_a = {"X-Real-IP": "10.9.9.1"}
        for _ in range(10):
            resp = client.post(
                "/api/v1/pipelines/run",
                json={"yaml_config": yaml_config, "name": "test_pipeline"},
                headers=ip_a,
            )
            assert resp.status_code in (200, 202, 422)
        resp = client.post(
            "/api/v1/pipelines/run",
            json={"yaml_config": yaml_config, "name": "test_pipeline"},
            headers=ip_a,
        )
        assert resp.status_code == 429

        # A different spoofed X-Real-IP from the same untrusted peer
        # does NOT escape the exhausted bucket (M-047).
        resp = client.post(
            "/api/v1/pipelines/run",
            json={"yaml_config": yaml_config, "name": "test_pipeline"},
            headers={"X-Real-IP": "10.9.9.2"},
        )
        assert resp.status_code == 429

    def test_trusted_proxy_peer_gets_independent_buckets_per_header(self):
        from fastapi import Request

        from backend.utils.rate_limiter import client_ip_key

        def key(peer, real_ip):
            scope = {
                "type": "http",
                "client": (peer, 1234),
                "headers": [(b"x-real-ip", real_ip.encode())],
            }
            return client_ip_key(Request(scope))

        # Loopback and docker-bridge peers (nginx) keep per-client buckets.
        assert key("127.0.0.1", "10.9.9.1") == "10.9.9.1"
        assert key("127.0.0.1", "10.9.9.2") == "10.9.9.2"
        assert key("172.18.0.2", "10.9.9.1") == "10.9.9.1"

    def test_client_ip_key_ignores_real_ip_header_from_untrusted_peer(self):
        from fastapi import Request

        from backend.utils.rate_limiter import client_ip_key

        scope = {
            "type": "http",
            "client": ("9.9.9.9", 1234),
            "headers": [(b"x-real-ip", b"10.1.2.3")],
        }
        assert client_ip_key(Request(scope)) == "9.9.9.9"

    def test_client_ip_key_prefers_real_ip_header_from_trusted_peer(self):
        from fastapi import Request

        from backend.utils.rate_limiter import client_ip_key

        scope = {
            "type": "http",
            "client": ("127.0.0.1", 1234),
            "headers": [(b"x-real-ip", b"10.1.2.3")],
        }
        assert client_ip_key(Request(scope)) == "10.1.2.3"

    def test_client_ip_key_falls_back_to_peer(self):
        from fastapi import Request

        from backend.utils.rate_limiter import client_ip_key

        scope = {"type": "http", "client": ("9.9.9.9", 1234), "headers": []}
        assert client_ip_key(Request(scope)) == "9.9.9.9"
