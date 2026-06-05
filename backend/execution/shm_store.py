"""/dev/shm storage for Arrow IPC payloads.

/dev/shm is a tmpfs on Linux — files live in RAM and auto-clear on reboot.
It is fast (~10GB/s read/write) and large (typically 50% of system RAM),
making it the right warm tier for DataFrames between 10MB and 500MB.

Redis handles small, frequently accessed tables. Spill storage handles rare
giants. /dev/shm bridges the middle: DataFrames too big for Redis but too
accessed for a network round-trip to MinIO or S3.

File naming: {shm_dir}/pipelineiq_{run_id}_{digest}.arrow

All functions accept an optional `shm_dir` parameter for test isolation.
When omitted, the default system /dev/shm is used.
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

SYSTEM_SHM_DIR = Path("/dev/shm")
SHM_PREFIX = "pipelineiq_"
SHM_EXTENSION = ".arrow"
SHM_MAX_AGE_HOURS = 24


def shm_available(shm_dir: Path | None = None) -> bool:
    target = shm_dir or SYSTEM_SHM_DIR
    return target.is_dir() and os.access(target, os.W_OK)


def shm_path_for(
    run_id: str,
    key: str,
    suffix: str = "",
    shm_dir: Path | None = None,
) -> Path:
    target = shm_dir or SYSTEM_SHM_DIR
    digest = hashlib.sha256(f"{run_id}:{key}".encode()).hexdigest()[:16]
    return target / f"{SHM_PREFIX}{run_id}_{digest}{suffix}{SHM_EXTENSION}"


def write(
    table: pa.Table,
    run_id: str,
    key: str,
    shm_dir: Path | None = None,
) -> tuple[Path, int]:
    path = shm_path_for(run_id, key, shm_dir=shm_dir)
    ipc_bytes = _table_to_ipc_bytes(table)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(ipc_bytes)
    os.chmod(path, 0o600)
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


def remove_for_run(
    run_id: str,
    shm_dir: Path | None = None,
) -> int:
    target = shm_dir or SYSTEM_SHM_DIR
    deleted = 0
    pattern = f"{SHM_PREFIX}{run_id}_*{SHM_EXTENSION}"
    for entry in target.glob(pattern):
        try:
            entry.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            pass
    return deleted


def usage_bytes(shm_dir: Path | None = None) -> tuple[int, int]:
    target = shm_dir or SYSTEM_SHM_DIR
    if not os.access(target, os.W_OK):
        return 0, 0
    stat = os.statvfs(target)
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    return total - free, total


def cleanup_stale(
    shm_dir: Path | None = None,
    max_age_hours: int = SHM_MAX_AGE_HOURS,
) -> int:
    target = shm_dir or SYSTEM_SHM_DIR
    if not target.is_dir() or not os.access(target, os.W_OK):
        return 0
    deleted = 0
    cutoff = time.time() - max_age_hours * 3600
    for entry in target.glob(f"{SHM_PREFIX}*{SHM_EXTENSION}"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                deleted += 1
        except OSError:
            pass
    if deleted:
        logger.info("shm stale cleanup: removed %d files from %s", deleted, target)
    return deleted


def _table_to_ipc_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def _ipc_bytes_to_table(data: bytes) -> pa.Table:
    reader = ipc.open_stream(pa.py_buffer(data))
    return reader.read_all()
