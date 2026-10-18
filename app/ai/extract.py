"""Turn whatever the donor gave us into DeviceFacts with honest provenance."""

from __future__ import annotations

import base64
from typing import Any

from app.ai.client import CLIENT, GenAiClient, GenAiError
from app.ai.prompts import (
    EXTRACTION_IMAGE_NOTE,
    EXTRACTION_SCHEMA,
    EXTRACTION_SYSTEM,
    EXTRACTION_USER,
)
from app.config import SETTINGS
from app.domain.device import DeviceFacts
from app.domain.provenance import UNKNOWN, Provenance, fact

MAX_IMAGE_BYTES = 6 * 1024 * 1024

SOURCE_BY_NAME = {
    "nameplate": Provenance.NAMEPLATE,
    "spec sheet": Provenance.SPEC_SHEET,
    "spec_sheet": Provenance.SPEC_SHEET,
    "model knowledge": Provenance.MODEL_KNOWLEDGE,
    "model_knowledge": Provenance.MODEL_KNOWLEDGE,
    "donor": Provenance.DONOR_STATED,
    "unknown": Provenance.UNKNOWN,
}

FIELDS = (
    "manufacturer",
    "model",
    "year_of_manufacture",
    "voltage_min_v",
    "voltage_max_v",
    "frequencies_hz",
    "power_watts",
    "needs_three_phase",
    "needs_stable_power",
    "temp_min_c",
    "temp_max_c",
    "humidity_max_pct",
    "needs_oxygen",
    "needs_distilled_water",
    "needs_compressed_air",
    "consumables",
    "manual_languages",
    "operating_complexity",
)


def image_to_data_url(raw: bytes, content_type: str) -> str:
    """Encode an uploaded photo for the vision model, refusing oversized files."""
    if not raw:
        raise ValueError("The uploaded image was empty.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"That image is {len(raw) // 1024 // 1024} MB. Keep it under "
            f"{MAX_IMAGE_BYTES // 1024 // 1024} MB."
        )
    media_type = content_type if content_type.startswith("image/") else "image/jpeg"
    return f"data:{media_type};base64,{base64.b64encode(raw).decode('ascii')}"


def extract_device(
    *,
    typed_text: str = "",
    image_data_url: str = "",
    client: GenAiClient = CLIENT,
) -> DeviceFacts:
    """Ask the model for structured facts. Raises GenAiError when it cannot."""
    source_text = typed_text.strip()
    if not source_text and not image_data_url:
        raise ValueError("Give a device name, some spec text, or a nameplate photo.")

    prompt = EXTRACTION_USER.format(
        schema=EXTRACTION_SCHEMA,
        source=source_text or "(no text supplied - read the image)",
    )
    system = EXTRACTION_SYSTEM
    model = ""
    if image_data_url:
        prompt = f"{EXTRACTION_IMAGE_NOTE}\n\n{prompt}"
        model = SETTINGS.vision_model

    payload = client.complete_json(
        prompt, system=system, model=model, image_data_url=image_data_url
    )
    return _to_device_facts(payload, source_text)


def _to_device_facts(payload: dict[str, Any], raw_input: str) -> DeviceFacts:
    sources = payload.get("sources") or {}
    values: dict[str, Any] = {}
    for name in FIELDS:
        raw_value = payload.get(name)
        if raw_value is None:
            values[name] = UNKNOWN
            continue
        source_name = str(sources.get(name, "model knowledge")).lower()
        values[name] = fact(
            raw_value, SOURCE_BY_NAME.get(source_name, Provenance.MODEL_KNOWLEDGE)
        )
    return DeviceFacts(raw_input=raw_input, **values)
