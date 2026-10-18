"""The output side: one result per check, rolled up into one verdict."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.provenance import Provenance
from app.domain.requirement import Requirement


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    MITIGABLE = "mitigable"
    NEEDS_INFO = "needs info"


class Outcome(str, Enum):
    WORKS = "works"
    WORKS_IF = "works if you also send"
    WONT_WORK = "won't work"


@dataclass(frozen=True)
class CheckResult:
    key: str
    label: str
    status: CheckStatus
    detail: str
    fix: str = ""
    source: Provenance = Provenance.UNKNOWN
    question: str = ""
    requirement: Requirement | None = None

    @property
    def blocking(self) -> bool:
        return self.status is CheckStatus.FAIL


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    results: tuple[CheckResult, ...]
    requirements: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    @property
    def needs(self) -> tuple[Requirement, ...]:
        """Every structured requirement this verdict raised, in result order."""
        return tuple(r.requirement for r in self.results if r.requirement is not None)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.FAIL)

    @property
    def passes(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.PASS)

    @property
    def unknowns(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.NEEDS_INFO)

    @property
    def conditional(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status is CheckStatus.MITIGABLE)

    @property
    def stamp(self) -> str:
        """Two words for the inspection stamp."""
        if self.outcome is Outcome.WORKS:
            return "Clear to ship"
        if self.outcome is Outcome.WONT_WORK:
            return "Do not ship"
        return "Ship with"

    @property
    def headline(self) -> str:
        if self.outcome is Outcome.WORKS:
            # Nothing blocked it, but an unanswered check is not a passed one.
            # Saying "all N pass" while N-2 passed is the kind of overstatement
            # this whole design exists to avoid.
            unanswered = len(self.unknowns)
            if unanswered:
                return f"{len(self.passes)} pass, {unanswered} unanswered"
            return f"All {len(self.results)} checks pass"
        if self.outcome is Outcome.WONT_WORK:
            blockers = len(self.failures)
            word = "blocker" if blockers == 1 else "blockers"
            return f"{blockers} {word}, {len(self.conditional)} conditions"
        count = len(self.requirements)
        return f"{count} thing{'' if count == 1 else 's'} it cannot arrive without"
