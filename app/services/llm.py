"""LLM vision service — sends the nameplate image directly to Groq's vision model."""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

from groq import Groq

from app.config import get_settings

logger = logging.getLogger(__name__)

_PROMPT = """\
This is a photo of an industrial equipment nameplate (motor, pump, transformer, generator, etc.).
Extract every attribute/value pair that is legible on the nameplate.

Return ONLY valid JSON — no markdown fences, no commentary.
The JSON must have exactly one top-level key "attributes" whose value is an array of objects.
Each object must have exactly two string keys: "name" and "value".
Normalise attribute names to Title Case (e.g. "Serial Number", "Rated Voltage", "Power Factor").
Omit any attribute whose value is unreadable. Do not include attributes with empty values.

Example:
{"attributes": [{"name": "Manufacturer", "value": "Siemens"}, {"name": "Serial Number", "value": "SN-12345"}]}
"""


def _image_to_data_url(image_path: str | Path) -> str:
    path = Path(image_path)
    suffix = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }.get(suffix, "image/jpeg")

    data = base64.standard_b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


def structure_with_llm(image_path: str | Path) -> tuple[list[dict[str, str]], str]:
    """
    Send *image_path* directly to a Groq vision model.
    Returns (attributes, raw_text) where raw_text is the model's full response
    (kept for debugging, stored in ocr_raw_text column).
    """
    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = Groq(api_key=settings.groq_api_key)
    data_url = _image_to_data_url(image_path)

    completion = client.chat.completions.create(
        model=settings.groq_vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
        temperature=0.1,
        max_tokens=1024,
    )

    raw_response = completion.choices[0].message.content.strip()
    logger.debug("Vision LLM raw response: %s", raw_response)

    # Strip accidental markdown fences
    clean = re.sub(r"^```(?:json)?\s*", "", raw_response)
    clean = re.sub(r"\s*```$", "", clean)

    try:
        data = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {raw_response!r}") from exc

    attributes = data.get("attributes", [])
    if not isinstance(attributes, list):
        raise ValueError(f"Expected 'attributes' list, got: {type(attributes)}")

    cleaned = [
        {"name": str(item.get("name", "")).strip(), "value": str(item.get("value", "")).strip()}
        for item in attributes
        if str(item.get("name", "")).strip() and str(item.get("value", "")).strip()
    ]

    return cleaned, raw_response
