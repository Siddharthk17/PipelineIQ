"""Unit tests for profiling task storage loading behavior."""

import io
import uuid as _uuid
from unittest.mock import MagicMock

import pytest

from backend.models import FileProfile, UploadedFile, User
from backend.tasks import profiling as profiling_tasks


def _create_uploaded_file_record(
    test_db,
    *,
    stored_path: str,
    original_filename: str,
) -> str:
    user = User(
        id=_uuid.uuid4(),
        email=f"{_uuid.uuid4().hex[:8]}@example.com",
        username=f"user_{_uuid.uuid4().hex[:8]}",
        hashed_password="hashed_password",
        role="viewer",
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()

    uploaded_file = UploadedFile(
        id=_uuid.uuid4(),
        original_filename=original_filename,
        stored_path=stored_path,
        file_size_bytes=128,
        row_count=2,
        column_count=2,
        columns=["a", "b"],
        dtypes={"a": "int64", "b": "int64"},
        user_id=user.id,
        version=1,
    )
    test_db.add(uploaded_file)
    test_db.commit()
    return str(uploaded_file.id)


def test_load_file_from_storage_csv_uses_storage_service(test_db, monkeypatch):
    file_id = _create_uploaded_file_record(
        test_db,
        stored_path="stored-key.csv",
        original_filename="input.csv",
    )

    exists_mock = MagicMock(return_value=True)
    download_mock = MagicMock(return_value=io.BytesIO(b"a,b\n1,2\n3,4\n"))

    monkeypatch.setattr(profiling_tasks, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(profiling_tasks.storage_service, "exists", exists_mock)
    monkeypatch.setattr(profiling_tasks.storage_service, "download", download_mock)

    df = profiling_tasks._load_file_from_disk(file_id)

    assert list(df.columns) == ["a", "b"]
    assert df.shape == (2, 2)
    assert df["a"].tolist() == [1, 3]
    exists_mock.assert_called_once_with("stored-key.csv")
    download_mock.assert_called_once_with("stored-key.csv")


def test_load_file_from_storage_json_uses_storage_service(test_db, monkeypatch):
    file_id = _create_uploaded_file_record(
        test_db,
        stored_path="stored-key.json",
        original_filename="input.json",
    )

    exists_mock = MagicMock(return_value=True)
    download_mock = MagicMock(
        return_value=io.BytesIO(b'[{"a": 10, "b": "x"}, {"a": 20, "b": "y"}]')
    )

    monkeypatch.setattr(profiling_tasks, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(profiling_tasks.storage_service, "exists", exists_mock)
    monkeypatch.setattr(profiling_tasks.storage_service, "download", download_mock)

    df = profiling_tasks._load_file_from_disk(file_id)

    assert list(df.columns) == ["a", "b"]
    assert df.shape == (2, 2)
    assert df["a"].tolist() == [10, 20]
    exists_mock.assert_called_once_with("stored-key.json")
    download_mock.assert_called_once_with("stored-key.json")


def test_load_file_from_storage_missing_path_raises_value_error(test_db, monkeypatch):
    file_id = _create_uploaded_file_record(
        test_db,
        stored_path="missing-key.csv",
        original_filename="input.csv",
    )

    exists_mock = MagicMock(return_value=False)
    download_mock = MagicMock()

    monkeypatch.setattr(profiling_tasks, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(profiling_tasks.storage_service, "exists", exists_mock)
    monkeypatch.setattr(profiling_tasks.storage_service, "download", download_mock)

    with pytest.raises(ValueError, match="File not found at path: missing-key.csv"):
        profiling_tasks._load_file_from_disk(file_id)

    exists_mock.assert_called_once_with("missing-key.csv")
    download_mock.assert_not_called()


def test_profile_file_missing_record_skips_without_profile_insert(test_db, monkeypatch):
    missing_file_id = str(_uuid.uuid4())
    monkeypatch.setattr(profiling_tasks, "SessionLocal", lambda: test_db)

    result = profiling_tasks.profile_file.run(missing_file_id)

    assert result["file_id"] == missing_file_id
    assert result["status"] == "skipped"
    assert result["reason"] == "file_not_found"
    assert test_db.query(FileProfile).count() == 0
