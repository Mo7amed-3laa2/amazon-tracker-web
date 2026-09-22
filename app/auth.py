"""Password hashing and cookie sessions.

No new dependency for hashing: `hashlib.pbkdf2_hmac` is stdlib and C-backed, so
it costs a Pi about as much as bcrypt would without adding a wheel that has to
compile there. Sessions are signed with itsdangerous rather than hand-rolled
HMAC, because getting the timestamp-and-constant-time-compare details right is
exactly the part worth not writing twice.
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets

from fastapi import Cookie, Depends, HTTPException, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import accounts
import database

logger = logging.getLogger(__name__)

SESSION_COOKIE = "session"
SESSION_MAX_AGE = 30 * 24 * 60 * 60  # 30 days
SESSION_SECRET_SETTING = "session_secret"

# OWASP's floor for PBKDF2-SHA256. Logins are rare enough that ~0.3s on a Pi is
# not worth trading away, and it is recorded in the hash so it can be raised
# later without invalidating existing passwords.
PBKDF2_ITERATIONS = 600_000
_HASH_SCHEME = "pbkdf2_sha256"


def _load_secret() -> str:
    """The session signing key, generated and persisted on first start.

    Kept in the settings table when the environment does not supply one, so
    logins survive a restart on a box where nobody wrote a .env. Setting
    SESSION_SECRET explicitly overrides it and invalidates every session.
    """
    from_env = os.getenv("SESSION_SECRET", "").strip()
    if from_env:
        return from_env

    stored = database.get_setting(SESSION_SECRET_SETTING)
    if stored:
        return stored

    generated = secrets.token_urlsafe(48)
    database.set_setting(SESSION_SECRET_SETTING, generated)
    logger.info("[auth] Generated a new session secret and stored it in settings")
    return generated


_serializer: URLSafeTimedSerializer | None = None


def init_auth():
    """Resolve the signing key once, after the database exists."""
    global _serializer
    _serializer = URLSafeTimedSerializer(_load_secret(), salt="amazon-tracker-session")


def _get_serializer() -> URLSafeTimedSerializer:
    if _serializer is None:
        init_auth()
    return _serializer


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "$".join([
        _HASH_SCHEME,
        str(PBKDF2_ITERATIONS),
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    ])


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = stored.split("$")
        if scheme != _HASH_SCHEME:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        logger.error("[auth] Malformed password hash in database")
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(candidate, expected)


def set_session_cookie(response: Response, user_id: int):
    token = _get_serializer().dumps({"uid": user_id})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        # Not `secure`: the site is served over plain HTTP on a LAN address,
        # where a secure cookie would simply never be sent. Put it behind TLS
        # and this should flip.
        secure=False,
        path="/",
    )


def clear_session_cookie(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")


def _user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        data = _get_serializer().loads(token, max_age=SESSION_MAX_AGE)
    except SignatureExpired:
        logger.debug("[auth] Session expired")
        return None
    except BadSignature:
        logger.warning("[auth] Rejected a session cookie with a bad signature")
        return None

    user_id = data.get("uid") if isinstance(data, dict) else None
    if user_id is None:
        return None

    # The cookie is signed but the account behind it may have been deleted, so
    # the row is what actually authorizes the request.
    return accounts.get_account(user_id)


async def current_user(session: str | None = Cookie(default=None)) -> dict:
    user = _user_from_token(session)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not signed in",
        )
    return user


async def optional_user(session: str | None = Cookie(default=None)) -> dict | None:
    return _user_from_token(session)


async def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user
