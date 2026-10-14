"""Evaluation preregistration: the runtime-visible initial conditions shared by
Fixed Playbook, Plan Once, and Adaptive, loaded from a versioned YAML file.

The preregistration holds one InitialInvestigationContext per frozen case and
the shared budget/timeout values. It holds no answer key: the strict schema
rejects any field it does not define, and a defensive check rejects answer-key
field names outright. This module reads only the YAML file it is given; it
never discovers or reads incident cases or evaluation answers.

Budget and timeout stay code-authoritative (MAX_AGENT_STEPS,
DEFAULT_TOOL_TIMEOUT_SECONDS). The YAML records the preregistered values and
loading fails if they drift from the code. The tool set is not duplicated
here; ALLOWED_TOOLS and verify_tool_set enforce it at runtime.
"""

from collections.abc import Collection, Hashable, Mapping
from pathlib import Path
from typing import Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tracepilot.investigation.executor import DEFAULT_TOOL_TIMEOUT_SECONDS
from tracepilot.investigation.models import InitialInvestigationContext
from tracepilot.investigation.playbook import PlaybookConfigurationError, build_fixed_playbook
from tracepilot.investigation.policies import MAX_AGENT_STEPS

PREREGISTRATION_ID: Final = "eval-preregistration-v1"
SCHEMA_VERSION: Final = 1

# Defensive key-name check; the strict schema is the primary boundary.
_PROHIBITED_KEYS: Final = frozenset({"root_cause", "category", "leakage_aliases"})
_PROHIBITED_KEY_PREFIXES: Final = (
    "required_evidence",
    "temporal_evidence",
    "mechanistic_evidence",
    "acceptable_evidence",
    "expected_",
    "answer",
    "distractor",
)


class PreregistrationError(ValueError):
    """The preregistration file is malformed, incomplete, or inconsistent."""


class UnknownPreregisteredCaseError(LookupError):
    """No preregistered context exists for the requested case."""


# ---------------------------------------------------------------------------
# Strict YAML
# ---------------------------------------------------------------------------


class _StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects merge keys and duplicate mapping keys and
    leaves timestamps as strings for Pydantic to validate. Anchors and aliases
    are rejected before loading (see parse_strict_yaml)."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Hashable, object]:
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key == "<<":
                raise yaml.constructor.ConstructorError(
                    None, None, "YAML merge keys are not allowed", key_node.start_mark
                )
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate mapping key: {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


# Resolve timestamps as plain strings: datetimes are validated by Pydantic.
_StrictSafeLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def parse_strict_yaml(text: str) -> object:
    """Parse YAML with the strict SafeLoader. Rejects anchors, aliases, merge
    keys, duplicate keys, and malformed YAML as PreregistrationError."""
    try:
        for event in yaml.parse(text, Loader=_StrictSafeLoader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise yaml.constructor.ConstructorError(
                    None, None, "YAML anchors and aliases are not allowed", event.start_mark
                )
        return yaml.load(text, Loader=_StrictSafeLoader)
    except yaml.YAMLError as error:
        raise PreregistrationError(f"invalid preregistration YAML: {error}") from error


def _reject_prohibited_keys(data: object, path: str = "") -> None:
    if isinstance(data, Mapping):
        for key, value in data.items():
            name = str(key).lower()
            if name in _PROHIBITED_KEYS or name.startswith(_PROHIBITED_KEY_PREFIXES):
                raise PreregistrationError(f"prohibited answer-key field: {path}{key}")
            _reject_prohibited_keys(value, f"{path}{key}.")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _reject_prohibited_keys(item, f"{path}{index}.")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SharedConditions(_FrozenModel):
    max_agent_steps: int = Field(ge=1)
    tool_timeout_seconds: float = Field(gt=0)


class CasePreregistration(_FrozenModel):
    case_id: str = Field(min_length=1)
    initial_context: InitialInvestigationContext


class Preregistration(_FrozenModel):
    """One context per case, identical for every mode. There is no mode field
    and no mode-specific context."""

    preregistration: Literal["eval-preregistration-v1"]
    schema_version: Literal[1]
    shared: SharedConditions
    cases: tuple[CasePreregistration, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_case_ids(self) -> Self:
        ids = [case.case_id for case in self.cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate case_id entries: {duplicates}")
        return self

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    def context_for(self, case_id: str) -> InitialInvestigationContext:
        for case in self.cases:
            if case.case_id == case_id:
                return case.initial_context
        raise UnknownPreregisteredCaseError(f"no preregistered context for case {case_id!r}")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _check_shared_conditions(shared: SharedConditions) -> None:
    if shared.max_agent_steps != MAX_AGENT_STEPS:
        raise PreregistrationError(
            f"max_agent_steps {shared.max_agent_steps} differs from the runtime "
            f"MAX_AGENT_STEPS {MAX_AGENT_STEPS}"
        )
    if shared.tool_timeout_seconds != DEFAULT_TOOL_TIMEOUT_SECONDS:
        raise PreregistrationError(
            f"tool_timeout_seconds {shared.tool_timeout_seconds} differs from the runtime "
            f"DEFAULT_TOOL_TIMEOUT_SECONDS {DEFAULT_TOOL_TIMEOUT_SECONDS}"
        )


def _check_case_coverage(case_ids: tuple[str, ...], expected_case_ids: Collection[str]) -> None:
    expected = set(expected_case_ids)
    if len(expected) != len(expected_case_ids):
        raise PreregistrationError("expected_case_ids contains duplicates")
    missing = sorted(expected - set(case_ids))
    extra = sorted(set(case_ids) - expected)
    if missing or extra:
        raise PreregistrationError(
            f"preregistered cases do not match the frozen cases: missing {missing}, "
            f"unexpected {extra}"
        )


def _check_fixed_playbooks(preregistration: Preregistration) -> None:
    for case in preregistration.cases:
        try:
            build_fixed_playbook(case.initial_context)
        except PlaybookConfigurationError as error:
            raise PreregistrationError(
                f"{case.case_id}: initial context is invalid for the Fixed playbook: {error}"
            ) from error


# 핵심 검사 6종
# parse_strict_yaml, YML 문법검사
# _reject_prohibited)keys, root_cause 같은 정답 관련 칸 이름 거절
# Preregistration.model_validate 양식검사(pydantic)
# _check_shared_conditions  예산 15, timeout 10이 코드와 같은가
# _check_case_coverage      case가 정확히 기대한 20개인가
# _check_fixed_playbooks    각 case로 Fixed 체크리스트가 만들어지는가
def load_preregistration(
    path: str | Path, *, expected_case_ids: Collection[str]
) -> Preregistration:
    """Load and validate a preregistration file. The caller supplies the
    frozen case IDs; this function reads only the given YAML file."""
    data = parse_strict_yaml(Path(path).read_text(encoding="utf-8"))
    _reject_prohibited_keys(data)
    try:
        preregistration = Preregistration.model_validate(data)
    except ValidationError as error:
        raise PreregistrationError(f"invalid preregistration: {error}") from error
    _check_shared_conditions(preregistration.shared)
    _check_case_coverage(preregistration.case_ids, expected_case_ids)
    _check_fixed_playbooks(preregistration)
    return preregistration
