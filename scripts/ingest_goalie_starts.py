"""Persist Daily Faceoff probable starting goalies into `goalie_starts`.

Deliberately low-traffic. One request to the starting-goalies page returns
every game on the slate, so a full refresh costs a single HTTP call. There
is no per-team fan-out here and there should never be one.

Recommended cadence is three fixed runs a day rather than polling:

    0 14 * * *   morning slate, most "Expected" tags are up by then
    0 21 * * *   afternoon, tags firm up
    0 23 * * *   last look before the early puck drops

`--min-interval` guards against accidental hammering when the script is run
by hand or retried: a run inside the interval since the last successful
scrape for that date exits without making a request.

Why we persist reports we already "know": the confirmation wording is the
training label for the start-probability model. Daily Faceoff only serves
the current day, so this history cannot be backfilled later. Every day the
job does not run is a day of labels we can never recover.

Past dates are refused by default. The starting-goalies page for a date
that has already been played shows the settled, post-hoc state: everything
reads "Confirmed" because the games have been played. Storing those rows
alongside live captures would teach the calibration that our reports are
far more certain than they are at the moment we actually have to decide.
`--allow-backfill` exists for deliberate one-offs and tags the rows with a
separate source so they can be excluded from calibration.

Usage:
    python -m scripts.ingest_goalie_starts                  # today
    python -m scripts.ingest_goalie_starts --date 2026-04-08
    python -m scripts.ingest_goalie_starts --force          # ignore interval
    python -m scripts.ingest_goalie_starts --dry-run
"""

import argparse
from datetime import date, datetime, timedelta

from sqlalchemy import or_, text

from src.core.db import get_session, init_db
from src.core.models import Game, GoalieStart, Team
from src.core.resolver import resolve_player
from src.ingest.daily_faceoff.scraper import scrape_goalie_starts

SOURCE = "dailyfaceoff"
# Rows scraped for an already-played date. Kept separate so calibration can
# exclude them: the page shows the settled result, not the live report.
BACKFILL_SOURCE = "dailyfaceoff_backfill"

# Minimum gap between scrapes of the same date, in minutes. Three runs a
# day sit comfortably outside this; a stuck retry loop does not.
DEFAULT_MIN_INTERVAL_MINUTES = 90

# Daily Faceoff team slug -> our team abbreviation.
SLUG_TO_ABBREV = {
    "anaheim-ducks": "ANA", "boston-bruins": "BOS", "buffalo-sabres": "BUF",
    "calgary-flames": "CGY", "carolina-hurricanes": "CAR",
    "chicago-blackhawks": "CHI", "colorado-avalanche": "COL",
    "columbus-blue-jackets": "CBJ", "dallas-stars": "DAL",
    "detroit-red-wings": "DET", "edmonton-oilers": "EDM",
    "florida-panthers": "FLA", "los-angeles-kings": "LAK",
    "minnesota-wild": "MIN", "montreal-canadiens": "MTL",
    "nashville-predators": "NSH", "new-jersey-devils": "NJD",
    "new-york-islanders": "NYI", "new-york-rangers": "NYR",
    "ottawa-senators": "OTT", "philadelphia-flyers": "PHI",
    "pittsburgh-penguins": "PIT", "san-jose-sharks": "SJS",
    "seattle-kraken": "SEA", "st-louis-blues": "STL",
    "tampa-bay-lightning": "TBL", "toronto-maple-leafs": "TOR",
    # Utah has changed identity twice; accept every slug they have used.
    "utah-hockey-club": "UTA", "utah-mammoth": "UTA", "utah-utah": "UTA",
    "vancouver-canucks": "VAN", "vegas-golden-knights": "VGK",
    "washington-capitals": "WSH", "winnipeg-jets": "WPG",
}

# Confirmation wording that means the start is locked in. Everything else
# is stored verbatim and gets its own calibrated probability later.
CONFIRMED_TIERS = {"confirmed"}


def recently_scraped(session, target_date: date, minutes: int) -> datetime | None:
    """Most recent scrape timestamp for this date, if inside the window."""
    cutoff = datetime.now() - timedelta(minutes=minutes)
    return session.execute(
        text("""
            SELECT MAX(gs.observed_at)
            FROM goalie_starts gs
            JOIN games g ON g.game_id = gs.game_id
            WHERE g.date = :d AND gs.source = :src AND gs.observed_at >= :cutoff
        """),
        {"d": target_date, "src": SOURCE, "cutoff": cutoff},
    ).scalar()


def find_game(session, target_date: date, team_id: int) -> Game | None:
    """The game this team plays on this date."""
    return (
        session.query(Game)
        .filter(
            Game.date == target_date,
            or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
        )
        .first()
    )


def _team_id_from_slug(teams: dict[str, int], slug: str | None) -> int | None:
    if not slug:
        return None
    return teams.get(SLUG_TO_ABBREV.get(slug, ""))


def ingest(
    target_date: date,
    dry_run: bool = False,
    force: bool = False,
    min_interval: int = DEFAULT_MIN_INTERVAL_MINUTES,
    allow_backfill: bool = False,
) -> dict:
    totals = {"games": 0, "written": 0, "updated": 0, "unchanged": 0,
              "unresolved": 0, "no_game": 0, "no_goalie": 0}

    is_backfill = target_date < date.today()
    if is_backfill and not allow_backfill:
        print(
            f"{target_date} has already been played. The page now shows the "
            f"settled result rather than the live report, so these rows would "
            f"skew confirmation calibration. Pass --allow-backfill to store "
            f"them under the '{BACKFILL_SOURCE}' source anyway."
        )
        totals["skipped"] = True
        return totals

    source = BACKFILL_SOURCE if is_backfill else SOURCE

    with get_session() as session:
        if not force:
            last = recently_scraped(session, target_date, min_interval)
            if last is not None:
                print(f"Already scraped {target_date} at {last:%H:%M} "
                      f"(within {min_interval} min). Use --force to override.")
                totals["skipped"] = True
                return totals

        slate = scrape_goalie_starts(str(target_date))
        if not slate:
            print(f"No slate returned for {target_date}.")
            return totals

        totals["games"] = len(slate)
        teams = {t.abbrev: t.team_id for t in session.query(Team).all()}
        now = datetime.now()

        for entry in slate:
            for side in ("home", "away"):
                goalie = entry.get(f"{side}_goalie") or {}
                name = goalie.get("name")
                if not name:
                    totals["no_goalie"] += 1
                    continue

                team_id = _team_id_from_slug(
                    teams, entry.get(f"{side}_team_slug")
                )
                if team_id is None:
                    print(f"  Unknown team slug: {entry.get(f'{side}_team_slug')}")
                    totals["unresolved"] += 1
                    continue

                abbrev = next(
                    (a for a, tid in teams.items() if tid == team_id), None
                )
                nhl_id = resolve_player(
                    session, name=name, team_abbrev=abbrev, position="G",
                )
                if nhl_id is None:
                    print(f"  Could not resolve goalie: {name} ({abbrev})")
                    totals["unresolved"] += 1
                    continue

                game = find_game(session, target_date, team_id)
                if game is None:
                    print(f"  No scheduled game for {abbrev} on {target_date}")
                    totals["no_game"] += 1
                    continue

                confirmation = goalie.get("confirmation")
                is_confirmed = bool(
                    confirmation
                    and confirmation.strip().lower() in CONFIRMED_TIERS
                )

                if dry_run:
                    print(f"  {abbrev} {name} -> {confirmation} "
                          f"(game {game.game_id})")
                    totals["written"] += 1
                    continue

                # Append, never update. An observation is a fact about
                # what was reported at a moment; overwriting it destroys
                # the record of what was knowable when, and silently turns
                # every backtest of a goalie decision into leakage.
                previous = (
                    session.query(GoalieStart)
                    .filter(
                        GoalieStart.game_id == game.game_id,
                        GoalieStart.nhl_id == nhl_id,
                        GoalieStart.source == source,
                    )
                    .order_by(GoalieStart.observed_at.desc())
                    .first()
                )

                # Skip only when nothing has changed, so repeated runs do
                # not pad the log with identical rows. A changed tier is
                # always worth a new row: the moment it changed is data.
                if previous is not None and previous.confirmation == confirmation:
                    totals["unchanged"] += 1
                    continue

                session.add(GoalieStart(
                    game_id=game.game_id,
                    team_id=team_id,
                    nhl_id=nhl_id,
                    confirmed=is_confirmed,
                    confirmation=confirmation,
                    source=source,
                    observed_at=now,
                ))
                if previous is None:
                    totals["written"] += 1
                else:
                    totals["updated"] += 1

    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Persist Daily Faceoff probable starters"
    )
    parser.add_argument("--date", type=str, default=None,
                        help="Slate date (YYYY-MM-DD), default today")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Scrape even if inside the minimum interval")
    parser.add_argument("--min-interval", type=int,
                        default=DEFAULT_MIN_INTERVAL_MINUTES,
                        help="Minutes to wait between scrapes of a date")
    parser.add_argument("--allow-backfill", action="store_true",
                        help="Store an already-played date, tagged as backfill")
    args = parser.parse_args()
    init_db()

    target = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date else date.today()
    )

    print(f"Daily Faceoff starting goalies for {target}")
    totals = ingest(
        target, dry_run=args.dry_run, force=args.force,
        min_interval=args.min_interval, allow_backfill=args.allow_backfill,
    )

    if not totals.get("skipped"):
        print(
            f"\nDone: {totals['games']} games on slate, "
            f"{totals['written']} new, {totals['updated']} changed, "
            f"{totals['unchanged']} unchanged, "
            f"{totals['unresolved']} unresolved, "
            f"{totals['no_game']} with no scheduled game, "
            f"{totals['no_goalie']} with no goalie listed"
        )
