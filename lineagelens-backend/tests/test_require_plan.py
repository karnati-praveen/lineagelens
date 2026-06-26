"""Tests for real-license plan enforcement (PART 3 #21).

require_plan() was defined but never used; runtime gating trusted the
BACKEND_MODE honor flag. It is now wired onto paid endpoints and enforces the
signed-license entitlement when LICENSE_ENFORCEMENT is on (default off → no-op).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.mode_guard import require_plan


def test_invalid_min_plan_raises():
    with pytest.raises(ValueError):
        require_plan("ultra")


def test_noop_when_enforcement_off():
    # Default honor-flag mode: even a lite effective_plan passes a max gate.
    dep = require_plan("max")
    assert dep(SimpleNamespace(license_enforcement=False, effective_plan="lite")) is None


def test_blocks_when_enforced_and_underplan():
    dep = require_plan("plus")
    with pytest.raises(HTTPException) as ei:
        dep(SimpleNamespace(license_enforcement=True, effective_plan="lite"))
    assert ei.value.status_code == 403
    assert "Plus" in ei.value.detail


def test_allows_when_enforced_and_sufficient():
    dep = require_plan("plus")
    assert dep(SimpleNamespace(license_enforcement=True, effective_plan="max")) is None


def test_exact_plan_match_allowed_when_enforced():
    dep = require_plan("plus")
    assert dep(SimpleNamespace(license_enforcement=True, effective_plan="plus")) is None


def test_aibom_route_has_plan_gate_wired():
    """The AI-BOM route declares require_plan as a dependency (it is actually used now)."""
    from app.api.routes import integrity

    route = next(
        r for r in integrity.router.routes
        if getattr(r, "path", "").endswith("/aibom")
    )
    dep_names = [getattr(d.dependency, "__name__", "") for d in route.dependencies]
    # require_plan returns an inner function named "_dependency".
    assert "_dependency" in dep_names
