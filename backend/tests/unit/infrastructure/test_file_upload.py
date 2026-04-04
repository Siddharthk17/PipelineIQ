"""Unit tests for upload routing and streaming safety guarantees."""

import ast
from pathlib import Path

from fastapi.responses import ORJSONResponse

from backend.main import app

FILES_MODULE_PATH = Path(__file__).resolve().parents[3] / "api" / "files.py"


def test_fastapi_default_response_is_orjson():
    assert app.router.default_response_class is ORJSONResponse


def test_files_module_has_no_unbounded_uploadfile_read_calls():
    tree = ast.parse(FILES_MODULE_PATH.read_text())

    class UploadReadVisitor(ast.NodeVisitor):
        def __init__(self):
            self.unbounded_upload_reads = []

        def visit_Await(self, node):
            call = node.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "read"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "file"
                and len(call.args) == 0
            ):
                self.unbounded_upload_reads.append(node.lineno)
            self.generic_visit(node)

    visitor = UploadReadVisitor()
    visitor.visit(tree)
    assert not visitor.unbounded_upload_reads, (
        f"Found unbounded `await file.read()` at lines {visitor.unbounded_upload_reads}"
    )


def test_upload_routing_constants_are_expected():
    import backend.api.files as files_module

    assert files_module.LARGE_FILE_THRESHOLD == 10 * 1024 * 1024
    assert files_module.MAX_DIRECT_UPLOAD_SIZE >= files_module.LARGE_FILE_THRESHOLD


def test_orjson_used_for_pending_upload_payloads():
    source = FILES_MODULE_PATH.read_text()
    assert "orjson.dumps(payload)" in source
    assert "orjson.loads(raw)" in source
