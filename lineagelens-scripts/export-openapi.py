#!/usr/bin/env python3
"""Dump the LineageLens OpenAPI schema to lineagelens-docs/openapi.json.

Run from the repo root:
    python lineagelens-scripts/export-openapi.py
"""
import json
import os
import sys

# Dummy secrets that satisfy the validator (>=32 chars, not in the disallowed list).
# These are never used for signing — this script only calls app.openapi().
os.environ.setdefault("JWT_SECRET_KEY", "openapi-schema-export-only--not-a-real-secret-key")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("BACKEND_MODE", "team")

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_repo_root, "lineagelens-backend"))

from app.main import app  # noqa: E402

out_path = os.path.join(_repo_root, "lineagelens-docs", "openapi.json")
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(app.openapi(), fh, indent=2)

print(f"Written: {out_path}")
