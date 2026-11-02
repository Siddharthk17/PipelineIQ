"""Seed a lived-in showcase: 50 pipelines, mixed outcomes, files, schedules.

Populates the demo account so every frontend surface (runs, history,
files, schedules, versions, lineage entry points) shows realistic data
instead of an empty product.

Usage (inside the api container):
    python -m backend.scripts.seed_showcase [--reset]

--reset wipes ONLY the demo account's runs/schedules/files (plus showcase
pipeline versions) and re-seeds from scratch. Never touches other users.

Idempotent without --reset: existing names are skipped.
"""

import csv
import io
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

import yaml

from backend.auth import get_password_hash
from backend.database import SessionLocal
from backend.models import (
    HealingAttempt,
    HealingAttemptStatus,
    PipelineRun,
    PipelineSchedule,
    PipelineStatus,
    PipelineVersion,
    ScheduleRun,
    StepResult,
    StepStatus,
    UploadedFile,
    User,
)
from backend.scripts.seed_demo import (
    DEMO_EMAIL,
    DEMO_PASSWORD,
    DEMO_USERNAME,
    seed_demo,
)
from backend.services.storage_service import storage_service

RNG = random.Random(20250601)


# Datasets

STATUSES = ["delivered", "delivered", "delivered", "cancelled", "pending",
            "delivered", "pending", "cancelled"]
REGIONS = ["North", "South", "East", "West"]
TIERS = ["gold", "silver", "bronze"]
EVENT_TYPES = ["view", "click", "purchase", "signup", "refund"]


def _dataset_orders(n=600):
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "order_id": 1000 + i,
            "customer_id": f"C{1 + (i % 40):03d}",
            "amount": round(RNG.uniform(20.0, 900.0), 2),
            "status": RNG.choice(STATUSES),
            "region": RNG.choice(REGIONS),
            "date": f"2024-01-{(i % 28) + 1:02d}",
        })
    return (["order_id", "customer_id", "amount", "status", "region", "date"],
            rows)


def _dataset_customers(n=200):
    first = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace",
             "Heidi", "Ivan", "Judy"]
    last = ["Smith", "Jones", "Brown", "Davis", "Wilson", "Miller", "Lee"]
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "customer_id": f"C{i:03d}",
            "customer_name": f"{RNG.choice(first)} {RNG.choice(last)}",
            "email": f"user{i}@example.com",
            "region": RNG.choice(REGIONS),
            "tier": RNG.choice(TIERS),
        })
    return (["customer_id", "customer_name", "email", "region", "tier"], rows)


def _dataset_products(n=60):
    cats = ["widgets", "gadgets", "tools"]
    rows = []
    for i in range(101, 101 + n):
        rows.append({
            "product_id": i,
            "product_name": f"{RNG.choice(['Widget', 'Gadget', 'Tool'])} {i}",
            "category": RNG.choice(cats),
            "price": round(RNG.uniform(9.99, 299.99), 2),
        })
    return (["product_id", "product_name", "category", "price"], rows)


def _dataset_web_events(n=1200):
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "event_id": f"e{i:05d}",
            "user_id": f"u{RNG.randint(1, 300):04d}",
            "event_type": RNG.choice(EVENT_TYPES),
            "page": RNG.choice(["/home", "/pricing", "/checkout", "/docs"]),
            "ts": f"2024-02-{(i % 28) + 1:02d}T{RNG.randint(0, 23):02d}:00:00Z",
        })
    return (["event_id", "user_id", "event_type", "page", "ts"], rows)


def _dataset_inventory(n=150):
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "sku": f"SKU-{1000 + i}",
            "warehouse": RNG.choice(["WH-1", "WH-2", "WH-3"]),
            "qty": RNG.randint(0, 500),
            "reorder_level": RNG.choice([20, 50, 100]),
        })
    return (["sku", "warehouse", "qty", "reorder_level"], rows)


DATASETS = [
    ("orders_2024.csv", _dataset_orders),
    ("customers.csv", _dataset_customers),
    ("products.csv", _dataset_products),
    ("web_events.csv", _dataset_web_events),
    ("inventory.csv", _dataset_inventory),
]


def _to_csv_bytes(columns, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def _dtypes_for(columns, rows):
    sample = rows[0] if rows else {}
    dtypes = {}
    for c in columns:
        v = sample.get(c)
        if isinstance(v, bool):
            dtypes[c] = "bool"
        elif isinstance(v, int):
            dtypes[c] = "int64"
        elif isinstance(v, float):
            dtypes[c] = "float64"
        else:
            dtypes[c] = "object"
    return dtypes


# Pipeline archetypes (all produce parser-valid YAML — verified in CI review)

def _yaml(name, description, steps):
    return yaml.safe_dump(
        {"pipeline": {"name": name, "description": description,
                       "steps": steps}},
        sort_keys=False,
    )


def _steps_sales_delivered(fid, status="delivered"):
    return [
        {"name": "load_sales", "type": "load", "file_id": fid},
        {"name": "filter_delivered", "type": "filter", "input": "load_sales",
         "column": "status", "operator": "equals", "value": status},
        {"name": "save_output", "type": "save", "input": "filter_delivered",
         "filename": "delivered_orders"},
    ]


def _steps_regional_summary(fid):
    return [
        {"name": "load_sales", "type": "load", "file_id": fid},
        {"name": "keep_cols", "type": "select", "input": "load_sales",
         "columns": ["region", "amount", "status"]},
        {"name": "sort_amount", "type": "sort", "input": "keep_cols",
         "by": "amount", "order": "desc"},
        {"name": "save_output", "type": "save", "input": "sort_amount",
         "filename": "regional_summary"},
    ]


def _steps_customer_cleanup(fid):
    return [
        {"name": "load_customers", "type": "load", "file_id": fid},
        {"name": "rename_cols", "type": "rename", "input": "load_customers",
         "mapping": {"customer_name": "name", "customer_id": "id"}},
        {"name": "save_output", "type": "save", "input": "rename_cols",
         "filename": "customers_clean"},
    ]


def _steps_orders_enriched(fid_orders, fid_customers):
    return [
        {"name": "load_orders", "type": "load", "file_id": fid_orders},
        {"name": "load_customers", "type": "load", "file_id": fid_customers},
        {"name": "enrich", "type": "join", "left": "load_orders",
         "right": "load_customers", "on": "customer_id", "how": "left"},
        {"name": "save_output", "type": "save", "input": "enrich",
         "filename": "orders_enriched"},
    ]


def _steps_revenue_by_region(fid):
    return [
        {"name": "load_sales", "type": "load", "file_id": fid},
        {"name": "revenue", "type": "aggregate", "input": "load_sales",
         "group_by": ["region"],
         "aggregations": [{"column": "amount", "function": "sum"},
                          {"column": "amount", "function": "mean"},
                          {"column": "order_id", "function": "count"}]},
        {"name": "save_output", "type": "save", "input": "revenue",
         "filename": "revenue_by_region"},
    ]


def _steps_quality_gate(fid):
    return [
        {"name": "load_sales", "type": "load", "file_id": fid},
        {"name": "check_quality", "type": "validate", "input": "load_sales",
         "rules": [{"column": "amount", "check": "not_null",
                    "severity": "error"},
                   {"column": "status", "check": "not_null",
                    "severity": "warning"}]},
        {"name": "good_rows", "type": "filter", "input": "check_quality",
         "column": "amount", "operator": "greater_than", "value": 0},
        {"name": "save_output", "type": "save", "input": "good_rows",
         "filename": "quality_passed"},
    ]


def _steps_pivot_sales(fid):
    return [
        {"name": "load_sales", "type": "load", "file_id": fid},
        {"name": "pivot_region", "type": "pivot", "input": "load_sales",
         "index": ["status"], "columns": "region", "values": "amount",
         "aggfunc": "sum", "fill_value": 0},
        {"name": "save_output", "type": "save", "input": "pivot_region",
         "filename": "sales_pivot"},
    ]


def _steps_melt_metrics(fid):
    return [
        {"name": "load_sales", "type": "load", "file_id": fid},
        {"name": "melt", "type": "unpivot", "input": "load_sales",
         "id_vars": ["order_id", "status"],
         "value_vars": ["amount"],
         "var_name": "metric", "value_name": "value"},
        {"name": "save_output", "type": "save", "input": "melt",
         "filename": "sales_melted"},
    ]


def _steps_dedupe_customers(fid):
    return [
        {"name": "load_customers", "type": "load", "file_id": fid},
        {"name": "dedupe", "type": "deduplicate", "input": "load_customers",
         "subset": ["email"], "keep": "first"},
        {"name": "fill_tier", "type": "fill_nulls", "input": "dedupe",
         "columns": ["tier"], "strategy": "constant",
         "constant_value": "bronze"},
        {"name": "save_output", "type": "save", "input": "fill_tier",
         "filename": "customers_deduped"},
    ]


def _steps_sample_qa(fid, frac=0.1):
    return [
        {"name": "load_events", "type": "load", "file_id": fid},
        {"name": "sample", "type": "sample", "input": "load_events",
         "fraction": frac, "random_state": 42},
        {"name": "save_output", "type": "save", "input": "sample",
         "filename": "events_sample"},
    ]


def _steps_sql_power_users(fid):
    return [
        {"name": "load_data", "type": "load", "file_id": fid},
        {"name": "power_users", "type": "sql", "input": "load_data",
         "query": "SELECT user_id, COUNT(*) AS events FROM {{input}} "
                  "GROUP BY user_id HAVING COUNT(*) > 5"},
        {"name": "save_output", "type": "save", "input": "power_users",
         "filename": "power_users"},
    ]


def _steps_wasm_risk(fid, wasm_id):
    return [
        {"name": "load_data", "type": "load", "file_id": fid},
        {"name": "recent", "type": "filter", "input": "load_data",
         "column": "amount", "operator": "greater_than", "value": 50},
        {"name": "risk", "type": "wasm_compute", "input": "recent",
         "wasm_file_id": wasm_id, "function": "compute_risk",
         "input_columns": ["amount"], "output_column": "risk_score"},
        {"name": "save_output", "type": "save", "input": "risk",
         "filename": "risk_scored"},
    ]


def _steps_stream_ingest(fid, topic):
    return [
        {"name": "load_data", "type": "load", "file_id": fid},
        {"name": "publish", "type": "stream_publish", "input": "load_data",
         "topic": topic, "key_column": "order_id"},
        {"name": "save_output", "type": "save", "input": "publish",
         "filename": "streamed_snapshot"},
    ]


def _steps_full_funnel(fid):
    return [
        {"name": "load_sales", "type": "load", "file_id": fid},
        {"name": "delivered", "type": "filter", "input": "load_sales",
         "column": "status", "operator": "equals", "value": "delivered"},
        {"name": "by_region", "type": "aggregate", "input": "delivered",
         "group_by": ["region"],
         "aggregations": [{"column": "amount", "function": "sum"}]},
        {"name": "ranked", "type": "sort", "input": "by_region",
         "by": "amount_sum", "order": "desc"},
        {"name": "save_output", "type": "save", "input": "ranked",
         "filename": "funnel_ranked"},
    ]


def _steps_churn_features(fid_orders, fid_customers):
    return [
        {"name": "load_orders", "type": "load", "file_id": fid_orders},
        {"name": "load_customers", "type": "load", "file_id": fid_customers},
        {"name": "joined", "type": "join", "left": "load_orders",
         "right": "load_customers", "on": "customer_id", "how": "inner"},
        {"name": "at_risk", "type": "filter", "input": "joined",
         "column": "tier", "operator": "equals", "value": "bronze"},
        {"name": "save_output", "type": "save", "input": "at_risk",
         "filename": "churn_candidates"},
    ]


# (name, builder-kind, domain) — 50 pipelines covering every step type.
PIPELINE_SPECS = (
    [("sales_delivered_eu", "delivered", "sales")] * 0  # placeholder guard
    + [("sales_delivered_flow", "delivered", "sales")] * 4
    + [("regional_revenue_summary", "summary", "sales")] * 4
    + [("customer_cleanup", "cleanup", "growth")] * 3
    + [("orders_enriched", "enriched", "sales")] * 4
    + [("revenue_by_region", "revenue", "finance")] * 4
    + [("sales_quality_gate", "quality", "quality")] * 4
    + [("sales_region_pivot", "pivot", "analytics")] * 3
    + [("sales_melted_metrics", "melt", "analytics")] * 3
    + [("customer_dedupe", "dedupe", "growth")] * 4
    + [("events_qa_sample", "sample", "product")] * 3
    + [("power_users_sql", "sql", "product")] * 3
    + [("order_risk_scoring", "wasm", "risk")] * 2
    + [("orders_stream_ingest", "stream", "streaming")] * 2
    + [("delivered_funnel_ranked", "funnel", "sales")] * 4
    + [("churn_candidates", "churn", "growth")] * 3
)

FAILURE_MESSAGES = [
    "ColumnNotFoundError: step='enrich', column='customer_id', available=['order_id', 'amount']",
    "FilterTypeMismatchError: step='good_rows', column='amount' is float64, value='abc' cannot be coerced",
    "JoinKeyMissingError: step='enrich', join key 'customer_id' missing from right side",
    "FileReadError: step='load_sales', file not found in storage",
    "AggregationError: step='revenue', column='amount', function='sum' requires numeric column",
    "RenameConflictError: step='rename_cols', target 'name' already exists",
    "ColumnNotFoundError: step='keep_cols', column='revenue', available=['region', 'amount', 'status']",
    "UnsupportedFileFormatError: step='load_sales', extension '.xml' not supported",
    "FilterTypeMismatchError: step='at_risk', column='tier' is object, value=3 cannot be coerced",
    "JoinKeyMissingError: step='joined', join key 'customer_id' missing from left side",
]


def _build_pipeline_yaml(kind, name, fids, wasm_id, topic):
    o, c = fids["orders"], fids["customers"]
    if kind == "delivered":
        steps = _steps_sales_delivered(o)
    elif kind == "summary":
        steps = _steps_regional_summary(o)
    elif kind == "cleanup":
        steps = _steps_customer_cleanup(c)
    elif kind == "enriched":
        steps = _steps_orders_enriched(o, c)
    elif kind == "revenue":
        steps = _steps_revenue_by_region(o)
    elif kind == "quality":
        steps = _steps_quality_gate(o)
    elif kind == "pivot":
        steps = _steps_pivot_sales(o)
    elif kind == "melt":
        steps = _steps_melt_metrics(o)
    elif kind == "dedupe":
        steps = _steps_dedupe_customers(c)
    elif kind == "sample":
        steps = _steps_sample_qa(fids["events"])
    elif kind == "sql":
        steps = _steps_sql_power_users(fids["events"])
    elif kind == "wasm":
        steps = _steps_wasm_risk(o, wasm_id)
    elif kind == "stream":
        steps = _steps_stream_ingest(o, topic)
    elif kind == "funnel":
        steps = _steps_full_funnel(o)
    else:
        steps = _steps_churn_features(o, c)
    return _yaml(name, f"Showcase pipeline ({kind})", steps)


# Seeding

def _ensure_demo_user(db):
    user = db.query(User).filter(User.email == DEMO_EMAIL).first()
    if user:
        return user
    from backend.auth import get_password_hash as _h  # noqa
    user = User(id=uuid.uuid4(), email=DEMO_EMAIL, username=DEMO_USERNAME,
                hashed_password=get_password_hash(DEMO_PASSWORD),
                role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_files(db, user):
    fids, records = {}, {}
    for fname, gen in DATASETS:
        columns, rows = gen()
        payload = _to_csv_bytes(columns, rows)
        file_id = str(uuid.uuid4())
        stored_path = f"{file_id}.csv"
        try:
            storage_service.upload(io.BytesIO(payload), stored_path)
        except Exception as exc:  # storage down? record metadata anyway
            print(f"WARNING: storage upload failed for {fname}: {exc}")
        rec = UploadedFile(
            id=uuid.UUID(file_id), original_filename=fname,
            stored_path=stored_path, file_size_bytes=len(payload),
            row_count=len(rows), column_count=len(columns),
            columns=columns, dtypes=_dtypes_for(columns, rows),
            user_id=user.id,
        )
        db.add(rec)
        fids[fname.split(".")[0]] = file_id
        records[fname] = (rec, len(rows))
    # key datasets by short name used in builders
    keyed = {
        "orders": fids["orders_2024"],
        "customers": fids["customers"],
        "events": fids["web_events"],
        "products": fids["products"],
        "inventory": fids["inventory"],
    }
    db.commit()
    return keyed, records


def _finish_run(db, run, status, started, ended, total_in, total_out,
                error=None, trigger="manual", schedule_id=None):
    from backend.models import PipelineStatus as PS
    run.status = status
    run.started_at = started
    run.completed_at = ended if status not in (
        PS.RUNNING, PS.PENDING, PS.STREAMING_ACTIVE) else None
    run.total_rows_in = total_in
    run.total_rows_out = total_out
    run.error_message = error
    run.trigger = trigger
    run.schedule_id = schedule_id
    db.add(run)


def _add_steps(db, run, spec_steps, status_ok, rows_in, rng, trace_base):
    from backend.models import StepStatus as SS
    n = len(spec_steps)
    for i, s in enumerate(spec_steps):
        if status_ok:
            st, err, out = SS.COMPLETED, None, max(
                0, rows_in - rng.randint(0, rows_in // 3))
        else:
            if i == n - 2:  # fail on the compute step, save never runs
                st, err, out = SS.FAILED, s.pop(
                    "_seed_error", "Step failed"), rows_in
            elif i == n - 1:
                st, err, out = SS.SKIPPED, None, 0
            else:
                st, err, out = SS.COMPLETED, None, max(
                    0, rows_in - rng.randint(0, rows_in // 4))
        db.add(StepResult(
            pipeline_run_id=run.id, step_name=s["name"],
            step_type=s["type"], step_index=i, status=st,
            rows_in=rows_in, rows_out=out,
            columns_in=["order_id", "amount"], columns_out=["order_id", "amount"],
            duration_ms=rng.randint(40, 4000),
            warnings=[] if st == SS.COMPLETED else ["seeded warning"],
            error_message=err,
            trace_id=trace_base, span_id=rng.randbytes(8).hex()[:16],
            engine=rng.choice(["pandas", "duckdb"]),
            started_at=run.started_at,
            completed_at=run.completed_at,
        ))


def seed_showcase(reset=False):
    from backend.models import PipelineStatus as PS
    db = SessionLocal()
    try:
        seed_demo()
        user = db.query(User).filter(User.email == DEMO_EMAIL).one()

        if reset:
            print("Reset: wiping demo-owned showcase data...")
            for run in db.query(PipelineRun).filter(
                    PipelineRun.user_id == user.id).all():
                db.delete(run)
            for sched in db.query(PipelineSchedule).filter(
                    PipelineSchedule.user_id == user.id).all():
                db.delete(sched)
            for f in db.query(UploadedFile).filter(
                    UploadedFile.user_id == user.id).all():
                try:
                    storage_service.delete(f.stored_path)
                except Exception:
                    pass
                db.delete(f)
            db.query(PipelineVersion).delete()
            db.commit()

        existing_names = {
            r[0] for r in db.query(PipelineRun.name).filter(
                PipelineRun.user_id == user.id).all()
        }

        fids, file_info = _seed_files(db, user)
        wasm_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        counts = {"runs": 0, "failed": 0, "healed": 0}

        for idx, (base, kind, domain) in enumerate(PIPELINE_SPECS):
            name = f"{base}_{domain}_{idx:02d}"
            if name in existing_names:
                continue
            topic = f"showcase-{base}-{idx:02d}"
            yaml_text = _build_pipeline_yaml(kind, name, fids, wasm_id,
                                             topic)
            # outcome distribution: 28 ok / 10 failed / 5 healed /
            # 4 running / 2 pending / 1 cancelled
            m = idx % 50
            if m in {4, 9, 14, 19, 24, 29, 34, 39, 44, 49}:
                status = PS.FAILED
            elif m in {7, 17, 27, 37, 47}:
                status = PS.HEALED
            elif m in {0, 13, 26, 39}:
                status = PS.RUNNING
            elif m in {12, 22}:
                status = PS.PENDING
            elif m == 48:
                status = PS.CANCELLED
            else:
                status = PS.COMPLETED

            created = now - timedelta(hours=idx * 6, minutes=RNG.randint(0, 59))
            if status in (PS.RUNNING, PS.PENDING):
                started = now - timedelta(minutes=RNG.randint(2, 9))
                ended = started
            else:
                started = created + timedelta(seconds=RNG.randint(5, 60))
                ended = started + timedelta(
                    seconds=RNG.randint(2, 90), milliseconds=RNG.randint(0, 999))
            total_in = RNG.choice([600, 600, 200, 1200, 150, 60, 1000])
            total_out = total_in if status == PS.COMPLETED else max(
                0, total_in - RNG.randint(10, total_in // 2))

            run = PipelineRun(
                id=uuid.uuid4(), name=name, status=PS.PENDING,
                yaml_config=yaml_text, created_at=created,
                user_id=user.id, trace_id=RNG.randbytes(16).hex(),
                trigger="manual",
            )
            db.add(run)
            db.flush()
            err = (FAILURE_MESSAGES[idx % len(FAILURE_MESSAGES)]
                   if status == PS.FAILED else None)
            _finish_run(db, run, status, started, ended, total_in,
                        total_out, error=err)

            import yaml as _y
            steps = _y.safe_load(yaml_text)["pipeline"]["steps"]
            if status == PS.FAILED:
                steps[-2]["_seed_error"] = err
            _add_steps(db, run, steps,
                       status_ok=status in (PS.COMPLETED, PS.HEALED),
                       rows_in=total_in, rng=RNG,
                       trace_base=run.trace_id)
            if status == PS.HEALED:
                db.add(HealingAttempt(
                    run_id=run.id, attempt_number=1,
                    status=HealingAttemptStatus.APPLIED,
                    pipeline_name=name,
                    failed_step=steps[-2]["name"],
                    error_type="ColumnNotFoundError",
                    error_message=err or "healed column reference",
                    applied=True, confidence=0.87,
                    healed_at=ended,
                    classification_reason="rename mapping drift",
                    parser_valid=True, sandbox_passed=True, ai_valid=True,
                ))
            if idx < 3:  # a few versioned pipelines
                for v in (1, 2):
                    db.add(PipelineVersion(
                        pipeline_name=name, version_number=v,
                        yaml_config=yaml_text, run_id=run.id,
                        change_summary=f"showcase revision {v}",
                    ))
            counts["runs"] += 1
            counts["failed" if status == PS.FAILED else "healed"] += (
                1 if status in (PS.FAILED, PS.HEALED) else 0)
            db.commit()

        # Schedules (3 active, 1 paused).
        sched_specs = [
            ("sales_daily_rollup", "0 6 * * *", True),
            ("churn_hourly_refresh", "15 * * * *", True),
            ("inventory_weekly_audit", "0 9 * * 1", True),
            ("legacy_nightly_export", "30 2 * * *", False),
        ]
        for sname, cron, active in sched_specs:
            if db.query(PipelineSchedule).filter(
                    PipelineSchedule.pipeline_name == sname,
                    PipelineSchedule.user_id == user.id).first():
                continue
            first_run = db.query(PipelineRun).filter(
                PipelineRun.user_id == user.id).order_by(
                    PipelineRun.created_at.desc()).first()
            sched = PipelineSchedule(
                user_id=user.id, pipeline_name=sname,
                yaml_config=_yaml(sname, "Showcase schedule", _steps_sales_delivered(
                    list(fids.values())[0])),
                cron_expression=cron,
                cron_human=f"cron {cron}", is_active=active,
                last_run_at=now - timedelta(hours=5),
                next_run_at=now + timedelta(hours=1) if active else None,
                last_run_status="COMPLETED" if active else "PAUSED",
                total_runs=RNG.randint(5, 40),
                successful_runs=RNG.randint(4, 30), failed_runs=RNG.randint(0, 3),
            )
            db.add(sched)
            db.flush()
            if first_run is not None and active:
                db.add(ScheduleRun(
                    schedule_id=sched.id, run_id=first_run.id,
                    triggered_at=now - timedelta(hours=5),
                    status="COMPLETED", duration_seconds=RNG.randint(10, 120),
                ))
            db.commit()

        db.commit()
        print(f"Showcase seeded: {counts['runs']} runs "
              f"({counts['failed']} failed, {counts['healed']} healed), "
              f"{len(file_info)} files, 4 schedules.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_showcase(reset="--reset" in sys.argv)
