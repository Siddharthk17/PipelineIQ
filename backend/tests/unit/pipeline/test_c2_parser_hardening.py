"""Regression tests for c2-parser remediation findings.

M-099: DB String() overflow on long input (pipeline names, filenames).
M-111: pipeline load cannot read .parquet/.xlsx (upload-boundary half).
M-144: input-validation 500s (pagination, status_filter, webhook id).
M-145: template `on:`->True + version-diff boolean-key edge cases.
M-159: task-dispatch path stored-YAML round-trip re-validation.
M-162: contract by-id endpoints str-vs-UUID binding (never 500).

Each test was written to FAIL on the pre-fix code (or pins fixed
behavior that must never regress). Pure-helper tests need no DB;
client tests reuse the shared `auth_client` fixture.
"""

import uuid

import pytest
import yaml
from fastapi import HTTPException

import backend.api.files as files_mod
from backend.api.pipelines import _validate_claim_name
from backend.api.webhooks import _parse_webhook_id
from backend.pipeline.cache import get_parsed_pipeline
from backend.pipeline.diff_utils import strip_diff_markers
from backend.pipeline.exceptions import InvalidYAMLError
from backend.pipeline.parser import PipelineParser
from backend.pipeline.versioning import diff_pipelines
from backend.utils.uuid_utils import as_uuid, validate_uuid_format


def _register_headers(auth_client):
    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={
            "email": f"c2_{suffix}@t.local",
            "username": f"c2_{suffix}",
            "password": "Testpass123!",
        },
    )
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"c2_{suffix}@t.local", "password": "Testpass123!"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# --- M-099: filename length validated at the upload boundary ---------------
def test_m099_overlong_filename_rejected_with_field_name():
    long_name = "a" * 252 + ".csv"  # 256 chars > String(255)
    assert len(long_name) > files_mod._MAX_FILENAME_LENGTH
    with pytest.raises(HTTPException) as exc_info:
        files_mod._validate_file_extension(long_name)
    assert exc_info.value.status_code == 400
    assert "filename" in exc_info.value.detail


def test_m099_boundary_filename_accepted():
    ok_name = "a" * 251 + ".csv"  # exactly 255 chars
    assert len(ok_name) == files_mod._MAX_FILENAME_LENGTH
    files_mod._validate_file_extension(ok_name)  # must not raise


def test_m099_overlong_pipeline_name_rejected_before_claim():
    with pytest.raises(HTTPException) as exc_info:
        _validate_claim_name("n" * 256)
    assert exc_info.value.status_code == 422


def test_m099_empty_pipeline_name_rejected_before_claim():
    with pytest.raises(HTTPException) as exc_info:
        _validate_claim_name("   ")
    assert exc_info.value.status_code == 422


def test_m099_valid_claim_name_passes_through_stripped():
    assert _validate_claim_name("  demo pipe  ") == "demo pipe"


# --- M-111: upload boundary is honest about parquet/xlsx --------------------
def test_m111_parquet_extension_accepted():
    files_mod._validate_file_extension("data.parquet")  # must not raise


def test_m111_parquet_magic_enforced():
    files_mod._check_sample_content(".parquet", b"PAR1")  # must not raise
    with pytest.raises(ValueError):
        files_mod._check_sample_content(".parquet", b"XXXX")


def test_m111_xlsx_rejected_with_honest_415():
    with pytest.raises(HTTPException) as exc_info:
        files_mod._validate_file_extension("data.xlsx")
    assert exc_info.value.status_code == 415
    assert "not supported" in exc_info.value.detail.lower()


# --- M-144: pagination / status_filter / webhook id never 500 ---------------
def test_m144_negative_huge_pagination_and_bad_status_never_500(auth_client):
    headers = _register_headers(auth_client)
    resp = auth_client.get(
        "/api/v1/pipelines/?page=-5&limit=1000000&status_filter=bogus",
        headers=headers,
    )
    assert resp.status_code == 200


def test_m144_enormous_page_offset_never_500(auth_client):
    headers = _register_headers(auth_client)
    resp = auth_client.get(
        f"/api/v1/pipelines/?page={10**18}&limit=100",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


def test_m144_non_integer_pagination_is_422_not_500(auth_client):
    headers = _register_headers(auth_client)
    resp = auth_client.get("/api/v1/pipelines/?page=abc", headers=headers)
    assert resp.status_code == 422


def test_m144_invalid_webhook_id_never_500():
    with pytest.raises(HTTPException) as exc_info:
        _parse_webhook_id("not-a-uuid")
    assert exc_info.value.status_code in (400, 422)


# --- M-145: boolean-key-safe YAML handling ----------------------------------
def test_m145_diff_stripper_preserves_boolean_key_yaml_byte_for_byte():
    # YAML 1.1 parses bare `on:`/`yes:` as True; the stripper never parses
    # YAML so such documents must pass through untouched.
    text = "pipeline:\n  name: p\n  steps:\n    - name: s\n      type: load\non: trigger\n"
    assert strip_diff_markers(text) == text


def test_m145_diff_stripper_still_recovers_after_state_with_bool_keys():
    diff = (
        "--- a/pipe.yaml\n+++ b/pipe.yaml\n@@ -1,2 +1,2 @@\n"
        " pipeline:\n"
        "-  name: old\n"
        "+  name: new\n"
    )
    out = strip_diff_markers(diff)
    assert "name: new" in out and "name: old" not in out


def test_m145_version_diff_survives_boolean_keys():
    yaml_a = "pipeline:\n  name: p\n  steps:\n    - name: s\n      type: load\n      file_id: f1\n      on: col\n"
    yaml_b = yaml_a.replace("load", "filter")
    parsed_a = yaml.safe_load(yaml_a)
    assert True in parsed_a["pipeline"]["steps"][0]  # `on:` -> True key
    result = diff_pipelines(yaml_a, yaml_b, 1, 2)
    assert isinstance(result.change_summary, str)
    for step in result.steps_modified:
        assert all(isinstance(f, str) for f in step.changed_fields)


def test_m145_parser_accepts_true_keyed_join_on_field():
    text = (
        "pipeline:\n  name: p\n  steps:\n"
        "    - name: l\n      type: load\n      file_id: f1\n"
        "    - name: j\n      type: join\n      left: l\n      right: l\n"
        "      on: id\n"
    )
    config = PipelineParser().parse(text)
    join_step = next(s for s in config.steps if s.name == "j")
    assert getattr(join_step, "on", None) == "id" or "id" in str(
        getattr(join_step, "on", join_step)
    )


# --- M-159: dispatch path re-validates stored YAML --------------------------
def test_m159_stored_yaml_round_trip_through_dispatch_cache():
    text = (
        "pipeline:\n  name: roundtrip\n  steps:\n"
        "    - name: load\n      type: load\n      file_id: f1\n"
    )
    fresh = PipelineParser().parse(text)
    cached = get_parsed_pipeline(text)
    assert [s.name for s in cached.steps] == [s.name for s in fresh.steps]
    assert cached.name == fresh.name == "roundtrip"


def test_m159_dispatch_rejects_tampered_stored_yaml():
    from backend.models import PipelineRun, PipelineStatus
    from backend.tasks.pipeline_tasks import _run_pipeline

    run = PipelineRun(
        name="tampered",
        status=PipelineStatus.PENDING,
        yaml_config="pipeline: [unclosed\n  name: nope",
    )
    with pytest.raises(InvalidYAMLError):
        _run_pipeline(None, run)


# --- M-162: contract by-id str-vs-UUID binding never 500 --------------------
def test_m162_garbage_uuid_raises_4xx_not_500():
    with pytest.raises(HTTPException) as exc_info:
        validate_uuid_format("not-a-uuid")
    assert 400 <= exc_info.value.status_code < 500
    with pytest.raises(HTTPException) as exc_info:
        as_uuid("not-a-uuid")
    assert 400 <= exc_info.value.status_code < 500


def test_m162_contract_by_id_with_garbage_uuid_is_4xx(auth_client):
    headers = _register_headers(auth_client)
    resp = auth_client.get(
        "/api/v1/contracts/pipelines/nope/not-a-uuid", headers=headers
    )
    assert 400 <= resp.status_code < 500
