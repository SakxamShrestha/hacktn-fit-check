"""A clinically equivalent device, for when nothing of the kind asked for passes.

A clinic asks for an x-ray system, every x-ray system on the shelf fails the
screening, and the useful answer is not an empty page: it is the mobile unit
that does the same job for this clinic and does pass.

Everything offered here comes from a hand-written table, data/substitutes.json,
and matching on the requested type is EXACT after normalising. The reason is
recorded in app/reference/device_class.py: fuzzy matching was tried there and
rejected after it paired "haemodialysis system" with "basic diagnostic x-ray
system". A wrong equivalence here would be worse than a wrong lookup, because
it is a clinical claim about what a clinic may treat with. So each pair is an
editorial decision somebody made on purpose, carries the reason it was made,
and nothing is ever derived, inferred or guessed.

A substitute that also fails the screening is not a substitute. Options are
built only from candidates the checks cleared, and the ordering never lets a
device that needs extras shipped alongside it outrank one that needs none.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.config import PROJECT_ROOT
from app.domain.facility import FacilityProfile
from app.domain.marketplace import Offer
from app.domain.result import Outcome
from app.matching.pipeline import Candidate, rank_candidates

logger = logging.getLogger(__name__)

DATA_PATH = PROJECT_ROOT / "data" / "substitutes.json"
PAIRS_KEY = "pairs"
FROM_KEY = "from"
TO_KEY = "to"
REASON_KEY = "reason"

#: Finds the open offers for one device type. Injected so this module never
#: reaches for the database itself. In production: repo.open_offers_for_type.
OfferLookup = Callable[[str], Sequence[Offer]]

#: Screens and orders offers for one facility. Defaults to the real pipeline.
Ranker = Callable[[FacilityProfile, Sequence[Offer]], Sequence[Candidate]]

# A device that works outright is always the better suggestion than one that
# only works if something else is shipped with it. These are ordinals, not
# scores: nothing adds to them and nothing trades against them.
WORKS_FIRST: Mapping[Outcome, int] = {Outcome.WORKS: 0, Outcome.WORKS_IF: 1}
UNRANKED = len(WORKS_FIRST)


class SubstituteDataError(RuntimeError):
    """Raised when the committed substitute table is missing or unreadable."""


@dataclass(frozen=True)
class Substitute:
    """One curated equivalence, and the editorial reason for it.

    The pair is one-way: `offered` may stand in for `requested`, and the
    reverse holds only if the table lists it separately.
    """

    requested: str
    offered: str
    reason: str


@dataclass(frozen=True)
class SubstituteOption:
    """A substitute device that an actual donor holds and the checks cleared."""

    substitute: Substitute
    candidate: Candidate

    @property
    def device_type(self) -> str:
        """What is being offered instead of what was asked for."""
        return self.substitute.offered

    @property
    def reason(self) -> str:
        """Why the two are held to be equivalent, for the clinic to read."""
        return self.substitute.reason


def _normalise(device_type: str) -> str:
    """Lowercase and collapse whitespace. Nothing else.

    This is the normalisation `RequestLine.__post_init__` applies in
    app/domain/marketplace.py, so a request line and this table agree on what
    counts as the same string, and on nothing more than that.
    """
    return " ".join(device_type.split()).lower()


def _as_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _build(entry: Mapping[str, Any]) -> Substitute | None:
    """One table row, or None with a warning if it cannot be trusted."""
    requested = _as_text(entry.get(FROM_KEY))
    offered = _as_text(entry.get(TO_KEY))
    reason = _as_text(entry.get(REASON_KEY))
    if requested is None or offered is None:
        logger.warning("Skipping substitute pair missing a device type: %r", entry)
        return None
    if reason is None:
        logger.warning("Skipping substitute pair with no stated reason: %r", entry)
        return None
    if _normalise(requested) == _normalise(offered):
        logger.warning("Skipping substitute pair that points at itself: %r", entry)
        return None
    return Substitute(requested=requested, offered=offered, reason=reason)


def _read_pairs(path: Path) -> list[Any]:
    """The raw `pairs` list from the committed file, or a named error."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SubstituteDataError(
            f"The substitute table is missing at {path}. "
            "It ships with the repo; restore it from version control."
        ) from error
    except OSError as error:
        raise SubstituteDataError(
            f"The substitute table at {path} could not be read: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise SubstituteDataError(
            f"The substitute table at {path} is not valid JSON: {error}"
        ) from error

    entries = payload.get(PAIRS_KEY) if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise SubstituteDataError(
            f"The substitute table at {path} has no '{PAIRS_KEY}' list."
        )
    return entries


@lru_cache(maxsize=1)
def _table() -> Mapping[str, tuple[Substitute, ...]]:
    """Load the table once, keyed by normalised requested type.

    A missing or malformed file costs the clinic its suggestions, which is a
    smaller harm than taking the whole marketplace down, so it degrades to an
    empty table and says so in the log.
    """
    try:
        entries = _read_pairs(Path(DATA_PATH))
    except SubstituteDataError as error:
        logger.warning("No substitutes will be offered: %s", error)
        return {}

    grouped: dict[str, list[Substitute]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            logger.warning("Skipping non-object substitute entry: %r", entry)
            continue
        pair = _build(entry)
        if pair is None:
            continue
        grouped.setdefault(_normalise(pair.requested), []).append(pair)
    return {key: tuple(pairs) for key, pairs in grouped.items()}


def known_terms() -> tuple[str, ...]:
    """Every device type named in the substitution table, asked-for or offered.

    The intake vocabulary needs both sides: a clinic may write the name of
    something only ever held as a substitute.
    """
    terms: set[str] = set()
    for requested, options in _table().items():
        terms.add(requested)
        terms.update(option.offered for option in options)
    return tuple(sorted(terms))


def substitutes_for(device_type: str) -> tuple[Substitute, ...]:
    """The curated equivalents of this device type. Empty on anything else.

    There is no fallback and no near miss: an unlisted type has no substitute,
    because inventing one would be a clinical claim nobody made.
    """
    if not device_type or not device_type.strip():
        return ()
    return _table().get(_normalise(device_type), ())


def _outcome_rank(option: SubstituteOption) -> int:
    return WORKS_FIRST.get(option.candidate.verdict.outcome, UNRANKED)


def find_substitutes(
    device_type: str,
    facility: FacilityProfile,
    find_offers: OfferLookup,
    rank: Ranker = rank_candidates,
) -> tuple[SubstituteOption, ...]:
    """Equivalent devices a donor actually holds and this clinic can actually use.

    Each curated equivalent is looked up in the inventory and screened for this
    facility. Anything the checks reject is dropped: a substitute that also
    fails is not a substitute. What survives is ordered works-outright first,
    keeping the ranker's own order within each group.
    """
    options: list[SubstituteOption] = []
    for substitute in substitutes_for(device_type):
        offers = find_offers(substitute.offered)
        if not offers:
            continue
        options.extend(
            SubstituteOption(substitute=substitute, candidate=candidate)
            for candidate in rank(facility, offers)
            if candidate.verdict.outcome is not Outcome.WONT_WORK
        )
    return tuple(sorted(options, key=_outcome_rank))
