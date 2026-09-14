"""Category 4 -- DiagnosticService.get_feature_flag_history.

Uses INC-003 (one real flag change) for basic filtering and time-range
checks, and the synthetic `ordering_service` fixture -- which has two
flags sharing a flag_name plus a third with a different one, all written
out of chronological order -- for the flag_name filter and ordering checks.
"""

from datetime import datetime

from tracepilot.diagnostics.service import DiagnosticService


def test_filters_by_service(service: DiagnosticService) -> None:
    matching = service.get_feature_flag_history("INC-003", "checkout-api")
    non_matching = service.get_feature_flag_history("INC-003", "billing-api")

    assert [record.source_id for record in matching] == ["flag-checkout-express-path-001"]
    assert non_matching == []


def test_optional_exact_flag_name_filter(ordering_service: DiagnosticService) -> None:
    checkout_x = ordering_service.get_feature_flag_history(
        "TEST-ORDERING", "checkout-api", flag_name="checkout_x"
    )
    checkout_y = ordering_service.get_feature_flag_history(
        "TEST-ORDERING", "checkout-api", flag_name="checkout_y"
    )
    unknown_flag = ordering_service.get_feature_flag_history(
        "TEST-ORDERING", "checkout-api", flag_name="does_not_exist"
    )

    assert [record.source_id for record in checkout_x] == ["flag-a", "flag-b"]
    assert [record.source_id for record in checkout_y] == ["flag-c"]
    assert unknown_flag == []


def test_optional_time_filtering(service: DiagnosticService) -> None:
    instant = datetime.fromisoformat("2026-01-26T15:20:00+00:00")

    included = service.get_feature_flag_history(
        "INC-003", "checkout-api", start_time=instant, end_time=instant
    )
    excluded = service.get_feature_flag_history(
        "INC-003",
        "checkout-api",
        start_time=datetime.fromisoformat("2026-01-26T15:21:00+00:00"),
        end_time=datetime.fromisoformat("2026-01-26T16:00:00+00:00"),
    )

    assert [record.source_id for record in included] == ["flag-checkout-express-path-001"]
    assert excluded == []


def test_chronological_ordering_is_deterministic(ordering_service: DiagnosticService) -> None:
    results = ordering_service.get_feature_flag_history("TEST-ORDERING", "checkout-api")

    assert [record.source_id for record in results] == ["flag-a", "flag-c", "flag-b"]


def test_no_matches_returns_empty_list(service: DiagnosticService) -> None:
    results = service.get_feature_flag_history("INC-001", "checkout-api")

    assert results == []
