"""Tweet classifier and entity extractor for NHL news.

Classifies tweets into actionable categories and extracts structured
entities (player names, teams, injury types) for display.
"""

import re

# Non-player name prefixes to filter out
COACH_TITLES = {"head coach", "coach", "assistant coach", "gm", "general manager"}

# Known coaches/non-players to filter from entity extraction.
# Includes current (2025-26) NHL head coaches plus recent former HCs.
KNOWN_NON_PLAYERS = {
    # Current 2025-26 head coaches
    "rod brind'amour", "bruce cassidy", "jon cooper", "craig berube",
    "sheldon keefe", "peter laviolette", "rick tocchet", "lindy ruff",
    "paul maurice", "todd mclellan", "patrick roy", "spencer carbery",
    "andre tourigny", "ryan warsofsky", "derek lalonde", "jared bednar",
    "peter deboer", "kris knoblauch", "greg cronin", "ryan huska",
    "travis green", "dean evason", "john hynes",
    "scott arniel", "jay woodcroft", "lane lambert", "jim montgomery",
    "dave hakstol", "mike sullivan",
    # Recent former coaches
    "joel quenneville", "andrew brunette", "rick bowness", "dj smith",
    "martin st. louis",
    # Common short forms used in tweets ("HC Keefe", etc.)
    "keefe", "berube", "cooper", "brind'amour", "laviolette", "tocchet",
    "ruff", "maurice", "bednar", "arniel", "sullivan", "deboer",
    "knoblauch", "cronin", "huska", "carbery", "tourigny", "warsofsky",
    "lalonde", "montgomery", "mclellan", "hakstol",
}

# Map tweet source handles to team abbreviations
SOURCE_HANDLE_TO_ABBREV = {
    "avalanche": "COL", "penguins": "PIT", "nhlbruins": "BOS",
    "lakings": "LAK", "dallasstars": "DAL", "stloulsblues": "STL",
    "nhlflyers": "PHI", "tbllightning": "TBL", "faboredwings": "DET",
    "nyjets": "WPG", "canucks": "VAN", "seattlekrakenpr": "SEA",
    "nhlcanes": "CAR", "maplelefs": "TOR", "senators": "OTT",
    "nhlducks": "ANA", "buffalosabres": "BUF", "nhlflames": "CGY",
    "floridapanthers": "FLA", "mnwild": "MIN", "canadiensmtl": "MTL",
    "nashvillepreds": "NSH", "njdevils": "NJD", "nyislanders": "NYI",
    "nyrangers": "NYR", "sjsharknews": "SJS", "vegasgoldenknights": "VGK",
    "capitals": "WSH", "utahmammoth": "UTA", "bluejacketsnhl": "CBJ",
    "blackhawks": "CHI", "penguinspr": "PIT", "seattlekraken": "SEA",
}

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

    # Goalie starts take priority — "start in net" is unambiguous even if
    # the same tweet mentions an injured teammate in parens.
    if re.search(
        r'(start[s]?\s+in\s+net|gets\s+the\s+(net|start)|between\s+the\s+pipes|'
        r'will\s+(go\s+in\s+goal|start\s+in\s+(net|goal))|in\s+goal\s+(tonight|today))',
        lower,
    ):
        return "GOALIE"

    # "Not returning to lineup" mid-game = injury (left game hurt)
    if re.search(
        r'(not\s+returning|will\s+not\s+return|won.?t\s+return)',
        lower,
    ):
        return "INJURY"

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
        r'(will\s+return|is\s+back|back\s+in\s+(the\s+)?(lineup|tonight|today)|'
        r'cleared\s+to|activated|off\s+ir|off\s+injured|'
        r'returning\s+to|return\s+tonight|return\s+today|'
        r'confirms\s+\w+\s+back)',
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
    # Extract player names — multiple patterns
    # Standard: "First Last" (e.g. "Roman Josi")
    raw_names = re.findall(
        r'(?:^|[\s,\-])([A-Z][a-z]+(?:\s(?:de\s|van\s|von\s)?[A-Z][a-z\'\-]+)+)',
        text,
    )
    # Initials: "TJ Hughes", "PK Subban"
    initial_names = re.findall(
        r'(?:^|[\s,])([A-Z]{1,2}\.?\s[A-Z][a-z\'\-]+)',
        text,
    )
    raw_names.extend(initial_names)
    # Single last name with context: "Gudas (lower-body)"
    parens_names = re.findall(
        r'(?:^|[\s,])([A-Z][a-z\'\-]{2,})(?:\s*\()', text
    )
    raw_names.extend(parens_names)
    # Single name before action keywords: 'said Gudas "could play"', "Gudas is out"
    action_names = re.findall(
        r'(?:said\s+)([A-Z][a-z\'\-]{2,})',
        text,
    )
    raw_names.extend(action_names)

    # Filter out coach names and non-players
    players = []
    for name in raw_names:
        name = name.strip()
        # Check against known non-players
        if name.lower() in KNOWN_NON_PLAYERS:
            continue
        # Check if preceded by a coach title
        idx = text.find(name)
        if idx > 0:
            before = text[:idx].lower().strip()
            if any(before.endswith(t) for t in COACH_TITLES):
                continue
        # Filter out obviously non-player strings
        if any(w in name.lower() for w in ["coach", "manager", "valley", "michigan"]):
            continue
        # Filter out common words that look like names
        if name.lower() in {"head coach", "game time", "no morning"}:
            continue
        # Deduplicate: skip if a longer version of this name is already in the list
        if any(name in existing and name != existing for existing in players):
            continue
        if name not in players:
            players.append(name)

    # Extract injury type — and identify the player it attaches to
    injury_type = None
    injured_player = None
    inj_match = re.search(
        r'([A-Z][\w\'\-\.]+(?:\s[A-Z][\w\'\-\.]+)+)\s*\(([\w\-\s]*body[\w\-\s]*)\)',
        text,
        re.I,
    )
    if inj_match:
        injured_player = inj_match.group(1).strip()
        injury_type = inj_match.group(2).strip()
        # Promote the injured player to the front of the players list
        if injured_player in players:
            players.remove(injured_player)
        players.insert(0, injured_player)
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
