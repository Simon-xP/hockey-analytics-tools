"""Tests for GameIndividualStats and GameOnIceStats models."""

from src.core.models.game_stats import GameIndividualStats, GameOnIceStats


class TestGameIndividualStats:
    def test_tablename(self):
        assert GameIndividualStats.__tablename__ == "game_individual_stats"

    def test_has_per_60_columns(self):
        col_names = {c.name for c in GameIndividualStats.__table__.columns}
        per_60_cols = [
            "goals_per_60", "total_assists_per_60", "first_assists_per_60",
            "second_assists_per_60", "total_points_per_60", "shots_per_60",
            "ixg_per_60", "icf_per_60", "iff_per_60", "iscf_per_60",
            "ihdcf_per_60", "rush_attempts_per_60", "rebounds_created_per_60",
            "pim_per_60", "total_penalties_per_60", "penalties_drawn_per_60",
            "giveaways_per_60", "takeaways_per_60", "hits_per_60",
            "hits_taken_per_60", "shots_blocked_per_60",
            "faceoffs_won_per_60", "faceoffs_lost_per_60",
        ]
        for col in per_60_cols:
            assert col in col_names, f"Missing column: {col}"

    def test_has_non_rate_columns(self):
        col_names = {c.name for c in GameIndividualStats.__table__.columns}
        assert "toi" in col_names
        assert "ipp" in col_names
        assert "sh_pct" in col_names

    def test_no_raw_count_columns(self):
        """Ensure old count column names aren't present."""
        col_names = {c.name for c in GameIndividualStats.__table__.columns}
        # These should NOT exist — they've been replaced by _per_60 versions
        assert "goals" not in col_names
        assert "shots" not in col_names
        assert "hits" not in col_names

    def test_per_60_columns_are_float(self):
        for col in GameIndividualStats.__table__.columns:
            if col.name.endswith("_per_60"):
                assert str(col.type) == "FLOAT", (
                    f"{col.name} should be FLOAT, got {col.type}"
                )

    def test_has_game_context(self):
        col_names = {c.name for c in GameIndividualStats.__table__.columns}
        assert "team_abbrev" in col_names
        assert "opponent_abbrev" in col_names
        assert "is_home" in col_names

    def test_unique_constraint(self):
        constraints = GameIndividualStats.__table__.constraints
        uq = [c for c in constraints if hasattr(c, "columns") and len(c.columns) == 3]
        assert len(uq) == 1
        col_names = {c.name for c in uq[0].columns}
        assert col_names == {"nhl_id", "game_date", "situation"}


class TestGameOnIceStats:
    def test_tablename(self):
        assert GameOnIceStats.__tablename__ == "game_on_ice_stats"

    def test_has_per_60_columns(self):
        col_names = {c.name for c in GameOnIceStats.__table__.columns}
        per_60_cols = [
            "cf_per_60", "ca_per_60", "ff_per_60", "fa_per_60",
            "sf_per_60", "sa_per_60", "gf_per_60", "ga_per_60",
            "xgf_per_60", "xga_per_60", "scf_per_60", "sca_per_60",
            "hdcf_per_60", "hdca_per_60", "hdgf_per_60", "hdga_per_60",
            "mdcf_per_60", "mdca_per_60", "mdgf_per_60", "mdga_per_60",
            "ldcf_per_60", "ldca_per_60", "ldgf_per_60", "ldga_per_60",
            "off_zone_starts_per_60", "neu_zone_starts_per_60",
            "def_zone_starts_per_60", "on_the_fly_starts_per_60",
        ]
        for col in per_60_cols:
            assert col in col_names, f"Missing column: {col}"

    def test_has_percentage_columns(self):
        col_names = {c.name for c in GameOnIceStats.__table__.columns}
        pct_cols = [
            "cf_pct", "ff_pct", "sf_pct", "gf_pct", "xgf_pct", "scf_pct",
            "hdcf_pct", "hdgf_pct", "mdcf_pct", "mdgf_pct",
            "ldcf_pct", "ldgf_pct",
            "on_ice_sh_pct", "on_ice_sv_pct", "pdo", "off_zone_start_pct",
        ]
        for col in pct_cols:
            assert col in col_names, f"Missing column: {col}"

    def test_no_raw_count_columns(self):
        col_names = {c.name for c in GameOnIceStats.__table__.columns}
        assert "cf" not in col_names
        assert "gf" not in col_names
        assert "xgf" not in col_names

    def test_per_60_columns_are_float(self):
        for col in GameOnIceStats.__table__.columns:
            if col.name.endswith("_per_60"):
                assert str(col.type) == "FLOAT", (
                    f"{col.name} should be FLOAT, got {col.type}"
                )
