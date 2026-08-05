"""Player-level adjustment signals, both scored on [-1.0, 1.0].

- `upside`      — individual talent ceiling. Is this player underperforming
  their own underlying skill? Persists across weeks and seasons.
- `opportunity` — situational favorability. How good is their environment
  right now? Temporary by nature; ends when the situation reverts.

See docs/upside-and-opportunity.md.
"""

from src.predict.signals.opportunity import (
    compute_opportunity_breakdown,
    compute_opportunity_score,
)
from src.predict.signals.upside import (
    compute_upside_breakdown,
    compute_upside_score,
    hold_patience_games,
)

__all__ = [
    "compute_opportunity_breakdown",
    "compute_opportunity_score",
    "compute_upside_breakdown",
    "compute_upside_score",
    "hold_patience_games",
]
