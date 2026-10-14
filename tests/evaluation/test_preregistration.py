"""Evaluation preregistration v1: strict loading, exact case coverage, the
human-approved context-construction rule, fairness across modes, drift
detection, and the answer-key boundary.

Test code may read the frozen manifests and ground truth to prove properties
of the checked-in file; the production loader reads only the YAML it is given.
"""

import ast
import asyncio
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from mcp.types import CallToolResult

import tracepilot.evaluation.preregistration as preregistration_module
from tests.domain.support import EVALUATION_ROOT
from tests.investigation.executor_support import StubClient
from tests.investigation.scripted_provider import ScriptedProvider
from tracepilot.evaluation.preregistration import (
    PREREGISTRATION_ID,
    Preregistration,
    PreregistrationError,
    UnknownPreregisteredCaseError,
    load_preregistration,
    parse_strict_yaml,
)
from tracepilot.investigation.executor import DEFAULT_TOOL_TIMEOUT_SECONDS, ToolExecutor
from tracepilot.investigation.graph import run_investigation
from tracepilot.investigation.models import (
    InitialInvestigationContext,
    InvestigationMode,
    InvestigationPlan,
    ToolTimeConstraint,
)
from tracepilot.investigation.policies import MAX_AGENT_STEPS
from tracepilot.investigation.provider import IncidentBrief, SynthesisProposal

PREREGISTRATION_PATH = EVALUATION_ROOT / "preregistration" / "eval-preregistration-v1.yaml"
CASES_ROOT = EVALUATION_ROOT / "cases"
GROUND_TRUTH_ROOT = EVALUATION_ROOT / "ground_truth"
QUESTION_TEMPLATE = "What explains the observed incident symptoms: {symptom}"
INC015_QUESTION = "What explains the billing-api slowdown and invoice failures?"


def frozen_case_ids() -> list[str]:
    return sorted(path.name for path in CASES_ROOT.iterdir() if (path / "incident.json").is_file())


def manifest(case_id: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CASES_ROOT / case_id / "incident.json").read_text())
    return data


def load(path: Path = PREREGISTRATION_PATH, expected: list[str] | None = None) -> Preregistration:
    return load_preregistration(
        path, expected_case_ids=frozen_case_ids() if expected is None else expected
    )


def raw() -> dict[str, Any]:
    data = yaml.safe_load(PREREGISTRATION_PATH.read_text())
    assert isinstance(data, dict)
    return data


def write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "preregistration.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def write_text(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "preregistration.yaml"
    path.write_text(text)
    return path


def case_entry(data: dict[str, Any], case_id: str) -> dict[str, Any]:
    [entry] = [case for case in data["cases"] if case["case_id"] == case_id]
    assert isinstance(entry, dict)
    return entry


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_v1_loads_with_identifier_and_schema_version() -> None:
    preregistration = load()
    assert preregistration.preregistration == PREREGISTRATION_ID == "eval-preregistration-v1"
    assert preregistration.schema_version == 1
    assert preregistration.shared.max_agent_steps == 15
    assert preregistration.shared.tool_timeout_seconds == 10.0


def test_loading_is_deterministic() -> None:
    assert load() == load()


def test_context_for_returns_the_initial_context_model() -> None:
    preregistration = load()
    for case_id in preregistration.case_ids:
        assert isinstance(preregistration.context_for(case_id), InitialInvestigationContext)


def test_unknown_case_has_no_context() -> None:
    with pytest.raises(UnknownPreregisteredCaseError, match="INC-999"):
        load().context_for("INC-999")


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_exactly_the_twenty_frozen_cases_in_order() -> None:
    preregistration = load()
    assert len(frozen_case_ids()) == 20
    assert preregistration.case_ids == tuple(frozen_case_ids())
    assert preregistration.case_ids == tuple(f"INC-{n:03d}" for n in range(1, 21))


def test_missing_case_is_rejected(tmp_path: Path) -> None:
    data = raw()
    data["cases"] = [case for case in data["cases"] if case["case_id"] != "INC-007"]
    with pytest.raises(PreregistrationError, match=r"missing \['INC-007'\]"):
        load(write(tmp_path, data))


def test_extra_case_is_rejected(tmp_path: Path) -> None:
    data = raw()
    extra = dict(copy.deepcopy(case_entry(data, "INC-001")), case_id="INC-021")
    data["cases"].append(extra)
    with pytest.raises(PreregistrationError, match=r"unexpected \['INC-021'\]"):
        load(write(tmp_path, data))


def test_unknown_expected_case_is_rejected() -> None:
    with pytest.raises(PreregistrationError, match=r"missing \['INC-099'\]"):
        load(expected=[*frozen_case_ids(), "INC-099"])


def test_duplicate_case_id_entry_is_rejected(tmp_path: Path) -> None:
    data = raw()
    data["cases"].append(copy.deepcopy(case_entry(data, "INC-003")))
    with pytest.raises(PreregistrationError, match="duplicate case_id"):
        load(write(tmp_path, data))


def test_duplicate_yaml_mapping_key_is_rejected(tmp_path: Path) -> None:
    text = PREREGISTRATION_PATH.read_text().replace(
        "schema_version: 1\n", "schema_version: 1\nschema_version: 1\n", 1
    )
    with pytest.raises(PreregistrationError, match="duplicate mapping key"):
        load(write_text(tmp_path, text))


def test_yaml_anchor_and_alias_are_rejected(tmp_path: Path) -> None:
    text = PREREGISTRATION_PATH.read_text().replace("shared:\n", "shared: &shared\n", 1)
    with pytest.raises(PreregistrationError, match="anchors and aliases"):
        load(write_text(tmp_path, text))
    with pytest.raises(PreregistrationError, match="anchors and aliases"):
        parse_strict_yaml("a: &x [1]\nb: *x\n")


@pytest.mark.parametrize(
    "text",
    ["a: [1, 2\n", "a: b: c\n", "<<: {a: 1}\n", "a: !!python/object:os.system x\n"],
    ids=["unclosed", "bad-mapping", "merge-key", "unsafe-tag"],
)
def test_malformed_or_unsafe_yaml_is_rejected(text: str) -> None:
    with pytest.raises(PreregistrationError):
        parse_strict_yaml(text)


def test_timestamps_stay_strings() -> None:
    assert parse_strict_yaml("t: 2026-04-06T13:50:00Z") == {"t": "2026-04-06T13:50:00Z"}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(notes="x"),
        lambda data: data["shared"].update(max_retries=0),
        lambda data: data["cases"][0].update(notes="x"),
        lambda data: data["cases"][0]["initial_context"].update(time_window="x"),
        lambda data: data["cases"][0].update(mode="adaptive"),
        lambda data: data.update(adaptive={"initial_context": {}}),
        lambda data: data.update(schema_version=2),
        lambda data: data.update(preregistration="eval-preregistration-v2"),
    ],
    ids=[
        "top-level",
        "shared",
        "case",
        "initial-context",
        "case-mode",
        "mode-section",
        "schema-version",
        "identifier",
    ],
)
def test_unknown_or_wrong_fields_are_rejected(tmp_path: Path, mutate: Any) -> None:
    data = raw()
    mutate(data)
    with pytest.raises(PreregistrationError):
        load(write(tmp_path, data))


# ---------------------------------------------------------------------------
# Validation reuse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [
        {"focus_services": ["no-such-service"]},
        {"focus_services": ["checkout-api", "checkout-api"]},
        {"focus_services": []},
        {"initial_question": "   "},
    ],
    ids=["unknown-service", "duplicate-service", "no-service", "blank-question"],
)
def test_initial_context_validation_is_reused(tmp_path: Path, update: dict[str, Any]) -> None:
    data = raw()
    case_entry(data, "INC-001")["initial_context"].update(update)
    with pytest.raises(PreregistrationError, match="INC-001|cases"):
        load(write(tmp_path, data))


@pytest.mark.parametrize(
    "start_time",
    ["2026-04-06T13:50:00", "2026-04-06 25:00", "yesterday"],
    ids=["naive", "malformed", "not-a-datetime"],
)
def test_bad_constraint_datetimes_are_rejected(tmp_path: Path, start_time: str) -> None:
    data = raw()
    case_entry(data, "INC-013")["initial_context"]["tool_constraints"][0]["start_time"] = start_time
    with pytest.raises(PreregistrationError):
        load(write(tmp_path, data))


@pytest.mark.parametrize(
    "constraint",
    [
        {"tool": "search_runbooks"},
        {"tool": "get_deployment_history", "service": "billing-api"},
    ],
    ids=["runbook-constraint", "unmatched-constraint"],
)
def test_fixed_playbook_validation_catches_bad_constraints(
    tmp_path: Path, constraint: dict[str, Any]
) -> None:
    data = raw()
    case_entry(data, "INC-001")["initial_context"]["tool_constraints"] = [
        {**constraint, "start_time": "2026-01-12T14:00:00Z", "end_time": "2026-01-12T14:30:00Z"}
    ]
    with pytest.raises(PreregistrationError, match="INC-001: initial context is invalid"):
        load(write(tmp_path, data))


def test_every_v1_case_builds_a_fixed_playbook() -> None:
    from tracepilot.investigation.playbook import build_fixed_playbook

    preregistration = load()
    for case_id in preregistration.case_ids:
        assert build_fixed_playbook(preregistration.context_for(case_id)).steps


# ---------------------------------------------------------------------------
# The human-approved construction rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", [f"INC-{n:03d}" for n in range(1, 21) if n != 15])
def test_default_rule_uses_the_frozen_manifest(case_id: str) -> None:
    context = load().context_for(case_id)
    frozen = manifest(case_id)
    assert list(context.focus_services) == frozen["affected_services"]
    assert context.initial_question == QUESTION_TEMPLATE.format(symptom=frozen["symptom"])


def test_inc015_keeps_its_approved_context() -> None:
    context = load().context_for("INC-015")
    assert context.focus_services == ("billing-api",)
    assert context.initial_question == INC015_QUESTION
    assert context.tool_constraints == ()


def test_inc013_has_exactly_one_checkout_deployment_constraint() -> None:
    context = load().context_for("INC-013")
    assert context.tool_constraints == (
        ToolTimeConstraint(
            tool="get_deployment_history",
            service="checkout-api",
            start_time=datetime(2026, 4, 6, 13, 50, tzinfo=UTC),
            end_time=datetime(2026, 4, 6, 14, 15, tzinfo=UTC),
        ),
    )
    [constraint] = context.tool_constraints
    assert constraint.start_time.utcoffset() == constraint.end_time.utcoffset()
    assert constraint.start_time.tzinfo is not None


def test_no_other_case_has_tool_constraints() -> None:
    preregistration = load()
    constrained = [
        case_id
        for case_id in preregistration.case_ids
        if preregistration.context_for(case_id).tool_constraints
    ]
    assert constrained == ["INC-013"]


# ---------------------------------------------------------------------------
# Fairness
# ---------------------------------------------------------------------------


def test_yaml_has_no_mode_specific_keys() -> None:
    mode_words = {mode.value for mode in InvestigationMode} | {"mode", "modes", "fixed"}

    def keys(data: object) -> set[str]:
        if isinstance(data, dict):
            return set(map(str, data)) | {k for value in data.values() for k in keys(value)}
        if isinstance(data, list):
            return {k for item in data for k in keys(item)}
        return set()

    assert not keys(raw()) & mode_words


def test_context_for_takes_no_mode() -> None:
    import inspect

    assert list(inspect.signature(Preregistration.context_for).parameters) == ["self", "case_id"]


@pytest.mark.parametrize("case_id", ["INC-013", "INC-015", "INC-004"])
def test_every_mode_receives_the_same_preregistered_context(case_id: str) -> None:
    context = load().context_for(case_id)
    frozen = manifest(case_id)
    incident = IncidentBrief(
        started_at=frozen["started_at"],
        affected_services=tuple(frozen["affected_services"]),
        symptom=frozen["symptom"],
    )
    synthesis = SynthesisProposal(root_cause_claim="c", cited_evidence_ids=(), explanation="e")
    client = StubClient(result=CallToolResult(content=[], structured_content={"result": []}))

    for mode in InvestigationMode:
        provider = ScriptedProvider(initial_plans=[InvestigationPlan()], syntheses=[synthesis])
        result = asyncio.run(
            run_investigation(
                case_id=case_id,
                mode=mode,
                incident=incident,
                context=context,
                executor=ToolExecutor(client),
                provider=provider,
            )
        )
        assert result.state.initial_context == context
        for _, view in provider.views:
            assert view.initial_context == context


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


def test_budget_drift_is_rejected(tmp_path: Path) -> None:
    data = raw()
    data["shared"]["max_agent_steps"] = MAX_AGENT_STEPS + 1
    with pytest.raises(PreregistrationError, match="max_agent_steps"):
        load(write(tmp_path, data))


def test_timeout_drift_is_rejected(tmp_path: Path) -> None:
    data = raw()
    data["shared"]["tool_timeout_seconds"] = DEFAULT_TOOL_TIMEOUT_SECONDS * 2
    with pytest.raises(PreregistrationError, match="tool_timeout_seconds"):
        load(write(tmp_path, data))


# ---------------------------------------------------------------------------
# Answer-key boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "root_cause",
        "category",
        "required_evidence_ids",
        "temporal_evidence_ids",
        "mechanistic_evidence_ids",
        "acceptable_evidence_sets",
        "leakage_aliases",
        "expected_tools",
        "answer",
        "distractors",
    ],
)
def test_prohibited_answer_key_fields_are_rejected(tmp_path: Path, key: str) -> None:
    data = raw()
    case_entry(data, "INC-002")[key] = "x"
    with pytest.raises(PreregistrationError, match="prohibited answer-key field"):
        load(write(tmp_path, data))


def test_free_text_resembling_answer_key_words_is_allowed(tmp_path: Path) -> None:
    data = raw()
    case_entry(data, "INC-002")["initial_context"]["initial_question"] = (
        "Is the root cause category an expected answer or a distractor?"
    )
    assert load(write(tmp_path, data)).context_for("INC-002").initial_question.startswith("Is")


def test_checked_in_yaml_holds_no_answer_key_fields() -> None:
    allowed = {
        "preregistration",
        "schema_version",
        "shared",
        "max_agent_steps",
        "tool_timeout_seconds",
        "cases",
        "case_id",
        "initial_context",
        "focus_services",
        "initial_question",
        "tool_constraints",
        "tool",
        "service",
        "start_time",
        "end_time",
    }

    def keys(data: object) -> set[str]:
        if isinstance(data, dict):
            return set(map(str, data)) | {k for value in data.values() for k in keys(value)}
        if isinstance(data, list):
            return {k for item in data for k in keys(item)}
        return set()

    assert keys(raw()) <= allowed


def test_ground_truth_aliases_do_not_appear_in_preregistered_text() -> None:
    # Evaluation-layer test: reading ground truth here is allowed.
    preregistration = load()
    for case_id in preregistration.case_ids:
        truth = json.loads((GROUND_TRUTH_ROOT / f"{case_id}.json").read_text())
        question = preregistration.context_for(case_id).initial_question.casefold()
        for alias in truth.get("leakage_aliases", []):
            assert alias.casefold() not in question, (case_id, alias)
        assert truth["root_cause"].casefold() not in question


def test_loader_has_no_ground_truth_or_case_data_path() -> None:
    tree = ast.parse(Path(preregistration_module.__file__).read_text())
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    for forbidden in ("tracepilot.domain", "tracepilot.diagnostics", "tracepilot.mcp"):
        assert not any(module.startswith(forbidden) for module in imported)
    assert not {"GroundTruth", "CaseRepository", "DEFAULT_EVALUATION_ROOT"} & names
    assert not any("ground_truth" in literal or "cases/" in literal for literal in literals)
