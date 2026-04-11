"""News scraper — pulls NHL news from GameDayTweets.

Scrapes gamedaytweets.com, classifies tweets, extracts entities,
and returns structured news items for the dashboard.
"""

import re

import requests

from src.ingest.news.classifier import classify, extract_entities, CATEGORY_CONFIG

GAMEDAYTWEETS_URL = "https://www.gamedaytweets.com/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Categories to show on the dashboard (skip LINEUP, OTHER, and GOALIE since streamable goalies card covers that)
ACTIONABLE_CATEGORIES = {"INJURY", "PP_CHANGE", "TRANSACTION", "SCRATCH", "RETURN"}


def fetch_news(limit: int = 20, include_all: bool = False) -> list[dict]:
    """Scrape GameDayTweets and return classified, structured news items.

    Returns list of dicts:
        source, text, category, category_label, category_color,
        summary, players, injury_type, team_tags
    """
    resp = requests.get(GAMEDAYTWEETS_URL, headers={"User-Agent": USER_AGENT}, timeout=10)
    if resp.status_code != 200:
        return []

    raw_tweets = re.findall(r'<p[^>]*>(.*?)</p>', resp.text, re.DOTALL)

    items = []
    seen = set()

    for t in raw_tweets:
        clean = re.sub(r'<[^>]+>', '', t).strip()
        clean = re.sub(r'\s+', ' ', clean)
        clean = re.sub(r'pic\.twitter\.com/\S+', '', clean).strip()
        clean = re.sub(r'https?://t\.co/\S+', '', clean).strip()

        if len(clean) < 40:
            continue
        if clean.startswith('Filter') or 'Select Player' in clean:
            continue

        # Extract reporter handle
        source = ""
        handle_match = re.match(r'^@(\w+)\s+', clean)
        if handle_match:
            source = f"@{handle_match.group(1)}"
            clean = clean[handle_match.end():].strip()

        # Deduplicate
        key = clean[:80]
        if key in seen:
            continue
        seen.add(key)

        # Classify
        category = classify(clean)

        # Filter to actionable unless include_all
        if not include_all and category not in ACTIONABLE_CATEGORIES:
            continue

        # Extract entities
        entities = extract_entities(clean, category)
        config = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["OTHER"])

        items.append({
            "source": source,
            "text": clean,
            "category": category,
            "category_label": config["label"],
            "category_color": config["color"],
            "summary": entities["summary"],
            "players": entities["players"],
            "injury_type": entities["injury_type"],
            "team_tags": entities["team_tags"],
        })

    return items[:limit]
