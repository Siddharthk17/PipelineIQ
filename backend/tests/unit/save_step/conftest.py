"""Shared fixtures for save-step unit tests.

`refresh_download_url`/`execute_save_step` resolve the signing client via
`_get_presign_client`, which builds a real boto3 client whenever
S3_PUBLIC_ENDPOINT_URL differs from S3_ENDPOINT_URL. Unit tests that mock
`_get_minio_client` expect the mock to be used for signing too, so bypass
the presign-client fork here and hand the (mocked) server client straight
through.
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _passthrough_presign_client():
    with patch(
        "backend.execution.steps.save_step._get_presign_client",
        side_effect=lambda server_client: server_client,
    ):
        yield
