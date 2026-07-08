#!/usr/bin/env python3
"""Export the DB-backed attestation key registry as a release asset (PART 5 #59).

Publishes the current active + retired + compromised key list alongside every
release, so a relying party can verify historical attestations (and, per the
vendor-failure covenant, a future steward can pick up key history) without a
live lookup against the production database.

Requires DATABASE_URL to point at the production key-registry DB. If unset,
this script prints a clear message and exits 0 (not a failure) — the release
workflow logs that explanation rather than silently omitting the asset
without saying why (no silent green, PART 5 #58).

Usage:
    DATABASE_URL=postgresql+asyncpg://... \\
        python lineagelens-scripts/export_key_registry.py --out lineagelens-release-keys-1.3.0.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "lineagelens-backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def format_registry_export(keys: list) -> dict:
    """Pure formatting function — testable without a DB connection.

    *keys* is a list of duck-typed objects with the same attributes as
    app.db.models.AttestationKey.
    """
    return {
        "schemaVersion": "1.0",
        "keys": [
            {
                "publicKeyId": k.public_key_id,
                "publicKeyHex": k.public_key_hex,
                "validFrom": k.valid_from.isoformat() if k.valid_from else None,
                "validUntil": k.valid_until.isoformat() if k.valid_until else None,
                "compromisedAt": k.compromised_at.isoformat() if k.compromised_at else None,
                "status": k.status,
                "label": k.label,
            }
            for k in keys
        ],
    }


async def _fetch_keys() -> list:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.db.models import AttestationKey

    database_url = os.environ.get("DATABASE_URL", "").strip()
    engine = create_async_engine(database_url)
    try:
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(select(AttestationKey))
            return list(result.scalars().all())
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Output JSON file path")
    args = parser.parse_args(argv)

    if not os.environ.get("DATABASE_URL", "").strip():
        print(
            "key registry publish skipped: DATABASE_URL not configured "
            "(set it to the production DB to include this release asset)",
            file=sys.stderr,
        )
        return 0  # optional asset, honestly skipped — not a release failure

    keys = asyncio.run(_fetch_keys())
    export = format_registry_export(keys)
    Path(args.out).write_text(json.dumps(export, indent=2), encoding="utf-8")
    print(f"wrote {len(export['keys'])} key(s) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
