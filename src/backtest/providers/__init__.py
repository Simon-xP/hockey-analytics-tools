"""Backtest-only data providers.

Reconstruct historical league state that has no live equivalent: who was on
which roster on a given day, and who was actually available in the FA pool.

The generic temporal-gated readers (`StatsProvider`, `ScheduleProvider`) are
*not* here — they live in `src.core.queries` because live code needs the same
`as_of` discipline that backtests do.
"""

from src.backtest.providers.fa_pool import FAPoolReconstructor
from src.backtest.providers.yahoo import YahooProvider

__all__ = ["FAPoolReconstructor", "YahooProvider"]
