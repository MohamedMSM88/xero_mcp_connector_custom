"""
Xero API client: OAuth exchange, automatic access-token refresh (with
rotation persistence), and thin helpers over the Accounting API.

Nothing here hard-codes secrets. Client ID/secret come from env vars.
"""
import os
import time
import base64
import httpx

from token_store import load_tokens, save_tokens

CLIENT_ID = os.getenv("XERO_CLIENT_ID")
CLIENT_SECRET = os.getenv("XERO_CLIENT_SECRET")
REDIRECT_URI = os.getenv("XERO_REDIRECT_URI")

# Full read+write scopes for the resources requested.
# NOTE: Xero manual journals are READ-ONLY in the public API — there is a
# .read scope but no general write endpoint. We include the read scope for
# visibility; "posting a journal" is done via bank transactions instead.
DEFAULT_SCOPES = (
    "openid profile email "
    "accounting.settings "
    "accounting.contacts "
    "accounting.invoices "
    "accounting.payments "
    "accounting.banktransactions "
    "accounting.manualjournals "
    "accounting.attachments "
    "accounting.reports.read "
    "offline_access"
)
SCOPES = os.getenv("XERO_SCOPES", DEFAULT_SCOPES)

AUTH_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
API_BASE = "https://api.xero.com/api.xro/2.0"

def missing_env_vars() -> list[str]:
    missing = []
    if not CLIENT_ID:
        missing.append("XERO_CLIENT_ID")
    if not CLIENT_SECRET:
        missing.append("XERO_CLIENT_SECRET")
    if not REDIRECT_URI:
        missing.append("XERO_REDIRECT_URI")
    return missing

def _require_env() -> tuple[str, str, str]:
    missing = missing_env_vars()
    if missing:
        raise RuntimeError(
            "Missing required env var(s): "
            + ", ".join(missing)
            + ". Configure them in your environment (Render env vars) before using /connect."
        )
    return CLIENT_ID, CLIENT_SECRET, REDIRECT_URI


def build_authorize_url(state: str) -> str:
    client_id, _, redirect_uri = _require_env()
    from urllib.parse import urlencode
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _basic_auth_header() -> str:
    client_id, client_secret, _ = _require_env()
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def exchange_code(code: str) -> dict:
    """Exchange the authorization code for tokens, then fetch and store the tenant_id."""
    _, _, redirect_uri = _require_env()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        tok = resp.json()

        # Fetch which Xero org (tenant) this connection is for.
        conn_resp = await client.get(
            CONNECTIONS_URL,
            headers={
                "Authorization": f"Bearer {tok['access_token']}",
                "Content-Type": "application/json",
            },
        )
        conn_resp.raise_for_status()
        connections = conn_resp.json()
        tenant_id = connections[0]["tenantId"] if connections else None

    save_tokens(
        access_token=tok["access_token"],
        refresh_token=tok["refresh_token"],
        expires_in=tok["expires_in"],
        tenant_id=tenant_id,
    )
    return {"tenant_id": tenant_id, "num_connections": len(connections)}


async def _refresh(refresh_token: str) -> dict:
    """Use the refresh token to get a new access token. Persists the rotated tokens."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        tok = resp.json()
    # CRITICAL: store the NEW refresh token — the old one is now dead.
    save_tokens(
        access_token=tok["access_token"],
        refresh_token=tok["refresh_token"],
        expires_in=tok["expires_in"],
    )
    return tok


async def get_valid_access() -> tuple[str, str]:
    """
    Return (access_token, tenant_id), refreshing if the access token is
    expired or about to expire. Raises if not yet authorized.
    """
    tokens = load_tokens()
    if tokens is None:
        raise RuntimeError("Not authorized yet. Visit /connect in a browser first.")

    # Refresh if within 60s of expiry.
    if tokens["expires_at"] - int(time.time()) < 60:
        await _refresh(tokens["refresh_token"])
        tokens = load_tokens()

    return tokens["access_token"], tokens["tenant_id"]


async def api_request(method: str, path: str, *, params: dict | None = None,
                      json_body: dict | None = None) -> dict:
    """Make an authenticated Accounting API request. Auto-refreshes once on 401."""
    access_token, tenant_id = await get_valid_access()

    async def _do(token: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.request(
                method,
                f"{API_BASE}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-tenant-id": tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                params=params,
                json=json_body,
            )

    resp = await _do(access_token)
    if resp.status_code == 401:
        # Token died early; force a refresh and retry once.
        tokens = load_tokens()
        await _refresh(tokens["refresh_token"])
        access_token, tenant_id = await get_valid_access()
        resp = await _do(access_token)

    resp.raise_for_status()
    return resp.json()
