from typing import Optional
from rapidfuzz import fuzz, process
from sqlalchemy.orm import Session

from src.core.models import Player, PlayerAlias
from src.core.resolver.normalize import normalize_name


# Minimum similarity score to consider a fuzzy match (0-100)
# 80% catches common variations: Zack/Zachary, Max/Maxwell, Alex/Alexander
FUZZY_THRESHOLD = 80


def exact_match_by_id(session: Session, nhl_id: int) -> Optional[Player]:
    """Find player by NHL ID (most reliable)."""
    return session.query(Player).filter(Player.nhl_id == nhl_id).first()


def exact_match_by_alias(session: Session, name: str) -> Optional[Player]:
    """Find player by exact alias match."""
    normalized = normalize_name(name)

    alias = session.query(PlayerAlias).filter(
        PlayerAlias.normalized_alias == normalized
    ).first()

    if alias:
        return session.query(Player).filter(Player.nhl_id == alias.nhl_id).first()

    return None


def exact_match_by_normalized_name(session: Session, name: str) -> Optional[Player]:
    """Find player by normalized name."""
    normalized = normalize_name(name)
    return session.query(Player).filter(Player.normalized_name == normalized).first()


def fuzzy_match(
    session: Session,
    name: str,
    team_abbrev: Optional[str] = None,
    position: Optional[str] = None,
    threshold: int = FUZZY_THRESHOLD
) -> Optional[tuple[Player, int]]:
    """
    Find player by fuzzy name matching.

    Returns tuple of (Player, score) or None if no match above threshold.
    If team_abbrev is provided, only matches players on that team.
    If position is provided, only matches players with that position.
    """
    normalized = normalize_name(name)

    # Build query with optional filters
    query = session.query(Player)
    if team_abbrev:
        from src.core.models import Team
        team = session.query(Team).filter(Team.abbrev == team_abbrev.upper()).first()
        if team:
            query = query.filter(Player.team_id == team.team_id)
    if position:
        query = query.filter(Player.position == position.upper())

    players = query.all()

    if not players:
        return None

    # Build choices dict: normalized_name -> player
    choices = {p.normalized_name: p for p in players if p.normalized_name}

    if not choices:
        return None

    # Find best match
    result = process.extractOne(
        normalized,
        choices.keys(),
        scorer=fuzz.ratio,
        score_cutoff=threshold
    )

    if result:
        matched_name, score, _ = result
        return (choices[matched_name], score)

    return None


def match_with_disambiguation(
    session: Session,
    name: str,
    team_abbrev: Optional[str] = None,
    position: Optional[str] = None
) -> list[tuple[Player, int, str]]:
    """
    Find all potential player matches with confidence levels.

    Returns list of (Player, score, confidence_level) tuples.
    confidence_level: "exact", "high", "medium", "low"

    Useful for flagging ambiguous matches for review.
    """
    matches = []

    # Try exact alias match
    player = exact_match_by_alias(session, name)
    if player:
        matches.append((player, 100, "exact"))
        return matches

    # Try exact normalized name match
    player = exact_match_by_normalized_name(session, name)
    if player:
        matches.append((player, 100, "exact"))
        return matches

    # Try fuzzy match with disambiguation filters
    result = fuzzy_match(session, name, team_abbrev, position, threshold=70)
    if result:
        player, score = result
        if score >= 95:
            confidence = "high"
        elif score >= 85:
            confidence = "medium"
        else:
            confidence = "low"
        matches.append((player, score, confidence))

    return matches
