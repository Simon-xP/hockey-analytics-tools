# Scoring Chance Zone Definitions

## Our Definitions

### Scoring Chance (SC)
"Home plate" trapezoid based on the War On Ice standard:
- Outer edge at x_adj = 54 (top of faceoff circles), |y_adj| <= 22
- Narrows linearly to x_adj = 89 (goal line), |y_adj| <= 9
- All shot attempts (SOG + missed + blocked) from within this zone

### High-Danger Scoring Chance (HDSC)
Inner slot, tightened after validation:
- Distance from net center (89, 0) <= 14 feet
- AND |y_adj| <= 9 (narrow central corridor)
- Roughly the crease area plus the immediate slot in front

### Medium-Danger
Scoring chance minus high-danger (the outer portions of the home plate).

### Low-Danger
Everything outside the scoring chance zone.

## Validation Against NST (McDavid 5v5, 63 games, 2024-25)

| Stat | Exact match | Within ±1 | Within ±2 | Mean bias |
|------|------------|-----------|-----------|-----------|
| SCF  | 17%        | 57%       | 81%       | +0.3      |
| SCA  | 40%        | 79%       | 95%       | +0.0      |
| HDCF | 21%        | 43%       | 70%       | +1.7      |
| HDCA | 32%        | 67%       | 79%       | +1.1      |

Our zone definitions don't perfectly match NST's — exact thresholds are not
published and likely differ by a few feet. The HD zone in particular runs
~1-2 events higher per game than NST.

## Why This Is OK For Our Purposes

Zone-based SC/HDSC are a crude proxy. They classify shots as dangerous based
solely on location, ignoring shot type, rebound, rush, pre-shot movement, and
shooter talent.

**For the forecasting model, we use xGF/xGA from our xG model instead of
zone-based SCF/SCA.** The xG model incorporates 30 features including
distance, angle, shot type, rebound, rush, game state, and event sequence.
It provides a continuous probability rather than a binary zone classification.

We keep SC/HDSC for:
- Quick communication ("they had 12 high-danger chances")
- Backward compatibility with NST-style analysis
- Sanity checking

But xGF/xGA is the primary shot quality signal for predictions.

## NST's Definition (best guess)

NST uses the War On Ice home-plate model. Their exact pixel thresholds are
not published. Based on our validation:
- Their SC zone appears similar to ours but possibly slightly different
  in the outer corners
- Their HD zone appears tighter than ours (we overcounted by ~1.7/game
  before tightening, still ~1/game after)
- The variance is partly explained by coordinate recording differences
  between arenas ("scorer bias") which we don't yet adjust for
