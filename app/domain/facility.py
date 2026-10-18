"""The facility side of the match: conditions only the clinic can report."""

from __future__ import annotations

from dataclasses import dataclass, field

# Above this many outages a week, equipment that assumes steady power is at risk.
OUTAGE_RISK_THRESHOLD_PER_WEEK = 3


@dataclass(frozen=True)
class FacilityProfile:
    """A clinic's real conditions. Stated by the clinic, so these are plain values."""

    slug: str
    name: str
    country: str

    mains_voltage_v: int
    mains_frequency_hz: int
    plug_types: tuple[str, ...] = ()

    ambient_temp_max_c: float = 30.0
    humidity_max_pct: float = 70.0
    has_air_conditioning: bool = False

    outages_per_week: int = 0
    has_voltage_stabilizer: bool = False
    has_generator: bool = False
    has_three_phase_power: bool = False

    has_oxygen_supply: bool = False
    has_distilled_water: bool = False
    has_compressed_air: bool = False

    has_biomed_technician: bool = False
    trained_operators: bool = False

    staff_languages: tuple[str, ...] = ("English",)
    notes: str = ""

    latitude: float | None = None
    longitude: float | None = None

    @property
    def power_is_unstable(self) -> bool:
        if self.has_generator and self.has_voltage_stabilizer:
            return False
        return self.outages_per_week >= OUTAGE_RISK_THRESHOLD_PER_WEEK

    @property
    def effective_indoor_temp_max_c(self) -> float:
        """Air conditioning pulls the room below the outdoor maximum."""
        if self.has_air_conditioning:
            return min(self.ambient_temp_max_c, 24.0)
        return self.ambient_temp_max_c
