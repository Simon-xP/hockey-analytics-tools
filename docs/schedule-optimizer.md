# Fantasy Hockey Schedule Optimizer

A tool for Yahoo Fantasy Hockey (points-based, daily lineups) that helps identify roster flexibility and predict player fantasy output.

## Overview

The tool has two main components:

1. **Schedule & Roster Flexibility** (Priority) - Determine which days have open roster slots and what positions can be streamed
2. **Fantasy Point Predictions** (Later) - Project player fantasy output based on stats and matchups

---

## Part 1: Schedule & Roster Flexibility

### Problem Statement

In daily lineup fantasy hockey, you need to know:
- Which days have roster flexibility (open slots)?
- Which positions can I stream on a given day?
- How does multi-position eligibility affect slot availability?

### Inputs

#### League Settings
```python
league_slots = {
    "C": 2,      # Center slots
    "LW": 2,     # Left wing slots
    "RW": 2,     # Right wing slots
    "D": 4,      # Defenseman slots
    "G": 2,      # Goalie slots
    "UTIL": 2,   # Utility (any F or D, not G)
    "BN": 4,     # Bench slots
    "IR": 2,     # Injured reserve
}
```

#### Roster
Each player has:
- Name or NHL ID
- Team (for schedule lookup)
- Positional eligibility (from Yahoo): `["C"]`, `["C", "LW"]`, `["LW", "RW"]`, etc.

```python
roster = [
    {"name": "Connor McDavid", "team": "EDM", "positions": ["C"]},
    {"name": "Leon Draisaitl", "team": "EDM", "positions": ["C", "LW"]},
    {"name": "Cale Makar", "team": "COL", "positions": ["D"]},
    # ...
]
```

#### Schedule
The NHL schedule CSV (`data/raw/nhl-schedule-raw.csv`) contains:
- `date` - Game date
- `teamId` - NHL team ID
- `team` - Team abbreviation (ANA, BOS, etc.)
- `opponent` - Opponent abbreviation
- `yahooWk` - Yahoo fantasy week number
- `b2b` - Back-to-back flag
- `offNight` - Off-night flag (fewer games league-wide)

### Algorithm: Slot Availability

The challenge is that multi-position eligibility creates an **assignment problem**. A player eligible for C/LW could fill either slot, affecting what's available for other players.

#### Naive Approach (Current Stubs)
Assign each player to their first eligible position. Simple but suboptimal.

#### Optimal Approach
Use **bipartite matching** to maximize roster utilization:

1. Build a graph: players → positions they're eligible for
2. Find maximum matching that fills the most slots
3. After matching, identify unfilled slots = available for streaming

For the UTIL slot:
- UTIL can be filled by any forward (C, LW, RW) or defenseman (D)
- Only use UTIL after primary position slots are filled
- Goalies cannot fill UTIL

#### Output

```python
# For a given week or date range:
{
    "2025-10-14": {
        "playing": ["McDavid", "Draisaitl", "Makar"],
        "slots_used": {"C": 2, "LW": 0, "D": 1, "UTIL": 0},
        "slots_available": {"C": 0, "LW": 2, "RW": 2, "D": 3, "UTIL": 2},
        "can_stream": ["LW", "RW", "D"],  # Positions with open slots
    },
    "2025-10-15": {
        # ...
    }
}
```

### Streaming Recommendations

Based on slot availability, recommend:
- **Best days to stream**: Days with most open slots
- **Best positions to stream**: Positions consistently available
- **Tight days**: Days near roster capacity (prioritize your best players)

---

## Part 2: Fantasy Point Predictions

**Status: Future work - details TBD**

Key factors to consider (will be refined later):
- Opponent goals against per game
- Player season stats
- Specific underlying stats (to be determined)
- Ice time
- Line deployment

The exact algorithm and weighting will be developed when we get to this phase. More research needed on which factors matter most.

---

## Data Sources

| Source | Data | Status |
|--------|------|--------|
| NHL Schedule CSV | Game dates, teams, opponents | Available |
| NHL API | Player IDs, teams, positions | Implemented |
| Natural Stat Trick | Season stats, L5 rolling, on-ice | Implemented |
| Daily Faceoff | Goalie starts | Not yet |
| Yahoo Fantasy API | Roster, positional eligibility | Not yet |

---

## Implementation Plan

### Phase 1: Schedule Flexibility (Current Focus)

1. Load schedule CSV into `games` table
2. Create roster input format (JSON config file)
3. Implement slot availability algorithm
4. Build CLI or notebook interface for queries

### Phase 2: Point Predictions (Later)

Details to be determined based on further research.

---

## File Structure

```
src/tools/schedule/
├── __init__.py
├── optimizer.py      # Slot availability algorithm
├── projections.py    # Fantasy point predictions (future)
└── models.py         # Roster, LeagueSettings dataclasses

config/
└── roster.json       # User's current roster

scripts/
└── run_optimizer.py  # CLI entry point
```
