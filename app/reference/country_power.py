"""Grid voltage, frequency and plug types by country.

The table is committed to the repo at data/country_power.csv so the platform
works with no network. It is derived from Wikipedia's "Mains electricity by
country" and the PlugVoltage dataset, CC BY-SA 4.0; see the file's upstream
README for the full credit.

One row per country or territory, keyed by ISO 3166-1 alpha-2 code. A country
with more than one nominal voltage lists them space-separated ("120 127 240"),
so the single int this module reports is the upper bound: the worst case a
device plugged into that grid has to survive.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

DATA_PATH = PROJECT_ROOT / "data" / "country_power.csv"
REQUIRED_COLUMNS = ("code", "country", "main_plugs", "all_plugs", "voltages", "frequencies")
PLUG_SEPARATOR = " "
VALUE_SEPARATOR = re.compile(r"[\s/]+")
RANGE_SEPARATOR = re.compile(r"\s*[-–—]\s*")
AMPERSAND_WORD = re.compile(r"\s+and\s+")


class CountryPowerDataError(RuntimeError):
    """Raised when the committed country power table is missing or unreadable."""


@dataclass(frozen=True)
class CountryPower:
    """What the wall socket does in one country."""

    country_code: str
    country_name: str
    mains_voltage_v: int
    mains_frequency_hz: int
    plug_types: tuple[str, ...]
    mains_voltage_raw: str = ""
    mains_frequency_raw: str = ""

    @property
    def is_multi_voltage(self) -> bool:
        """True when the country runs more than one nominal socket voltage."""
        return len(parse_values(self.mains_voltage_raw)) > 1


def normalise_country(name: str) -> str:
    """Lowercase and collapse whitespace so '  Sri  Lanka ' matches 'Sri Lanka'."""
    collapsed = re.sub(r"\s+", " ", name).strip().lower()
    return AMPERSAND_WORD.sub(" & ", collapsed)


def parse_values(raw: str) -> tuple[int, ...]:
    """Read '230', '220-240' and '120 127 240' alike into every value listed.

    Ranges are expanded to their endpoints only: the source states nominal
    voltages, not a continuous span, and the endpoints are what callers need.
    """
    text = (raw or "").strip()
    if not text:
        return ()
    values: list[int] = []
    for token in VALUE_SEPARATOR.split(text):
        for part in RANGE_SEPARATOR.split(token):
            digits = re.sub(r"[^0-9]", "", part)
            if digits:
                values.append(int(digits))
    return tuple(values)


def representative_value(raw: str) -> int | None:
    """The upper bound of everything listed, or None when nothing parses."""
    values = parse_values(raw)
    return max(values) if values else None


def _build_record(row: Mapping[str, str]) -> CountryPower | None:
    voltage = representative_value(row.get("voltages", ""))
    frequency = representative_value(row.get("frequencies", ""))
    code = (row.get("code") or "").strip()
    name = (row.get("country") or "").strip()
    if voltage is None or frequency is None or not code or not name:
        logger.warning("Skipping unusable country power row: %r", dict(row))
        return None
    plugs = (row.get("all_plugs") or row.get("main_plugs") or "").split(PLUG_SEPARATOR)
    return CountryPower(
        country_code=code.upper(),
        country_name=name,
        mains_voltage_v=voltage,
        mains_frequency_hz=frequency,
        plug_types=tuple(p.strip().upper() for p in plugs if p.strip()),
        mains_voltage_raw=(row.get("voltages") or "").strip(),
        mains_frequency_raw=(row.get("frequencies") or "").strip(),
    )


@lru_cache(maxsize=1)
def _index() -> Mapping[str, CountryPower]:
    """Load the table once, keyed by both ISO code and normalised country name."""
    path = Path(DATA_PATH)
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except FileNotFoundError as error:
        raise CountryPowerDataError(
            f"The country power table is missing at {path}. "
            "It ships with the repo; restore it from version control."
        ) from error
    except OSError as error:
        raise CountryPowerDataError(
            f"The country power table at {path} could not be read: {error}"
        ) from error

    if not rows:
        raise CountryPowerDataError(f"The country power table at {path} is empty.")
    missing = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
    if missing:
        raise CountryPowerDataError(
            f"The country power table at {path} is missing columns: {', '.join(missing)}"
        )

    index: dict[str, CountryPower] = {}
    for row in rows:
        record = _build_record(row)
        if record is None:
            continue
        index[record.country_code.lower()] = record
        index.setdefault(normalise_country(record.country_name), record)
    return index


def lookup(country: str) -> CountryPower | None:
    """Find a country by English name or ISO 3166-1 alpha-2 code. None on a miss."""
    if not country or not country.strip():
        return None
    return _index().get(normalise_country(country))


def country_count() -> int:
    """How many countries and territories the table holds."""
    return len({record.country_code for record in _index().values()})


# Centroids for the countries this platform ships between most often, so a
# shipping distance can be estimated before a facility gives its coordinates.
# Decimal degrees, WGS 84, rounded to the country's approximate centre.
_CENTROIDS: tuple[tuple[str, str, float, float], ...] = (
    ("UG", "Uganda", 1.37, 32.29),
    ("HT", "Haiti", 18.97, -72.29),
    ("NP", "Nepal", 28.39, 84.12),
    ("US", "United States", 39.83, -98.58),
    ("IN", "India", 22.35, 78.67),
    ("KE", "Kenya", 0.53, 37.86),
    ("NG", "Nigeria", 9.08, 8.68),
    ("GH", "Ghana", 7.95, -1.02),
    ("BD", "Bangladesh", 23.68, 90.36),
    ("PH", "Philippines", 12.88, 121.77),
    ("GT", "Guatemala", 15.78, -90.23),
    ("MW", "Malawi", -13.25, 34.30),
    ("TZ", "Tanzania", -6.37, 34.89),
    ("ZM", "Zambia", -13.13, 27.85),
    ("ET", "Ethiopia", 9.15, 40.49),
)

_CENTROID_INDEX: Mapping[str, tuple[float, float]] = {
    key: (lat, lon)
    for code, name, lat, lon in _CENTROIDS
    for key in (code.lower(), normalise_country(name))
}


def known_countries() -> tuple[str, ...]:
    """Every country the power table carries, alphabetically.

    Offered as a list rather than a text box so a clinic cannot name a country
    the table has no grid data for: typing one silently lost the voltage and
    frequency lookup the whole screening depends on.
    """
    return tuple(sorted({row.country_name for row in _index().values()}))


def country_centroid(country: str) -> tuple[float, float] | None:
    """Approximate (latitude, longitude) of a country's centre. None on a miss."""
    if not country or not country.strip():
        return None
    key = normalise_country(country)
    direct = _CENTROID_INDEX.get(key)
    if direct is not None:
        return direct
    # Fall back to the power table so an alias it knows resolves to a centroid.
    record = lookup(country)
    if record is None:
        return None
    return _CENTROID_INDEX.get(record.country_code.lower())
