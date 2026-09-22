"""Site-only queries over `user_products`, and dict views of database.py's rows.

Two jobs, both of which exist so `database.py` can stay a byte-identical copy of
the bot's (CLAUDE.md, "Scraper drift"):

1. Queries the bot never needed. `update_user_product_preferences()` skips any
   argument that is None, so it can set a threshold but never clear one — fine
   for a chat command, useless for a form with an empty field in it. And
   `get_user_for_product_notification()` joins `authorized_users`, which the site
   does not populate, so it needs an `accounts` equivalent.
2. Named-field views. database.py returns positional tuples; a JSON API that
   indexed into a 14-tuple would break silently the next time a column is added
   to the SELECT. The field lists below are declared once, next to each other,
   and mirror the column order in database.py's queries.
"""

import logging

import database
from database import get_connection

logger = logging.getLogger(__name__)

# Column order of database.get_user_products(). Keep in step with that SELECT.
USER_PRODUCT_FIELDS = (
    "id", "url", "name", "last_price", "previous_price", "image_url", "added_at",
    "merchant_name", "is_amazon", "alert_threshold", "price_min", "price_max",
    "require_amazon_merchant", "available",
)

# Column order of database.get_product_by_id().
PRODUCT_FIELDS = (
    "id", "url", "name", "last_price", "previous_price", "image_url", "added_at",
    "merchant_name", "is_amazon", "available",
)

# Column order of database.get_all_products(), which the sweep iterates.
ALL_PRODUCT_FIELDS = (
    "id", "url", "name", "last_price", "previous_price", "image_url", "added_at", "available",
)

# Distinguishes "leave this alone" from "set this to NULL". None is a real value
# here — it is how the UI clears a threshold — so it cannot double as "absent".
UNSET = object()


def get_user_products(user_id: int) -> list[dict]:
    return [dict(zip(USER_PRODUCT_FIELDS, row)) for row in database.get_user_products(user_id)]


def get_user_product(user_id: int, product_id: int) -> dict | None:
    """One joined row: the product plus this account's controls for it.

    Filtered from the list rather than given its own SELECT so the join stays
    defined in exactly one place. A tracked list is a handful of rows, so the
    scan costs nothing worth a second query to save.
    """
    for product in get_user_products(user_id):
        if product["id"] == product_id:
            return product
    return None


def get_product(product_id: int) -> dict | None:
    row = database.get_product_by_id(product_id)
    return dict(zip(PRODUCT_FIELDS, row)) if row else None


def get_all_products() -> list[dict]:
    return [dict(zip(ALL_PRODUCT_FIELDS, row)) for row in database.get_all_products()]


def user_tracks_product(user_id: int, product_id: int) -> bool:
    """Whether this account tracks this product.

    Every per-product endpoint checks this before doing anything: product ids are
    global and guessable, so without it any logged-in account could read or edit
    another's tracked items by id.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_products WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        ).fetchone()
    return row is not None


def set_user_product_prefs(
    user_id: int,
    product_id: int,
    price_min=UNSET,
    price_max=UNSET,
    alert_threshold=UNSET,
    require_amazon_merchant=UNSET,
) -> bool:
    """Write whichever preferences were supplied, including clearing them to NULL.

    Returns False when the account does not track the product, so the caller can
    answer 404 rather than silently updating nothing.
    """
    updates = []
    params: list = []

    for column, value in (
        ("price_min", price_min),
        ("price_max", price_max),
        ("alert_threshold", alert_threshold),
    ):
        if value is not UNSET:
            updates.append(f"{column} = ?")
            params.append(value)

    if require_amazon_merchant is not UNSET:
        updates.append("require_amazon_merchant = ?")
        params.append(1 if require_amazon_merchant else 0)

    if not updates:
        return user_tracks_product(user_id, product_id)

    params.extend([user_id, product_id])
    with get_connection() as conn:
        cursor = conn.execute(
            f"UPDATE user_products SET {', '.join(updates)} WHERE user_id = ? AND product_id = ?",
            params,
        )
        return cursor.rowcount > 0


def get_users_for_product_notification(product_id: int) -> list[dict]:
    """Accounts tracking this product, with the preferences the sweep decides on.

    The `accounts` counterpart of database.get_user_for_product_notification().
    Note which column is the alert ceiling: the bot's scheduler read the fourth
    value of that query as `alert_price`, and the fourth value is `price_max`.
    `alert_threshold` is written by nothing and read by nothing — it predates
    price_min/price_max and is dead in both repos.
    """
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT a.user_id, a.language, up.price_min, up.price_max, up.require_amazon_merchant
            FROM accounts a
            JOIN user_products up ON a.user_id = up.user_id
            WHERE up.product_id = ?
        """, (product_id,)).fetchall()
    return [dict(row) for row in rows]


def backfill_product_image(url: str, image_url: str | None) -> bool:
    """Fill in a thumbnail for a product that has none.

    `database.update_price()` writes only prices, so a product whose page was
    first scraped without an image — or which was added before the scraper read
    one — would stay blank in the table for ever. Only ever fills a hole: an
    image already stored is left alone, so a sweep does not rewrite every row on
    every pass just because Amazon rotated a CDN path.
    """
    if not image_url:
        return False

    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE products SET image_url = ? "
            "WHERE url = ? AND (image_url IS NULL OR image_url = '')",
            (image_url, url),
        )
        return cursor.rowcount > 0
