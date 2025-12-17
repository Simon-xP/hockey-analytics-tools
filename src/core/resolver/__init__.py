from typing import Optional
from sqlalchemy.orm import Session

from src.core.models import Player, PlayerAlias
from src.core.resolver.normalize import normalize_name, normalize_name_keep_order
from src.core.resolver.matcher import (
    exact_match_by_id,
    exact_match_by_alias,
    exact_match_by_normalized_name,
    fuzzy_match,
    match_with_disambiguation,
)


def resolve_player(
    session: Session,
    name: str = None,
    nhl_id: int = None,
    team_abbrev: str = None,
    position: str = None,
    create_alias: bool = False,
    alias_source: str = None,
) -> Optional[int]:
    """
    Resolve a player reference to their NHL ID.

    This is the main entry point for player resolution. It tries multiple
    matching strategies in order of reliability.

    Args:
        session: Database session
        name: Player name to search for
        nhl_id: NHL player ID (if known, skips name matching)
        team_abbrev: Team abbreviation for disambiguation (e.g., "CAR", "NYI")
        position: Position for disambiguation (e.g., "C", "LW", "D", "G")
        create_alias: If True, creates an alias record for successful fuzzy matches
        alias_source: Source name for the alias (e.g., "naturalstattrick")

    Returns:
        NHL player ID if found, None otherwise

    Examples:
        # Simple lookup
        nhl_id = resolve_player(session, name="Alex Ovechkin")

        # With team disambiguation (for names like "Sebastian Aho")
        nhl_id = resolve_player(session, name="Sebastian Aho", team_abbrev="CAR")

        # With position disambiguation
        nhl_id = resolve_player(session, name="Some Name", position="D")

        # Auto-create alias for future lookups
        nhl_id = resolve_player(
            session,
            name="A. Ovechkin",
            create_alias=True,
            alias_source="yahoo"
        )
    """
    # If we already have the ID, just validate it exists
    if nhl_id is not None:
        player = exact_match_by_id(session, nhl_id)
        return player.nhl_id if player else None

    if not name:
        return None

    # Try exact matches first (fast path)
    player = exact_match_by_alias(session, name)
    if player:
        return player.nhl_id

    player = exact_match_by_normalized_name(session, name)
    if player:
        return player.nhl_id

    # Fall back to fuzzy matching
    result = fuzzy_match(session, name, team_abbrev, position)
    if result:
        player, score = result

        # Optionally create alias for future exact matches
        if create_alias and alias_source and score >= 85:
            _create_alias(session, player.nhl_id, name, alias_source)

        return player.nhl_id

    return None


def _create_alias(session: Session, nhl_id: int, alias: str, source: str) -> None:
    """Create a new alias record for a player."""
    normalized = normalize_name(alias)

    # Check if this alias already exists
    existing = session.query(PlayerAlias).filter(
        PlayerAlias.nhl_id == nhl_id,
        PlayerAlias.normalized_alias == normalized
    ).first()

    if not existing:
        new_alias = PlayerAlias(
            nhl_id=nhl_id,
            alias=alias,
            normalized_alias=normalized,
            source=source
        )
        session.add(new_alias)


__all__ = [
    "resolve_player",
    "normalize_name",
    "normalize_name_keep_order",
    "exact_match_by_id",
    "exact_match_by_alias",
    "exact_match_by_normalized_name",
    "fuzzy_match",
    "match_with_disambiguation",
]
