"""Real accounts for the site.

This lives outside `database.py` on purpose. That file is a byte-identical copy
of the bot's and is kept diffable against it (CLAUDE.md, "Scraper drift"), so
site-only tables and queries go here instead of being retrofitted into it.

It also resolves "The user_id problem". In the bot, `user_id` was a Telegram
chat id doing three jobs at once: account identity, authorization record and
delivery address. Here it is only the first two, and `accounts.user_id` is the
single source of them. `user_products.user_id` is a plain foreign key into this
table, and nothing about the value says how to reach anyone — there is no
delivery address in the system at all yet.

`authorized_users` is left alone: unused by the site, still in the schema so the
copy of database.py stays identical to the bot's.
"""

import logging
import sqlite3

from database import get_connection

logger = logging.getLogger(__name__)


def init_accounts_db():
    with get_connection() as conn:
        # COLLATE NOCASE on username so "Mohamed" and "mohamed" cannot both be
        # registered and then race each other at the login form.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'en',
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("[accounts] Accounts table ready")


def account_count() -> int:
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]


def create_account(username: str, password_hash: str, is_admin: bool = False) -> int | None:
    """Insert an account, or return None if the username is taken.

    The UNIQUE constraint is the check: testing first and inserting after would
    leave a window where two registrations both pass the test.
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO accounts (username, password_hash, is_admin) VALUES (?, ?, ?)",
                (username, password_hash, 1 if is_admin else 0),
            )
            user_id = cursor.lastrowid
        logger.info(f"[accounts] Created account {user_id} ({username}) admin={is_admin}")
        return user_id
    except sqlite3.IntegrityError:
        logger.warning(f"[accounts] Username already taken: {username}")
        return None


def get_account_by_username(username: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT user_id, username, password_hash, language, is_admin FROM accounts WHERE username = ?",
            (username,),
        ).fetchone()
    return dict(row) if row else None


def get_account(user_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT user_id, username, language, is_admin, created_at FROM accounts WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def set_password_hash(user_id: int, password_hash: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE accounts SET password_hash = ? WHERE user_id = ?",
            (password_hash, user_id),
        )
    logger.info(f"[accounts] Password changed for account {user_id}")


def get_language(user_id: int) -> str:
    """Language for this account, defaulting to English.

    Mirrors database.get_user_language(), which reads authorized_users and so
    would answer "en" for every account the site creates.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT language FROM accounts WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else "en"


def set_language(user_id: int, language: str):
    with get_connection() as conn:
        conn.execute("UPDATE accounts SET language = ? WHERE user_id = ?", (language, user_id))


def list_accounts() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id, username, language, is_admin, created_at FROM accounts ORDER BY created_at"
        ).fetchall()
    return [dict(row) for row in rows]
