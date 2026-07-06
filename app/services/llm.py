"""LLM structuring service — converts raw OCR text into attribute/value pairs via Groq."""
from __future__ import annotations

import json
import logging
import re

from groq import Groq

from app.config import get_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert at reading industrial equipment nameplates (motors, pumps, transformers, generators, etc.).
You will receive raw OCR text extracted from a nameplate photo.
Your job is to identify and extract all meaningful attribute/value pairs.

Rules:
- Return ONLY valid JSON — no markdown fences, no commentary.
- The JSON must have exactly one top-level key "attributes" whose value is an array of objects.
- Each object must have exactly two string keys: "name" and "value".
- Normalise attribute names to Title Case (e.g. "Serial Number", "Rated Voltage", "Power Factor").
- If a value is clearly unreadable or absent, omit that attribute entirely (do not guess).
- Do not include attributes whose values are empty strings.

Example output:
{"attributes": [{"name": "Manufacturer", "value": "Siemens"}, {"name": "Serial Number", "value": "SN-12345"}]}
"""


def structure_with_llm(raw_text: str) -> list[dict[str, str]]:
    """
    Send *raw_text* to a Groq-hosted model and return a list of {"name": ..., "value": ...} dicts.
    Raises on API errors or invalid JSON.
    """
    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = Groq(api_key=settings.groq_api_key)

    completion = client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Here is the raw OCR text from the nameplate:\n\n{raw_text}",
            },
        ],
        temperature=0.1,
        max_tokens=1024,
        # Ask the model to return JSON directly
        response_format={"type": "json_object"},
    )

    raw_response = completion.choices[0].message.content.strip()
    logger.debug("LLM raw response: %s", raw_response)

    # Strip accidental markdown fences just in case
    raw_response = re.sub(r"^```(?:json)?\s*", "", raw_response)
    raw_response = re.sub(r"\s*```$", "", raw_response)

    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {raw_response!r}") from exc

    attributes = data.get("attributes", [])
    if not isinstance(attributes, list):
        raise ValueError(f"Expected 'attributes' list, got: {type(attributes)}")

    cleaned = []
    for item in attributes:
        name = str(item.get("name", "")).strip()
        value = str(item.get("value", "")).strip()
        if name and value:
            cleaned.append({"name": name, "value": value})

    return cleaned
