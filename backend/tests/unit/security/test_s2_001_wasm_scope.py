"""S2-001: worker must not load another tenant's WASM/files (Medium)."""
import uuid as uuid_module
from unittest.mock import patch


def _make_module(test_db, owner_id):
    from backend.models import WasmModule
    mod = WasmModule(
        id=uuid_module.uuid4(), name=f"m-{uuid_module.uuid4().hex[:6]}",
        storage_key=f"modules/{uuid_module.uuid4().hex}.wasm",
        file_size_bytes=128, sha256_hash="ab" * 32,
        exports=[], imports=[], fuel_budget=1000,
        is_active=True, user_id=owner_id,
    )
    test_db.add(mod)
    test_db.commit()
    return mod


def test_load_wasm_modules_scoped_to_owner(test_db):
    from backend.tasks.pipeline_tasks import _load_wasm_modules

    owner = uuid_module.uuid4()
    stranger = uuid_module.uuid4()
    mod = _make_module(test_db, owner)
    with patch(
        "backend.repositories.wasm.load_wasm_bytes_sync",
        return_value=b"\x00asm",
    ):
        assert _load_wasm_modules(
            test_db, {str(mod.id)}, owner_id=owner) != {}
        assert _load_wasm_modules(
            test_db, {str(mod.id)}, owner_id=stranger) == {}


def test_run_pipeline_does_not_resolve_foreign_file(test_db, tmp_path):
    """Stored YAML with a foreign file_id must not load its bytes (S2-001)."""
    from unittest.mock import MagicMock, patch

    from backend.models import PipelineRun, UploadedFile
    from backend.tasks import pipeline_tasks as pt

    owner = uuid_module.uuid4()
    stranger = uuid_module.uuid4()
    f = UploadedFile(
        original_filename="a.csv", stored_path=str(tmp_path / "a.csv"),
        file_size_bytes=10, row_count=1, column_count=1,
        columns=["x"], dtypes={}, user_id=owner,
    )
    test_db.add(f)
    test_db.commit()
    yaml_text = (
        "pipeline:\n  name: s\n  steps:\n"
        f"    - name: l\n      type: load\n      file_id: {f.id}\n"
        "    - name: s\n      type: save\n      input: l\n      filename: o.csv\n"
    )
    run = PipelineRun(
        id=uuid_module.uuid4(), name="s", status="PENDING",
        yaml_config=yaml_text, user_id=stranger,
    )
    test_db.add(run)
    test_db.commit()
    captured = {}
    fake_runner = MagicMock()
    fake_runner.execute.side_effect = lambda **kw: captured.update(kw) or MagicMock()
    with (
        patch.object(pt, "PipelineRunner", return_value=fake_runner),
        patch.object(pt, "make_redis_progress_callback",
                     return_value=lambda *a, **k: None),
    ):
        pt._run_pipeline(test_db, run)
    assert str(f.id) not in captured.get("file_paths", {str(f.id): 1}), (
        "foreign file bytes must never resolve into the worker")
