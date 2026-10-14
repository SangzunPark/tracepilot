"""Human-approved plan-cursor, plan-review, and termination contract for the
mode loops, expressed as state snapshots checked against the pure helpers.

No Adaptive loop is implemented here. Each snapshot is the state a loop must
produce after a transition; the helpers say what may happen next. The KEEP and
REPLACE transitions below are test-local stand-ins for the future runner.

- A plan step is consumed once attempted, including when policy blocks it, so
  a blocked step advances current_step_index to the next existing step.
- No mode replaces a blocked step with an ad-hoc action.
- Adaptive: every fresh executed Observation, while budget remains, gets
  exactly one plan review (KEEP or REPLACE) before the runner proceeds or lets
  plan exhaustion become terminal. A blocked attempt creates no review.
- Both KEEP and REPLACE set plan_decision_at_step = step_count, so a review
  can never trigger another review from the same Observation.
- With the budget exhausted, BUDGET_EXHAUSTED comes before any review.
"""

import pytest

from tests.investigation.support import blocked, executed, plan, state
from tracepilot.investigation.models import (
    InvestigationMode,
    InvestigationPlan,
    InvestigationState,
    TerminationReason,
    ToolCallRecord,
)
from tracepilot.investigation.policies import (
    is_plan_exhausted,
    plan_review_pending,
    structural_termination,
)

NON_ADAPTIVE_MODES = [InvestigationMode.FIXED_PLAYBOOK, InvestigationMode.PLAN_ONCE]
TWO_STEPS = plan("search_application_logs", "get_deployment_history")
ONE_STEP = plan("search_application_logs")


def _review_due(current: InvestigationState) -> bool:
    """The loop's routing predicate: review the plan before anything else."""
    return structural_termination(current) is None and plan_review_pending(current)


def _keep(current: InvestigationState, hypothesis: str | None = "kept") -> InvestigationState:
    # KEEP: plan, version, and cursor unchanged; hypothesis/questions replaced.
    return InvestigationState(
        **{
            **dict(current),
            "working_hypothesis": hypothesis,
            "open_questions": ("q",),
            "plan_decision_at_step": current.step_count,
        }
    )


def _replace(current: InvestigationState, new_plan: InvestigationPlan) -> InvestigationState:
    # REPLACE: future-actions plan from cursor 0, next version.
    return InvestigationState(
        **{
            **dict(current),
            "plan": new_plan,
            "plan_version": current.plan_version + 1,
            "current_step_index": 0,
            "plan_decision_at_step": current.step_count,
            "working_hypothesis": "replaced",
            "open_questions": (),
        }
    )


# ---------------------------------------------------------------------------
# Fixed Playbook and Plan Once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", NON_ADAPTIVE_MODES)
def test_blocked_step_advances_to_next_existing_step(mode: InvestigationMode) -> None:
    after_block = state(
        mode, records=(blocked(1),), investigation_plan=TWO_STEPS, current_step_index=1
    )

    assert not is_plan_exhausted(after_block)
    assert structural_termination(after_block) is None
    assert not plan_review_pending(after_block)


@pytest.mark.parametrize("mode", NON_ADAPTIVE_MODES)
def test_plan_ends_normally_after_last_step(mode: InvestigationMode) -> None:
    done = state(
        mode,
        records=(blocked(1), executed(2)),
        investigation_plan=TWO_STEPS,
        current_step_index=2,
    )
    assert not plan_review_pending(done)
    assert structural_termination(done) is TerminationReason.PLAN_EXHAUSTED


@pytest.mark.parametrize("mode", NON_ADAPTIVE_MODES)
def test_blocked_last_step_exhausts_the_plan(mode: InvestigationMode) -> None:
    done = state(mode, records=(blocked(1),), investigation_plan=ONE_STEP, current_step_index=1)
    assert structural_termination(done) is TerminationReason.PLAN_EXHAUSTED


@pytest.mark.parametrize("mode", NON_ADAPTIVE_MODES)
def test_non_adaptive_modes_keep_decision_marker_at_zero(mode: InvestigationMode) -> None:
    with pytest.raises(ValueError):
        state(mode, records=(executed(1),), plan_decision_at_step=1)


# ---------------------------------------------------------------------------
# Adaptive: when a review is due
# ---------------------------------------------------------------------------


def test_fresh_observation_mid_plan_requires_a_review() -> None:
    mid_plan = state(records=(executed(1),), investigation_plan=TWO_STEPS, current_step_index=1)

    assert not is_plan_exhausted(mid_plan)
    assert _review_due(mid_plan)


def test_fresh_observation_with_exhausted_plan_requires_a_review_before_termination() -> None:
    exhausted = state(records=(executed(1),), investigation_plan=ONE_STEP, current_step_index=1)

    assert is_plan_exhausted(exhausted)
    assert structural_termination(exhausted) is None
    assert _review_due(exhausted)


def test_executed_error_observation_requires_a_review() -> None:
    errored = state(
        records=(executed(1, error=True),), investigation_plan=TWO_STEPS, current_step_index=1
    )
    assert _review_due(errored)


def test_blocked_step_creates_no_review() -> None:
    after_block = state(records=(blocked(1),), investigation_plan=TWO_STEPS, current_step_index=1)

    assert not plan_review_pending(after_block)
    assert not _review_due(after_block)
    assert structural_termination(after_block) is None


def test_blocked_final_step_exhausts_without_review() -> None:
    done = state(records=(blocked(1),), investigation_plan=ONE_STEP, current_step_index=1)

    assert not _review_due(done)
    assert structural_termination(done) is TerminationReason.PLAN_EXHAUSTED


def test_budget_exhausted_comes_before_any_review() -> None:
    records = tuple(executed(step, fingerprint=f"{step:064x}") for step in range(1, 16))
    for chosen_plan in (ONE_STEP, TWO_STEPS):
        current = state(records=records, investigation_plan=chosen_plan, current_step_index=1)
        assert plan_review_pending(current)
        assert not _review_due(current)
        assert structural_termination(current) is TerminationReason.BUDGET_EXHAUSTED


# ---------------------------------------------------------------------------
# Adaptive: after KEEP
# ---------------------------------------------------------------------------


def test_keep_mid_plan_consumes_the_review_and_continues_the_same_plan() -> None:
    before = state(records=(executed(1),), investigation_plan=TWO_STEPS, current_step_index=1)

    kept = _keep(before)

    assert (kept.plan, kept.plan_version, kept.current_step_index) == (TWO_STEPS, 1, 1)
    assert kept.plan_decision_at_step == 1
    assert not _review_due(kept)
    assert structural_termination(kept) is None  # next event: the plan's next action


def test_keep_with_exhausted_plan_is_valid_and_ends_plan_exhausted() -> None:
    before = state(records=(executed(1),), investigation_plan=ONE_STEP, current_step_index=1)

    kept = _keep(before)

    assert kept.plan == ONE_STEP
    assert not _review_due(kept)
    assert structural_termination(kept) is TerminationReason.PLAN_EXHAUSTED


def test_keep_cannot_trigger_another_review_from_the_same_observation() -> None:
    # Observation -> KEEP -> review again -> KEEP ... must be impossible.
    kept = _keep(state(records=(executed(1),), investigation_plan=TWO_STEPS, current_step_index=1))
    for _ in range(3):
        assert not plan_review_pending(kept)
        assert not _review_due(kept)


# ---------------------------------------------------------------------------
# Adaptive: after REPLACE
# ---------------------------------------------------------------------------


def test_replace_with_nonempty_plan_resumes_actions() -> None:
    before = state(records=(executed(1),), investigation_plan=ONE_STEP, current_step_index=1)

    replaced = _replace(before, plan("get_deployment_history"))

    assert (replaced.plan_version, replaced.current_step_index) == (2, 0)
    assert replaced.plan_decision_at_step == 1
    assert not _review_due(replaced)
    assert structural_termination(replaced) is None


def test_replace_with_empty_plan_ends_plan_exhausted() -> None:
    before = state(records=(executed(1),), investigation_plan=TWO_STEPS, current_step_index=1)
    replaced = _replace(before, InvestigationPlan())
    assert structural_termination(replaced) is TerminationReason.PLAN_EXHAUSTED


def test_blocked_step_after_replace_does_not_reopen_review() -> None:
    done = state(
        records=(executed(1), blocked(2)),
        investigation_plan=ONE_STEP,
        plan_version=2,
        plan_decision_at_step=1,
        current_step_index=1,
    )

    assert not _review_due(done)
    assert structural_termination(done) is TerminationReason.PLAN_EXHAUSTED


def test_new_executed_observation_after_a_decision_opens_exactly_one_review() -> None:
    reviewed = state(
        records=(executed(1), blocked(2), executed(3, fingerprint="0" * 64)),
        investigation_plan=TWO_STEPS,
        plan_decision_at_step=1,
        current_step_index=2,
    )
    assert _review_due(reviewed)
    assert not _review_due(_keep(reviewed))


# ---------------------------------------------------------------------------
# Termination invariant: a contract-level simulation of the future loop
# ---------------------------------------------------------------------------


def _simulate(
    outcomes: list[bool], plan_length: int, decide: str, max_steps: int = 15
) -> tuple[TerminationReason, list[str]]:
    """Drive the contract with scripted action outcomes (True = executed,
    False = blocked) and a fixed KEEP/REPLACE policy. Returns the termination
    reason and the event sequence ("executed", "blocked", "review")."""
    current = InvestigationState(
        case_id="INC-001",
        mode=InvestigationMode.ADAPTIVE,
        initial_context=state().initial_context,
        plan=plan(*["search_runbooks"] * plan_length),
        plan_version=1,
    )
    events: list[str] = []
    for _ in range(4 * max_steps + 4):  # generous bound; the loop must end well before
        reason = structural_termination(current, max_steps)
        if reason is not None:
            return reason, events
        if plan_review_pending(current):
            events.append("review")
            current = (
                _keep(current)
                if decide == "keep"
                else _replace(current, plan(*["search_runbooks"] * plan_length))
            )
            continue
        step = current.step_count + 1
        succeeded = outcomes[(step - 1) % len(outcomes)]
        record: ToolCallRecord = (
            executed(step, fingerprint=f"{step:064x}") if succeeded else blocked(step)
        )
        events.append("executed" if succeeded else "blocked")
        current = InvestigationState(
            **{
                **dict(current),
                "tool_calls": (*current.tool_calls, record),
                "current_step_index": current.current_step_index + 1,
            }
        )
    raise AssertionError("contract loop did not terminate")


@pytest.mark.parametrize("decide", ["keep", "replace"])
@pytest.mark.parametrize(
    "outcomes", [[True], [False], [True, False], [False, False, True]], ids=str
)
@pytest.mark.parametrize("plan_length", [0, 1, 3])
def test_loop_always_terminates_with_one_review_per_executed_observation(
    decide: str, outcomes: list[bool], plan_length: int
) -> None:
    reason, events = _simulate(outcomes, plan_length, decide)
    actions = [event for event in events if event != "review"]
    executed_count = events.count("executed")
    reviews = events.count("review")

    # Every action attempt is one step; the shared budget bounds them.
    assert len(actions) <= 15
    # Each executed Observation gets exactly one review, except an action that
    # exhausts the budget; blocked attempts get none.
    final_executed_unreviewed = bool(events) and events[-1] == "executed"
    assert reviews == executed_count - (1 if final_executed_unreviewed else 0)
    if final_executed_unreviewed:
        assert reason is TerminationReason.BUDGET_EXHAUSTED
    # A review is always followed by an action or termination, never a review,
    # and only ever follows an executed action.
    for previous, following in zip(events, events[1:], strict=False):
        assert not (previous == "review" and following == "review")
        if following == "review":
            assert previous == "executed"
    if decide == "keep" or plan_length == 0:
        assert reason is TerminationReason.PLAN_EXHAUSTED or len(actions) == 15
