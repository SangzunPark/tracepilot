"""Deterministic ScriptedProvider for tests: returns preconfigured responses
in order, counts every call, and fails clearly when a script runs out.

No I/O, no randomness, no model calls. Lives under tests/, not src/.
"""

from collections.abc import Iterable
from typing import Final

from tracepilot.investigation.models import InvestigationPlan
from tracepilot.investigation.provider import PlanReviewDecision, ProviderView, SynthesisProposal

OPERATIONS: Final = ("propose_initial_plan", "review_plan", "synthesize")


class ScriptExhaustedError(RuntimeError):
    """A provider operation was called more times than it was scripted for."""


class ScriptedProvider:
    def __init__(
        self,
        *,
        initial_plans: Iterable[InvestigationPlan] = (),
        reviews: Iterable[PlanReviewDecision] = (),
        syntheses: Iterable[SynthesisProposal] = (),
    ) -> None:
        # Copied, so later changes to the caller's lists can't alter the script.
        self._initial_plans = tuple(initial_plans)
        self._reviews = tuple(reviews)
        self._syntheses = tuple(syntheses)
        self.calls: dict[str, int] = dict.fromkeys(OPERATIONS, 0)
        self.views: list[tuple[str, ProviderView]] = []

    def _take[T](self, operation: str, script: tuple[T, ...], view: ProviderView) -> T:
        # Every call is counted and recorded, including one that exhausts the script.
        index = self.calls[operation]
        self.calls[operation] = index + 1
        self.views.append((operation, view))
        if index >= len(script):
            raise ScriptExhaustedError(
                f"{operation} called {index + 1} time(s) but scripted for {len(script)}"
            )
        return script[index]

    async def propose_initial_plan(self, view: ProviderView) -> InvestigationPlan:
        return self._take("propose_initial_plan", self._initial_plans, view)

    async def review_plan(self, view: ProviderView) -> PlanReviewDecision:
        return self._take("review_plan", self._reviews, view)

    async def synthesize(self, view: ProviderView) -> SynthesisProposal:
        return self._take("synthesize", self._syntheses, view)
