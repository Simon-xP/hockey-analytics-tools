# Shooting Talent and xG Limitations

## The Problem

Most xG models (including ours, MoneyPuck, Evolving Hockey) predict the
probability of a goal based on shot context: location, shot type, game state,
rebound, etc. They do NOT account for **who is shooting**.

This means a Connor McDavid wrist shot from the slot gets the same xG as a
4th-liner's wrist shot from the same spot. In reality, elite shooters convert
at higher rates than their xG suggests — this isn't luck, it's skill.

## Why This Matters for Fantasy

### "Sell High" is misleading for elite shooters
If a player's goals significantly exceed their xG, the naive interpretation is
"they're getting lucky, regression is coming." But for players with proven
shooting talent (high career shooting %, consistently beating xG over multiple
seasons), this outperformance is **sustainable**.

Examples of players who consistently beat xG:
- Elite snipers with exceptional release (Ovechkin, Laine type players)
- Players with high tip-in/deflection rates (net-front power forwards)
- Players who generate shots in chaos (rebounds, scrambles)

### "Buy Low" is misleading for bad shooters
Conversely, a player underperforming their xG might not be unlucky — they
might just be a poor finisher. Their xG overestimates their actual goal output
because the model assumes average shooting talent.

## How MoneyPuck Handles This

MoneyPuck's about page mentions they include "shooter talent" as a feature in
their model — specifically, they regress a player's historical shooting
percentage toward the league mean to get an adjusted talent estimate. This
means their xG for McDavid from the slot would be slightly higher than for a
replacement-level player.

Harry Shomer's Shooter-xG-Model (GitHub) takes this further: it uses a
player's career Goals/xGoals ratio, regressed to the mean, as an additional
feature.

## Implications for PuckAgent

1. **Do NOT use raw xG vs actual goals as a "luck" indicator without context.**
   A player outperforming their xG for 3+ seasons is not getting lucky.

2. **When we build the forecasting model**, consider adding a shooter talent
   adjustment:
   - Career shooting % (regressed to position/league mean)
   - Multi-season goals/xG ratio
   - Shot type distribution (players who get more tip-ins/deflections have
     higher conversion rates that aren't luck)

3. **For the "optimal adds" / sell-high buy-low feature**: cross-reference
   xG over/underperformance with career shooting metrics before labeling
   someone as a sell-high or buy-low.

4. **Future xG model improvement**: Consider a "shooter-adjusted xG" variant
   that incorporates shooter identity. This is Phase 5 territory but would
   meaningfully improve fantasy projections. The trade-off is that it makes
   the model player-specific rather than context-only, which could overfit
   on small samples for callups or low-TOI players.

## Open Question

Should we build two xG variants?
- **Context-only xG**: What we have now. Useful for evaluating shot quality
  independent of who's shooting. Good for team-level analysis and measuring
  how well a line generates chances.
- **Shooter-adjusted xG**: Adds player identity. Better for predicting actual
  goal output. More useful for fantasy projections.

Both have value for different purposes.
