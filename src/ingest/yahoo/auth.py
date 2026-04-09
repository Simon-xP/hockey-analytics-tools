"""Yahoo Fantasy OAuth 2.0 authentication.

Handles the OAuth flow:
1. Generate authorization URL → user approves in browser
2. Exchange auth code for access/refresh tokens
3. Persist tokens to disk for reuse
4. Auto-refresh expired tokens

Token file is stored at config/yahoo_token.json (gitignored).
"""

import json
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).parents[3]
ENV_PATH = PROJECT_ROOT / ".env"
TOKEN_PATH = PROJECT_ROOT / "config" / "yahoo_token.json"

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

# Read-only scope for fantasy sports (write requires separate approval from Yahoo)
SCOPE = "fspt-r"


def _load_credentials() -> tuple[str, str]:
    env = dotenv_values(ENV_PATH)
    client_id = env.get("YAHOO_CLIENT_ID", "")
    client_secret = env.get("YAHOO_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise ValueError("YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET must be set in .env")
    return client_id, client_secret


def get_auth_url() -> str:
    """Generate the Yahoo OAuth authorization URL.

    Redirects to localhost:8000 callback which captures the code automatically.
    """
    client_id, _ = _load_credentials()
    params = {
        "client_id": client_id,
        "redirect_uri": "https://localhost:8000/api/yahoo/callback",
        "response_type": "code",
        "scope": SCOPE,
    }
    req = requests.Request("GET", AUTH_URL, params=params)
    return req.prepare().url


def exchange_code(auth_code: str) -> dict:
    """Exchange an authorization code for access and refresh tokens."""
    client_id, client_secret = _load_credentials()

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://localhost:8000/api/yahoo/callback",
        },
        auth=(client_id, client_secret),
    )
    resp.raise_for_status()
    token_data = resp.json()

    # Add expiry timestamp for easy checking
    token_data["expires_at"] = time.time() + token_data.get("expires_in", 3600)

    _save_token(token_data)
    return token_data


def refresh_token() -> dict:
    """Refresh an expired access token using the stored refresh token."""
    token = _load_token()
    if not token or "refresh_token" not in token:
        raise ValueError("No refresh token available. Re-authenticate.")

    client_id, client_secret = _load_credentials()

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        },
        auth=(client_id, client_secret),
    )
    resp.raise_for_status()
    token_data = resp.json()

    token_data["expires_at"] = time.time() + token_data.get("expires_in", 3600)
    # Preserve refresh token if not returned in refresh response
    if "refresh_token" not in token_data:
        token_data["refresh_token"] = token["refresh_token"]

    _save_token(token_data)
    return token_data


def get_access_token() -> str | None:
    """Get a valid access token, refreshing if needed.

    Returns None if not authenticated.
    """
    token = _load_token()
    if not token:
        return None

    # Check if expired (with 60s buffer)
    if time.time() >= token.get("expires_at", 0) - 60:
        try:
            token = refresh_token()
        except Exception:
            return None

    return token.get("access_token")


def is_authenticated() -> bool:
    """Check if we have a valid (or refreshable) token."""
    return get_access_token() is not None


def _save_token(token_data: dict) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        json.dump(token_data, f, indent=2)


def _load_token() -> dict | None:
    if not TOKEN_PATH.exists():
        return None
    with open(TOKEN_PATH) as f:
        return json.load(f)
