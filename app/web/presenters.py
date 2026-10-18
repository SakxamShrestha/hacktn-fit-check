"""Turn domain objects into the few strings the templates render.

Keeps formatting decisions out of Jinja, where they are hard to test.
"""

from __future__ import annotations

from app.domain.device import DeviceFacts
from app.domain.result import CheckStatus, Outcome

TONE_BY_STATUS = {
    CheckStatus.PASS: "good",
    CheckStatus.FAIL: "bad",
    CheckStatus.MITIGABLE: "hold",
    CheckStatus.NEEDS_INFO: "ask",
}

TONE_BY_OUTCOME = {
    Outcome.WORKS: "good",
    Outcome.WORKS_IF: "hold",
    Outcome.WONT_WORK: "bad",
}

# The log reads outcomes back as stored strings, not enum members.
TONE_BY_OUTCOME_VALUE = {o.value: tone for o, tone in TONE_BY_OUTCOME.items()}


def _range(low, high, unit: str) -> str | None:
    if not (low.known and high.known):
        return None
    if low.value == high.value:
        return f"{low.value:g} {unit}"
    return f"{low.value:g}–{high.value:g} {unit}"


def identity_pairs(device: DeviceFacts) -> list[tuple[str, str]]:
    """Label and value pairs for the rating-plate block. Unknowns are left out."""
    pairs: list[tuple[str, str]] = []

    supply = _range(device.voltage_min_v, device.voltage_max_v, "V")
    if supply:
        pairs.append(("Supply", supply))

    if device.frequencies_hz.known:
        shown = "/".join(str(v) for v in device.frequencies_hz.value)
        pairs.append(("Frequency", f"{shown} Hz"))

    if device.power_watts.known:
        pairs.append(("Draw", f"{device.power_watts.value:g} VA"))

    climate = _range(device.temp_min_c, device.temp_max_c, "°C")
    if climate:
        pairs.append(("Rated for", climate))

    if device.humidity_max_pct.known:
        pairs.append(("Humidity", f"to {device.humidity_max_pct.value:g}%"))

    if device.year_of_manufacture.known:
        pairs.append(("Made", str(device.year_of_manufacture.value)))

    return pairs


def device_kind(device: DeviceFacts) -> str:
    """What the donor called it, minus the name already shown above it."""
    raw = (device.raw_input or "").strip().splitlines()
    if not raw:
        return ""
    line = raw[0].strip()
    label = device.label
    if label and label.lower() in line.lower():
        start = line.lower().index(label.lower())
        line = (line[:start] + line[start + len(label):]).strip(" ,.-\u2013")
    return line[:90]
