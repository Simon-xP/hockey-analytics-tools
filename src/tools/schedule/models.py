"""Data models for fantasy roster and league settings."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LeagueSettings:
    """Fantasy league roster slot configuration."""

    # Forward slots
    c: int = 2
    lw: int = 2
    rw: int = 2

    # Defense slots
    d: int = 4

    # Goalie slots
    g: int = 2

    # Utility slot (any F or D, not G)
    util: int = 1

    # Bench and IR (not used for slot availability, but tracked)
    bn: int = 4
    ir: int = 2
    ir_plus: int = 2  # IR+ slots

    def active_slots(self) -> dict[str, int]:
        """Return only the slots that count for daily lineups (not BN/IR)."""
        return {
            "C": self.c,
            "LW": self.lw,
            "RW": self.rw,
            "D": self.d,
            "G": self.g,
            "UTIL": self.util,
        }

    def total_active_skater_slots(self) -> int:
        """Total skater slots (excludes G, BN, IR)."""
        return self.c + self.lw + self.rw + self.d + self.util


@dataclass
class RosterPlayer:
    """A player on the fantasy roster with positional eligibility."""

    name: str
    team: str  # Team abbreviation (EDM, TOR, etc.)
    positions: list[str]  # Yahoo positional eligibility: ["C"], ["C", "LW"], etc.
    nhl_id: Optional[int] = None  # Resolved NHL player ID

    def is_forward(self) -> bool:
        """Check if player is eligible for any forward position."""
        return any(pos in ["C", "LW", "RW"] for pos in self.positions)

    def is_defenseman(self) -> bool:
        """Check if player is eligible for D."""
        return "D" in self.positions

    def is_goalie(self) -> bool:
        """Check if player is eligible for G."""
        return "G" in self.positions

    def can_fill_util(self) -> bool:
        """Check if player can fill UTIL slot (any F or D, not G)."""
        return self.is_forward() or self.is_defenseman()


@dataclass
class Roster:
    """Complete fantasy roster."""

    players: list[RosterPlayer] = field(default_factory=list)
    league_settings: LeagueSettings = field(default_factory=LeagueSettings)

    def skaters(self) -> list[RosterPlayer]:
        """Return all non-goalie players."""
        return [p for p in self.players if not p.is_goalie()]

    def goalies(self) -> list[RosterPlayer]:
        """Return all goalies."""
        return [p for p in self.players if p.is_goalie()]

    # not super useful
    def players_by_team(self, team: str) -> list[RosterPlayer]:
        """Return all players on a specific team."""
        return [p for p in self.players if p.team == team]
