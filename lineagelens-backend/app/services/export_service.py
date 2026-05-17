from __future__ import annotations

import asyncio
import base64
import csv
import io
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.redis_store import RedisStore

logger = logging.getLogger(__name__)


@dataclass
class ExportJob:
    job_id: str
    status: str  # "pending", "running", "done", "failed"
    result_bytes: bytes | None = None
    result_content_type: str = "application/octet-stream"
    filename: str = "export"
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None


def new_job() -> ExportJob:
    return ExportJob(job_id=str(uuid.uuid4()), status="pending")


def serialize_job(job: ExportJob) -> dict:
    d = asdict(job)
    if d.get("result_bytes") is not None:
        d["result_bytes"] = base64.b64encode(d["result_bytes"]).decode()
        d["_bytes_b64"] = True
    return d


def deserialize_job(data: dict | None) -> ExportJob | None:
    if data is None:
        return None
    data = dict(data)
    if data.pop("_bytes_b64", False) and data.get("result_bytes"):
        data["result_bytes"] = base64.b64decode(data["result_bytes"])
    return ExportJob(**data)


async def run_export_job(
    job: ExportJob,
    records: list[dict],
    fmt: str,
    kv_store: RedisStore,
    store_key: str,
) -> None:
    """
    Background coroutine that writes records to the requested format.
    Updates job status in the kv_store after completion.
    Supports: "json", "csv", "parquet"
    """
    job.status = "running"
    try:
        if fmt == "json":
            import json as _json
            data = _json.dumps({"results": records, "count": len(records)}, default=str).encode()
            job.result_bytes = data
            job.result_content_type = "application/json"
            job.filename = "export.json"

        elif fmt == "csv":
            output = io.StringIO()
            if records:
                keys = ["uuid", "filePath", "modelName", "timestampIso", "riskScore", "insertedCode", "tokenCount", "costUsd"]
                writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore", quoting=csv.QUOTE_ALL)
                writer.writeheader()
                for row in records:
                    writer.writerow({k: str(row.get(k, "")) for k in keys})
            job.result_bytes = output.getvalue().encode()
            job.result_content_type = "text/csv"
            job.filename = "export.csv"

        elif fmt == "parquet":
            job.result_bytes = await asyncio.get_running_loop().run_in_executor(
                None, _write_parquet, records
            )
            job.result_content_type = "application/octet-stream"
            job.filename = "export.parquet"

        else:
            raise ValueError(f"Unknown format: {fmt}")

        job.status = "done"
        job.completed_at = time.time()

    except Exception as exc:
        logger.exception("Export job %s failed", job.job_id)
        job.status = "failed"
        job.error = str(exc)
        job.completed_at = time.time()
    finally:
        await kv_store.set(store_key, serialize_job(job))


def _write_parquet(records: list[dict]) -> bytes:
    """Write records to Parquet bytes. Requires pyarrow."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        raise RuntimeError(
            "pyarrow is not installed. Install it with: pip install pyarrow"
        )

    if not records:
        schema = pa.schema([
            ("uuid", pa.string()),
            ("filePath", pa.string()),
            ("modelName", pa.string()),
            ("timestampIso", pa.string()),
            ("riskScore", pa.int64()),
            ("insertedCode", pa.string()),
        ])
        table = pa.table({f.name: pa.array([], type=f.type) for f in schema}, schema=schema)
    else:
        cols: dict[str, list] = {}
        for key in records[0]:
            cols[key] = [r.get(key) for r in records]
        table = pa.table(cols)

    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


async def cleanup_old_jobs(kv_store: RedisStore, max_age_seconds: int = 3600) -> int:
    """Remove completed/failed jobs older than max_age_seconds from in-memory store.

    Redis-backed stores handle expiry via TTL — this function is a no-op for them.
    """
    if kv_store.uses_redis:
        return 0
    now = time.time()
    to_remove = []
    for key, val in kv_store.local_items():
        if isinstance(val, dict):
            completed_at = val.get("completed_at")
            status = val.get("status", "")
            if status in {"done", "failed"} and completed_at and (now - completed_at) > max_age_seconds:
                to_remove.append(key)
    for key in to_remove:
        kv_store.local_pop(key)
    return len(to_remove)
