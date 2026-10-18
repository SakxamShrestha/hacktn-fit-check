"""Power checks. Pure arithmetic once the facts are in hand."""

from __future__ import annotations

from app.domain.device import DeviceFacts, SupplyIntel
from app.domain.facility import FacilityProfile
from app.domain.requirement import Requirement, RequirementKind
from app.domain.result import CheckResult, CheckStatus

TRANSFORMER_HEADROOM = 1.25

# Named so the phase check describes a supply the same way every time it
# says it, in a question and in a verdict a donor reads side by side.
SINGLE_PHASE = "single-phase"
THREE_PHASE = "three-phase"


def check_frequency(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    rated = device.frequencies_hz
    if not rated.known:
        return CheckResult(
            key="frequency",
            label="Supply frequency",
            status=CheckStatus.NEEDS_INFO,
            detail=f"The grid at {facility.name} is {facility.mains_frequency_hz} Hz. "
            "The device's rated frequency is not known.",
            question="What supply frequency is the device rated for? "
            "The nameplate prints it in Hz, next to the voltage.",
            source=rated.source,
        )

    rated_values = tuple(rated.value)
    if facility.mains_frequency_hz in rated_values:
        shown = "/".join(str(v) for v in rated_values)
        return CheckResult(
            key="frequency",
            label="Supply frequency",
            status=CheckStatus.PASS,
            detail=f"Rated {shown} Hz, and the grid is {facility.mains_frequency_hz} Hz.",
            source=rated.source,
        )

    shown = "/".join(str(v) for v in rated_values)
    return CheckResult(
        key="frequency",
        label="Supply frequency",
        status=CheckStatus.FAIL,
        detail=f"Rated {shown} Hz, but the grid is {facility.mains_frequency_hz} Hz. "
        "A motor built for one runs slow or burns out on the other.",
        fix="A rotary frequency converter, which normally costs more than a "
        "surplus device is worth. Send a different device instead.",
        source=rated.source,
    )


def check_voltage(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    low, high = device.voltage_min_v, device.voltage_max_v
    mains = facility.mains_voltage_v

    if not (low.known and high.known):
        return CheckResult(
            key="voltage",
            label="Supply voltage",
            status=CheckStatus.NEEDS_INFO,
            detail=f"The grid at {facility.name} is {mains} V. "
            "The device's rated voltage range is not known.",
            question="What voltage range is the device rated for? "
            "A nameplate reading 100-240 V means it handles both grids.",
            source=low.source,
        )

    if low.value <= mains <= high.value:
        return CheckResult(
            key="voltage",
            label="Supply voltage",
            status=CheckStatus.PASS,
            detail=f"Rated {low.value}-{high.value} V, which covers the {mains} V grid.",
            source=low.source,
        )

    watts = device.power_watts
    rating = (
        f"at least {int(watts.value * TRANSFORMER_HEADROOM)} W"
        if watts.known
        else "rated above the device's power draw"
    )
    return CheckResult(
        key="voltage",
        label="Supply voltage",
        status=CheckStatus.MITIGABLE,
        detail=f"Rated {low.value}-{high.value} V, but the grid is {mains} V.",
        fix=f"A step transformer {rating}, shipped with the device.",
        source=low.source,
        requirement=Requirement(RequirementKind.TRANSFORMER,
                                 f"A step transformer {rating}.", "voltage"),
    )


def check_supply_phase(
    device: DeviceFacts, facility: FacilityProfile, intel: SupplyIntel
) -> CheckResult:
    needs = device.needs_three_phase
    supply = THREE_PHASE if facility.has_three_phase_power else SINGLE_PHASE

    if not needs.known:
        return CheckResult(
            key="phase",
            label="Supply phase",
            status=CheckStatus.NEEDS_INFO,
            detail=f"The supply at {facility.name} is {supply}. "
            "Whether the device needs three-phase power is not known.",
            question="Does the device need a three-phase supply? "
            'The rating plate prints the phase beside the voltage, usually as '
            '"3~" or "3 PH"; a single-phase plate reads "1~" or says nothing.',
            source=needs.source,
        )

    if not needs.value:
        return CheckResult(
            key="phase",
            label="Supply phase",
            status=CheckStatus.PASS,
            detail=f"Runs on a {SINGLE_PHASE} supply, which {facility.name} has.",
            source=needs.source,
        )

    if facility.has_three_phase_power:
        return CheckResult(
            key="phase",
            label="Supply phase",
            status=CheckStatus.PASS,
            detail=f"Needs a {THREE_PHASE} supply, and {facility.name} has one.",
            source=needs.source,
        )

    return CheckResult(
        key="phase",
        label="Supply phase",
        status=CheckStatus.FAIL,
        detail=f"Needs a {THREE_PHASE} supply, and {facility.name} is "
        f"{SINGLE_PHASE} only. A phase converter sized for a machine this big "
        "is not an accessory that ships in the crate, it is a rewiring job "
        "for the building and a new connection from the utility.",
        fix="Nothing shipped alongside closes this gap. Send a "
        f"{SINGLE_PHASE} device of the same kind instead, such as a mobile "
        "unit, which runs from the sockets the clinic already has.",
        source=needs.source,
    )
