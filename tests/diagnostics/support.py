"""Synthetic fixture builders for diagnostics tests.

These write a small, isolated CaseRepository root under a pytest tmp_path --
never touching evaluation/ -- for behavior (tie-broken/chronological
ordering, multi-value filter exclusion, limiting) that the real fixtures
don't carry enough records to exercise cleanly on their own.
"""

import json
from pathlib import Path


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def build_ordering_case_root(root: Path) -> Path:
    """A single synthetic case with 2-3 records per category, written in
    scrambled (non-chronological) order, for deterministic-ordering checks
    and multi-value filter-exclusion checks (e.g. flag_name)."""
    case_dir = root / "cases" / "TEST-ORDERING"

    _write(
        case_dir / "incident.json",
        {
            "case_id": "TEST-ORDERING",
            "title": "Synthetic ordering fixture",
            "started_at": "2026-01-01T00:00:00Z",
            "affected_services": ["checkout-api"],
            "symptom": "n/a",
        },
    )
    _write(
        case_dir / "deployments.json",
        [
            {
                "source_id": "deploy-b",
                "service": "checkout-api",
                "timestamp": "2026-01-01T02:00:00Z",
                "version": "2.0.0",
                "status": "completed",
            },
            {
                "source_id": "deploy-a",
                "service": "checkout-api",
                "timestamp": "2026-01-01T01:00:00Z",
                "version": "1.0.0",
                "status": "completed",
            },
        ],
    )
    _write(
        case_dir / "feature_flags.json",
        [
            {
                "source_id": "flag-b",
                "service": "checkout-api",
                "timestamp": "2026-01-01T04:00:00Z",
                "flag_name": "checkout_x",
                "old_value": "0",
                "new_value": "1",
            },
            {
                "source_id": "flag-a",
                "service": "checkout-api",
                "timestamp": "2026-01-01T03:00:00Z",
                "flag_name": "checkout_x",
                "old_value": "1",
                "new_value": "0",
            },
            {
                "source_id": "flag-c",
                "service": "checkout-api",
                "timestamp": "2026-01-01T03:30:00Z",
                "flag_name": "checkout_y",
                "old_value": "a",
                "new_value": "b",
            },
        ],
    )
    _write(
        case_dir / "external_status.json",
        [
            {
                "source_id": "status-b",
                "provider": "payment-gateway",
                "timestamp": "2026-01-01T06:00:00Z",
                "status": "operational",
                "message": "recovered",
            },
            {
                "source_id": "status-a",
                "provider": "payment-gateway",
                "timestamp": "2026-01-01T05:00:00Z",
                "status": "degraded",
                "message": "degraded",
            },
        ],
    )
    return root


def build_runbook_root(root: Path) -> Path:
    """Three synthetic runbooks for search/filter/limit checks the single
    real runbook fixture can't exercise (a title-only match, a content-only
    match, more than one service, and more than one result to limit/order)."""
    runbooks_dir = root / "runbooks"
    runbooks: list[dict[str, str | None]] = [
        {
            "source_id": "runbook-alpha-001",
            "service": "checkout-api",
            "title": "Zephyr Retry Guidance",
            "content": "Investigate backoff settings and retry storms for checkout-api.",
        },
        {
            "source_id": "runbook-beta-002",
            "service": "billing-api",
            "title": "Invoice Batch Sizing",
            "content": "Review invoice batch size configuration and queue depth.",
        },
        {
            "source_id": "runbook-gamma-003",
            "service": None,
            "title": "General Triage Steps",
            "content": (
                "Start broad: check metrics dashboards, then logs, then external dependencies."
            ),
        },
    ]
    for runbook in runbooks:
        _write(runbooks_dir / f"{runbook['source_id']}.json", runbook)
    return root
