from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# In-memory job store. app.state.export_jobs is set in main.py lifespan.
# Each job: {status, result_bytes, result_content_type, filename, error, created_at, completed_at}


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


async def run_export_job(
    job: ExportJob,
    records: list[dict],
    fmt: str,
    jobs_store: dict[str, ExportJob],
    store_key: str | None = None,
) -> None:
    """
    Background coroutine that writes records to the requested format.
    Updates job in-place and in jobs_store.
    Supports: "json", "csv", "parquet"
    """
    job.status = "running"
    try:
        if fmt == "json":
            import json
            data = json.dumps({"results": records, "count": len(records)}, default=str).encode()
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
        jobs_store[store_key or job.job_id] = job


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


def cleanup_old_jobs(jobs_store: dict[str, ExportJob], max_age_seconds: int = 3600) -> int:
    """Remove completed/failed jobs older than max_age_seconds. Returns count removed."""
    now = time.time()
    to_remove = [
        jid for jid, j in jobs_store.items()
        if j.status in {"done", "failed"} and j.completed_at and (now - j.completed_at) > max_age_seconds
    ]
    for jid in to_remove:
        del jobs_store[jid]
    return len(to_remove)
