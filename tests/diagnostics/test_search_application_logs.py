"""Category 2 -- DiagnosticService.search_application_logs.

Uses INC-001's 9 checkout-api logs (all distinct timestamps, so limit and
sort order are unambiguous) and INC-004 (which has both authentication-
service and checkout-api logs in the same case) to prove service filtering
actually excludes non-matching records.
"""

from datetime import datetime

import pytest

from tracepilot.diagnostics.service import DiagnosticService


def test_filters_by_exact_service(service: DiagnosticService) -> None:
    auth_results = service.search_application_logs("INC-004", "authentication-service")
    checkout_results = service.search_application_logs("INC-004", "checkout-api")

    assert len(auth_results) == 3
    assert all(record.service == "authentication-service" for record in auth_results)

    assert len(checkout_results) == 1
    assert checkout_results[0].source_id == "log-checkout-auth-dependency-001"


def test_case_insensitive_substring_matching(service: DiagnosticService) -> None:
    results = service.search_application_logs("INC-001", "checkout-api", query="TIMED OUT")

    assert [record.source_id for record in results] == ["log-checkout-db-timeout-001"]


def test_optional_time_filtering(service: DiagnosticService) -> None:
    start = datetime.fromisoformat("2026-01-12T14:06:00+00:00")
    end = datetime.fromisoformat("2026-01-12T14:10:00+00:00")

    results = service.search_application_logs(
        "INC-001", "checkout-api", start_time=start, end_time=end
    )

    assert [record.source_id for record in results] == [
        "log-checkout-slow-001",
        "log-checkout-db-timeout-001",
        "log-checkout-500-001",
    ]


def test_results_are_sorted_before_limit_is_applied(service: DiagnosticService) -> None:
    results = service.search_application_logs("INC-001", "checkout-api", limit=3)

    assert [record.source_id for record in results] == [
        "log-checkout-normal-001",  # 13:42
        "log-checkout-normal-002",  # 13:47
        "log-checkout-normal-003",  # 13:55
    ]


def test_limit_works(service: DiagnosticService) -> None:
    results = service.search_application_logs("INC-001", "checkout-api", limit=1)

    assert len(results) == 1
    assert results[0].source_id == "log-checkout-normal-001"


def test_limit_less_than_one_raises_value_error(service: DiagnosticService) -> None:
    with pytest.raises(ValueError):
        service.search_application_logs("INC-001", "checkout-api", limit=0)


def test_no_matches_returns_empty_list(service: DiagnosticService) -> None:
    results = service.search_application_logs(
        "INC-001", "checkout-api", query="this-substring-does-not-exist-anywhere"
    )

    assert results == []
