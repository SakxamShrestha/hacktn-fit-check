"""The device side of the match: facts about one piece of equipment."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.domain.provenance import UNKNOWN, Fact, Provenance, fact

# IEC 60601-1: unless the instructions for use say otherwise, medical electrical
# equipment is only required to work from 5 to 40 C and 15 to 93 % humidity.
IEC_DEFAULT_TEMP_MIN_C = 5.0
IEC_DEFAULT_TEMP_MAX_C = 40.0
IEC_DEFAULT_HUMIDITY_MAX_PCT = 93.0


@dataclass(frozen=True)
class DeviceFacts:
    """Everything known about one device. Every field is a Fact, never a bare value."""

    manufacturer: Fact = UNKNOWN
    model: Fact = UNKNOWN
    year_of_manufacture: Fact = UNKNOWN

    voltage_min_v: Fact = UNKNOWN
    voltage_max_v: Fact = UNKNOWN
    frequencies_hz: Fact = UNKNOWN
    power_watts: Fact = UNKNOWN
    needs_three_phase: Fact = UNKNOWN
    needs_stable_power: Fact = UNKNOWN

    temp_min_c: Fact = UNKNOWN
    temp_max_c: Fact = UNKNOWN
    humidity_max_pct: Fact = UNKNOWN

    needs_oxygen: Fact = UNKNOWN
    needs_distilled_water: Fact = UNKNOWN
    needs_compressed_air: Fact = UNKNOWN

    consumables: Fact = UNKNOWN
    expiry_date: Fact = UNKNOWN
    manual_languages: Fact = UNKNOWN
    operating_complexity: Fact = UNKNOWN

    raw_input: str = ""

    @property
    def label(self) -> str:
        parts = [str(self.manufacturer.value or ""), str(self.model.value or "")]
        name = " ".join(p for p in parts if p).strip()
        return name or "this device"

    def with_defaults(self) -> DeviceFacts:
        """Fill the climate fields from the standard's default when nothing is stated.

        This is not a guess. A device with no stated range is required to work
        only inside the default range, so the default is the honest reading.
        """
        updates: dict[str, Any] = {}
        if not self.temp_min_c.known:
            updates["temp_min_c"] = fact(
                IEC_DEFAULT_TEMP_MIN_C, Provenance.STANDARD_DEFAULT
            )
        if not self.temp_max_c.known:
            updates["temp_max_c"] = fact(
                IEC_DEFAULT_TEMP_MAX_C, Provenance.STANDARD_DEFAULT
            )
        if not self.humidity_max_pct.known:
            updates["humidity_max_pct"] = fact(
                IEC_DEFAULT_HUMIDITY_MAX_PCT, Provenance.STANDARD_DEFAULT
            )
        return replace(self, **updates) if updates else self

    def unknown_fields(self) -> tuple[str, ...]:
        names = (
            "frequencies_hz",
            "voltage_min_v",
            "voltage_max_v",
            "needs_three_phase",
            "year_of_manufacture",
            "consumables",
        )
        return tuple(n for n in names if not getattr(self, n).known)


@dataclass(frozen=True)
class SupplyIntel:
    """What the model found about supporting this device in one country.

    Separate from DeviceFacts because this is research about a country, not a
    property of the machine.
    """

    country: str = ""
    consumables_available: Fact = UNKNOWN
    spare_parts_available: Fact = UNKNOWN
    manufacturer_supported: Fact = UNKNOWN
    reasoning: str = ""
