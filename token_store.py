"""
Token storage with automatic refresh-token rotation handling.

Xero rotates refresh tokens on every use: each refresh returns a NEW
refresh token and invalidates the old one. We persist the latest tokens
in Postgres so they survive Render restarts/redeploys/cold-starts.

The table is namespaced (xero_tokens) so it lives alongside any other
connector tables already in the shared database without colliding.
"""
import os
import time
import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]

# We only ever store ONE row (single Xero org connection).
# id is fixed to 1 so upserts overwrite the same row every time.
_ROW_ID = 1


def init_db() -> None:
    """Create the xero_tokens table if it does not exist. Safe to call on every boot."""
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
