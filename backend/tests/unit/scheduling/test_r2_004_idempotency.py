"""R2-004: Idempotency-Key on schedule/webhook create + contract PUT."""
import uuid as uuid_module

YAML = "contracts:\n  - column: a\n    rule: not_null\n"


def test_schedule_create_replays_same_key(client, test_db, sales_csv_bytes):
    from backend.models import PipelineSchedule
    from backend.tests.conftest import upload_file

    file_id = upload_file(client, sales_csv_bytes, "sales.csv")
    name = f"idem_{uuid_module.uuid4().hex[:8]}"
    sched_yaml = (
        "pipeline:\n"
        f"  name: {name}\n"
        "  steps:\n"
        "    - name: load_orders\n"
        "      type: load\n"
        f"      file_id: {file_id}\n"
    )
    key = f"sched-{uuid_module.uuid4().hex}"
    body = {"pipeline_name": name,
            "yaml_config": sched_yaml, "cron_expression": "0 6 * * *"}
    first = client.post("/api/v1/schedules/", json=body,
                        headers={"Idempotency-Key": key})
    assert first.status_code == 201, first.text
    second = client.post("/api/v1/schedules/", json=body,
                         headers={"Idempotency-Key": key})
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    rows = test_db.query(PipelineSchedule).filter(
        PipelineSchedule.pipeline_name == body["pipeline_name"]).all()
    assert len(rows) == 1


def test_webhook_create_replays_same_key(client, test_db):
    from backend.models import Webhook
    key = f"hook-{uuid_module.uuid4().hex}"
    body = {"url": "https://example.com/hook",
            "events": ["pipeline_completed"]}
    first = client.post("/webhooks/", json=body,
                        headers={"Idempotency-Key": key})
    assert first.status_code == 201, first.text
    second = client.post("/webhooks/", json=body,
                         headers={"Idempotency-Key": key})
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    rows = test_db.query(Webhook).filter(
        Webhook.url == body["url"]).all()
    assert len(rows) == 1


def test_contract_put_replays_same_key(client, test_db):
    from backend.models import PipelineContract
    name = f"idemc_{uuid_module.uuid4().hex[:8]}"
    created = client.post(f"/api/v1/contracts/pipelines/{name}",
                          json={"yaml_content": YAML})
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    key = f"put-{uuid_module.uuid4().hex}"
    body = {"yaml_content": YAML}
    first = client.put(f"/api/v1/contracts/pipelines/{name}/{cid}",
                       json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 200, first.text
    versions_before = test_db.query(PipelineContract).filter(
        PipelineContract.pipeline_name == name).count()
    second = client.put(f"/api/v1/contracts/pipelines/{name}/{cid}",
                        json=body, headers={"Idempotency-Key": key})
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    versions_after = test_db.query(PipelineContract).filter(
        PipelineContract.pipeline_name == name).count()
    assert versions_after == versions_before
