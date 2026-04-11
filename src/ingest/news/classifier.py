"""Tweet classifier and entity extractor for NHL news.

Classifies tweets into actionable categories and extracts structured
entities (player names, teams, injury types) for display.
"""

import re

# Non-player name prefixes to filter out
COACH_TITLES = {"head coach", "coach", "assistant coach", "gm", "general manager"}

# Map common hashtags/nicknames to NHL team abbreviations
HASHTAG_TO_ABBREV = {
    # Direct abbreviations
    "nyr": "NYR", "nyi": "NYI", "njd": "NJD", "cbj": "CBJ", "sjs": "SJS",
    "lak": "LAK", "tbl": "TBL", "vgk": "VGK", "wsh": "WSH", "wpg": "WPG",
    # Team names / common hashtags
    "preds": "NSH", "predators": "NSH",
    "ducks": "ANA", "flytogether": "ANA",
    "coyotes": "UTA", "tusksup": "UTA", "utahhc": "UTA",
    "bruins": "BOS", "nhlbruins": "BOS",
    "sabres": "BUF", "letsgobuffalo": "BUF",
    "flames": "CGY", "goflamesgo": "CGY",
    "canes": "CAR", "letsgocanes": "CAR", "takewarning": "CAR",
    "blackhawks": "CHI", "hawks": "CHI",
    "avalanche": "COL", "avs": "COL", "goavsgo": "COL",
    "bluejackets": "CBJ",
    "stars": "DAL", "gostars": "DAL", "texashockey": "DAL",
    "redwings": "DET", "lgrw": "DET",
    "oilers": "EDM", "letsgooilers": "EDM",
    "flapanthers": "FLA", "flpanthers": "FLA", "timetoshine": "FLA",
    "lakings": "LAK", "gokingsgo": "LAK",
    "mnwild": "MIN", "wild": "MIN",
    "habs": "MTL", "gohabsgo": "MTL",
    "devils": "NJD",
    "isles": "NYI", "islanders": "NYI",
    "rangers": "NYR",
    "sens": "OTT", "gosensgo": "OTT",
    "flyers": "PHI", "flyersnation": "PHI",
    "penguins": "PIT", "letsgopens": "PIT",
    "sjsharks": "SJS", "sharks": "SJS",
    "seakraken": "SEA", "kraken": "SEA",
    "stlblues": "STL", "blues": "STL",
    "gobolts": "TBL", "lightning": "TBL",
    "leafsforever": "TOR", "tmltalk": "TOR", "leafs": "TOR",
    "canucks": "VAN", "gocanucksgo": "VAN",
    "vegasgoldenknights": "VGK", "vegasborn": "VGK",
    "caps": "WSH", "allcaps": "WSH", "capitals": "WSH",
    "gojetsgo": "WPG", "jets": "WPG",
}


def classify(text: str) -> str:
    """Classify a tweet into an actionable category.

    Categories:
        INJURY   — injury reports, day-to-day, game-time decisions
        GOALIE   — goalie start confirmations
        PP_CHANGE — power play unit changes
        TRANSACTION — trades, waivers, recalls, signings
        SCRATCH  — player scratched or ruled out
        RETURN   — player returning from injury
        LINEUP   — general line combinations
        OTHER    — everything else
    """
    lower = text.lower()

    if re.search(
        r'(injur|day-to-day|dtd|upper.?body|lower.?body|concuss|surgery|broken|'
        r'fracture|sidelined|miss\w* \d|out \d|out indefin|placed on ir|ltir|'
        r'game.?time|questionable|doubtful|could play|might play)',
        lower,
    ):
        return "INJURY"

    if re.search(
        r'(start[s ]|in net|gets the net|between the pipes|leads?\s+\w+\s+onto|'
        r'will go in goal|confirmed.*start|off first.*start)',
        lower,
    ):
        return "GOALIE"

    if re.search(r'(pp1|pp2|power.?play unit|power.?play.*change)', lower):
        return "PP_CHANGE"

    if re.search(
        r'(traded|waiver|recalled|called up|sent down|assigned|acquired|'
        r'claimed|signed.*contract|signed.*deal|released)',
        lower,
    ):
        return "TRANSACTION"

    if re.search(
        r'(scratch|benched|out of.lineup|not in.lineup|will not play|'
        r'won.t play|unavailable|ruled out)',
        lower,
    ):
        return "SCRATCH"

    if re.search(
        r'(return|back in|cleared to|activated|off ir|off injured|'
        r'back in the lineup)',
        lower,
    ):
        return "RETURN"

    if re.search(
        r'(warmup|line rush|morning skate|practice lines|lines tonight|'
        r'lines today|line combo|projected.*lines|d-pair)',
        lower,
    ):
        return "LINEUP"

    return "OTHER"


def extract_entities(text: str, category: str) -> dict:
    """Extract structured entities from a tweet.

    Returns dict with:
        players: list of player names
        injury_type: str or None (e.g. "upper-body", "day-to-day")
        team_tags: list of team hashtags
        summary: short actionable summary
    """
    # Extract player names (First Last pattern)
    raw_names = re.findall(
        r'(?:^|[\s,\-])([A-Z][a-z]+(?:\s(?:de\s|van\s|von\s)?[A-Z][a-z\'\-]+)+)',
        text,
    )

    # Filter out coach names
    players = []
    for name in raw_names:
        name = name.strip()
        # Check if preceded by a coach title
        idx = text.find(name)
        if idx > 0:
            before = text[:idx].lower().strip()
            if any(before.endswith(t) for t in COACH_TITLES):
                continue
        # Filter out obviously non-player strings
        if any(w in name.lower() for w in ["coach", "manager", "valley", "michigan"]):
            continue
        players.append(name)

    # Extract injury type
    injury_type = None
    inj_match = re.search(r'\(([\w\-\s]*body[\w\-\s]*)\)', text, re.I)
    if inj_match:
        injury_type = inj_match.group(1).strip()
    elif re.search(r'day.to.day', text, re.I):
        injury_type = "day-to-day"
    elif re.search(r'game.?time', text, re.I):
        injury_type = "game-time decision"
    elif re.search(r'concuss', text, re.I):
        injury_type = "concussion"

    # Extract team hashtags and resolve to abbreviations
    raw_tags = re.findall(r'#(\w+)', text)
    team_tags = []
    for tag in raw_tags:
        abbrev = HASHTAG_TO_ABBREV.get(tag.lower())
        if abbrev:
            team_tags.append(abbrev)
        elif tag.upper() in {"ANA", "UTA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL",
                             "CBJ", "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL",
                             "NSH", "NJD", "NYI", "NYR", "OTT", "PHI", "PIT", "SJS",
                             "SEA", "STL", "TBL", "TOR", "VAN", "VGK", "WSH", "WPG"}:
            team_tags.append(tag.upper())

    # Generate summary
    summary = _make_summary(text, category, players, injury_type)

    return {
        "players": players,
        "injury_type": injury_type,
        "team_tags": team_tags,
        "summary": summary,
    }


def _make_summary(text: str, category: str, players: list, injury_type: str | None) -> str:
    """Generate a short actionable summary from the tweet."""
    if not players:
        # Truncate original text
        return text[:100] + ("..." if len(text) > 100 else "")

    name = players[0]

    if category == "INJURY":
        if injury_type:
            return f"{name} — {injury_type}"
        return f"{name} — injury update"

    if category == "GOALIE":
        # Find opponent
        vs_match = re.search(r'(?:vs\.?|against|@)\s*([A-Z]{2,3}|\w+)', text)
        opp = vs_match.group(1) if vs_match else ""
        return f"{name} starts{' vs ' + opp if opp else ''}"

    if category == "SCRATCH":
        return f"{name} — scratched"

    if category == "RETURN":
        return f"{name} — returning to lineup"

    if category == "TRANSACTION":
        if "recalled" in text.lower() or "called up" in text.lower():
            return f"{name} — recalled"
        if "signed" in text.lower():
            return f"{name} — signed"
        if "traded" in text.lower():
            return f"{name} — traded"
        if "waiver" in text.lower():
            return f"{name} — placed on waivers"
        return f"{name} — roster move"

    if category == "PP_CHANGE":
        pp_match = re.search(r'(PP1|PP2)', text, re.I)
        unit = pp_match.group(1).upper() if pp_match else "PP"
        return f"{name} — {unit} change"

    return text[:100] + ("..." if len(text) > 100 else "")


# Category display config
CATEGORY_CONFIG = {
    "INJURY": {"label": "Injury", "color": "#f87171"},
    "GOALIE": {"label": "Goalie", "color": "#60a5fa"},
    "PP_CHANGE": {"label": "PP Change", "color": "#fbbf24"},
    "TRANSACTION": {"label": "Transaction", "color": "#a78bfa"},
    "SCRATCH": {"label": "Scratch", "color": "#f97316"},
    "RETURN": {"label": "Return", "color": "#34d399"},
    "LINEUP": {"label": "Lineup", "color": "#6b7280"},
    "OTHER": {"label": "News", "color": "#6b7280"},
}
