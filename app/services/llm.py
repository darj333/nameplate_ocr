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


def _bytes_to_data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    data = base64.standard_b64encode(image_bytes).decode()
    return f"data:{mime};base64,{data}"


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
    return _bytes_to_data_url(path.read_bytes(), mime)


def _extract_json(raw: str) -> dict:
    """
    Robustly extract a JSON object from a model response that may be wrapped
    in markdown code fences or contain extra commentary.

    Strategy:
    1. Strip ``` fences if present.
    2. If that still fails to parse, find the outermost { … } block.
    3. Raise a clear ValueError if nothing works.
    """
    # Step 1: strip markdown fences (handles ```json ... ``` and ``` ... ```)
    clean = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), flags=re.IGNORECASE)
    clean = re.sub(r"\n?```\s*$", "", clean).strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Step 2: extract the outermost { … } block
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"LLM returned invalid JSON: {raw!r}")


def _call_vision_llm(data_url: str) -> tuple[list[dict[str, str]], str]:
    """Send a single image data-URL to the Groq vision model and parse the result."""
    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = Groq(api_key=settings.groq_api_key)
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

    data = _extract_json(raw_response)

    attributes = data.get("attributes", [])
    if not isinstance(attributes, list):
        raise ValueError(f"Expected 'attributes' list, got: {type(attributes)}")

    cleaned = [
        {"name": str(item.get("name", "")).strip(), "value": str(item.get("value", "")).strip()}
        for item in attributes
        if str(item.get("name", "")).strip() and str(item.get("value", "")).strip()
    ]
    return cleaned, raw_response


def structure_with_llm(image_path: str | Path) -> tuple[list[dict[str, str]], str]:
    """
    Send *image_path* (image file) to the Groq vision model.
    Returns (attributes, raw_text).
    """
    data_url = _image_to_data_url(image_path)
    return _call_vision_llm(data_url)


def structure_with_llm_bytes(
    image_bytes: bytes, mime: str = "image/png"
) -> tuple[list[dict[str, str]], str]:
    """
    Send raw image *bytes* to the Groq vision model (used for in-memory PDF pages).
    Returns (attributes, raw_text).
    """
    data_url = _bytes_to_data_url(image_bytes, mime)
    return _call_vision_llm(data_url)
