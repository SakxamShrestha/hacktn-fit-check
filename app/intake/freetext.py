"""Turn a clinic's typed wish-list into request lines.

The model does one narrow job here: read a sentence and list the kinds of
equipment in it with their counts. The prompt tells it, in as many words,
that suitability is none of its business - whether a device can actually run
at the facility is decided later by app.checks against real power, climate
and supply facts. When the model is unreachable the text is split
deterministically instead, so a clinic can always file its request.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from app.ai.client import CLIENT, GenAiClient, GenAiError
from app.domain.marketplace import DEFAULT_LINE_QUANTITY, MIN_LINE_QUANTITY, RequestLine
from app.intake.tables import to_quantity

LOGGER = logging.getLogger(__name__)

# One request, not a procurement catalogue.
MAX_ITEMS = 50
MAX_TOKENS = 1200

LISTING_SYSTEM = """\
You read a clinic's typed equipment wish-list and list what it asks for.

Rules:
- Reply with ONLY a JSON object, no prose and no code fence.
- Extract only what is written: the kind of equipment, and how many.
- Never judge whether an item is suitable, safe, useful or available, and
  never leave an item out because you doubt it. Another part of the system
  decides what the clinic can run; you are only reading the list.
- Never invent an item, a brand or a model the clinic did not type, and
  never merge two different kinds of equipment into one entry.
- "device_type" is the kind of equipment, lowercase and singular.
- A list of terms this platform already stocks may be supplied. When the
  clinic plainly means one of them, answer with that exact term, because it
  is what the inventory is searched on: "x-ray machine" becomes "x-ray
  system" if that term is listed. When nothing listed fits, keep the
  clinic's own words rather than forcing a match.
- "quantity" is a whole number, at least 1. Use 1 when no count is given."""

LISTING_USER = """\
{vocabulary}Wish-list:
{text}

Answer as: {{"items": [{{"device_type": "<kind of equipment>", "quantity": <whole number>}}]}}"""

# A typed list is separated by lines, commas, semicolons or the word "and".
SEPARATORS = re.compile(r"[\n\r,;&]+|\band\b", re.IGNORECASE)
BULLETS = "-*•·–— \t"
# "2 ultrasounds", "x3 ventilators", "3 x ventilators", "1. ventilator".
LEADING_COUNT = re.compile(r"^(?:x\s*)?(?P<count>\d+)\s*(?:x\b\s*)?[.):-]?\s*", re.IGNORECASE)
LEADING_NOISE = re.compile(r"^(?:(?:and|also|plus|a|an|the)\s+)+", re.IGNORECASE)

# At or below this a trailing "s" is likelier to be the word than a plural.
MIN_SINGULAR_LENGTH = 3
PLURAL_ENDINGS_TO_KEEP = ("ss", "us", "is", "as")


def parse_list(
    text: str,
    *,
    client: GenAiClient = CLIENT,
    vocabulary: Sequence[str] = (),
) -> tuple[RequestLine, ...]:
    """Read a typed wish-list into request lines. Empty text asks for nothing.

    `vocabulary` is the set of device types the platform actually stocks. It is
    passed in rather than looked up so this module stays free of the database.
    Without it a clinic writing "x-ray machine" produces a line that matches no
    offer and no substitute, because both are keyed on the stored wording.
    """
    source = text.strip()
    if not source:
        return ()

    known = ""
    if vocabulary:
        listed = ", ".join(sorted({term.strip() for term in vocabulary if term.strip()}))
        known = f"Terms this platform stocks: {listed}\n\n"

    try:
        payload = client.complete_json(
            LISTING_USER.format(text=source, vocabulary=known),
            system=LISTING_SYSTEM,
            max_tokens=MAX_TOKENS,
        )
    except GenAiError as exc:
        LOGGER.warning("free-text intake fell back to splitting the text: %s", exc)
        return _split_lines(source)

    lines = _lines_from_payload(payload)
    if not lines:
        LOGGER.warning("the model listed nothing usable; splitting the text instead")
        return _split_lines(source)
    return lines


def _lines_from_payload(payload: dict[str, object]) -> tuple[RequestLine, ...]:
    """Read the agreed shape, tolerating anything else by returning nothing."""
    items = payload.get("items")
    if not isinstance(items, list):
        return ()

    found: list[RequestLine] = []
    for item in items[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        name = _clean(str(item.get("device_type") or ""))
        if not name:
            continue
        found.append(RequestLine(device_type=name, quantity=to_quantity(item.get("quantity"))))
    return tuple(found)


def _split_lines(text: str) -> tuple[RequestLine, ...]:
    """Split a typed list without a model. Never raises, may return nothing."""
    found: list[RequestLine] = []
    for fragment in SEPARATORS.split(text):
        line = _line_from(fragment)
        if line is not None:
            found.append(line)
        if len(found) == MAX_ITEMS:
            LOGGER.warning("typed wish-list truncated to its first %d items", MAX_ITEMS)
            break
    return tuple(found)


def _line_from(fragment: str) -> RequestLine | None:
    """One fragment of a typed list, or None when it names no equipment."""
    rest = LEADING_NOISE.sub("", fragment.strip().strip(BULLETS).strip())
    quantity = DEFAULT_LINE_QUANTITY
    counted = LEADING_COUNT.match(rest)
    if counted is not None:
        quantity = to_quantity(counted.group("count"))
        rest = LEADING_NOISE.sub("", rest[counted.end() :].strip())

    name = _clean(rest)
    if not name:
        return None
    if quantity > MIN_LINE_QUANTITY:
        name = _singular(name)
    return RequestLine(device_type=name, quantity=quantity)


def _clean(name: str) -> str:
    return name.strip().strip(BULLETS).strip()


def _singular(name: str) -> str:
    """Drop a plural "s" the clinic typed, so "2 ultrasounds" reads right."""
    if len(name) <= MIN_SINGULAR_LENGTH or not name.endswith("s"):
        return name
    if name.endswith(PLURAL_ENDINGS_TO_KEEP):
        return name
    return name[:-1]
