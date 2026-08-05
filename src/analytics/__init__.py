"""Analytics layer — derived metrics computed from raw ingested data.

Not predictions and not decisions: these modules turn play-by-play events
and shifts into the enriched stats everything else reads.

- `advanced_stats/` — shift/event correlation → `GameAdvancedStats`
- `xg/`             — shot-level expected goals model → `shot_attempts.xg`
- `rapm/`           — regularized adjusted plus-minus player ratings
"""
