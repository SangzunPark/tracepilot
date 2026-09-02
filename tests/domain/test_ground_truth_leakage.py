"""Category 4 -- nothing an agent under evaluation can read should give away
the answer: neither the ground-truth field names themselves, nor the
human-readable phrasing of the root cause.

The two checks below have deliberately different scopes:

- Forbidden ground-truth structures (the field names in
  FORBIDDEN_GROUND_TRUTH_KEYS) are prohibited everywhere an agent can look:
  case fixtures and shared runbooks alike. There is no legitimate reason for
  those keys to appear outside evaluation/ground_truth/.
- Root-cause leakage aliases are checked only against a case's own
  observable data, not shared runbooks. A runbook is general organizational
  knowledge and may legitimately describe a failure mode by name (e.g.
  "connection pool exhaustion") without that mention revealing which
  incident it is the root cause of.

Deliberately not tested here: individual words from `root_cause` appearing
in unrelated observable text (e.g. the word "connection" showing up in a
routine log line). That is ordinary telemetry vocabulary, not leakage --
only the curated `leakage_aliases` phrases are treated as verbatim leaks.
"""

from pathlib import Path

from tests.domain.support import case_text_blob, find_forbidden_keys, load_json
from tracepilot.domain.models import GroundTruth


def test_agent_visible_data_has_no_ground_truth_keys(agent_visible_json_path: Path) -> None:
    data = load_json(agent_visible_json_path)
    leaked_keys = find_forbidden_keys(data)
    assert not leaked_keys, f"{agent_visible_json_path}: found {leaked_keys}"


def test_leakage_aliases_do_not_appear_verbatim_in_case_data(ground_truth_path: Path) -> None:
    ground_truth = GroundTruth.model_validate(load_json(ground_truth_path))
    blob = case_text_blob(ground_truth.case_id)
    leaked = [alias for alias in ground_truth.leakage_aliases if alias.lower() in blob]
    assert not leaked, f"{ground_truth.case_id}: leaked aliases {leaked}"
