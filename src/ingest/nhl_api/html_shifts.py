"""Fallback shift report parser using NHL's HTML shift reports.

The `shiftcharts` stats API returns empty data for ~38% of recent games
for no clear reason. The official HTML shift reports at
`www.nhl.com/scores/htmlreports/{season}/T{H|V}{short_id}.HTM` are reliable.

This module mirrors the approach used by HarryShomer's hockey-scraper.
"""

import re
import time

import httpx
from bs4 import BeautifulSoup

HTML_URL = "https://www.nhl.com/scores/htmlreports/{season}/T{side}{short_id}.HTM"


def _parse_time(s: str) -> str:
    """Extract elapsed time from 'MM:SS / MM:SS' format."""
    s = s.strip()
    if "/" in s:
        s = s.split("/")[0].strip()
    return s


def _parse_period(s: str) -> int | None:
    s = s.strip().upper()
    if s == "OT":
        return 4
    if s == "SO":
        return 5
    try:
        return int(s)
    except ValueError:
        return None


def _parse_html_report(
    html: str, side: str, team_id: int | None, sweater_map: dict
) -> list[dict]:
    """Parse a single team's HTML shift report.

    Walks `<tr>` elements in document order. A row containing a
    `<td class="playerHeading">` marks the start of a new player's shift
    block; subsequent rows with class `oddColor`/`evenColor` and 6 cells
    are that player's shifts until the next heading.
    """
    soup = BeautifulSoup(html, "html.parser")
    shifts: list[dict] = []
    cur_pid: int | None = None

    for tr in soup.find_all("tr"):
        heading = tr.find("td", class_="playerHeading")
        if heading is not None:
            txt = heading.get_text(strip=True)
            m = re.match(r"^(\d+)\s+(.+)", txt)
            if m:
                cur_pid = sweater_map.get((side, int(m.group(1))))
            else:
                cur_pid = None
            continue

        if cur_pid is None:
            continue

        tr_classes = tr.get("class") or []
        if "oddColor" not in tr_classes and "evenColor" not in tr_classes:
            continue

        tds = tr.find_all("td", recursive=False)
        if len(tds) < 5:
            continue

        shift_num_txt = tds[0].get_text(strip=True)
        period_txt = tds[1].get_text(strip=True)
        start_txt = tds[2].get_text(strip=True)
        end_txt = tds[3].get_text(strip=True)
        duration_txt = tds[4].get_text(strip=True)

        # Real shift rows have start/end columns formatted as
        # "MM:SS / MM:SS" (elapsed / remaining). Per-player stat summary
        # tables also use oddColor/evenColor but their columns are
        # Per/SHF/AVG/TOI/EV/TOT — skip them.
        if "/" not in start_txt or "/" not in end_txt:
            continue

        try:
            shift_num = int(shift_num_txt)
        except ValueError:
            continue
        per = _parse_period(period_txt)
        if per is None:
            continue

        shifts.append(
            {
                "player_id": cur_pid,
                "shift_number": shift_num,
                "period": per,
                "start_time": _parse_time(start_txt),
                "end_time": _parse_time(end_txt),
                "duration": duration_txt,
                "team_id": team_id,
            }
        )

    return shifts


def get_game_shifts_from_html(game_id: int) -> list[dict]:
    """Fetch shifts for a game by parsing NHL HTML shift reports.

    Returns the same shape as `get_game_shifts`. Requires a boxscore lookup
    to map jersey numbers to player IDs.
    """
    from src.ingest.nhl_api.client import (
        REQUEST_DELAY, canonical_team_id, get_game_boxscore,
    )

    try:
        boxscore = get_game_boxscore(game_id)
    except Exception as e:
        print(f"  HTML fallback: boxscore lookup failed for {game_id}: {e}")
        return []

    home_id = canonical_team_id(boxscore.get("homeTeam", {}).get("id"))
    away_id = canonical_team_id(boxscore.get("awayTeam", {}).get("id"))

    sweater_map: dict[tuple[str, int], int] = {}
    pg = boxscore.get("playerByGameStats", {})
    for yh_side, side_label in [("homeTeam", "H"), ("awayTeam", "V")]:
        for grp in ("forwards", "defense", "goalies"):
            for p in pg.get(yh_side, {}).get(grp, []):
                sweater = p.get("sweaterNumber")
                pid = p.get("playerId")
                if sweater is not None and pid is not None:
                    sweater_map[(side_label, int(sweater))] = int(pid)

    gid = str(game_id)
    season_start = int(gid[:4])
    season = f"{season_start}{season_start + 1}"
    short_id = gid[4:]

    all_shifts: list[dict] = []
    for side, team_id in (("H", home_id), ("V", away_id)):
        time.sleep(REQUEST_DELAY)
        url = HTML_URL.format(season=season, side=side, short_id=short_id)
        try:
            r = httpx.get(url, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print(f"  HTML shift fetch failed for {game_id} side={side}: {e}")
            continue
        shifts = _parse_html_report(r.text, side, team_id, sweater_map)
        all_shifts.extend(shifts)

    return all_shifts
