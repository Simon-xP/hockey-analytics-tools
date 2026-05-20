"""News pipeline — scrape, classify once, persist, serve.

Two public entrypoints:

- `ingest_news()` walks pages of GameDayTweets, classifies any tweets
  we haven't seen before (via Gemini, with regex fallback), and inserts
  rows into `news_items`. Run on a schedule.

- `query_news(limit, offset)` reads classified items from the DB for
  the API. No scraping, no LLM calls, no external requests.

The DB is the single source of truth: each tweet is classified exactly
once at ingest time and never touched again unless its row is deleted.
"""

import re

import httpx
from sqlalchemy import select, text as sa_text

from src.core.db import get_session
from src.core.models import NewsItem
from src.ingest.news.classifier import (
    classify, extract_entities, CATEGORY_CONFIG, SOURCE_HANDLE_TO_ABBREV,
)
from src.ingest.news.llm_classifier import classify_tweets, tweet_hash

GAMEDAYTWEETS_URL = "https://www.gamedaytweets.com/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Snippet categories we surface to clients. Items with no actionable
# snippets are still persisted (so we don't re-classify them next run)
# but filtered out at query time.
# INJURY is excluded — the Injuries tab (player_injuries table) handles
# injury reporting with structured body-part/severity/timeline data.
ACTIONABLE_CATEGORIES = {"PP_CHANGE", "TRANSACTION", "SCRATCH", "RETURN"}

DEFAULT_INGEST_PAGES = 10


# ---------- Scraping ----------

def _fetch_page(page: int) -> list[str]:
    """Fetch one page of GameDayTweets and return raw <p>-tag contents."""
    url = GAMEDAYTWEETS_URL if page == 1 else f"{GAMEDAYTWEETS_URL}?page={page}"
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    except Exception:
        return []
    if resp.status_code != 200:
        return []
    return re.findall(r'<p[^>]*>(.*?)</p>', resp.text, re.DOTALL)


def _clean_tweet(raw: str) -> tuple[str, str] | None:
    """Strip HTML, normalize whitespace, extract source handle.

    Returns (source_handle, clean_text) or None if the candidate is junk.
    """
    clean = re.sub(r'<[^>]+>', '', raw).strip()
    clean = re.sub(r'\s+', ' ', clean)
    clean = re.sub(r'pic\.twitter\.com/\S+', '', clean).strip()
    clean = re.sub(r'https?://t\.co/\S+', '', clean).strip()

    if len(clean) < 40:
        return None
    if clean.startswith('Filter') or 'Select Player' in clean:
        return None

    source = ""
    handle_match = re.match(r'^@(\w+)\s+', clean)
    if handle_match:
        source = f"@{handle_match.group(1)}"
        clean = clean[handle_match.end():].strip()
    return source, clean


# ---------- Classification → snippet shape ----------

def _build_snippets(
    raw_snippets: list[dict],
    source_team: str | None,
) -> list[dict]:
    """Convert raw classifier output into the persisted snippet shape."""
    snippets = []
    for s in raw_snippets:
        cat = s.get("category", "OTHER")
        cfg = CATEGORY_CONFIG.get(cat, CATEGORY_CONFIG["OTHER"])
        team_tag = s.get("team_abbrev") or source_team or ""
        snippets.append({
            "category": cat,
            "category_label": cfg["label"],
            "category_color": cfg["color"],
            "summary": s.get("summary", ""),
            "player_name": s.get("primary_player") or None,
            "injury_type": s.get("injury_type") or None,
            "team_tag": team_tag or None,
        })
    return snippets


def _regex_fallback(text: str) -> list[dict]:
    """Single-snippet output from the regex classifier."""
    category = classify(text)
    entities = extract_entities(text, category)
    return [{
        "category": category,
        "primary_player": entities["players"][0] if entities["players"] else "",
        "injury_type": entities["injury_type"] or "",
        "team_abbrev": entities["team_tags"][0] if entities["team_tags"] else "",
        "summary": entities["summary"],
    }]


# ---------- Ingest ----------

def ingest_news(max_pages: int = DEFAULT_INGEST_PAGES) -> dict:
    """Scrape, classify new tweets once, persist to news_items.

    Returns {pages_scraped, scraped, new, llm_classified, fallback_classified}.
    Idempotent — running twice in a row yields zero new rows.
    """
    pages_scraped = 0
    seen_hashes_in_this_run: set[str] = set()
    scraped_total = 0

    # 1. Scrape pages → list of (hash, source, text) for new candidates only
    new_candidates: list[tuple[str, str, str]] = []
    with get_session() as session:
        existing_hashes: set[str] = set(
            session.scalars(select(NewsItem.text_hash)).all()
        )

    for page in range(1, max_pages + 1):
        raw_tweets = _fetch_page(page)
        if not raw_tweets:
            break
        pages_scraped += 1

        for raw in raw_tweets:
            cleaned = _clean_tweet(raw)
            if cleaned is None:
                continue
            source, text = cleaned
            scraped_total += 1
            h = tweet_hash(text)
            if h in existing_hashes or h in seen_hashes_in_this_run:
                continue
            seen_hashes_in_this_run.add(h)
            new_candidates.append((h, source, text))

    if not new_candidates:
        return {
            "pages_scraped": pages_scraped,
            "scraped": scraped_total,
            "new": 0,
            "llm_classified": 0,
            "fallback_classified": 0,
        }

    # 2. Classify all new tweets via LLM (single call set, batched inside)
    llm_results = classify_tweets([text for _, _, text in new_candidates])

    # 3. Build rows and insert
    llm_count = 0
    fallback_count = 0
    rows = []
    for h, source, text in new_candidates:
        source_team = (
            SOURCE_HANDLE_TO_ABBREV.get(source.lstrip("@").lower())
            if source else None
        )
        raw_snippets = llm_results.get(h)
        if raw_snippets is None:
            raw_snippets = _regex_fallback(text)
            fallback_count += 1
        else:
            llm_count += 1

        snippets = _build_snippets(raw_snippets, source_team)
        rows.append(NewsItem(
            text_hash=h,
            source_handle=source or None,
            text=text,
            snippets=snippets,
        ))

    with get_session() as session:
        session.add_all(rows)

    return {
        "pages_scraped": pages_scraped,
        "scraped": scraped_total,
        "new": len(rows),
        "llm_classified": llm_count,
        "fallback_classified": fallback_count,
    }


# ---------- Query ----------

def query_news(
    limit: int = 20,
    offset: int = 0,
    include_all: bool = False,
) -> list[dict]:
    """Read classified news from the DB, newest first.

    When `include_all` is False (default), filters at the SQL level to
    items containing at least one actionable snippet using a JSONB
    array-element scan. Returns API-ready item shape.
    """
    with get_session() as session:
        stmt = select(NewsItem)

        if not include_all:
            stmt = stmt.where(
                sa_text(
                    "EXISTS (SELECT 1 FROM jsonb_array_elements(snippets) AS s "
                    "WHERE s->>'category' = ANY(:actionable))"
                ).bindparams(actionable=list(ACTIONABLE_CATEGORIES))
            )

        rows = session.scalars(
            stmt.order_by(NewsItem.created_at.desc(), NewsItem.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()

        items = []
        for row in rows:
            snippets = row.snippets or []
            if not include_all:
                snippets = [s for s in snippets if s.get("category") in ACTIONABLE_CATEGORIES]
            if not snippets:
                continue
            # Refresh display label/color from current config so rename/recolor
            # takes effect without reclassifying historical rows.
            for s in snippets:
                cfg = CATEGORY_CONFIG.get(s.get("category"), CATEGORY_CONFIG["OTHER"])
                s["category_label"] = cfg["label"]
                s["category_color"] = cfg["color"]
            items.append({
                "source": row.source_handle or "",
                "text": row.text,
                "snippets": snippets,
            })

    return items
