"""
Token storage with automatic refresh-token rotation handling.

Xero rotates refresh tokens on every use: each refresh returns a NEW
refresh token and invalidates the old one. We persist the latest tokens
in Postgres so they survive Render restarts/redeploys/cold-starts.

If DATABASE_URL is not set, we fall back to a local JSON file store.
This avoids requiring a database, but tokens will be lost if the instance
restarts and the filesystem is ephemeral (common on PaaS).

The table is namespaced (xero_tokens) so it lives alongside any other
connector tables already in the shared database without colliding.
"""
import os
import time
import json
from pathlib import Path
import psycopg

DATABASE_URL = os.getenv("DATABASE_URL")
TOKEN_STORE_PATH = os.getenv("TOKEN_STORE_PATH", "xero_tokens.json")

# We only ever store ONE row (single Xero org connection).
# id is fixed to 1 so upserts overwrite the same row every time.
_ROW_ID = 1


def _file_path() -> Path:
    return Path(TOKEN_STORE_PATH).expanduser()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def init_db() -> None:
    """Create the xero_tokens table if it does not exist. Safe to call on every boot."""
    if not DATABASE_URL:
        # File store requires no init step.
        return
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS xero_tokens (
                id            INTEGER PRIMARY KEY,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                expires_at    BIGINT NOT NULL,
                tenant_id     TEXT,
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.commit()


def save_tokens(access_token: str, refresh_token: str, expires_in: int,
                tenant_id: str | None = None) -> None:
    """Persist tokens. expires_in is seconds-from-now (Xero gives 1800)."""
    expires_at = int(time.time()) + int(expires_in)
    if not DATABASE_URL:
        path = _file_path()
        existing = load_tokens() or {}
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "tenant_id": tenant_id if tenant_id is not None else existing.get("tenant_id"),
        }
        _atomic_write_json(path, payload)
        return

    with psycopg.connect(DATABASE_URL) as conn:
        if tenant_id is not None:
            conn.execute(
                """
                INSERT INTO xero_tokens (id, access_token, refresh_token, expires_at, tenant_id, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    access_token  = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_at    = EXCLUDED.expires_at,
                    tenant_id     = EXCLUDED.tenant_id,
                    updated_at    = now()
                """,
                (_ROW_ID, access_token, refresh_token, expires_at, tenant_id),
            )
        else:
            # Preserve existing tenant_id when we're only refreshing tokens.
            conn.execute(
                """
                INSERT INTO xero_tokens (id, access_token, refresh_token, expires_at, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    access_token  = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_at    = EXCLUDED.expires_at,
                    updated_at    = now()
                """,
                (_ROW_ID, access_token, refresh_token, expires_at),
            )
        conn.commit()


def load_tokens() -> dict | None:
    """Return the stored token row as a dict, or None if not yet authorized."""
    if not DATABASE_URL:
        path = _file_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if not data.get("access_token") or not data.get("refresh_token") or not data.get("expires_at"):
            return None
        return {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": int(data["expires_at"]),
            "tenant_id": data.get("tenant_id"),
        }
    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            "SELECT access_token, refresh_token, expires_at, tenant_id FROM xero_tokens WHERE id = %s",
            (_ROW_ID,),
        ).fetchone()
    if row is None:
        return None
    return {
        "access_token": row[0],
        "refresh_token": row[1],
        "expires_at": row[2],
        "tenant_id": row[3],
    }
