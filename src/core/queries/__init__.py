"""Temporal-gated database reads.

Every query here takes an `as_of` date and uses a strict `Game.date < as_of`
cutoff, so a decision made on day D cannot see day D's own games. Live code
passes today; backtests pass the simulated decision date. Same code path
either way, which is the only reliable defense against time leakage.

- `stats`    — `StatsProvider`: FPTS/GP, trailing rankings, replacement level
- `schedule` — `ScheduleProvider`: team games in a range, fantasy week dates
- `stats_helpers` — standalone FPTS/GP computation shared by the API
"""

from src.core.queries.schedule import ScheduleProvider
from src.core.queries.stats import StatsProvider
from src.core.queries.stats_helpers import compute_fpts_per_gp

__all__ = ["ScheduleProvider", "StatsProvider", "compute_fpts_per_gp"]
