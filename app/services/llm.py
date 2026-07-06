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


# ── Prompts ────────────────────────────────────────────────────────────────────

_NAMEPLATE_PROMPT = """\
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

_TABLE_PROMPT = """\
This page is from a technical document and contains one or more tables listing \
electrical or industrial equipment items (bill of materials, equipment schedules, etc.).

Extract every table and every data row from this page.

Return ONLY valid JSON — no markdown fences, no commentary — in exactly this structure:
{
  "tables": [
    {
      "title": "<heading text — see rules below>",
      "rows": [
        {"<column header 1>": "<cell value>", "<column header 2>": "<cell value>", ...},
        ...
      ]
    }
  ]
}

Title rules:
- Look for ANY text that appears ABOVE the table: underlined text, bold text, ALL-CAPS text,
  alphanumeric codes (e.g. "ANTEMĂSURĂTOARE TE403", "LISTA ECHIPAMENTE", "TABLOU TE-01").
- Copy that heading text EXACTLY as it appears on the page (preserve diacritics, spacing).
- Only use an empty string "" if there is truly NO text outside the table grid itself.

Data rules:
- Preserve all column header names exactly as they appear (including diacritics).
- Preserve all cell values exactly as written.
- Where a cell says "Idem" (meaning "same as above"), replace it with the full text from the \
most recent non-Idem cell in that same column.
- Omit a key from a row object only if that cell is completely empty.
- Do not skip any data rows.
- If a column contains only row numbers (e.g. 1, 2, 3…) use "Nr. Crt." as the column name.
"""

_TITLE_PROMPT = """\
Look at this document page. There is a table on the page.

What is the heading or title written ABOVE the table?
It is often underlined, in capital letters, or contains an alphanumeric code \
(for example: "ANTEMĂSURĂTOARE TE403", "LISTA ECHIPAMENTE", "TABLOU ELECTRIC T1").

Reply with ONLY the heading text, exactly as it appears on the page.
Do not add any explanation, punctuation, or quotes around it.
If there is truly no heading text above the table, reply with exactly: NONE
"""


# ── Internal helpers ───────────────────────────────────────────────────────────

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

    1. Strip ``` fences if present.
    2. Fall back to extracting the outermost { … } block.
    """
    clean = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), flags=re.IGNORECASE)
    clean = re.sub(r"\n?```\s*$", "", clean).strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"LLM returned invalid JSON: {raw!r}")


def _groq_client() -> Groq:
    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")
    return Groq(api_key=settings.groq_api_key)


def _vision_completion(data_url: str, prompt: str, max_tokens: int = 4096) -> str:
    """Call the Groq vision model with a given image data-URL and prompt."""
    client = _groq_client()
    settings = get_settings()
    completion = client.chat.completions.create(
        model=settings.groq_vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        temperature=0.1,
        max_tokens=max_tokens,
    )
    raw = completion.choices[0].message.content.strip()
    logger.debug("Vision LLM raw response: %s", raw)
    return raw


# ── Public API ─────────────────────────────────────────────────────────────────

def structure_with_llm(image_path: str | Path) -> tuple[list[dict[str, str]], str]:
    """
    Send an image file to the Groq vision model using the nameplate prompt.
    Returns (attributes, raw_text).
    """
    data_url = _image_to_data_url(image_path)
    raw = _vision_completion(data_url, _NAMEPLATE_PROMPT)
    data = _extract_json(raw)

    attributes = data.get("attributes", [])
    if not isinstance(attributes, list):
        raise ValueError(f"Expected 'attributes' list, got: {type(attributes)}")

    cleaned = [
        {"name": str(item.get("name", "")).strip(), "value": str(item.get("value", "")).strip()}
        for item in attributes
        if str(item.get("name", "")).strip() and str(item.get("value", "")).strip()
    ]
    return cleaned, raw


def _fetch_page_title(data_url: str) -> str:
    """
    Make a focused second call to ask only for the page heading above the table.
    Returns the title string, or "" if none is found.
    """
    raw = _vision_completion(data_url, _TITLE_PROMPT, max_tokens=128)
    title = raw.strip().strip('"').strip("'")
    if title.upper() == "NONE" or not title:
        return ""
    return title


def extract_tables_from_pdf_page(
    image_bytes: bytes, mime: str = "image/png"
) -> tuple[list[dict], str]:
    """
    Send a rendered PDF page (as PNG bytes) to the Groq vision model.

    Pass 1 — extract tables + titles in one call.
    Pass 2 — if any table came back with an empty title, make a second focused
              call asking only for the heading text and apply it to those tables.

    Returns:
        tables   – list of {"title": str, "rows": [dict, ...]}
        raw_text – the pass-1 model response (for debugging)
    """
    data_url = _bytes_to_data_url(image_bytes, mime)
    raw = _vision_completion(data_url, _TABLE_PROMPT, max_tokens=4096)
    data = _extract_json(raw)

    tables = data.get("tables", [])
    if not isinstance(tables, list):
        raise ValueError(f"Expected 'tables' list, got: {type(tables)}")

    validated: list[dict] = []
    for tbl in tables:
        title = str(tbl.get("title", "")).strip()
        rows = tbl.get("rows", [])
        if not isinstance(rows, list):
            continue
        clean_rows = [r for r in rows if isinstance(r, dict) and any(str(v).strip() for v in r.values())]
        if clean_rows:
            validated.append({"title": title, "rows": clean_rows})

    # Pass 2: recover missing titles with a dedicated focused call
    missing = [t for t in validated if not t["title"]]
    if missing:
        logger.info("Pass-2 title fetch for %d untitled table(s) on this page", len(missing))
        recovered = _fetch_page_title(data_url)
        logger.info("Pass-2 title result: %r", recovered)
        for tbl in missing:
            tbl["title"] = recovered

    return validated, raw
