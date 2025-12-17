import os

# Database configuration
DATABASE_URL = os.getenv(
    "HOCKEY_DATABASE_URL",
    "postgresql://postgres@localhost:5432/hockey"
)

# Legacy database (Natural Stat Trick) - for migration reference
NST_DATABASE_URL = os.getenv(
    "NST_DATABASE_URL",
    "postgresql://postgres@localhost:5432/naturalstattrick"
)
