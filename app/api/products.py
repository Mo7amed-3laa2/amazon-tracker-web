"""Tracked products: the list, adding, per-product controls and history."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

import auth
import database
import fetch_web
import scan
import tracking
from schemas import NewProduct, ProductPrefs
from scraper import canonical_product_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products", tags=["products"])

# What the row sparkline draws. Matches Sparkline.tsx, which trims to the
# same count.
SPARKLINE_POINTS = 12


def _shape(product: dict) -> dict:
    """Add the fields the table shows but the database does not store.

    Computed here rather than in the frontend so the change figures are the same
    everywhere they appear, and so `available` arrives as a real boolean instead
    of SQLite's 0/1/NULL.
    """
    last_price = product.get("last_price")
    previous_price = product.get("previous_price")

    change = None
    change_percent = None
    if last_price is not None and previous_price:
        change = last_price - previous_price
        change_percent = (change / previous_price) * 100

    return {
        **product,
        "available": product.get("available") in (None, 1),
        "is_amazon": bool(product.get("is_amazon", 1)),
        "require_amazon_merchant": bool(product.get("require_amazon_merchant", 0)),
        "change": change,
        "change_percent": change_percent,
        "trend": _trend(product["id"]),
    }


def _trend(product_id: int) -> list[float]:
    """The last dozen prices, oldest first, for the row's sparkline.

    One small query per row rather than a join, which is the cheaper trade here:
    the list is a handful of items on a local SQLite file, and the alternative is
    the frontend firing a request per row to fill a column.
    """
    rows = database.get_price_history(product_id, SPARKLINE_POINTS)
    return [price for price, _ in reversed(rows)]


def _owned(user_id: int, product_id: int) -> dict:
    """Fetch a product this account tracks, or raise 404.

    Product ids are global and sequential, so this ownership check is what stops
    one account reading or editing another's items by guessing ids. Untracked
    and nonexistent both answer 404 — a 403 would confirm the id is real.
    """
    product = tracking.get_user_product(user_id, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such product")
    return product


@router.get("")
async def list_products(user: dict = Depends(auth.current_user)) -> list[dict]:
    return [_shape(p) for p in tracking.get_user_products(user["user_id"])]


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_product(body: NewProduct, user: dict = Depends(auth.current_user)) -> dict:
    # Stored in /dp/<ASIN> form so the same item added from a share link and
    # from the address bar lands on one row: products.url is UNIQUE, and the
    # tracking-parameter tail on a share URL would otherwise defeat it.
    url = canonical_product_url(body.url)

    existing = database.get_product_by_url(url)
    if existing and tracking.user_tracks_product(user["user_id"], existing[0]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already tracking that product",
        )

    # Blocking network I/O with retries and sleeps — never on the event loop.
    fetched = await asyncio.to_thread(fetch_web.fetch, url, user["language"])
    result = fetched.product

    if result is None:
        # A link Amazon has no page for is the caller's mistake, so it answers
        # 400; a bot check or a dead connection is not, so those stay 502.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST
            if fetched.reason == fetch_web.NOT_FOUND
            else status.HTTP_502_BAD_GATEWAY,
            detail=fetched.detail,
        )

    # Nothing useful can be tracked from a sold-out listing: it has no price of
    # its own, only a stale one or another seller's offer.
    if not result.get("available", True):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{fetched.detail} Add it again once it is back in stock.",
        )

    product_id = database.add_product(
        url,
        result["name"],
        result["price"],
        result.get("image"),
        result.get("merchant_name"),
        result.get("is_amazon", True),
    )
    database.add_user_product(
        user["user_id"],
        product_id,
        price_min=body.price_min,
        price_max=body.price_max,
        require_amazon_merchant=body.require_amazon_merchant,
    )
    logger.info(f"[products] Account {user['user_id']} now tracks {product_id} ({result['name'][:40]})")

    return _shape(_owned(user["user_id"], product_id))


@router.patch("/{product_id}")
async def update_prefs(
    product_id: int,
    body: ProductPrefs,
    user: dict = Depends(auth.current_user),
) -> dict:
    """Update this account's controls for one product.

    Reads the raw submitted fields, not the model attributes: an omitted field
    and an explicit null both arrive as None on the model, and they mean
    different things here — leave alone versus clear the threshold.
    """
    _owned(user["user_id"], product_id)

    supplied = body.model_dump(exclude_unset=True)
    if not supplied:
        return _shape(_owned(user["user_id"], product_id))

    price_min = supplied.get("price_min", tracking.UNSET)
    price_max = supplied.get("price_max", tracking.UNSET)

    if (
        price_min is not tracking.UNSET
        and price_max is not tracking.UNSET
        and price_min is not None
        and price_max is not None
        and price_min > price_max
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Minimum price cannot be above the alert price",
        )

    tracking.set_user_product_prefs(
        user["user_id"],
        product_id,
        price_min=price_min,
        price_max=price_max,
        alert_threshold=supplied.get("alert_threshold", tracking.UNSET),
        require_amazon_merchant=supplied.get("require_amazon_merchant", tracking.UNSET),
    )
    return _shape(_owned(user["user_id"], product_id))


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def untrack(product_id: int, user: dict = Depends(auth.current_user)):
    _owned(user["user_id"], product_id)
    # Drops the product and its history too, but only once no account tracks it.
    database.remove_user_product(user["user_id"], product_id)
    logger.info(f"[products] Account {user['user_id']} untracked {product_id}")


@router.get("/{product_id}/history")
async def price_history(
    product_id: int,
    limit: int = Query(default=90, ge=1, le=1000),
    user: dict = Depends(auth.current_user),
) -> list[dict]:
    """Price points for the chart, oldest first.

    The query returns newest-first because that is what a "last 30" list wants;
    a time axis wants the opposite, so it is reversed here.
    """
    _owned(user["user_id"], product_id)
    rows = database.get_price_history(product_id, limit)
    return [{"price": price, "recorded_at": recorded_at} for price, recorded_at in reversed(rows)]


@router.post("/{product_id}/refresh")
async def refresh(product_id: int, user: dict = Depends(auth.current_user)) -> dict:
    _owned(user["user_id"], product_id)

    updated, detail = await scan.refresh_product(product_id)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        )

    return _shape(_owned(user["user_id"], product_id))
