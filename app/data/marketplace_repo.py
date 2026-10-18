"""All marketplace database access, behind named operations.

Same shape as app/data/repository.py: module-level functions, one error type,
every database call wrapped. Domain objects go in and come back out, so no
caller outside this module ever sees a row or a JSON blob.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, insert, or_, select, update

from app.data.db import get_engine
from app.data.marketplace_schema import (
    MATCH_ID_LENGTH,
    facilities_v2,
    matches,
    offers,
    request_lines,
    requests,
)
from app.data.serialize import (
    device_from_dict,
    device_to_dict,
    intel_from_dict,
    intel_to_dict,
    verdict_from_dict,
    verdict_to_dict,
)
from app.domain.facility import FacilityProfile
from app.domain.facility_trust import Verification, VerificationStatus
from app.domain.marketplace import (
    OPEN_STATES,
    GeoPoint,
    MatchRecord,
    MatchState,
    Offer,
    OfferStatus,
    Request,
    RequestLine,
    RequestStatus,
)

NO_STOCK = 0
MINIMUM_SWEEP_HOURS = 0
LIKE_ESCAPE = "\\"
LIKE_WILDCARDS = ("%", "_")
EMPTY_JSON_OBJECT = "{}"

# FacilityProfile holds these as tuples; JSON hands them back as lists.
FACILITY_TUPLE_FIELDS = ("plug_types", "staff_languages")
FACILITY_FIELD_NAMES = frozenset(f.name for f in fields(FacilityProfile))

OPEN_MATCH_STATES = tuple(state.value for state in OPEN_STATES)


class RepositoryError(RuntimeError):
    """Any storage failure, with a message safe to show a user."""


def _first_row(statement: Any, failure: str) -> Any:
    try:
        with get_engine().connect() as conn:
            return conn.execute(statement).mappings().first()
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"{failure}: {exc}") from exc


def _all_rows(statement: Any, failure: str) -> list[Any]:
    try:
        with get_engine().connect() as conn:
            return list(conn.execute(statement).mappings().all())
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"{failure}: {exc}") from exc


def _write(statement: Any, failure: str, params: Any = None) -> None:
    """Run a write. With params this is one executemany, not a loop of inserts."""
    try:
        with get_engine().begin() as conn:
            conn.execute(statement) if params is None else conn.execute(statement, params)
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"{failure}: {exc}") from exc


def _write_returning_id(statement: Any, failure: str) -> int:
    try:
        with get_engine().begin() as conn:
            return int(conn.execute(statement).inserted_primary_key[0])
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"{failure}: {exc}") from exc


def _new_match_id() -> str:
    return uuid.uuid4().hex[:MATCH_ID_LENGTH]


def _contains_clause(column: Any, needle: str) -> Any:
    """Case-insensitive substring match that survives % and _ inside the needle."""
    pattern = needle.lower().replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
    for wildcard in LIKE_WILDCARDS:
        pattern = pattern.replace(wildcard, LIKE_ESCAPE + wildcard)
    return func.lower(column).like(f"%{pattern}%", escape=LIKE_ESCAPE)


def _type_matches(wanted: str, stored: str) -> bool:
    """True when either device type contains the other.

    A request for "ventilator" should find an offer typed "transport
    ventilator", and a request for "transport ventilator" should still find a
    plainly typed "ventilator". Done in Python because the SQL for a
    bidirectional match is not portable between SQLite and MySQL.
    """
    left, right = wanted.strip().lower(), stored.strip().lower()
    if not left or not right:
        return False
    return left in right or right in left


# --- Facilities -----------------------------------------------------------


def _facility_values(profile: FacilityProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "country": profile.country,
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "profile_json": json.dumps(asdict(profile)),
    }


def _facility_from_row(row: Any) -> FacilityProfile:
    payload = dict(json.loads(row["profile_json"]))
    for name in FACILITY_TUPLE_FIELDS:
        payload[name] = tuple(payload.get(name) or ())
    return FacilityProfile(**{k: v for k, v in payload.items() if k in FACILITY_FIELD_NAMES})


def load_verification(slug: str) -> VerificationStatus:
    """How far this facility's claim has been checked. Unknown slugs read as self-declared."""
    row = _first_row(
        select(facilities_v2.c.verification, facilities_v2.c.verification_note)
        .where(facilities_v2.c.slug == slug),
        "Could not load the verification status",
    )
    if row is None:
        return VerificationStatus()
    try:
        tier = Verification(row["verification"])
    except ValueError:
        tier = Verification.SELF_DECLARED
    return VerificationStatus(tier=tier, note=row["verification_note"] or "")


def set_verification(slug: str, tier: Verification, note: str = "") -> None:
    """Record a reviewer's decision about a facility."""
    _write(
        update(facilities_v2)
        .where(facilities_v2.c.slug == slug)
        .values(verification=tier.value, verification_note=note),
        "Could not record the verification",
    )


def verifications_for(slugs: list[str]) -> dict[str, VerificationStatus]:
    """Verification for many facilities in one query, for list pages."""
    if not slugs:
        return {}
    rows = _all_rows(
        select(
            facilities_v2.c.slug,
            facilities_v2.c.verification,
            facilities_v2.c.verification_note,
        ).where(facilities_v2.c.slug.in_(slugs)),
        "Could not load the verification statuses",
    )
    found = {}
    for row in rows:
        try:
            tier = Verification(row["verification"])
        except ValueError:
            tier = Verification.SELF_DECLARED
        found[row["slug"]] = VerificationStatus(tier=tier, note=row["verification_note"] or "")
    return found


def save_facility(profile: FacilityProfile) -> str:
    """Insert the profile, or replace whatever is already stored under its slug."""
    values = _facility_values(profile)
    slug_match = facilities_v2.c.slug == profile.slug
    try:
        with get_engine().begin() as conn:
            stored = conn.execute(select(facilities_v2.c.slug).where(slug_match)).first()
            statement = (
                update(facilities_v2).where(slug_match).values(**values)
                if stored
                else insert(facilities_v2).values(
                    slug=profile.slug, created_at=datetime.now(), **values
                )
            )
            conn.execute(statement)
    except Exception as exc:  # noqa: BLE001
        raise RepositoryError(f"Could not save the facility: {exc}") from exc
    return profile.slug


def load_facility(slug: str) -> FacilityProfile | None:
    row = _first_row(
        select(facilities_v2).where(facilities_v2.c.slug == slug),
        "Could not load the facility",
    )
    return None if row is None else _facility_from_row(row)


def list_facilities() -> list[FacilityProfile]:
    rows = _all_rows(
        select(facilities_v2).order_by(facilities_v2.c.name),
        "Could not list the facilities",
    )
    return [_facility_from_row(row) for row in rows]


# --- Offers ---------------------------------------------------------------


def _offer_values(offer: Offer) -> dict[str, Any]:
    return {
        "donor_name": offer.donor_name,
        "donor_email": offer.donor_email,
        "device_label": offer.device.label,
        "device_type": offer.device_type,
        "support_kind": offer.support_kind,
        "device_json": json.dumps(device_to_dict(offer.device)),
        "country": offer.origin.country,
        "city": offer.origin.city,
        "latitude": offer.origin.latitude,
        "longitude": offer.origin.longitude,
        "quantity_offered": offer.quantity_offered,
        "quantity_reserved": offer.quantity_reserved,
        "quantity_shipped": offer.quantity_shipped,
        "available_until": offer.available_until,
        "photo_key": offer.photo_key,
        "status": offer.status.value,
    }


def _offer_from_row(row: Any) -> Offer:
    return Offer(
        donor_name=row["donor_name"],
        donor_email=row["donor_email"],
        device=device_from_dict(json.loads(row["device_json"])),
        device_type=row["device_type"] or "",
        support_kind=row["support_kind"] or "",
        origin=GeoPoint(
            country=row["country"],
            city=row["city"] or "",
            latitude=row["latitude"],
            longitude=row["longitude"],
        ),
        quantity_offered=row["quantity_offered"],
        quantity_reserved=row["quantity_reserved"],
        quantity_shipped=row["quantity_shipped"],
        available_until=row["available_until"],
        photo_key=row["photo_key"] or "",
        status=OfferStatus(row["status"]),
        id=row["id"],
        created_at=row["created_at"],
    )


def save_offer(offer: Offer) -> int:
    statement = insert(offers).values(
        created_at=offer.created_at or datetime.now(), **_offer_values(offer)
    )
    return _write_returning_id(statement, "Could not save the offer")


def load_offer(offer_id: int) -> Offer | None:
    row = _first_row(
        select(offers).where(offers.c.id == offer_id), "Could not load the offer"
    )
    return None if row is None else _offer_from_row(row)


def update_offer(offer: Offer) -> None:
    """Write an offer back. The caller holds a new Offer; nothing is mutated here."""
    if offer.id is None:
        raise RepositoryError("Cannot update an offer that was never saved.")
    statement = update(offers).where(offers.c.id == offer.id).values(**_offer_values(offer))
    _write(statement, "Could not update the offer")


def known_device_types() -> list[str]:
    """Every device type currently on an open offer, plus the substitute terms.

    Fed to the free-text intake as a controlled vocabulary, so a clinic writing
    "x-ray machine" produces a line that actually matches stock rather than a
    dead one.
    """
    rows = _all_rows(
        select(offers.c.device_type)
        .where(offers.c.status == OfferStatus.OPEN.value)
        .distinct(),
        "Could not list the stocked device types",
    )
    found = {str(row["device_type"]).strip() for row in rows if row["device_type"]}

    from app.matching.substitutes import known_terms

    return sorted(found | set(known_terms()))


def open_offers_for_type(device_type: str, today: date | None = None) -> list[Offer]:
    """Offers that can still be matched against a request for this device type.

    Open, with stock left, and not past the donor's cut-off date. Status, stock
    and date are filtered in SQL; the type is matched against the stored label,
    because an offer carries a whole device and not a type. A blank type
    matches nothing rather than everything.
    """
    if not device_type.strip():
        return []
    available = (
        offers.c.quantity_offered - offers.c.quantity_reserved - offers.c.quantity_shipped
    )
    criteria = and_(
        offers.c.status == OfferStatus.OPEN.value,
        available > NO_STOCK,
        or_(offers.c.available_until.is_(None), offers.c.available_until >= (today or date.today())),
    )
    rows = _all_rows(
        select(offers).where(criteria).order_by(offers.c.created_at),
        "Could not list the open offers",
    )
    rows = [row for row in rows if _type_matches(device_type, row["device_type"] or "")]
    return [_offer_from_row(row) for row in rows]


# --- Requests -------------------------------------------------------------


def _request_from_row(row: Any, lines: tuple[RequestLine, ...] = ()) -> Request:
    return Request(
        facility_slug=row["facility_slug"],
        lines=lines,
        note=row["note"] or "",
        status=RequestStatus(row["status"]),
        id=row["id"],
        created_at=row["created_at"],
    )


def _lines_for(request_ids: list[int]) -> dict[int, tuple[RequestLine, ...]]:
    """Every line for these requests, in one query rather than one per request."""
    if not request_ids:
        return {}
    rows = _all_rows(
        select(request_lines)
        .where(request_lines.c.request_id.in_(request_ids))
        .order_by(request_lines.c.request_id, request_lines.c.position),
        "Could not load the request lines",
    )
    grouped: dict[int, list[RequestLine]] = {rid: [] for rid in request_ids}
    for row in rows:
        grouped[row["request_id"]].append(
            RequestLine(
                device_type=row["device_type"],
                quantity=row["quantity"],
                id=row["id"],
            )
        )
    return {rid: tuple(found) for rid, found in grouped.items()}


def save_request(request: Request) -> int:
    """Store the request and its lines. A request with no lines is refused."""
    if not request.lines:
        raise RepositoryError("A request must ask for at least one kind of equipment.")

    statement = insert(requests).values(
        facility_slug=request.facility_slug,
        note=request.note,
        status=request.status.value,
        created_at=request.created_at or datetime.now(),
    )
    request_id = _write_returning_id(statement, "Could not save the request")

    rows = [
        {
            "request_id": request_id,
            "device_type": line.device_type,
            "quantity": line.quantity,
            "position": position,
        }
        for position, line in enumerate(request.lines)
    ]
    _write(insert(request_lines), "Could not save the request lines", rows)
    return request_id


def load_request(request_id: int) -> Request | None:
    row = _first_row(
        select(requests).where(requests.c.id == request_id), "Could not load the request"
    )
    if row is None:
        return None
    return _request_from_row(row, _lines_for([request_id]).get(request_id, ()))


def list_open_requests() -> list[Request]:
    rows = _all_rows(
        select(requests)
        .where(requests.c.status == RequestStatus.OPEN.value)
        .order_by(requests.c.created_at),
        "Could not list the open requests",
    )
    by_request = _lines_for([row["id"] for row in rows])
    return [_request_from_row(row, by_request.get(row["id"], ())) for row in rows]


# --- Matches --------------------------------------------------------------


def _match_from_row(row: Any) -> MatchRecord:
    return MatchRecord(
        request_id=row["request_id"],
        offer_id=row["offer_id"],
        verdict=verdict_from_dict(json.loads(row["verdict_json"])),
        intel=intel_from_dict(json.loads(row["intel_json"] or EMPTY_JSON_OBJECT)),
        quantity=row["quantity"],
        distance_km=row["distance_km"],
        state=MatchState(row["state"]),
        token=row["token"],
        id=row["id"],
        created_at=row["created_at"],
        responded_at=row["responded_at"],
    )


def save_match(match: MatchRecord) -> str:
    """Store a proposed match and return its id, generated here when absent.

    created_at is written from Python, not left to the server default, so that
    open_matches_older_than compares two readings of the same clock.
    """
    match_id = match.id or _new_match_id()
    statement = insert(matches).values(
        id=match_id,
        request_id=match.request_id,
        offer_id=match.offer_id,
        quantity=match.quantity,
        distance_km=match.distance_km,
        state=match.state.value,
        token=match.token,
        verdict_json=json.dumps(verdict_to_dict(match.verdict)),
        intel_json=json.dumps(intel_to_dict(match.intel)),
        created_at=match.created_at or datetime.now(),
        responded_at=match.responded_at,
    )
    _write(statement, "Could not save the match")
    return match_id


def load_match(match_id: str) -> MatchRecord | None:
    row = _first_row(
        select(matches).where(matches.c.id == match_id), "Could not load the match"
    )
    return None if row is None else _match_from_row(row)


def load_match_by_token(token: str) -> MatchRecord | None:
    """Find the match behind a donor's confirm link. A blank token matches nothing."""
    if not token:
        return None
    row = _first_row(
        select(matches).where(matches.c.token == token), "Could not load the match"
    )
    return None if row is None else _match_from_row(row)


def update_match_state(
    match_id: str, state: MatchState, responded_at: datetime | None = None
) -> None:
    statement = (
        update(matches)
        .where(matches.c.id == match_id)
        .values(state=state.value, responded_at=responded_at or datetime.now())
    )
    _write(statement, "Could not update the match")


def matches_for_request(request_id: int) -> list[MatchRecord]:
    rows = _all_rows(
        select(matches)
        .where(matches.c.request_id == request_id)
        .order_by(matches.c.created_at),
        "Could not list the matches",
    )
    return [_match_from_row(row) for row in rows]


def open_matches_older_than(hours: int, now: datetime | None = None) -> list[MatchRecord]:
    """Matches still waiting on a donor past the confirmation window. For the sweep."""
    if hours < MINIMUM_SWEEP_HOURS:
        raise RepositoryError("The timeout window cannot be negative.")
    cutoff = (now or datetime.now()) - timedelta(hours=hours)
    criteria = and_(
        matches.c.state.in_(OPEN_MATCH_STATES), matches.c.created_at <= cutoff
    )
    rows = _all_rows(
        select(matches).where(criteria).order_by(matches.c.created_at),
        "Could not list the timed-out matches",
    )
    return [_match_from_row(row) for row in rows]


def offers_for_support(kind_value: str, terms: tuple[str, ...], today: date | None = None) -> list[Offer]:
    """Open offers that could satisfy a support requirement.

    Matches the categorised support kind first, then falls back to the
    requirement's own search terms against the donor's free-text device type,
    so an uncategorised offer is still findable.
    """
    available = (
        offers.c.quantity_offered - offers.c.quantity_reserved - offers.c.quantity_shipped
    )
    criteria = and_(
        offers.c.status == OfferStatus.OPEN.value,
        available > 0,
        or_(offers.c.available_until.is_(None),
            offers.c.available_until >= (today or date.today())),
    )
    rows = _all_rows(
        select(offers).where(criteria).order_by(offers.c.created_at),
        "Could not list the support offers",
    )
    found = []
    for row in rows:
        stored_kind = (row["support_kind"] or "").strip().lower()
        haystack = f"{row['device_type']} {row['device_label']}".lower()
        if stored_kind == kind_value.lower() or any(t in haystack for t in terms):
            found.append(_offer_from_row(row))
    return found
