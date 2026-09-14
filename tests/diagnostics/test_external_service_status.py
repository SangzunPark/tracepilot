"""Category 5 -- DiagnosticService.get_external_service_status.

Uses INC-005 (two payment-gateway status events: a degradation and its
recovery) for filtering and time-range checks, and the synthetic
`ordering_service` fixture for chronological ordering.
"""

from datetime import datetime

from tracepilot.diagnostics.service import DiagnosticService


def test_filters_by_provider(service: DiagnosticService) -> None:
    matching = service.get_external_service_status("INC-005", "payment-gateway")
    non_matching = service.get_external_service_status("INC-005", "postgresql")

    assert [record.source_id for record in matching] == [
        "status-payment-gateway-001",
        "status-payment-gateway-002",
    ]
    assert non_matching == []


def test_optional_time_filtering(service: DiagnosticService) -> None:
    instant = datetime.fromisoformat("2026-02-09T11:14:00+00:00")

    included = service.get_external_service_status(
        "INC-005", "payment-gateway", start_time=instant, end_time=instant
    )
    excluded = service.get_external_service_status(
        "INC-005",
        "payment-gateway",
        start_time=datetime.fromisoformat("2026-02-09T11:15:00+00:00"),
        end_time=datetime.fromisoformat("2026-02-09T11:51:00+00:00"),
    )

    assert [record.source_id for record in included] == ["status-payment-gateway-001"]
    assert excluded == []


def test_chronological_ordering_is_deterministic(ordering_service: DiagnosticService) -> None:
    results = ordering_service.get_external_service_status("TEST-ORDERING", "payment-gateway")

    assert [record.source_id for record in results] == ["status-a", "status-b"]


def test_no_matches_returns_empty_list(service: DiagnosticService) -> None:
    results = service.get_external_service_status("INC-002", "payment-gateway")

    assert results == []
