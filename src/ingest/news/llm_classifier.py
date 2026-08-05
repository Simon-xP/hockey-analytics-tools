"""LLM-based tweet classifier using Gemini 2.0 Flash.

Replaces the regex classifier with a structured-output LLM call.
Designed with anti-hallucination guards: every field returned by the
model is validated against the source tweet before we accept it. On any
validation or API failure, callers fall back to the regex classifier.

This module is a pure function — it does NOT cache. Callers (the news
ingester) are responsible for only passing tweets that haven't already
been classified and persisted. Single source of truth is the DB.
"""

import hashlib
import json
import os
import re
import time

import httpx

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
BATCH_SIZE = 25
REQUEST_TIMEOUT = 60
# Gentle pacing so we don't blow through free-tier RPM. flash-lite is
# 15 RPM so 4.5s between starts leaves headroom.
MIN_BATCH_INTERVAL = 4.5
MAX_429_RETRIES = 3

VALID_CATEGORIES = {
    "INJURY", "GOALIE", "PP_CHANGE", "TRANSACTION",
    "SCRATCH", "RETURN", "LINEUP", "OTHER",
}

VALID_TEAM_ABBREVS = {
    "ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET",
    "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI", "NYR", "OTT",
    "PHI", "PIT", "SJS", "SEA", "STL", "TBL", "TOR", "UTA", "VAN", "VGK",
    "WSH", "WPG",
}

SNIPPET_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "category": {
            "type": "STRING",
            "enum": sorted(VALID_CATEGORIES),
        },
        "primary_player": {
            "type": "STRING",
            "description": (
                "The single player this snippet is about. Must be a verbatim "
                "substring of the source tweet. Empty string if no clear "
                "primary player."
            ),
        },
        "injury_type": {
            "type": "STRING",
            "description": (
                "Body part or injury descriptor (e.g. 'upper-body', "
                "'lower-body', 'concussion', 'day-to-day'). Empty string "
                "if not an injury or not stated."
            ),
        },
        "team_abbrev": {
            "type": "STRING",
            "description": (
                "Three-letter NHL team abbreviation for the team the primary "
                "player plays for. Empty string if unknown."
            ),
        },
        "summary": {
            "type": "STRING",
            "description": (
                "Short actionable headline, max 80 chars. Format: "
                "'<Player> — <action>'. Examples: 'Roman Josi — upper-body', "
                "'Clay Stevenson — starts vs CBJ', 'Frank Nazar — left game'."
            ),
        },
    },
    "required": [
        "category", "primary_player", "injury_type",
        "team_abbrev", "summary",
    ],
}

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "INTEGER"},
            "snippets": {
                "type": "ARRAY",
                "items": SNIPPET_SCHEMA,
                "description": (
                    "List of distinct news snippets in this tweet. Most "
                    "tweets have 1 snippet. Split into multiple ONLY when "
                    "the tweet conveys two or more distinct fantasy-relevant "
                    "facts about different players or different events."
                ),
            },
        },
        "required": ["id", "snippets"],
    },
}

PROMPT_INSTRUCTIONS = """You are an NHL fantasy hockey news classifier. For each tweet, return a JSON object {id, snippets: [...]} where each snippet is one distinct fantasy-actionable fact.

CRITICAL RULES — these prevent hallucinations:
1. Every `primary_player` MUST appear verbatim in the source tweet. If unsure, return empty string. NEVER invent or guess names.
2. Coaches, GMs, and reporters are NOT players. Filter them out.
3. `team_abbrev` must be one of the 32 NHL abbreviations or empty string.
4. Most tweets produce exactly ONE snippet. Only split into multiple snippets when the tweet states two or more distinct fantasy-relevant facts about *different* players or *different* events.

When to split into multiple snippets:
- "Stevenson will start in net. Dubois (upper body) won't play." → 2 snippets: GOALIE for Stevenson + INJURY for Dubois.
- "Hughes activated off IR. Meier (lower body) ruled out." → 2 snippets: RETURN for Hughes + INJURY for Meier.
- "Lines tonight: McDavid–Draisaitl–Hyman, ..." → 1 snippet (LINEUP), not 12.
- "McDavid (2G, 1A) leads Oilers past Flames" → 1 snippet (OTHER), this is a recap.

Categories:
- INJURY: any injury, day-to-day, GTD, "left game", "won't play", "(upper-body)", "not returning to lineup"
- GOALIE: confirmed goalie start ("starts in net", "between the pipes", "gets the start")
- PP_CHANGE: power-play unit changes (PP1, PP2 promotions/demotions)
- TRANSACTION: trade, waiver, recall, signing, claim
- SCRATCH: healthy scratch, benched (NOT mid-game injury — that's INJURY)
- RETURN: player returning from injury, activated off IR, "back in the lineup"
- LINEUP: line combinations, morning skate, line rushes (no specific player action)
- OTHER: game recaps, opinions, scores, anything else

Summary format: "<Player Name> — <short action>", max 80 chars. Examples:
- "Roman Josi — upper-body, day-to-day"
- "Clay Stevenson — starts vs CBJ"
- "Frank Nazar — left game, did not return"
- "Cole Caufield — promoted to PP1"

Return a JSON array. Each element must have `id` matching the input tweet's id and a `snippets` array.
"""


def tweet_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _normalize_for_substring(s: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation for fuzzy substring."""
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def _validate_snippet(item: dict, tweet_text: str) -> dict | None:
    """Apply anti-hallucination guards. Returns sanitized snippet or None if invalid."""
    category = item.get("category", "").strip().upper()
    if category not in VALID_CATEGORIES:
        return None

    player = (item.get("primary_player") or "").strip()
    if player:
        # Player name must appear in tweet (case-insensitive, punctuation-tolerant)
        norm_tweet = _normalize_for_substring(tweet_text)
        norm_player = _normalize_for_substring(player)
        if norm_player and norm_player not in norm_tweet:
            # Allow last-name match if full name doesn't substring (handles
            # accents normalized differently)
            last = norm_player.split()[-1] if norm_player.split() else ""
            if not last or last not in norm_tweet:
                player = ""

    team = (item.get("team_abbrev") or "").strip().upper()
    if team and team not in VALID_TEAM_ABBREVS:
        team = ""

    injury = (item.get("injury_type") or "").strip()
    # Only allow injury_type if category is INJURY or RETURN
    if category not in {"INJURY", "RETURN"}:
        injury = ""

    summary = (item.get("summary") or "").strip()
    if len(summary) > 120:
        summary = summary[:117] + "..."
    if not summary:
        summary = tweet_text[:100] + ("..." if len(tweet_text) > 100 else "")

    return {
        "category": category,
        "primary_player": player,
        "injury_type": injury,
        "team_abbrev": team,
        "summary": summary,
    }


def _parse_retry_delay(body: str) -> float | None:
    """Extract 'retry in Xs' from Gemini 429 error body, if present."""
    m = re.search(r'retry in ([\d.]+)s', body)
    return float(m.group(1)) + 0.5 if m else None


def _call_gemini(batch: list[tuple[int, str]], api_key: str) -> list[dict] | None:
    """Send a batch to Gemini. Returns raw item dicts or None on failure.

    Retries on 429 (rate limit) up to MAX_429_RETRIES times, respecting
    the server's suggested retry delay when present.
    """
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT_INSTRUCTIONS},
                    {
                        "text": "Tweets to classify:\n"
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
            print(f"[gemini] exception: {e!r}", file=sys.stderr)
            return None

        if resp.status_code == 200:
            try:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                return parsed if isinstance(parsed, list) else None
            except Exception as e:
                print(f"[gemini] parse error: {e!r}", file=sys.stderr)
                return None

        if resp.status_code == 429 and attempt < MAX_429_RETRIES:
            delay = _parse_retry_delay(resp.text) or (5 * (attempt + 1))
            print(f"[gemini] 429, retrying in {delay:.1f}s (attempt {attempt + 1})", file=sys.stderr)
            time.sleep(delay)
            continue

        print(f"[gemini] HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        return None

    return None


def classify_tweets(tweets: list[str]) -> dict[str, list[dict] | None]:
    """Classify a list of tweets via Gemini.

    Returns a dict mapping tweet_hash -> list of validated snippets
    (or None if the LLM call failed entirely, so the caller can fall
    back to the regex classifier for that tweet).

    Each snippet: {category, primary_player, injury_type, team_abbrev, summary}.

    Pure function — no caching. Callers should only pass tweets that
    are not already persisted.
    """
    api_key = os.environ.get("GEMINI_API_KEY")

    # Dedupe within this batch
    hash_to_text: dict[str, str] = {}
    for t in tweets:
        hash_to_text.setdefault(tweet_hash(t), t)

    results: dict[str, list[dict] | None] = {}

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
        for i, (h, text) in enumerate(batch):
            item = by_id.get(i)
            if item is None:
                results[h] = None
                continue
            raw_snippets = item.get("snippets") or []
            if not isinstance(raw_snippets, list):
                results[h] = None
                continue
            validated = []
            for snip in raw_snippets:
                if not isinstance(snip, dict):
                    continue
                v = _validate_snippet(snip, text)
                if v is not None:
                    validated.append(v)
            results[h] = validated

    return results
