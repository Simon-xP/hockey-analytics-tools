"""LLM-based parser for Daily Faceoff injury news blurbs.

Takes the free-text `news.details` string from Daily Faceoff and
extracts structured fields: body part, severity, timeline, expected
return date. Uses Gemini 2.5 Flash Lite with structured output.

Pure function — no caching. Callers are responsible for only passing
blurbs that haven't already been parsed and persisted.
"""

import hashlib
import json
import os
import re
import time
from datetime import date, datetime, timedelta

import httpx

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
BATCH_SIZE = 30
REQUEST_TIMEOUT = 60
MIN_BATCH_INTERVAL = 4.5
MAX_429_RETRIES = 3

VALID_SEVERITIES = {
    "day-to-day", "week-to-week", "month-plus", "season", "unknown",
}

VALID_CATEGORIES = {
    "injury", "goalie_start", "scratch", "personal", "transaction", "return",
}

PARSED_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "id": {"type": "INTEGER"},
        "category": {
            "type": "STRING",
            "enum": sorted(VALID_CATEGORIES),
            "description": (
                "What this blurb is actually about: "
                "injury: physical injury, illness, surgery, or undisclosed condition. "
                "goalie_start: goalie confirmed/expected to start a game. "
                "scratch: healthy scratch, benched (no injury). "
                "personal: personal matter, leave of absence. "
                "transaction: trade, waiver, recall, AHL assignment, IR activation (not injury itself). "
                "return: player returning to lineup, activated from IR, back in practice."
            ),
        },
        "body_part": {
            "type": "STRING",
            "description": (
                "Injured area using canonical terms: 'upper-body', "
                "'lower-body', 'head', 'concussion', 'knee', 'ankle', "
                "'shoulder', 'wrist', 'hand', 'foot', 'hip', 'groin', "
                "'back', 'ribs', 'illness', 'undisclosed'. Empty string "
                "if the blurb does not state a body part or if category "
                "is not 'injury'."
            ),
        },
        "severity": {
            "type": "STRING",
            "enum": sorted(VALID_SEVERITIES),
            "description": (
                "day-to-day: gtd / probable / 1-3 days. "
                "week-to-week: 4-20 days. "
                "month-plus: 21-90 days. "
                "season: out for the remainder of the season / LTIR. "
                "unknown: no duration stated. "
                "Only relevant for category='injury'; set to 'unknown' otherwise."
            ),
        },
        "timeline_days_min": {
            "type": "INTEGER",
            "description": "Lower bound of expected absence in days. 0 if unknown.",
        },
        "timeline_days_max": {
            "type": "INTEGER",
            "description": "Upper bound of expected absence in days. 0 if unknown.",
        },
        "expected_return_date": {
            "type": "STRING",
            "description": (
                "ISO date (YYYY-MM-DD) if the blurb states a specific "
                "return target, otherwise empty string. Do NOT infer "
                "or compute — only extract if explicitly stated."
            ),
        },
        "summary": {
            "type": "STRING",
            "description": (
                "Short actionable summary, max 120 chars. Format: "
                "'<body-part>, <timeline>'. Examples: "
                "'upper-body, week-to-week', 'knee surgery, out for season', "
                "'concussion, day-to-day', 'starts vs. DET', 'healthy scratch', "
                "'personal leave', 'assigned to AHL', 'activated from IR'."
            ),
        },
    },
    "required": [
        "id", "category", "body_part", "severity", "timeline_days_min",
        "timeline_days_max", "expected_return_date", "summary",
    ],
}

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": PARSED_SCHEMA,
}

PROMPT_INSTRUCTIONS = """You are an NHL news blurb classifier and parser. Each input is a short free-text blurb from Daily Faceoff about a player. These come from injury/status pages, but NOT all of them are actual injuries.

STEP 1 — Classify the category:
- injury: actual physical injury, illness, surgery, undisclosed medical condition, "left game and did not return"
- goalie_start: goalie confirmed/expected to start a game ("will start", "gets the nod", "between the pipes")
- scratch: healthy scratch ("will be a healthy scratch", benched with no injury)
- personal: personal matter, leave of absence ("personal", "stepped away")
- transaction: trade, waiver claim, AHL assignment, recall, conditioning loan, IR activation/deactivation as an administrative move
- return: player returning from injury to the lineup, "back in the lineup", "activated from IR", "full participant in practice" with clear return signal

STEP 2 — For category="injury", extract structured fields. For all other categories, leave body_part empty, severity="unknown", timelines=0.

CRITICAL RULES:
1. NEVER invent details not stated in the blurb.
2. "undisclosed" injuries ARE real injuries (category="injury", body_part="undisclosed") — do NOT mark as scratch/personal just because the body part is unknown.
3. Use canonical body-part terms. Prefer 'upper-body'/'lower-body' only when the blurb uses those vague terms. If specific (knee, shoulder, etc.), use that. "core muscle" = "groin". "blood clot" = "illness".
4. Severity buckets (for injuries only):
   - day-to-day: gtd, probable, questionable, 1-3 days
   - week-to-week: 4-20 days
   - month-plus: 21-90 days
   - season: out for the remainder of the season / LTIR / season-ending
   - unknown: no duration stated
5. expected_return_date: ONLY if a specific date is stated. Do NOT estimate.
6. summary: terse. For injuries: "knee, out 4-6 weeks". For non-injuries: "starts vs. DET", "healthy scratch", "assigned to AHL".

Examples:
- "Lankinen will start Tuesday vs. the Kings." → category=goalie_start, summary="starts vs. LAK"
- "Mangiapane is expected to be a healthy scratch" → category=scratch, summary="healthy scratch"
- "Hedman (personal) will be out indefinitely" → category=personal, summary="personal leave, indefinite"
- "Blomqvist activated from IR and assigned to AHL" → category=transaction, summary="activated from IR, assigned to AHL"
- "Lundkvist (illness) will be back in the lineup on Wednesday" → category=return, summary="illness, back Wednesday"
- "Lindgren (upper-body) was a full participant in practice" → category=return, summary="upper-body, full practice"
- "Markstrom (undisclosed) will miss the remainder of the season" → category=injury, body_part="undisclosed", severity="season"
- "Bean (undisclosed) is expected to undergo surgery and will be out indefinitely" → category=injury, body_part="undisclosed", severity="season"

Return a JSON array. Each element must have `id` matching the input's id.
"""


def blurb_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _parse_retry_delay(body: str) -> float | None:
    m = re.search(r'retry in ([\d.]+)s', body)
    return float(m.group(1)) + 0.5 if m else None


def _parse_return_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _validate_parsed(item: dict) -> dict | None:
    """Sanitize one LLM output. Returns None on structural failure."""
    category = (item.get("category") or "").strip().lower()
    if category not in VALID_CATEGORIES:
        category = "injury"  # default assumption from DF injury page

    severity = (item.get("severity") or "").strip().lower()
    if severity not in VALID_SEVERITIES:
        severity = "unknown"

    body_part = (item.get("body_part") or "").strip().lower() or None
    # Non-injury categories shouldn't have body parts
    if category != "injury":
        body_part = None
        severity = "unknown"

    dmin = item.get("timeline_days_min") or 0
    dmax = item.get("timeline_days_max") or 0
    if not isinstance(dmin, int) or dmin < 0:
        dmin = 0
    if not isinstance(dmax, int) or dmax < 0:
        dmax = 0
    if dmin == 0 and dmax == 0:
        dmin = dmax = None
    elif dmax < dmin:
        dmax = dmin
    # Non-injury categories shouldn't have timelines
    if category != "injury":
        dmin = dmax = None

    expected_return = _parse_return_date(item.get("expected_return_date") or "")

    summary = (item.get("summary") or "").strip()
    if len(summary) > 240:
        summary = summary[:237] + "..."

    return {
        "category": category,
        "body_part": body_part,
        "severity": severity,
        "timeline_days_min": dmin,
        "timeline_days_max": dmax,
        "expected_return": expected_return,
        "summary": summary or None,
    }


def _call_gemini(batch: list[tuple[int, str]], api_key: str) -> list[dict] | None:
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT_INSTRUCTIONS},
                    {
                        "text": "Injury blurbs to parse:\n"
                        + json.dumps(
                            [{"id": i, "text": t} for i, t in batch],
                            ensure_ascii=False,
                        )
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0.0,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    import sys

    for attempt in range(MAX_429_RETRIES + 1):
        try:
            resp = httpx.post(
                f"{GEMINI_URL}?key={api_key}",
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
        except Exception as e:
            print(f"[gemini-injury] exception: {e!r}", file=sys.stderr)
            return None

        if resp.status_code == 200:
            try:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                return parsed if isinstance(parsed, list) else None
            except Exception as e:
                print(f"[gemini-injury] parse error: {e!r}", file=sys.stderr)
                return None

        if resp.status_code == 429 and attempt < MAX_429_RETRIES:
            delay = _parse_retry_delay(resp.text) or (5 * (attempt + 1))
            print(f"[gemini-injury] 429, retrying in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
            continue

        print(
            f"[gemini-injury] HTTP {resp.status_code}: {resp.text[:300]}",
            file=sys.stderr,
        )
        return None

    return None


def parse_injury_blurbs(blurbs: list[str]) -> dict[str, dict | None]:
    """Parse a list of injury blurbs.

    Returns dict mapping blurb_hash -> parsed fields (or None if the
    call failed for that blurb). Parsed fields:
        {body_part, severity, timeline_days_min, timeline_days_max,
         expected_return, summary}
    """
    api_key = os.environ.get("GEMINI_API_KEY")

    hash_to_text: dict[str, str] = {}
    for b in blurbs:
        if b and b.strip():
            hash_to_text.setdefault(blurb_hash(b), b.strip())

    results: dict[str, dict | None] = {}
    if not hash_to_text:
        return results
    if not api_key:
        for h in hash_to_text:
            results[h] = None
        return results

    pairs = list(hash_to_text.items())
    last_call_at = 0.0
    for batch_start in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[batch_start : batch_start + BATCH_SIZE]
        indexed = [(i, text) for i, (_, text) in enumerate(batch)]

        elapsed = time.time() - last_call_at
        if elapsed < MIN_BATCH_INTERVAL:
            time.sleep(MIN_BATCH_INTERVAL - elapsed)
        last_call_at = time.time()

        raw_items = _call_gemini(indexed, api_key)
        if raw_items is None:
            for h, _ in batch:
                results[h] = None
            continue

        by_id = {item.get("id"): item for item in raw_items if isinstance(item, dict)}
        for i, (h, _text) in enumerate(batch):
            item = by_id.get(i)
            results[h] = _validate_parsed(item) if item else None

    return results
