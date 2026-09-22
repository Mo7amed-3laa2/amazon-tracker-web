"""Registration, sign-in and the current account."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status

import accounts
import auth
from schemas import Account, Credentials, LanguageChange, NewCredentials, PasswordChange

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _as_account(row: dict) -> Account:
    return Account(
        user_id=row["user_id"],
        username=row["username"],
        language=row["language"],
        is_admin=bool(row["is_admin"]),
    )


@router.get("/bootstrap")
async def bootstrap_state() -> dict:
    """Whether anyone has registered yet.

    The sign-in screen asks before rendering, so a fresh install shows "create
    your account" instead of a login form nobody can pass.
    """
    return {"needs_first_account": accounts.account_count() == 0}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: NewCredentials,
    response: Response,
    signed_in: dict | None = Depends(auth.optional_user),
) -> Account:
    """Create an account.

    Open only while there are none — the first account is the owner's and is
    made admin. After that it takes an admin, so a site reachable on the LAN
    cannot have accounts added to it by whoever finds the port.
    """
    first_account = accounts.account_count() == 0

    if not first_account and not (signed_in and signed_in.get("is_admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin can add accounts",
        )

    # ~0.3s of PBKDF2 — off the event loop, same reason the scraper runs there.
    password_hash = await asyncio.to_thread(auth.hash_password, body.password)
    user_id = accounts.create_account(body.username, password_hash, is_admin=first_account)

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That username is taken",
        )

    # Signing the first account straight in saves it a redundant trip through
    # the login form; an admin adding someone else keeps their own session.
    if first_account:
        auth.set_session_cookie(response, user_id)

    return _as_account(accounts.get_account(user_id))


@router.post("/login")
async def login(body: Credentials, response: Response) -> Account:
    account = accounts.get_account_by_username(body.username)

    # One message for both "no such user" and "wrong password", so the form
    # cannot be used to find out which usernames exist.
    if account is None or not await asyncio.to_thread(
        auth.verify_password, body.password, account["password_hash"]
    ):
        logger.warning(f"[auth] Failed sign-in for {body.username!r}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong username or password",
        )

    auth.set_session_cookie(response, account["user_id"])
    logger.info(f"[auth] {body.username} signed in")
    return _as_account(account)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    auth.clear_session_cookie(response)


@router.get("/me")
async def me(user: dict = Depends(auth.current_user)) -> Account:
    return _as_account(user)


@router.put("/me/language")
async def set_language(
    body: LanguageChange, user: dict = Depends(auth.current_user)
) -> Account:
    accounts.set_language(user["user_id"], body.language)
    return _as_account(accounts.get_account(user["user_id"]))


@router.put("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordChange, user: dict = Depends(auth.current_user)):
    stored = accounts.get_account_by_username(user["username"])
    if stored is None or not await asyncio.to_thread(
        auth.verify_password, body.current_password, stored["password_hash"]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password is wrong",
        )

    password_hash = await asyncio.to_thread(auth.hash_password, body.new_password)
    accounts.set_password_hash(user["user_id"], password_hash)


@router.get("/accounts")
async def list_accounts(_: dict = Depends(auth.require_admin)) -> list[dict]:
    return accounts.list_accounts()
