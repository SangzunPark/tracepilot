"""Category 1 -- DiagnosticService.query_service_metrics.

Uses INC-001's checkout-api metrics (http_5xx_rate, db_connection_pool_usage,
request_latency_p95 -- 9 points total), which happen to include two
same-timestamp collisions (13:50 and 14:00), giving a real-data tie-break
check for the (timestamp, source_id) sort without needing synthetic data.
"""

from datetime import datetime

from tracepilot.diagnostics.service import DiagnosticService


def test_filters_by_case_and_exact_service(service: DiagnosticService) -> None:
    results = service.query_service_metrics("INC-001", "checkout-api")

    assert len(results) == 9
    assert all(record.service == "checkout-api" for record in results)


def test_optional_exact_metric_filter(service: DiagnosticService) -> None:
    results = service.query_service_metrics("INC-001", "checkout-api", metric="http_5xx_rate")

    assert len(results) == 4
    assert all(record.metric == "http_5xx_rate" for record in results)


def test_start_and_end_time_are_inclusive_at_a_single_instant(
    service: DiagnosticService,
) -> None:
    instant = datetime.fromisoformat("2026-01-12T14:00:00+00:00")

    results = service.query_service_metrics(
        "INC-001", "checkout-api", start_time=instant, end_time=instant
    )

    ids = {record.source_id for record in results}
    assert ids == {
        "metric-checkout-5xx-002",
        "metric-checkout-latency-001",
        "metric-db-pool-002",
    }


def test_start_and_end_time_range_is_inclusive_at_both_boundaries(
    service: DiagnosticService,
) -> None:
    start = datetime.fromisoformat("2026-01-12T13:50:00+00:00")
    end = datetime.fromisoformat("2026-01-12T14:00:00+00:00")

    results = service.query_service_metrics(
        "INC-001",
        "checkout-api",
        metric="http_5xx_rate",
        start_time=start,
        end_time=end,
    )

    assert [record.source_id for record in results] == [
        "metric-checkout-5xx-001",
        "metric-checkout-5xx-002",
    ]


def test_results_sorted_by_timestamp_then_source_id(service: DiagnosticService) -> None:
    results = service.query_service_metrics("INC-001", "checkout-api")

    assert [record.source_id for record in results] == [
        "metric-checkout-5xx-001",  # 13:50
        "metric-db-pool-001",  # 13:50 (tie broken by source_id)
        "metric-checkout-5xx-002",  # 14:00
        "metric-checkout-latency-001",  # 14:00 (tie broken by source_id)
        "metric-db-pool-002",  # 14:00 (tie broken by source_id)
        "metric-db-pool-003",  # 14:07
        "metric-checkout-5xx-003",  # 14:10
        "metric-checkout-latency-002",  # 14:10 (tie broken by source_id)
        "metric-checkout-5xx-004",  # 14:15
    ]


def test_no_matching_metric_returns_empty_list(service: DiagnosticService) -> None:
    results = service.query_service_metrics("INC-001", "checkout-api", metric="nonexistent_metric")

    assert results == []
