# Forecasting v2 — Feature Inventory

All 112 features currently fed to every stat model (goals, assists, shots, hits, blocks).
Each situation trains separate XGBoost regressors per stat, but they all receive the same feature vector.

Situations and their stats:
- 5v5: goals, assists, shots, hits, blocks
- PP: goals, assists, shots
- PK: shots, hits, blocks
- Other (4v4/3v3/EN): goals, assists


## Rolling window features (L5 = last 5 games, L6-15 = games 6-15, season_avg = all prior games)

Each stat below appears 4x: l5_, l6_15_, l16_30_, season_avg_ (except l16_30 may be missing if <15 GP)

l5_goals                        per-60 goals rate, last 5 games
l5_first_assists                per-60 primary assists
l5_second_assists               per-60 secondary assists
l5_shots                        per-60 shots on goal
l5_ixg                          per-60 individual expected goals (from our xG model)
l5_shot_attempts                per-60 shot attempts (Corsi individual)
l5_hits                         per-60 hits
l5_blocks                       per-60 blocked shots
l5_penalties                    per-60 penalties taken
l5_penalties_drawn              per-60 penalties drawn
l5_oi_cf                        per-60 on-ice Corsi for (team shot attempts while on ice)
l5_oi_ca                        per-60 on-ice Corsi against
l5_oi_xgf                      per-60 on-ice expected goals for
l5_oi_xga                      per-60 on-ice expected goals against
l5_oi_hdcf                      per-60 on-ice high-danger chances for
l5_cf_pct                       Corsi for % (CF / (CF + CA))
l5_xgf_pct                      expected goals for % (xGF / (xGF + xGA))
l5_ozs_pct                      offensive zone start % (OZ / (OZ + DZ))
l5_sh_pct                       shooting percentage (goals / shots)
l5_toi                          raw TOI in seconds (not per-60)

l6_15_goals
l6_15_first_assists
l6_15_second_assists
l6_15_shots
l6_15_ixg
l6_15_shot_attempts
l6_15_hits
l6_15_blocks
l6_15_penalties
l6_15_penalties_drawn
l6_15_oi_cf
l6_15_oi_ca
l6_15_oi_xgf
l6_15_oi_xga
l6_15_oi_hdcf
l6_15_cf_pct
l6_15_xgf_pct
l6_15_ozs_pct
l6_15_sh_pct
l6_15_toi

season_avg_goals
season_avg_first_assists
season_avg_second_assists
season_avg_shots
season_avg_ixg
season_avg_shot_attempts
season_avg_hits
season_avg_blocks
season_avg_penalties
season_avg_penalties_drawn
season_avg_oi_cf
season_avg_oi_ca
season_avg_oi_xgf
season_avg_oi_xga
season_avg_oi_hdcf
season_avg_cf_pct
season_avg_xgf_pct
season_avg_ozs_pct
season_avg_sh_pct
season_avg_toi

season_gp                       games played this season (before prediction date)


## Prior season features

prior_gp                        games played last season
prior_toi_per_gp                average TOI per game last season
prior_goals                     per-60 goals last season
prior_first_assists             per-60 primary assists last season
prior_second_assists            per-60 secondary assists last season
prior_shots                     per-60 shots last season
prior_ixg                       per-60 individual xG last season
prior_shot_attempts             per-60 shot attempts last season
prior_hits                      per-60 hits last season
prior_blocks                    per-60 blocks last season
prior_penalties                 per-60 penalties last season
prior_cf                        per-60 on-ice Corsi for last season
prior_ca                        per-60 on-ice Corsi against last season
prior_hdcf                      per-60 on-ice high-danger chances for last season
prior_xgf                       per-60 on-ice xGF last season
prior_xga                       per-60 on-ice xGA last season
prior_cf_pct                    Corsi for % last season
prior_xgf_pct                   expected goals for % last season
prior_ozs_pct                   offensive zone start % last season
prior_sh_pct                    shooting % last season
prior_ipp                       individual points percentage last season (points / team goals while on ice)


## Blended features (Bayesian blend of prior season + current season)

Weighted combination: (prior * k + current * GP) / (k + GP), where k=20.
At 0 GP it's 100% prior, at 20 GP it's 50/50.

blended_goals
blended_first_assists
blended_second_assists
blended_shots
blended_ixg
blended_shot_attempts
blended_hits
blended_blocks
blended_ca
blended_cf
blended_hdcf
blended_xga
blended_xgf


## IPP features (individual points percentage)

ipp_season_raw                  raw IPP this season (points / team goals while on ice)
ipp_regressed                   EB-regressed IPP toward position mean (k=20 stabilization)
ipp_ewma_10                     EWMA of per-game IPP, half-life 10 games
ipp_ewma_15                     EWMA of per-game IPP, half-life 15 games


## Career shooting talent

career_shooting_talent          EB-shrunk goals over expected per shot (career-long, k=200)
career_shots                    total career shots (confidence weight for the talent estimate)


## Opponent features

opp_gaa                         opponent goals against per 60, full season average
opp_gaa_10                      opponent goals against per 60, last 10 games
opp_gfa                         opponent goals for per 60, full season average
opp_is_b2b                      1 if opponent is on a back-to-back


## Game context

is_home                         1 if player's team is home
is_b2b                          1 if player's team is on a back-to-back
days_rest                       days since last game (capped at 7)


## Position

is_forward                      1 if player is C/LW/RW
is_center                       1 if player is C
