import re
from unidecode import unidecode


def normalize_name(name: str) -> str:
    """
    Normalize a player name for consistent matching.

    Handles:
    - Accented characters (Höglander -> hoglander)
    - Punctuation (O'Reilly -> oreilly)
    - Case differences
    - Name order (sorts words alphabetically for order-independent matching)

    Examples:
        "Alex Ovechkin" -> "alex ovechkin" (a < o, so alex stays first)
        "Nikolaj Ehlers" -> "ehlers nikolaj" (e < n, so ehlers moves first)
        "O'Reilly, Ryan" -> "oreilly ryan" (o < r, so oreilly stays first)
    """
    if not name:
        return ""

    # Handle accented characters
    name = unidecode(name)

    # Lowercase
    name = name.lower()

    # Remove punctuation (apostrophes, commas, hyphens, periods)
    name = re.sub(r"[^\w\s]", "", name)

    # Normalize whitespace
    name = " ".join(name.split())

    # Sort words alphabetically for order-independent matching
    # "Ryan O'Reilly" and "O'Reilly, Ryan" both become "oreilly ryan"
    words = sorted(name.split())

    return " ".join(words)


def normalize_name_keep_order(name: str) -> str:
    """
    Normalize name but preserve word order.

    Useful for display or when order matters.
    """
    if not name:
        return ""

    name = unidecode(name)
    name = name.lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = " ".join(name.split())

    return name
