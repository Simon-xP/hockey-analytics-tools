from src.core.db import get_session
from src.core.models import Team, Player
from src.core.resolver.normalize import normalize_name
from src.ingest.nhl_api.client import get_all_teams, get_all_players


def seed_teams() -> int:
    """
    Seed teams table from NHL API.

    Returns number of teams inserted.
    """
    teams_data = get_all_teams()
    count = 0

    with get_session() as session:
        for team_data in teams_data:
            team_id = team_data["team_id"]

            if not team_id:
                print(f"Warning: No team ID for {team_data['abbrev']}, skipping")
                continue

            # Check if team already exists
            existing = session.query(Team).filter(Team.team_id == team_id).first()
            if existing:
                continue

            team = Team(
                team_id=team_id,
                abbrev=team_data["abbrev"],
                full_name=team_data["full_name"],
                short_name=team_data["short_name"],
            )
            session.add(team)
            count += 1
            print(f"Added team: {team_data['abbrev']} ({team_data['full_name']})")

    return count


def seed_players() -> int:
    """
    Seed players table from NHL API.

    Returns number of players inserted.
    """
    players_data = get_all_players()
    count = 0

    with get_session() as session:
        # Build team abbrev -> team_id lookup
        teams = {t.abbrev: t.team_id for t in session.query(Team).all()}

        for player_data in players_data:
            nhl_id = player_data["nhl_id"]

            # Check if player already exists
            existing = session.query(Player).filter(Player.nhl_id == nhl_id).first()
            if existing:
                continue

            team_id = teams.get(player_data["team_abbrev"])

            player = Player(
                nhl_id=nhl_id,
                full_name=player_data["full_name"],
                normalized_name=normalize_name(player_data["full_name"]),
                team_id=team_id,
                position=player_data["position"],
            )
            session.add(player)
            count += 1

        print(f"Added {count} players")

    return count


def seed_all() -> dict:
    """Seed both teams and players."""
    print("Seeding teams...")
    teams_count = seed_teams()

    print("\nSeeding players...")
    players_count = seed_players()

    return {"teams": teams_count, "players": players_count}


if __name__ == "__main__":
    result = seed_all()
    print(f"\nDone! Added {result['teams']} teams and {result['players']} players.")
