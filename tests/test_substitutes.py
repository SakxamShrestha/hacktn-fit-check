"""Offering a clinically equivalent device when the asked-for kind all fails.

Nothing here touches the database or the network: offers are built by hand and
both the inventory lookup and the ranker are injected.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.domain.facility import FacilityProfile
from app.domain.marketplace import GeoPoint, Offer
from app.domain.result import Outcome, Verdict
from app.matching import substitutes
from app.matching.pipeline import Candidate
from app.matching.substitutes import (
    Substitute,
    SubstituteOption,
    find_substitutes,
    substitutes_for,
)
from tests.factories import device, facility

SOMEWHERE = GeoPoint(country="Testland", city="Town", latitude=0.0, longitude=0.0)

REQUESTED = "x-ray system"
EQUIVALENT = "mobile x-ray unit"


@pytest.fixture(autouse=True)
def _clear_table():
    """Each test reloads the table, so a patched path cannot leak."""
    substitutes._table.cache_clear()
    yield
    substitutes._table.cache_clear()


def offer(device_type: str, offer_id: int = 1) -> Offer:
    return Offer(
        donor_name=f"Donor {offer_id}",
        donor_email="d@example.org",
        device=device(),
        device_type=device_type,
        origin=SOMEWHERE,
        quantity_offered=1,
        id=offer_id,
    )


def candidate(held: Offer, outcome: Outcome = Outcome.WORKS) -> Candidate:
    return Candidate(offer=held, verdict=Verdict(outcome=outcome, results=()))


def inventory(stock: dict[str, list[Offer]], asked: list[str] | None = None):
    """A stand-in for repo.open_offers_for_type, recording what it was asked."""

    def find_offers(device_type: str) -> list[Offer]:
        if asked is not None:
            asked.append(device_type)
        return stock.get(device_type, [])

    return find_offers


def ranker(outcomes: dict[int, Outcome] | None = None):
    """A stand-in for rank_candidates that screens nothing out by itself.

    The real pipeline already drops blockers. This one keeps them, so that the
    filtering under test is the module's own and not the ranker's.
    """
    verdicts = outcomes or {}

    def rank(_: FacilityProfile, offers) -> list[Candidate]:
        return [candidate(held, verdicts.get(held.id, Outcome.WORKS)) for held in offers]

    return rank


# --------------------------------------------------------------------------
# substitutes_for: the curated table, exact on the normalised name
# --------------------------------------------------------------------------


def test_a_known_device_type_returns_its_curated_equivalent():
    # Arrange / Act
    found = substitutes_for(REQUESTED)

    # Assert
    assert len(found) == 1
    assert found[0].offered == EQUIVALENT
    assert found[0].reason


def test_the_second_curated_pair_is_the_portable_ultrasound():
    found = substitutes_for("ultrasound scanner")

    assert [pair.offered for pair in found] == ["portable ultrasound"]


def test_an_unknown_device_type_returns_no_substitutes():
    # Arrange: a real device, deliberately absent from the table.
    unknown = "haemodialysis system"

    # Act
    found = substitutes_for(unknown)

    # Assert
    assert found == ()


def test_a_near_miss_on_a_known_type_returns_nothing_rather_than_guessing():
    """Exact matching only: the table is a set of claims, not a similarity score."""
    assert substitutes_for("x-ray systems") == ()
    assert substitutes_for("portable x-ray system") == ()


def test_a_blank_device_type_returns_no_substitutes():
    assert substitutes_for("   ") == ()


def test_normalisation_means_a_messily_typed_request_still_matches():
    # Arrange
    messy = "  X-Ray   System "

    # Act
    found = substitutes_for(messy)

    # Assert
    assert found == substitutes_for(REQUESTED)
    assert found != ()


def test_the_pairs_are_one_way_so_the_reverse_is_not_claimed():
    """A clinic that asked for the mobile unit needs the mobility it asked for."""
    assert substitutes_for(EQUIVALENT) == ()
    assert substitutes_for("portable ultrasound") == ()


# --------------------------------------------------------------------------
# find_substitutes: only equivalents a donor holds and the checks cleared
# --------------------------------------------------------------------------


def test_an_equivalent_a_donor_holds_is_offered_with_its_candidate():
    # Arrange
    held = offer(EQUIVALENT)

    # Act
    options = find_substitutes(
        REQUESTED, facility(), inventory({EQUIVALENT: [held]}), rank=ranker()
    )

    # Assert
    assert len(options) == 1
    assert isinstance(options[0], SubstituteOption)
    assert options[0].candidate.offer is held
    assert options[0].device_type == EQUIVALENT


def test_the_curated_reason_survives_to_the_option():
    # Arrange
    held = offer(EQUIVALENT)

    # Act
    options = find_substitutes(
        REQUESTED, facility(), inventory({EQUIVALENT: [held]}), rank=ranker()
    )

    # Assert
    assert isinstance(options[0].substitute, Substitute)
    assert options[0].reason == options[0].substitute.reason
    assert "single-phase" in options[0].reason


def test_a_substitute_that_also_fails_the_checks_is_not_offered():
    # Arrange: the only mobile unit on the shelf is a blocker here too.
    held = offer(EQUIVALENT, offer_id=7)

    # Act
    options = find_substitutes(
        REQUESTED,
        facility(),
        inventory({EQUIVALENT: [held]}),
        rank=ranker({7: Outcome.WONT_WORK}),
    )

    # Assert
    assert options == ()


def test_a_substitute_that_works_outright_sorts_ahead_of_one_that_needs_extras():
    # Arrange: the conditional unit is listed first, so order cannot be luck.
    conditional = offer(EQUIVALENT, offer_id=1)
    outright = offer(EQUIVALENT, offer_id=2)

    # Act
    options = find_substitutes(
        REQUESTED,
        facility(),
        inventory({EQUIVALENT: [conditional, outright]}),
        rank=ranker({1: Outcome.WORKS_IF, 2: Outcome.WORKS}),
    )

    # Assert
    assert [option.candidate.offer.id for option in options] == [2, 1]
    assert options[0].candidate.verdict.outcome is Outcome.WORKS


def test_an_empty_shelf_of_equivalents_yields_no_options():
    options = find_substitutes(REQUESTED, facility(), inventory({}), rank=ranker())

    assert options == ()


def test_the_inventory_is_searched_for_the_equivalent_not_the_requested_type():
    # Arrange
    asked: list[str] = []

    # Act
    find_substitutes(
        REQUESTED, facility(), inventory({}, asked), rank=ranker()
    )

    # Assert
    assert asked == [EQUIVALENT]


def test_an_unknown_device_type_never_touches_the_inventory():
    # Arrange
    asked: list[str] = []

    # Act
    options = find_substitutes(
        "haemodialysis system", facility(), inventory({}, asked), rank=ranker()
    )

    # Assert
    assert options == ()
    assert asked == []


def test_find_substitutes_normalises_the_requested_type_too():
    # Arrange
    held = offer(EQUIVALENT)

    # Act
    options = find_substitutes(
        "  X-Ray   System ", facility(), inventory({EQUIVALENT: [held]}), rank=ranker()
    )

    # Assert
    assert len(options) == 1


# --------------------------------------------------------------------------
# The committed table: shape, and degrading instead of crashing
# --------------------------------------------------------------------------


def test_every_committed_pair_states_a_reason_and_points_somewhere_else():
    # Arrange / Act
    pairs = [pair for pairs in substitutes._table().values() for pair in pairs]

    # Assert
    assert pairs
    for pair in pairs:
        assert pair.reason.strip()
        assert pair.requested != pair.offered


def test_a_missing_table_degrades_to_no_substitutes_instead_of_raising(tmp_path):
    # Arrange
    missing = tmp_path / "gone.json"

    # Act
    with patch.object(substitutes, "DATA_PATH", missing):
        found = substitutes_for(REQUESTED)

    # Assert
    assert found == ()


def test_a_malformed_table_degrades_to_no_substitutes_instead_of_raising(tmp_path):
    # Arrange
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    # Act
    with patch.object(substitutes, "DATA_PATH", broken):
        found = substitutes_for(REQUESTED)

    # Assert
    assert found == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"source": {"note": "no pairs at all"}},
        {"pairs": "not a list"},
        {"pairs": ["not an object"]},
        {"pairs": [{"from": REQUESTED}]},
        {"pairs": [{"from": REQUESTED, "to": REQUESTED, "reason": "circular"}]},
    ],
)
def test_an_unusable_table_entry_is_dropped_rather_than_crashing(tmp_path, payload):
    # Arrange
    table = tmp_path / "substitutes.json"
    table.write_text(json.dumps(payload), encoding="utf-8")

    # Act
    with patch.object(substitutes, "DATA_PATH", table):
        found = substitutes_for(REQUESTED)

    # Assert
    assert found == ()


def test_a_pair_with_no_stated_reason_is_dropped_rather_than_offered(tmp_path):
    # Arrange: an unexplained equivalence is an unattributed clinical claim.
    table = tmp_path / "substitutes.json"
    table.write_text(
        json.dumps({"pairs": [{"from": REQUESTED, "to": EQUIVALENT}]}),
        encoding="utf-8",
    )

    # Act
    with patch.object(substitutes, "DATA_PATH", table):
        found = substitutes_for(REQUESTED)

    # Assert
    assert found == ()


def test_the_substitute_and_the_option_are_both_frozen():
    # Arrange
    pair = substitutes_for(REQUESTED)[0]
    option = SubstituteOption(substitute=pair, candidate=candidate(offer(EQUIVALENT)))

    # Act / Assert
    with pytest.raises(Exception):
        pair.offered = "something else"
    with pytest.raises(Exception):
        option.substitute = pair
