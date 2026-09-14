"""Category 3 -- DiagnosticService.get_deployment_history.

Uses INC-001 and INC-003 (each has exactly one deployment, on a single
service) for filtering and time-range checks, and the synthetic
`ordering_service` fixture for chronological ordering, since no real case
has two deployments on the same service.
"""

from datetime import datetime

from tracepilot.diagnostics.service import DiagnosticService


def test_filters_by_service(service: DiagnosticService) -> None:
    matching = service.get_deployment_history("INC-003", "billing-api")
    non_matching = service.get_deployment_history("INC-003", "checkout-api")

    assert [record.source_id for record in matching] == ["deploy-billing-001"]
    assert non_matching == []


def test_optional_time_filtering(service: DiagnosticService) -> None:
    instant = datetime.fromisoformat("2026-01-12T13:40:00+00:00")

    included = service.get_deployment_history(
        "INC-001", "checkout-api", start_time=instant, end_time=instant
    )
    excluded = service.get_deployment_history(
        "INC-001",
        "checkout-api",
        start_time=datetime.fromisoformat("2026-01-12T13:41:00+00:00"),
        end_time=datetime.fromisoformat("2026-01-12T14:00:00+00:00"),
    )

    assert [record.source_id for record in included] == ["deploy-checkout-001"]
    assert excluded == []


def test_chronological_ordering_is_deterministic(ordering_service: DiagnosticService) -> None:
    results = ordering_service.get_deployment_history("TEST-ORDERING", "checkout-api")

    assert [record.source_id for record in results] == ["deploy-a", "deploy-b"]


def test_no_matches_returns_empty_list(service: DiagnosticService) -> None:
    results = service.get_deployment_history("INC-004", "checkout-api")

    assert results == []
