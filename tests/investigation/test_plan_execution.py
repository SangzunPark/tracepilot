"""Week 4 shared static-plan execution (Fixed Playbook and Plan Once)."""

import ast
import asyncio
import inspect
from pathlib import Path

import pytest
from mcp.types import CallToolResult
from pydantic import ValidationError

import tracepilot.investigation.plan_execution as plan_execution_module
from tests.investigation.executor_support import StubClient
from tests.investigation.support import inc015_context
from tracepilot.investigation.executor import ToolExecutionError, ToolExecutor
from tracepilot.investigation.models import (
    BlockReason,
    InvestigationMode,
    InvestigationPlan,
    InvestigationState,
    PlanStep,
    TerminationReason,
    ToolRequest,
)
from tracepilot.investigation.plan_execution import (
    advance_cursor,
    execute_static_plan,
    record_termination,
)

STATIC_MODES = [InvestigationMode.FIXED_PLAYBOOK, InvestigationMode.PLAN_ONCE]
FORBIDDEN_IMPORT_PREFIXES = (
    "tracepilot.investigation.provider",
    "tracepilot.domain",
    "tracepilot.diagnostics",
    "tracepilot.mcp",
    "mcp",
    "langgraph",
    "openai",
    "anthropic",
)


def step(tool: str, **arguments: object) -> PlanStep:
    return PlanStep(request=ToolRequest.model_validate({"tool": tool, "arguments": arguments}))


PLAN = InvestigationPlan(
    steps=(
        step("search_runbooks", query="a"),
        step("search_runbooks", query="a", case_id="INC-002"),
        step("search_runbooks", query="a"),
        step("delete_database"),
        step("search_runbooks", query="b"),
    )
)


def static_state(
    mode: InvestigationMode = InvestigationMode.PLAN_ONCE,
    plan: InvestigationPlan | None = PLAN,
) -> InvestigationState:
    return InvestigationState(
        case_id="INC-015",
        mode=mode,
        initial_context=inc015_context(),
        plan=plan,
        plan_version=0 if plan is None else 1,
    )


def executor(max_steps: int = 15) -> tuple[ToolExecutor, StubClient]:
    client = StubClient(result=CallToolResult(content=[], structured_content={"result": []}))
    return ToolExecutor(client, max_steps=max_steps), client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_advance_cursor_moves_one_step_without_mutating() -> None:
    state = static_state()
    advanced = advance_cursor(state)
    assert (state.current_step_index, advanced.current_step_index) == (0, 1)
    assert advanced.plan == state.plan


def test_advance_cursor_is_validated() -> None:
    at_end = static_state(plan=InvestigationPlan())
    with pytest.raises(ValidationError):
        advance_cursor(at_end)


def test_record_termination_is_final() -> None:
    terminated = record_termination(static_state(), TerminationReason.PLAN_EXHAUSTED)
    assert terminated.termination_reason is TerminationReason.PLAN_EXHAUSTED
    with pytest.raises(RuntimeError, match="already recorded"):
        record_termination(terminated, TerminationReason.BUDGET_EXHAUSTED)


# ---------------------------------------------------------------------------
# execute_static_plan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", STATIC_MODES)
def test_runs_plan_to_plan_exhausted_including_blocked_steps(mode: InvestigationMode) -> None:
    runner, client = executor()

    final = asyncio.run(execute_static_plan(static_state(mode), runner))

    assert [record.block_reason for record in final.tool_calls] == [
        None,
        BlockReason.FORBIDDEN_ARGUMENT,
        BlockReason.DUPLICATE,
        BlockReason.UNKNOWN_TOOL,
        None,
    ]
    assert final.current_step_index == 5
    assert final.termination_reason is TerminationReason.PLAN_EXHAUSTED
    assert len(client.calls) == 2


def test_plan_and_version_are_never_changed() -> None:
    snapshot = PLAN.model_copy(deep=True)

    final = asyncio.run(execute_static_plan(static_state(), executor()[0]))

    assert final.plan == snapshot
    assert PLAN == snapshot
    assert final.plan_version == 1
    assert final.plan_decision_at_step == 0


def test_budget_binds_before_the_plan_ends() -> None:
    runner, client = executor(max_steps=2)

    final = asyncio.run(execute_static_plan(static_state(), runner))

    assert final.step_count == 2
    assert final.current_step_index == 2
    assert final.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert len(client.calls) == 1


def test_plan_ending_at_the_budget_is_plan_exhausted() -> None:
    final = asyncio.run(execute_static_plan(static_state(), executor(max_steps=5)[0]))
    assert final.termination_reason is TerminationReason.PLAN_EXHAUSTED


def test_empty_plan_terminates_without_attempts() -> None:
    runner, client = executor()
    final = asyncio.run(execute_static_plan(static_state(plan=InvestigationPlan()), runner))
    assert (final.step_count, final.termination_reason) == (0, TerminationReason.PLAN_EXHAUSTED)
    assert client.calls == []


def test_tool_execution_error_propagates() -> None:
    client = StubClient(error=OSError("pipe closed"))
    with pytest.raises(ToolExecutionError):
        asyncio.run(execute_static_plan(static_state(), ToolExecutor(client)))
    assert len(client.calls) == 1


def test_rejects_adaptive_missing_plan_and_terminated_state() -> None:
    runner, client = executor()
    terminated = record_termination(static_state(), TerminationReason.PLAN_EXHAUSTED)
    for bad in (
        static_state(InvestigationMode.ADAPTIVE),
        static_state(plan=None),
        terminated,
    ):
        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(execute_static_plan(bad, runner))
    assert client.calls == []


def test_takes_no_provider() -> None:
    assert list(inspect.signature(execute_static_plan).parameters) == ["state", "executor"]


def test_module_has_no_provider_data_or_sdk_dependency() -> None:
    tree = ast.parse(Path(plan_execution_module.__file__).read_text())
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    for module in imported:
        assert not module.startswith(FORBIDDEN_IMPORT_PREFIXES), module
