from app.checks.power import check_frequency, check_supply_phase, check_voltage
from app.checks.runner import ALL_CHECKS, ROUTE_CHECKS
from app.domain.provenance import Provenance, UNKNOWN, fact
from app.domain.result import CheckStatus
from tests.factories import device, facility, intel


def test_frequency_passes_when_the_device_is_rated_for_both_grids():
    # Arrange
    dev = device(frequencies_hz=fact([50, 60], Provenance.NAMEPLATE))
    site = facility(mains_frequency_hz=50)

    # Act
    result = check_frequency(dev, site, intel())

    # Assert
    assert result.status is CheckStatus.PASS


def test_frequency_fails_when_a_60_hz_device_meets_a_50_hz_grid():
    dev = device(frequencies_hz=fact([60], Provenance.NAMEPLATE))
    site = facility(mains_frequency_hz=50)

    result = check_frequency(dev, site, intel())

    assert result.status is CheckStatus.FAIL
    assert "60" in result.detail and "50" in result.detail


def test_frequency_asks_a_question_when_the_rating_is_unknown():
    dev = device(frequencies_hz=UNKNOWN)

    result = check_frequency(dev, facility(), intel())

    assert result.status is CheckStatus.NEEDS_INFO
    assert result.question


def test_voltage_passes_for_an_auto_ranging_supply():
    dev = device(
        voltage_min_v=fact(100, Provenance.NAMEPLATE),
        voltage_max_v=fact(240, Provenance.NAMEPLATE),
    )
    site = facility(mains_voltage_v=240)

    result = check_voltage(dev, site, intel())

    assert result.status is CheckStatus.PASS


def test_voltage_is_mitigable_with_a_transformer_sized_above_the_draw():
    dev = device(
        voltage_min_v=fact(110, Provenance.NAMEPLATE),
        voltage_max_v=fact(120, Provenance.NAMEPLATE),
        power_watts=fact(400, Provenance.NAMEPLATE),
    )
    site = facility(mains_voltage_v=240)

    result = check_voltage(dev, site, intel())

    assert result.status is CheckStatus.MITIGABLE
    assert "500 W" in result.fix


def test_phase_asks_a_question_when_the_plate_was_not_read():
    # Arrange
    dev = device(needs_three_phase=UNKNOWN)

    # Act
    result = check_supply_phase(dev, facility(), intel())

    # Assert
    assert result.status is CheckStatus.NEEDS_INFO
    assert "3 PH" in result.question


def test_phase_fails_when_a_stationary_unit_meets_a_single_phase_clinic():
    # Arrange
    dev = device(needs_three_phase=fact(True, Provenance.NAMEPLATE))
    site = facility(has_three_phase_power=False)

    # Act
    result = check_supply_phase(dev, site, intel())

    # Assert
    assert result.status is CheckStatus.FAIL
    assert "rewiring" in result.detail
    assert "single-phase" in result.fix


def test_phase_passes_when_the_clinic_already_has_three_phase_power():
    dev = device(needs_three_phase=fact(True, Provenance.NAMEPLATE))
    site = facility(has_three_phase_power=True)

    result = check_supply_phase(dev, site, intel())

    assert result.status is CheckStatus.PASS


def test_phase_passes_for_a_mobile_unit_on_a_single_phase_clinic():
    dev = device(needs_three_phase=fact(False, Provenance.NAMEPLATE))
    site = facility(has_three_phase_power=False)

    result = check_supply_phase(dev, site, intel())

    assert result.status is CheckStatus.PASS


def test_phase_raises_no_requirement_because_no_donor_can_ship_one():
    # A phase converter is not a shippable accessory, so the check must not
    # put one into the inventory search.
    dev = device(needs_three_phase=fact(True, Provenance.NAMEPLATE))
    site = facility(has_three_phase_power=False)

    result = check_supply_phase(dev, site, intel())

    assert result.requirement is None


def test_the_registry_holds_fifteen_deterministic_checks():
    # The top bar reads this number off the registry, so the registry is
    # what the test pins.
    total = len(ALL_CHECKS) + len(ROUTE_CHECKS)

    assert total == 15
