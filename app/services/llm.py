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

_TABLES_PROMPT = """\
This page is from a technical document.

Find EVERY genuine TABLE on this page. A table is a GRID of cells laid out in rows \
and columns with a clear header row of column names at the top — for example an \
equipment schedule, bill of materials, or parts list.

For each table return:
- "title": the heading printed directly above the grid (often underlined, in ALL \
  CAPS, or containing an alphanumeric code, e.g. "ANTEMĂSURĂTOARE TE403", \
  "LISTA ECHIPAMENTE", "TABLOU ELECTRIC T1"). Use "" only if there is truly no text \
  above the grid.
- "headers": the column names of the table's header row, left to right, in order.

Return ONLY valid JSON — no markdown fences, no commentary:
{"tables": [{"title": "EXACT HEADING", "headers": ["Col 1", "Col 2", "Col 3"]}]}

Rules:
- A genuine table MUST have at least TWO columns. A single column of text is a list \
  or a paragraph, NOT a table — do not include it.
- Do NOT include section/chapter headings, figure or image captions, paragraphs, \
  bullet or numbered lists, footers, or signature blocks — only row/column grids.
- When in doubt about whether a block of text is a table, do NOT include it.
- Copy titles and column names EXACTLY as they appear (preserve diacritics, spacing, codes).
- If this page contains no genuine table, return: {"tables": []}
"""

_ROWS_PROMPT_TEMPLATE = """\
This page is from a technical document. Extract ALL data rows from the table \
titled "{title}".

That table's column headers, in left-to-right order, are:
{headers}

Each data row MUST be an object with EXACTLY those keys (use no other keys).

Return ONLY valid JSON — no markdown fences, no commentary — in exactly this structure:
{{
  "rows": [
    {{"<header 1>": "<cell value>", "<header 2>": "<cell value>", ...}},
    ...
  ]
}}

Rules:
- Use the header names listed above as the keys of every row object, exactly as written.
- Preserve all cell values exactly as written (including diacritics).
- Where a cell says "Idem" (meaning "same as above"), replace it with the full text \
from the most recent non-Idem cell in that same column.
- Omit a key from a row object only if that cell is completely empty.
- Do NOT skip any data rows — include every row in the table.
- If a column contains only row numbers (e.g. 1, 2, 3…) use "Nr. Crt." as that header.
"""

# A genuine table has at least this many columns; fewer = list/paragraph, not a table.
MIN_TABLE_COLUMNS = 2


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


def extract_tables_from_pdf_page(
    image_bytes: bytes, mime: str = "image/png"
) -> tuple[list[dict], str]:
    """
    Extract all genuine tables from a rendered PDF page using two passes:

    Pass 1 — DETECT tables: for each real row/column grid on the page, capture its
             title and its column headers. Non-tables (section headings, captions,
             paragraphs, single-column lists) are filtered out here so they never
             reach Pass 2.
    Pass 2 — for each detected table, make a dedicated call to extract just that
             table's rows, anchored on the headers from Pass 1. One call per table
             means token limits never truncate a table mid-way.

    Returns:
        tables   – list of {"title": str, "rows": [dict, ...]}
        raw_text – concatenated raw responses (for debugging)
    """
    data_url = _bytes_to_data_url(image_bytes, mime)

    # ── Pass 1: detect genuine tables (title + headers) ────────────────────────
    raw_detect = _vision_completion(data_url, _TABLES_PROMPT, max_tokens=1024)
    logger.info("Pass-1 detect raw: %s", raw_detect)
    try:
        detected = _extract_json(raw_detect).get("tables", [])
    except Exception as exc:
        logger.warning("Pass-1 parse failed (%s) — no tables extracted", exc)
        return [], raw_detect
    if not isinstance(detected, list):
        logger.warning("Pass-1 'tables' not a list: %s", type(detected))
        return [], raw_detect

    detected_tables: list[dict] = []
    for tbl in detected:
        if not isinstance(tbl, dict):
            continue
        title = str(tbl.get("title", "")).strip()
        raw_headers = tbl.get("headers", [])
        if not isinstance(raw_headers, list):
            continue
        headers = [str(h).strip() for h in raw_headers if str(h).strip()]
        if len(headers) < MIN_TABLE_COLUMNS:
            logger.info("Pass-1 rejecting non-table %r (headers=%r)", title, headers)
            continue
        detected_tables.append({"title": title, "headers": headers})

    logger.info("Pass-1 detected %d genuine table(s)", len(detected_tables))
    if not detected_tables:
        return [], raw_detect

    # ── Pass 2: extract rows per table, anchored on detected headers ───────────
    all_raw: list[str] = [raw_detect]
    tables: list[dict] = []

    for tbl in detected_tables:
        title, headers = tbl["title"], tbl["headers"]
        prompt = _ROWS_PROMPT_TEMPLATE.format(title=title, headers=", ".join(headers))
        raw_rows = _vision_completion(data_url, prompt, max_tokens=8192)
        all_raw.append(f"\n--- {title} ---\n{raw_rows}")
        logger.info("Pass-2 rows for %r: %d chars", title, len(raw_rows))

        try:
            rows = _extract_json(raw_rows).get("rows", [])
        except Exception as exc:
            logger.warning("Pass-2 row parse failed for %r: %s", title, exc)
            continue
        if not isinstance(rows, list):
            logger.warning("Pass-2 'rows' not a list for %r: %s", title, type(rows))
            continue

        # Coerce every row to the detected headers → fixed columns + stable order,
        # and drop rows that are entirely empty.
        clean_rows: list[dict] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            coerced = {h: str(r.get(h, "")).strip() for h in headers}
            if any(coerced.values()):
                clean_rows.append(coerced)

        if clean_rows:
            tables.append({"title": title, "rows": clean_rows})
            logger.info("Table %r: %d row(s) extracted", title, len(clean_rows))
        else:
            logger.warning("Table %r: no non-empty rows found", title)

    return tables, "\n".join(all_raw)
