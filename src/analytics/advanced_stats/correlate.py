"""Shift-event correlation engine.

The core algorithm that determines which players were on ice for each game
event. This is how we compute on-ice stats (Corsi, Fenwick, xGF, etc.)
from raw play-by-play data — the same approach used by NST, MoneyPuck,
and Evolving Hockey.

## Algorithm

For a single game:

1. Load all events (sorted by time) and all shifts.

2. Build a time-indexed shift lookup: for each (team, period), a sorted
   list of shifts. To find who's on ice at time T, binary search for shifts
   where start_time <= T <= end_time.

3. Handle situation-code transitions within shifts. A player's shift might
   span a penalty being called. We split TOI credit at each transition:
   if a shift runs 2:00-3:00 and a penalty starts at 2:30, the player gets
   30s of 5v5 TOI and 30s of PP/PK TOI.

4. For each event, look up on-ice players for both teams, then credit:
   - Individual stats to the acting player (shooter, hitter, etc.)
   - On-ice "for" stats to all players on the acting team
   - On-ice "against" stats to all players on the opposing team

5. Aggregate into per-player, per-situation totals.

## Situation Classification

From the situation_code (format: away_goalie, away_skaters, home_skaters,
home_goalie), we classify from each team's perspective:

- "5v5": strictly 5 skaters per side with both goalies in net
- "4v4": 4 skaters per side (offsetting minors)
- "3v3": 3 skaters per side (overtime)
- "pp": this team has MORE skaters than opponent
- "pk": this team has FEWER skaters than opponent
- "other": empty net, pulled goalie, or unusual states
- "all": sum of everything (always computed)

Note: 4v4 and 3v3 are separate from 5v5. This matches NST's convention
where "5v5" means literally 5-on-5, not all even-strength play.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field

from src.core.models.shot_attempts import NET_X
from src.analytics.advanced_stats.shifts import (
    build_shift_index,
    build_situation_timeline,
    classify_situation,
    compute_attack_directions,
    load_events,
    load_shifts,
    load_shot_xg,
    players_on_ice,
    split_shift_by_situation,
    time_to_seconds,
)


def is_scoring_chance(x_adj: float, y_adj: float) -> bool:
    """Is this shot from the scoring chance area (the 'home plate')?

    Uses the standard War On Ice / NST "home plate" pentagon definition.
    The zone is a trapezoid that's wide at the top of the faceoff circles
    and narrows toward the net:

    - Outer edge: x_adj = 54 (top of the faceoff circles), |y_adj| <= 22
    - Inner edge: x_adj = 89 (goal line), |y_adj| <= 9
    - Sides: linear taper from 22 wide to 9 wide

    The max allowed |y| at any x is linearly interpolated:
        at x=54: |y| <= 22
        at x=89: |y| <= 9
    """
    if x_adj < 54:
        return False

    # Linear interpolation of max |y| from outer (54, 22) to inner (89, 9)
    # slope = (9 - 22) / (89 - 54) = -13/35
    max_y = 22 - (x_adj - 54) * (13 / 35)
    return abs(y_adj) <= max_y


def is_high_danger(x_adj: float, y_adj: float) -> bool:
    """Is this shot from the high-danger area (inner slot)?

    Tighter definition based on validation against NST: the area
    directly in front of the net, roughly within the crease extended.

    Uses distance from net center (89, 0) <= 14 feet, combined with
    a lateral constraint of |y| <= 9. This avoids counting shots
    from sharp angles behind the faceoff dots.

    Note: Our HD counts run ~1-2 higher per game than NST on average.
    Zone-based SC/HDSC are inherently approximate — for precision,
    use xGF/xGA from our xG model instead.
    """
    dist = math.sqrt((x_adj - NET_X) ** 2 + y_adj ** 2)
    return dist <= 14 and abs(y_adj) <= 9


@dataclass
class PlayerGameStats:
    """Accumulator for one player's stats in one game, one situation."""
    toi_seconds: float = 0.0

    # Individual
    goals: int = 0
    assists: int = 0
    first_assists: int = 0
    second_assists: int = 0
    shots: int = 0
    shot_attempts: int = 0
    missed_shots: int = 0
    blocked_shots: int = 0  # this player's shots that got blocked
    hits: int = 0
    hits_taken: int = 0
    blocks: int = 0  # shots this player blocked defensively
    giveaways: int = 0
    takeaways: int = 0
    penalties: int = 0
    penalties_drawn: int = 0
    faceoff_wins: int = 0
    faceoff_losses: int = 0
    ixg: float = 0.0

    # On-ice
    cf: int = 0
    ca: int = 0
    ff: int = 0
    fa: int = 0
    sf: int = 0
    sa: int = 0
    gf: int = 0
    ga: int = 0
    xgf: float = 0.0
    xga: float = 0.0
    scf: int = 0
    sca: int = 0
    hdcf: int = 0
    hdca: int = 0

    # Zone starts
    oz_starts: int = 0
    dz_starts: int = 0
    nz_starts: int = 0


def compute_game_advanced_stats(
    session,
    game_id: int,
    home_team_id: int,
    away_team_id: int,
) -> dict[tuple[int, str], PlayerGameStats]:
    """Compute advanced stats for all players in a single game.

    This is the main entry point for the correlation engine.

    Args:
        session: SQLAlchemy session.
        game_id: Game to process.
        home_team_id: Home team ID.
        away_team_id: Away team ID.

    Returns:
        Dict of (player_id, situation) -> PlayerGameStats.
        Each player will have entries for specific situations they played in
        (e.g., "5v5", "pp") plus an "all" entry summing everything.
    """
    # ------------------------------------------------------------------
    # Step 1: Load events and shifts
    # ------------------------------------------------------------------
    events = load_events(session, game_id)
    shifts = load_shifts(session, game_id)
    shot_xg = load_shot_xg(session, game_id)

    if not events or not shifts:
        return {}

    # ------------------------------------------------------------------
    # Step 2: Build shift lookup for fast on-ice queries
    # ------------------------------------------------------------------
    shift_index = build_shift_index(shifts)

    # Build a map of player_id -> team_id from shift data
    player_team = {}
    for s in shifts:
        player_team[s["player_id"]] = s["team_id"]

    # ------------------------------------------------------------------
    # Step 3: Compute situation-split TOI for each player
    # ------------------------------------------------------------------
    situation_timeline = build_situation_timeline(events)

    stats = defaultdict(PlayerGameStats)

    for s in shifts:
        start_s = time_to_seconds(s["start_time"])
        end_s = time_to_seconds(s["end_time"])
        if end_s <= start_s:
            continue

        period = s["period"]
        player_id = s["player_id"]
        team_id = s["team_id"]

        segments = split_shift_by_situation(
            start_s, end_s, period, team_id, home_team_id, situation_timeline
        )

        for situation, seg_seconds in segments:
            stats[(player_id, situation)].toi_seconds += seg_seconds

    # ------------------------------------------------------------------
    # Step 4: Process each event — credit individual + on-ice stats
    # ------------------------------------------------------------------
    attack_dirs = compute_attack_directions(events)

    for e in events:
        event_type = e["event_type"]
        period = e["period"]
        event_time_s = time_to_seconds(e["time_in_period"])
        event_team_id = e["team_id"]
        situation_code = e["situation_code"]

        if event_team_id is None:
            continue  # period-start, period-end, etc.

        # Classify situation from each team's perspective
        home_sit = classify_situation(situation_code, home_team_id, home_team_id)
        away_sit = classify_situation(situation_code, away_team_id, home_team_id)

        def team_situation(tid):
            return home_sit if tid == home_team_id else away_sit

        opp_team_id = away_team_id if event_team_id == home_team_id else home_team_id

        # Find players on ice for both teams
        on_ice_event_team = players_on_ice(
            shift_index, event_team_id, period, event_time_s
        )
        on_ice_opp_team = players_on_ice(
            shift_index, opp_team_id, period, event_time_s
        )

        sit_for_event_team = team_situation(event_team_id)
        sit_for_opp_team = team_situation(opp_team_id)

        # Compute adjusted coordinates for danger zone classification
        is_sc = False
        is_hd = False
        if e["x_coord"] is not None and e["y_coord"] is not None:
            attacks_pos = attack_dirs.get((event_team_id, period), True)
            x_adj = e["x_coord"] if attacks_pos else -e["x_coord"]
            y_adj = e["y_coord"] if attacks_pos else -e["y_coord"]
            is_sc = is_scoring_chance(x_adj, y_adj)
            is_hd = is_high_danger(x_adj, y_adj)

        # Get xG for this event if it's a shot attempt
        event_xg = shot_xg.get((game_id, e["event_id"]), 0.0)

        # === SHOT ATTEMPTS (shot-on-goal, missed-shot, blocked-shot, goal) ===
        is_shot_attempt = event_type in (
            "shot-on-goal", "missed-shot", "blocked-shot", "goal"
        )

        if is_shot_attempt:
            shooter_id = e["player_1_id"]

            # --- Individual stats for shooter ---
            if shooter_id:
                s_stats = stats[(shooter_id, sit_for_event_team)]
                s_stats.shot_attempts += 1
                s_stats.ixg += event_xg

                if event_type == "shot-on-goal":
                    s_stats.shots += 1
                elif event_type == "missed-shot":
                    s_stats.missed_shots += 1
                elif event_type == "blocked-shot":
                    s_stats.blocked_shots += 1
                elif event_type == "goal":
                    s_stats.shots += 1  # goals count as shots on goal
                    s_stats.goals += 1

            # --- Individual stats for blocker (defensive block) ---
            if event_type == "blocked-shot" and e["player_2_id"]:
                blocker_id = e["player_2_id"]
                stats[(blocker_id, sit_for_opp_team)].blocks += 1

            # --- Goal: credit assists from detail JSON ---
            if event_type == "goal" and e.get("detail"):
                detail = e["detail"]
                a1 = detail.get("assist1PlayerId")
                a2 = detail.get("assist2PlayerId")
                if a1:
                    a1_stats = stats[(a1, sit_for_event_team)]
                    a1_stats.assists += 1
                    a1_stats.first_assists += 1
                if a2:
                    a2_stats = stats[(a2, sit_for_event_team)]
                    a2_stats.assists += 1
                    a2_stats.second_assists += 1

            # --- On-ice stats for all players on ice ---
            # Corsi: all shot attempts count
            for pid in on_ice_event_team:
                ps = stats[(pid, sit_for_event_team)]
                ps.cf += 1
                if event_type != "blocked-shot":
                    ps.ff += 1  # Fenwick excludes blocked shots
                if event_type in ("shot-on-goal", "goal"):
                    ps.sf += 1
                if event_type == "goal":
                    ps.gf += 1
                ps.xgf += event_xg
                if is_sc:
                    ps.scf += 1
                if is_hd:
                    ps.hdcf += 1

            for pid in on_ice_opp_team:
                ps = stats[(pid, sit_for_opp_team)]
                ps.ca += 1
                if event_type != "blocked-shot":
                    ps.fa += 1
                if event_type in ("shot-on-goal", "goal"):
                    ps.sa += 1
                if event_type == "goal":
                    ps.ga += 1
                ps.xga += event_xg
                if is_sc:
                    ps.sca += 1
                if is_hd:
                    ps.hdca += 1

        # === HITS ===
        elif event_type == "hit":
            hitter_id = e["player_1_id"]
            hittee_id = e["player_2_id"]
            if hitter_id:
                stats[(hitter_id, sit_for_event_team)].hits += 1
            if hittee_id:
                stats[(hittee_id, sit_for_opp_team)].hits_taken += 1

        # === GIVEAWAYS / TAKEAWAYS ===
        elif event_type == "giveaway" and e["player_1_id"]:
            stats[(e["player_1_id"], sit_for_event_team)].giveaways += 1
        elif event_type == "takeaway" and e["player_1_id"]:
            stats[(e["player_1_id"], sit_for_event_team)].takeaways += 1

        # === FACEOFFS ===
        elif event_type == "faceoff":
            winner_id = e["player_1_id"]
            loser_id = e["player_2_id"]
            zone = e.get("zone_code")

            if winner_id:
                stats[(winner_id, sit_for_event_team)].faceoff_wins += 1
            if loser_id:
                stats[(loser_id, sit_for_opp_team)].faceoff_losses += 1

            # Zone starts: credit all on-ice players.
            # zone_code is from the event-owning (winning) team's perspective.
            # For the opposing team, O and D are flipped.
            if zone:
                opp_zone = {"O": "D", "D": "O", "N": "N"}.get(zone, zone)
                for pid in on_ice_event_team:
                    _credit_zone_start(
                        stats[(pid, sit_for_event_team)], zone
                    )
                for pid in on_ice_opp_team:
                    _credit_zone_start(
                        stats[(pid, sit_for_opp_team)], opp_zone
                    )

        # === PENALTIES ===
        elif event_type == "penalty":
            committed_by = e["player_1_id"]
            drawn_by = e["player_2_id"]
            if committed_by:
                stats[(committed_by, sit_for_event_team)].penalties += 1
            if drawn_by:
                stats[(drawn_by, sit_for_opp_team)].penalties_drawn += 1

    # ------------------------------------------------------------------
    # Step 5: Compute "all" situation rows and derived stats
    # ------------------------------------------------------------------
    # Group by player to build "all" rows
    player_situations = defaultdict(list)
    for (pid, sit), st in stats.items():
        if sit != "all":
            player_situations[pid].append((sit, st))

    for pid, sit_stats_list in player_situations.items():
        all_stats = stats[(pid, "all")]
        for sit, st in sit_stats_list:
            # Sum every numeric field
            all_stats.toi_seconds += st.toi_seconds
            for attr in [
                "goals", "assists", "first_assists", "second_assists",
                "shots", "shot_attempts", "missed_shots", "blocked_shots",
                "hits", "hits_taken", "blocks", "giveaways", "takeaways",
                "penalties", "penalties_drawn", "faceoff_wins", "faceoff_losses",
                "cf", "ca", "ff", "fa", "sf", "sa", "gf", "ga",
                "scf", "sca", "hdcf", "hdca",
                "oz_starts", "dz_starts", "nz_starts",
            ]:
                setattr(all_stats, attr, getattr(all_stats, attr) + getattr(st, attr))
            all_stats.ixg += st.ixg
            all_stats.xgf += st.xgf
            all_stats.xga += st.xga

    # Compute points and IPP for all entries
    for (pid, sit), st in stats.items():
        st.points = st.goals + st.assists
        if st.gf > 0:
            st.ipp = st.points / st.gf
        else:
            st.ipp = None

    return dict(stats), dict(player_team)


def _credit_zone_start(player_stats: PlayerGameStats, zone_code: str):
    if zone_code == "O":
        player_stats.oz_starts += 1
    elif zone_code == "D":
        player_stats.dz_starts += 1
    elif zone_code == "N":
        player_stats.nz_starts += 1
