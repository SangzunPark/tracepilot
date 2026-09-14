"""Category 8 -- running the same diagnostic query twice returns equal
ordered results."""

from tracepilot.diagnostics.service import DiagnosticService


def test_query_service_metrics_is_deterministic(service: DiagnosticService) -> None:
    first = service.query_service_metrics("INC-001", "checkout-api")
    second = service.query_service_metrics("INC-001", "checkout-api")

    assert first == second


def test_search_application_logs_is_deterministic(service: DiagnosticService) -> None:
    first = service.search_application_logs("INC-001", "checkout-api")
    second = service.search_application_logs("INC-001", "checkout-api")

    assert first == second


def test_get_deployment_history_is_deterministic(service: DiagnosticService) -> None:
    first = service.get_deployment_history("INC-001", "checkout-api")
    second = service.get_deployment_history("INC-001", "checkout-api")

    assert first == second


def test_get_feature_flag_history_is_deterministic(service: DiagnosticService) -> None:
    first = service.get_feature_flag_history("INC-003", "checkout-api")
    second = service.get_feature_flag_history("INC-003", "checkout-api")

    assert first == second


def test_get_external_service_status_is_deterministic(service: DiagnosticService) -> None:
    first = service.get_external_service_status("INC-005", "payment-gateway")
    second = service.get_external_service_status("INC-005", "payment-gateway")

    assert first == second


def test_search_runbooks_is_deterministic(service: DiagnosticService) -> None:
    first = service.search_runbooks("database")
    second = service.search_runbooks("database")

    assert first == second
