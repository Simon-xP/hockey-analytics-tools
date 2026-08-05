"""Injury ingest pipeline — Daily Faceoff → LLM parser → player_injuries.

Walks all 32 Daily Faceoff team pages, extracts injured players with
news blurbs, LLM-parses any blurbs we haven't seen before, resolves
player names to nhl_ids, and upserts structured rows into
`player_injuries`.

The `news_hash` column is the dedup key — re-running this is idempotent
(same blurb → already in DB → skipped). An injury that evolves will
show up as a new blurb on DF, produce a new hash, and be inserted as
a new row. The current-injury query is "newest row per nhl_id".
"""

from datetime import date, datetime, timedelta

from sqlalchemy import select

from src.core.db import get_session
from src.core.models import Player, PlayerInjury
from src.core.resolver import resolve_player
from src.ingest.daily_faceoff.scraper import scrape_lines, TEAM_SLUGS
from src.ingest.news.injury_parser import blurb_hash, parse_injury_blurbs


def _parse_news_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        return None


def _resolve_player_id(session, name: str, team_abbrev: str | None) -> int | None:
    if not name:
        return None
    try:
        nhl_id = resolve_player(session, name=name, team_abbrev=team_abbrev)
        if nhl_id:
            return nhl_id
    except Exception:
        pass
    try:
        nhl_id = resolve_player(session, name=name)
        if nhl_id:
            return nhl_id
    except Exception:
        pass
    parts = name.split()
    if len(parts) >= 2:
        last = parts[-1]
        matches = (
            session.query(Player).filter(Player.full_name.ilike(f"%{last}%")).all()
        )
        if len(matches) == 1:
            return matches[0].nhl_id
        if len(matches) > 1 and team_abbrev:
            for m in matches:
                if m.team_abbrev == team_abbrev:
                    return m.nhl_id
    return None


def ingest_injuries() -> dict:
    """Scrape DF, LLM-parse new blurbs, persist.

    Returns {teams, injured, new_blurbs, llm_parsed, llm_failed,
             no_blurb, skipped_existing}.
    """
    # 1. Scrape all teams and collect injured players with blurbs
    raw_injuries: list[dict] = []
    teams_scraped = 0
    for slug in TEAM_SLUGS:
        team_data = scrape_lines(slug)
        if not team_data:
            continue
        teams_scraped += 1
        for p in team_data["players"]:
            if not p.get("injury_status"):
                continue
            news = p.get("news") or {}
            details = news.get("details")
            raw_injuries.append({
                "name": p["name"],
                "position": p.get("position"),
                "team_abbrev": team_data["team_abbrev"],
                "injury_status": p["injury_status"],
                "game_time_decision": bool(p.get("game_time_decision")),
                "news_details": details,
                "news_date": _parse_news_date(news.get("created_at")),
            })

    # 2. Figure out which blurbs are new (not already in the DB by hash)
    with get_session() as session:
        existing_hashes: set[str] = set(
            session.scalars(select(PlayerInjury.news_hash)).all()
        )

    # The dedup key is (player, blurb-content) — pure content hash
    # would collide when DF uses the same boilerplate blurb for
    # multiple players (e.g. "is not expected to play Monday").
    for r in raw_injuries:
        blurb = r["news_details"] or f"{r['injury_status']}|no-blurb"
        key = f"{r['name']}|{r['team_abbrev']}|{blurb}"
        r["news_hash"] = blurb_hash(key)
        r["content_hash"] = blurb_hash(r["news_details"]) if r["news_details"] else None

    seen_hashes: set[str] = set()
    new_entries: list[dict] = []
    for r in raw_injuries:
        h = r["news_hash"]
        if h in existing_hashes or h in seen_hashes:
            continue
        seen_hashes.add(h)
        new_entries.append(r)

    # 3. LLM-parse the new blurbs (only those with text)
    new_with_blurb = [r for r in new_entries if r["news_details"]]
    blurbs = [r["news_details"] for r in new_with_blurb]
    parsed_by_hash = parse_injury_blurbs(blurbs) if blurbs else {}

    llm_parsed = 0
    llm_failed = 0
    no_blurb_count = 0
    rows: list[PlayerInjury] = []

    with get_session() as session:
        for r in new_entries:
            nhl_id = _resolve_player_id(session, r["name"], r["team_abbrev"])
            parsed = parsed_by_hash.get(r["content_hash"]) if r["content_hash"] else None

            if r["news_details"]:
                if parsed is not None:
                    llm_parsed += 1
                else:
                    llm_failed += 1
            else:
                no_blurb_count += 1

            rows.append(PlayerInjury(
                nhl_id=nhl_id,
                player_name=r["name"],
                team_abbrev=r["team_abbrev"],
                position=r["position"],
                injury_status=r["injury_status"],
                game_time_decision=r["game_time_decision"],
                news_details=r["news_details"],
                news_date=r["news_date"],
                news_hash=r["news_hash"],
                category=parsed["category"] if parsed else None,
                body_part=parsed["body_part"] if parsed else None,
                severity=parsed["severity"] if parsed else None,
                timeline_days_min=parsed["timeline_days_min"] if parsed else None,
                timeline_days_max=parsed["timeline_days_max"] if parsed else None,
                expected_return=parsed["expected_return"] if parsed else None,
                summary=parsed["summary"] if parsed else None,
                llm_parsed=parsed is not None,
            ))

        session.add_all(rows)

    return {
        "teams": teams_scraped,
        "injured": len(raw_injuries),
        "new_blurbs": len(new_entries),
        "llm_parsed": llm_parsed,
        "llm_failed": llm_failed,
        "no_blurb": no_blurb_count,
        "skipped_existing": len(raw_injuries) - len(new_entries),
    }


def reparse_failed(reparse_all: bool = False) -> dict:
    """Re-run the LLM on rows that need (re-)parsing.

    With reparse_all=False (default): only rows where llm_parsed=False.
    With reparse_all=True: all rows with news_details (schema change etc).
    """
    with get_session() as session:
        q = session.query(PlayerInjury).filter(
            PlayerInjury.news_details.isnot(None)
        )
        if not reparse_all:
            q = q.filter(PlayerInjury.llm_parsed.is_(False))
        pending = q.all()
        stubs = [(row.id, row.news_details) for row in pending]

    if not stubs:
        return {"pending": 0, "parsed": 0, "failed": 0}

    # Dedup by content — multiple rows may share boilerplate blurb
    content_by_id = {row_id: text for row_id, text in stubs}
    unique_blurbs = list({text for text in content_by_id.values()})
    parsed_by_hash = parse_injury_blurbs(unique_blurbs)

    parsed_count = 0
    failed_count = 0
    with get_session() as session:
        for row_id, text in stubs:
            parsed = parsed_by_hash.get(blurb_hash(text))
            if parsed is None:
                failed_count += 1
                continue
            row = session.get(PlayerInjury, row_id)
            if row is None:
                continue
            row.category = parsed["category"]
            row.body_part = parsed["body_part"]
            row.severity = parsed["severity"]
            row.timeline_days_min = parsed["timeline_days_min"]
            row.timeline_days_max = parsed["timeline_days_max"]
            row.expected_return = parsed["expected_return"]
            row.summary = parsed["summary"]
            row.llm_parsed = True
            parsed_count += 1

    return {"pending": len(stubs), "parsed": parsed_count, "failed": failed_count}


# Severity → day-range fallbacks when the blurb doesn't state an
# explicit timeline. Conservative ranges tuned for fantasy relevance.
SEVERITY_DAY_DEFAULTS = {
    "day-to-day": (1, 4),
    "week-to-week": (7, 21),
    "month-plus": (30, 90),
    # "season" is handled dynamically from the schedule
}


def _team_season_end(session, team_abbrev: str | None) -> date | None:
    """Last scheduled game date for this team (regular season end)."""
    if not team_abbrev:
        return None
    from src.core.models import Game, Team
    team = session.query(Team).filter(Team.abbrev == team_abbrev).first()
    if not team:
        return None
    from sqlalchemy import func
    return session.query(func.max(Game.date)).filter(
        (Game.home_team_id == team.team_id) | (Game.away_team_id == team.team_id)
    ).scalar()


def _estimate_return_window(
    row, session
) -> tuple[date | None, date | None]:
    """Compute (soonest_return, latest_return) for an injury row.

    Priority:
    1. Explicit expected_return from the blurb → both dates = that date.
    2. Explicit timeline_days_min/max → news_date + days.
    3. Severity defaults → news_date + default range.
    4. severity=season → team's last game.
    5. severity=unknown → (None, None).
    """
    from datetime import timedelta

    # 1. Explicit return date from blurb
    if row.expected_return:
        return row.expected_return, row.expected_return

    anchor = row.news_date.date() if row.news_date else date.today()

    # 2. Explicit timeline from blurb
    if row.timeline_days_min and row.timeline_days_max:
        return (
            anchor + timedelta(days=row.timeline_days_min),
            anchor + timedelta(days=row.timeline_days_max),
        )

    # 3. Severity-based defaults
    sev = row.severity or "unknown"
    if sev in SEVERITY_DAY_DEFAULTS:
        lo, hi = SEVERITY_DAY_DEFAULTS[sev]
        return anchor + timedelta(days=lo), anchor + timedelta(days=hi)

    # 4. Season-ending: null = not returning this season
    if sev == "season":
        return None, None

    # 5. Unknown: null = insufficient data to estimate
    return None, None


def current_injuries(team_abbrev: str | None = None) -> list[dict]:
    """Return the most recent injury row per player, filtered to actual injuries.

    Includes computed `soonest_return` and `latest_return` dates for
    model consumption. Both are null when severity='season' (player is
    not returning this season) or 'unknown' (no data to estimate from).
    Only rows with category='injury' are included.
    """
    from sqlalchemy import func, or_

    with get_session() as session:
        # Latest row per (player_name, team_abbrev)
        subq = (
            select(
                PlayerInjury.player_name,
                PlayerInjury.team_abbrev,
                func.max(PlayerInjury.id).label("max_id"),
            )
            .group_by(PlayerInjury.player_name, PlayerInjury.team_abbrev)
            .subquery()
        )
        q = session.query(PlayerInjury).join(
            subq, PlayerInjury.id == subq.c.max_id
        ).filter(
            or_(
                PlayerInjury.category == "injury",
                PlayerInjury.category.is_(None),  # pre-schema rows
            )
        )
        if team_abbrev:
            q = q.filter(PlayerInjury.team_abbrev == team_abbrev)

        rows = q.order_by(
            PlayerInjury.team_abbrev, PlayerInjury.player_name
        ).all()

        results = []
        for r in rows:
            soonest, latest = _estimate_return_window(r, session)
            results.append({
                "nhl_id": r.nhl_id,
                "player_name": r.player_name,
                "team_abbrev": r.team_abbrev,
                "position": r.position,
                "injury_status": r.injury_status,
                "game_time_decision": r.game_time_decision,
                "category": r.category,
                "body_part": r.body_part,
                "severity": r.severity,
                "timeline_days_min": r.timeline_days_min,
                "timeline_days_max": r.timeline_days_max,
                "expected_return": r.expected_return.isoformat() if r.expected_return else None,
                "soonest_return": soonest.isoformat() if soonest else None,
                "latest_return": latest.isoformat() if latest else None,
                "summary": r.summary,
                "news_details": r.news_details,
                "news_date": r.news_date.isoformat() if r.news_date else None,
            })

        return results
