import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

# Database configuration
DATABASE_URL = os.getenv(
    "HOCKEY_DATABASE_URL",
    "postgresql://simonh@localhost:5432/hockey"
)

# Legacy database (Natural Stat Trick) - for migration reference
NST_DATABASE_URL = os.getenv(
    "NST_DATABASE_URL",
    "postgresql://simonh@localhost:5432/naturalstattrick"
)

# ---------------------------------------------------------------------------
# Weekly optimizer
# ---------------------------------------------------------------------------

# NHL IDs the optimizer may never drop, whatever the numbers say. This is the
# hard override on top of hold value: a protected player fails `DropFloor`
# regardless of aggression. Edit by hand, or set PROTECTED_NHL_IDS in .env as
# a comma-separated list.
PROTECTED_NHL_IDS: set[int] = {
    int(x) for x in os.getenv("PROTECTED_NHL_IDS", "").replace(" ", "").split(",") if x
}

# Fallback league configuration, used only when the Yahoo league settings
# endpoint is unreachable (it is, all off-season). Every use is logged so a
# defaulted value never passes silently for a real one.
LEAGUE_SETTINGS_FALLBACK = {
    "n_teams": 16,
    "adds_per_week": 4,
    "waiver_days": 2,
    "roster_size": 19,
}
