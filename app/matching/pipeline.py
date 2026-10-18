"""Ranking donors for one request, in two stages that must never merge.

Stage one is compatibility: `check_fit` decides whether a device can work at
this clinic at all. It is a predicate, it calls no model, and its answer is
the only thing allowed to remove an offer from the list.

Stage two is allocation: among the devices that work, which donor is nearest.
It calls no model either.

There is deliberately no combined score. A device that cannot work does not
become usable by being close, so distance is never allowed to outrank a
failed check, and a short distance can never buy a blocker off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable

from app.checks import check_fit
from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.marketplace import GeoPoint, MatchRecord, MatchState, Offer, Request
from app.domain.result import Outcome, Verdict
from app.domain.shipment import Shipment, estimate_shipment
from app.matching.distance import facility_point, haversine_km

#: Looks up what the country can support for one device. Injected so the
#: pipeline never reaches for a model itself, and tests never need one.
IntelLookup = Callable[[DeviceFacts, str], SupplyIntel]

MIN_MATCH_QUANTITY = 1

# Sort ranks, so an offer with no coordinates lands behind every located one
# instead of being treated as zero kilometres away.
LOCATED_RANK = 0
UNLOCATED_RANK = 1

# Presentation only. Distances are computed and compared in kilometres.
MILES_PER_KM = 0.621371
UNLOCATED_DISTANCE_PLACEHOLDER = 0.0


@dataclass(frozen=True)
class Candidate:
    """One surviving offer, with the verdict that cleared it and how far it is.

    `intel` is the research the verdict was built from. It is carried so that
    proposing a match reuses the answer instead of asking for it again.
    """

    offer: Offer
    verdict: Verdict
    distance_km: float | None = None
    intel: SupplyIntel = SupplyIntel()
    shipment: Shipment | None = None

    @property
    def condition_count(self) -> int:
        """How many things must be shipped alongside for this to work."""
        return len(self.verdict.conditional)

    @property
    def distance_miles(self) -> float | None:
        """The same distance in miles, for display only.

        Every calculation in this package stays metric. Conversion happens at
        the edge so the sort key and the haversine never see two units.
        """
        if self.distance_km is None:
            return None
        return self.distance_km * MILES_PER_KM


def empty_intel(device: DeviceFacts, country: str) -> SupplyIntel:
    """The default lookup: states nothing, so the pipeline runs with no network."""
    return SupplyIntel(country=country)


def rank_candidates(
    facility: FacilityProfile,
    offers: Iterable[Offer],
    intel_for: IntelLookup = empty_intel,
    *,
    today: date | None = None,
) -> list[Candidate]:
    """Compatible offers, nearest-and-cleanest first.

    Selecting which offers to consider is the caller's query, one request line
    at a time. This function decides which of the offers handed to it can work
    at `facility`, and in what order.
    """
    destination = facility_point(facility)
    matchable = tuple(offer for offer in offers if offer.is_matchable(today))
    assessed = tuple(
        _assess(offer, facility, destination, intel_for) for offer in matchable
    )
    survivors = tuple(c for c in assessed if c.verdict.outcome is not Outcome.WONT_WORK)
    return sorted(survivors, key=_sort_key)


def propose_match(
    request: Request, candidate: Candidate, quantity: int = MIN_MATCH_QUANTITY
) -> MatchRecord:
    """A PROPOSED record for this candidate, carrying the verdict that justified it.

    `created_at` is left unset: the store stamps it, and the confirmation
    window is measured from that stamp.
    """
    offer = candidate.offer
    if request.id is None or offer.id is None:
        raise ValueError("Cannot propose a match for an unsaved request or offer.")
    if quantity < MIN_MATCH_QUANTITY:
        raise ValueError(
            f"Match quantity must be at least {MIN_MATCH_QUANTITY}; got {quantity}."
        )
    if quantity > offer.quantity_available:
        raise ValueError(
            f"Cannot propose {quantity}; only {offer.quantity_available} available "
            f"on offer {offer.id}."
        )

    return MatchRecord(
        request_id=request.id,
        offer_id=offer.id,
        verdict=candidate.verdict,
        intel=candidate.intel,
        quantity=quantity,
        distance_km=candidate.distance_km,
        state=MatchState.PROPOSED,
    )


def _assess(
    offer: Offer,
    facility: FacilityProfile,
    destination: GeoPoint,
    intel_for: IntelLookup,
) -> Candidate:
    """Run the compatibility predicate, then measure the distance. In that order."""
    intel = intel_for(offer.device, facility.country)
    if not isinstance(intel, SupplyIntel):
        raise TypeError(
            f"The intel lookup must return a SupplyIntel; got {type(intel).__name__}."
        )
    distance_km = haversine_km(offer.origin, destination)
    shipment = estimate_shipment(
        distance_km, crosses_border=offer.origin.country != facility.country
    )
    return Candidate(
        offer=offer,
        verdict=check_fit(offer.device, facility, intel, shipment),
        distance_km=distance_km,
        intel=intel,
        shipment=shipment,
    )


def _sort_key(candidate: Candidate) -> tuple[int, int, float]:
    """Fewest conditions first, then nearest. Unlocated offers come last.

    Both parts are ordinals in one tuple, not terms in a sum: no number of
    kilometres saved can offset a condition, and nothing here can resurrect a
    device the checks rejected, because those are already gone.
    """
    distance = candidate.distance_km
    if distance is None:
        return (candidate.condition_count, UNLOCATED_RANK, UNLOCATED_DISTANCE_PLACEHOLDER)
    return (candidate.condition_count, LOCATED_RANK, distance)
