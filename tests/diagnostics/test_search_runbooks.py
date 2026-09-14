"""Category 6 -- DiagnosticService.search_runbooks.

Uses the real shared runbook (evaluation/runbooks/database.json) to confirm
the method needs no case context at all, and the synthetic `runbook_service`
fixture (three runbooks, one query term that only appears in a title, one
that only appears in a different runbook's content) for the checks the
single real runbook can't cleanly isolate on its own.
"""

import inspect

import pytest

from tracepilot.diagnostics.service import DiagnosticService


def test_requires_no_case_id(service: DiagnosticService) -> None:
    assert "case_id" not in inspect.signature(service.search_runbooks).parameters

    results = service.search_runbooks("database")
    assert any(record.source_id == "runbook-db-pool-001" for record in results)


def test_query_matches_case_insensitively_in_title(runbook_service: DiagnosticService) -> None:
    results = runbook_service.search_runbooks("zephyr")

    assert [record.source_id for record in results] == ["runbook-alpha-001"]


def test_query_matches_case_insensitively_in_content(runbook_service: DiagnosticService) -> None:
    results = runbook_service.search_runbooks("QUEUE DEPTH")

    assert [record.source_id for record in results] == ["runbook-beta-002"]


def test_optional_service_filtering(runbook_service: DiagnosticService) -> None:
    results = runbook_service.search_runbooks("", service="billing-api")

    assert [record.source_id for record in results] == ["runbook-beta-002"]


def test_empty_query_returns_otherwise_eligible_runbooks(
    runbook_service: DiagnosticService,
) -> None:
    results = runbook_service.search_runbooks("")

    assert {record.source_id for record in results} == {
        "runbook-alpha-001",
        "runbook-beta-002",
        "runbook-gamma-003",
    }


def test_results_sorted_deterministically_by_source_id(
    runbook_service: DiagnosticService,
) -> None:
    results = runbook_service.search_runbooks("")

    assert [record.source_id for record in results] == [
        "runbook-alpha-001",
        "runbook-beta-002",
        "runbook-gamma-003",
    ]


def test_limit_works(runbook_service: DiagnosticService) -> None:
    results = runbook_service.search_runbooks("", limit=2)

    assert [record.source_id for record in results] == [
        "runbook-alpha-001",
        "runbook-beta-002",
    ]


def test_limit_less_than_one_raises_value_error(runbook_service: DiagnosticService) -> None:
    with pytest.raises(ValueError):
        runbook_service.search_runbooks("", limit=0)


def test_no_matches_returns_empty_list(runbook_service: DiagnosticService) -> None:
    results = runbook_service.search_runbooks("no-such-term-anywhere")

    assert results == []
