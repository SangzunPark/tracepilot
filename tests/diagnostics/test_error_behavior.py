"""Category 7 -- validation error behavior shared across DiagnosticService
methods."""

from datetime import datetime

import pytest

from tracepilot.diagnostics.errors import (
    CaseNotFoundError,
    InvalidTimeRangeError,
    UnknownServiceError,
)
from tracepilot.diagnostics.service import DiagnosticService


def test_unknown_case_raises_case_not_found_error(service: DiagnosticService) -> None:
    with pytest.raises(CaseNotFoundError):
        service.query_service_metrics("INC-DOES-NOT-EXIST", "checkout-api")


def test_path_traversal_case_id_raises_case_not_found_error(
    service: DiagnosticService,
) -> None:
    with pytest.raises(CaseNotFoundError):
        service.query_service_metrics("../ground_truth", "checkout-api")


def test_unknown_case_raises_for_log_search_too(service: DiagnosticService) -> None:
    with pytest.raises(CaseNotFoundError):
        service.search_application_logs("INC-DOES-NOT-EXIST", "checkout-api")


def test_unknown_service_raises_unknown_service_error(service: DiagnosticService) -> None:
    with pytest.raises(UnknownServiceError):
        service.query_service_metrics("INC-001", "not-a-real-service")


def test_unknown_provider_raises_unknown_service_error(service: DiagnosticService) -> None:
    with pytest.raises(UnknownServiceError):
        service.get_external_service_status("INC-005", "not-a-real-provider")


def test_search_runbooks_unknown_service_raises_unknown_service_error(
    service: DiagnosticService,
) -> None:
    with pytest.raises(UnknownServiceError):
        service.search_runbooks("database", service="not-a-real-service")


def test_start_after_end_raises_invalid_time_range_error(service: DiagnosticService) -> None:
    start = datetime.fromisoformat("2026-01-12T15:00:00+00:00")
    end = datetime.fromisoformat("2026-01-12T14:00:00+00:00")

    with pytest.raises(InvalidTimeRangeError):
        service.query_service_metrics("INC-001", "checkout-api", start_time=start, end_time=end)


def test_start_equal_end_is_valid(service: DiagnosticService) -> None:
    instant = datetime.fromisoformat("2026-01-12T14:00:00+00:00")

    results = service.query_service_metrics(
        "INC-001", "checkout-api", start_time=instant, end_time=instant
    )

    assert results != []


def test_naive_datetime_is_interpreted_as_utc(
    service: DiagnosticService,
) -> None:
    naive_instant = datetime.fromisoformat("2026-01-12T14:00:00")
    utc_instant = datetime.fromisoformat("2026-01-12T14:00:00+00:00")

    naive_results = service.query_service_metrics(
        "INC-001",
        "checkout-api",
        metric="db_connection_pool_usage",
        start_time=naive_instant,
        end_time=naive_instant,
    )

    utc_results = service.query_service_metrics(
        "INC-001",
        "checkout-api",
        metric="db_connection_pool_usage",
        start_time=utc_instant,
        end_time=utc_instant,
    )

    assert naive_results != []
    assert naive_results == utc_results
