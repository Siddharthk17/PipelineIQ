"""/dev/shm storage for Arrow IPC payloads.

/dev/shm is a tmpfs on Linux — files live in RAM and auto-clear on reboot.
It is fast (~10GB/s read/write) and large (typically 50% of system RAM),
making it the right warm tier for DataFrames between 10MB and 500MB.

Redis handles small, frequently accessed tables. MinIO handles rare giants.
/dev/shm bridges the middle: DataFrames too big for Redis but too accessed
for a network round-trip to MinIO.

File naming: /dev/shm/pipelineiq_<run_id>_<safe_key>_<uuid>.arrow
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc

logger = logging.getLogger(__name__)

SHM_DIR = Path("/dev/shm")
SHM_PREFIX = "pipelineiq_"
SHM_EXTENSION = ".arrow"
SHM_MAX_AGE_HOURS = 24


def shm_available() -> bool:
    return SHM_DIR.is_dir() and os.access(SHM_DIR, os.W_OK)


def shm_path_for(run_id: str, key: str, suffix: str = "") -> Path:
    digest = hashlib.sha256(f"{run_id}:{key}".encode()).hexdigest()[:16]
    return SHM_DIR / f"{SHM_PREFIX}{run_id}_{digest}{suffix}{SHM_EXTENSION}"


def write(table: pa.Table, run_id: str, key: str) -> tuple[Path, int]:
    path = shm_path_for(run_id, key)
    ipc_bytes = _table_to_ipc_bytes(table)
    path.write_bytes(ipc_bytes)
    logger.debug("shm write: key=%s size=%.0fKB", key, len(ipc_bytes) / 1024)
    return path, len(ipc_bytes)


def read(path: Path) -> pa.Table | None:
    if not path.exists():
        return None
    return _ipc_bytes_to_table(path.read_bytes())


def remove(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def remove_for_run(run_id: str) -> int:
    deleted = 0
    pattern = f"{SHM_PREFIX}{run_id}_*{SHM_EXTENSION}"
    for entry in SHM_DIR.glob(pattern):
        try:
            entry.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            pass
    return deleted


def usage_bytes() -> tuple[int, int]:
    if not shm_available():
        return 0, 0
    stat = os.statvfs(SHM_DIR)
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    return total - free, total


def cleanup_stale() -> int:
    if not shm_available():
        return 0
    deleted = 0
    cutoff = time.time() - SHM_MAX_AGE_HOURS * 3600
    for entry in SHM_DIR.glob(f"{SHM_PREFIX}*{SHM_EXTENSION}"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                deleted += 1
        except OSError:
            pass
    if deleted:
        logger.info("shm stale cleanup: removed %d files", deleted)
    return deleted


def _table_to_ipc_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def _ipc_bytes_to_table(data: bytes) -> pa.Table:
    reader = ipc.open_stream(pa.py_buffer(data))
    return reader.read_all()
