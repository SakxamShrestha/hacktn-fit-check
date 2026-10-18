"""The verification state machine.

Four legal moves and no others:

    PROPOSED -> AWAITING_DONOR -> CONFIRMED | WITHDRAWN
    CONFIRMED -> SHIPPED

Every other move raises, including a move onto the state a match is already
in. WITHDRAWN and SHIPPED are terminal. Nothing here mutates a record: each
transition returns a new one.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Iterable, Mapping

from app.domain.marketplace import DEFAULT_CONFIRM_WINDOW_HOURS, MatchRecord, MatchState

LEGAL_TRANSITIONS: Mapping[MatchState, frozenset[MatchState]] = MappingProxyType(
    {
        MatchState.PROPOSED: frozenset({MatchState.AWAITING_DONOR}),
        MatchState.AWAITING_DONOR: frozenset(
            {MatchState.CONFIRMED, MatchState.WITHDRAWN}
        ),
        MatchState.CONFIRMED: frozenset({MatchState.SHIPPED}),
        MatchState.WITHDRAWN: frozenset(),
        MatchState.SHIPPED: frozenset(),
    }
)


class InvalidTransition(ValueError):
    """Raised when a match is asked to make a move the spec does not allow."""

    def __init__(self, current: MatchState, target: MatchState) -> None:
        super().__init__(
            f"A match cannot move from {current.value} to {target.value}."
        )
        self.current = current
        self.target = target


def transition(
    match: MatchRecord, target: MatchState, now: datetime | None = None
) -> MatchRecord:
    """The only door into the state machine. Returns a new record."""
    allowed = LEGAL_TRANSITIONS.get(match.state, frozenset())
    if target not in allowed:
        raise InvalidTransition(match.state, target)
    return match.to_state(target, now)


def request_confirmation(
    match: MatchRecord, now: datetime | None = None
) -> MatchRecord:
    """Send the proposal to the donor and start the confirmation window."""
    return transition(match, MatchState.AWAITING_DONOR, now)


def confirm(match: MatchRecord, now: datetime | None = None) -> MatchRecord:
    """The donor said yes."""
    return transition(match, MatchState.CONFIRMED, now)


def withdraw(match: MatchRecord, now: datetime | None = None) -> MatchRecord:
    """The donor said no, or the window closed on silence."""
    return transition(match, MatchState.WITHDRAWN, now)


def mark_shipped(match: MatchRecord, now: datetime | None = None) -> MatchRecord:
    """The device is on its way."""
    return transition(match, MatchState.SHIPPED, now)


def has_timed_out(
    match: MatchRecord,
    now: datetime,
    window_hours: int = DEFAULT_CONFIRM_WINDOW_HOURS,
) -> bool:
    """True when a match is still awaiting a donor and the window has passed.

    A match with no creation time cannot be aged, so it is never swept: the
    honest answer to "how old is this" is unknown, not "old enough".
    """
    if window_hours < 0:
        raise ValueError(f"The confirmation window cannot be negative: {window_hours}.")
    if match.state is not MatchState.AWAITING_DONOR:
        return False
    if match.created_at is None:
        return False

    created_at = match.created_at
    if (created_at.tzinfo is None) != (now.tzinfo is None):
        raise ValueError(
            "Cannot age a match against a time zone it does not share: "
            f"created_at={created_at!r}, now={now!r}."
        )
    return created_at + timedelta(hours=window_hours) < now


def sweep_timeouts(
    matches: Iterable[MatchRecord],
    now: datetime,
    window_hours: int = DEFAULT_CONFIRM_WINDOW_HOURS,
) -> list[MatchRecord]:
    """Withdraw every match the donor let expire, and return the new records."""
    return [
        withdraw(match, now)
        for match in matches
        if has_timed_out(match, now, window_hours)
    ]
