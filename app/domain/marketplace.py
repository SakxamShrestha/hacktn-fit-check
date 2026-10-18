"""Shared types for the two-sided platform.

Every module in the build imports these. Nothing here depends on the database,
the web layer or any model call, so all three can be built against it at once.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum

from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.result import Verdict

TOKEN_BYTES = 16
DEFAULT_CONFIRM_WINDOW_HOURS = 72

# A clinic asking for nothing is a form error, not a request.
MIN_LINE_QUANTITY = 1
DEFAULT_LINE_QUANTITY = 1


class OfferStatus(str, Enum):
    OPEN = "open"
    EXHAUSTED = "exhausted"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


class RequestStatus(str, Enum):
    OPEN = "open"
    MATCHED = "matched"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


class MatchState(str, Enum):
    """The verification state machine from the spec."""

    PROPOSED = "proposed"
    AWAITING_DONOR = "awaiting donor"
    CONFIRMED = "confirmed"
    WITHDRAWN = "withdrawn"
    SHIPPED = "shipped"


OPEN_STATES = frozenset({MatchState.PROPOSED, MatchState.AWAITING_DONOR})


@dataclass(frozen=True)
class GeoPoint:
    """Where something sits. Country is required; coordinates may be absent."""

    country: str
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None

    @property
    def located(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass(frozen=True)
class Offer:
    """What a donor holds. Inventory columns follow the spec exactly."""

    donor_name: str
    donor_email: str
    device: DeviceFacts
    origin: GeoPoint
    device_type: str = ""
    support_kind: str = ""
    quantity_offered: int = 1
    quantity_reserved: int = 0
    quantity_shipped: int = 0
    available_until: date | None = None
    photo_key: str = ""
    status: OfferStatus = OfferStatus.OPEN
    id: int | None = None
    created_at: datetime | None = None

    @property
    def quantity_available(self) -> int:
        return max(0, self.quantity_offered - self.quantity_reserved - self.quantity_shipped)

    def is_expired(self, today: date | None = None) -> bool:
        if self.available_until is None:
            return False
        return self.available_until < (today or date.today())

    def is_matchable(self, today: date | None = None) -> bool:
        if self.status is not OfferStatus.OPEN:
            return False
        return self.quantity_available > 0 and not self.is_expired(today)

    def reserve(self, quantity: int) -> Offer:
        """Return a new Offer with stock committed. Never mutates."""
        if quantity <= 0:
            raise ValueError("Reserved quantity must be positive.")
        if quantity > self.quantity_available:
            raise ValueError(
                f"Cannot reserve {quantity}; only {self.quantity_available} available."
            )
        return replace(self, quantity_reserved=self.quantity_reserved + quantity)

    def release(self, quantity: int) -> Offer:
        """Give reserved stock back, after a donor withdraws or a match times out."""
        return replace(self, quantity_reserved=max(0, self.quantity_reserved - quantity))


@dataclass(frozen=True)
class RequestLine:
    """One kind of equipment a clinic needs, and how many of it.

    The device type is normalised here rather than at every call site: it is
    the string the inventory is searched on, and clinics type it by hand.
    """

    device_type: str
    quantity: int = DEFAULT_LINE_QUANTITY
    id: int | None = None

    def __post_init__(self) -> None:
        cleaned = " ".join(self.device_type.split()).lower()
        if not cleaned:
            raise ValueError("A request line must name a kind of equipment.")
        if self.quantity < MIN_LINE_QUANTITY:
            raise ValueError(
                f"A request line must ask for at least {MIN_LINE_QUANTITY} unit; "
                f"got {self.quantity}."
            )
        object.__setattr__(self, "device_type", cleaned)

    @property
    def label(self) -> str:
        """The line as a person would say it: '2 ventilators'."""
        plural = "" if self.quantity == MIN_LINE_QUANTITY else "s"
        return f"{self.quantity} {self.device_type}{plural}"


@dataclass(frozen=True)
class Request:
    """Everything a clinic is asking for in one go."""

    facility_slug: str
    lines: tuple[RequestLine, ...] = ()
    note: str = ""
    status: RequestStatus = RequestStatus.OPEN
    id: int | None = None
    created_at: datetime | None = None

    @property
    def total_quantity(self) -> int:
        """Every unit asked for, across every line."""
        return sum(line.quantity for line in self.lines)

    @property
    def device_types(self) -> tuple[str, ...]:
        """Each kind asked for, once, in the order the clinic listed them."""
        seen: list[str] = []
        for line in self.lines:
            if line.device_type not in seen:
                seen.append(line.device_type)
        return tuple(seen)

    @property
    def summary(self) -> str:
        """The whole request in one readable phrase."""
        labels = [line.label for line in self.lines]
        if not labels:
            return "nothing yet"
        if len(labels) == 1:
            return labels[0]
        return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def new_token() -> str:
    """An unguessable token for the donor's confirm link."""
    return secrets.token_urlsafe(TOKEN_BYTES)


@dataclass(frozen=True)
class MatchRecord:
    """One offer proposed against one request, with the verdict that justified it."""

    request_id: int
    offer_id: int
    verdict: Verdict
    intel: SupplyIntel
    quantity: int = 1
    distance_km: float | None = None
    state: MatchState = MatchState.PROPOSED
    token: str = field(default_factory=new_token)
    id: str | None = None
    created_at: datetime | None = None
    responded_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES

    def to_state(self, state: MatchState, when: datetime | None = None) -> MatchRecord:
        """Advance the state machine, returning a new record."""
        return replace(self, state=state, responded_at=when or datetime.now())
