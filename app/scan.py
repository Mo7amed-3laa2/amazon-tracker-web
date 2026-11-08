"""The price sweep, ported from reference/scheduler_telegram.py.

The loop shape, the availability handling and `_should_notify_user()` are the
bot's, unchanged in substance — they encode rules that were expensive to learn
(see the comments they carry). What is gone is the delivery: `bot.send_message()`
was the last line of the per-user branch and is now `_deliver_alert()`, which
logs and drops. No channel has been chosen yet (CLAUDE.md, "Alert delivery"), so
the decision is still made and recorded, and plugging a channel in later means
replacing one function.

`get_localized_notification()` is deliberately not ported: its EN/AR copy is
built out of Telegram's HTML markup, and the site renders alerts from data
instead. reference/scheduler_telegram.py keeps the wording for when it is wanted.
"""

import asyncio
import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import tracking
from database import set_product_availability, update_price
import fetch_web

logger = logging.getLogger(__name__)

PRICE_CHECK_JOB_ID = "price_check"

# A sweep fetches every tracked product with warm-up and retries, so it can
# run for several minutes; below this the next sweep would collide with the
# one still running (and hammering Amazon is what gets the host IP blocked).
MIN_CHECK_INTERVAL_MINUTES = 10
MAX_CHECK_INTERVAL_MINUTES = 7 * 24 * 60

# Fetching back-to-back is what trips Akamai's bot check on amazon.eg: a
# 42-item sweep that ran in about a minute had a few random items challenged
# on every run. A human-sized gap between products keeps the request rate
# under that threshold.
PRODUCT_DELAY_SECONDS = (4.0, 8.0)
# Items still challenged after their own retries get one more try at the end
# of the sweep, once the burst has had time to cool off, instead of waiting a
# whole interval for the next sweep.
RETRY_COOLDOWN_SECONDS = 60


async def _pause():
    await asyncio.sleep(random.uniform(*PRODUCT_DELAY_SECONDS))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ScanState:
    """What the last sweep did, for the status strip in the UI.

    In memory only: it describes this process's activity, not a fact about the
    tracked products, and a restart legitimately resets it.
    """

    running: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    products_checked: int = 0
    products_changed: int = 0
    alerts_matched: int = 0
    fetch_failures: int = 0
    last_error: str | None = None
    log: list[str] = field(default_factory=list)


state = ScanState()

# One sweep at a time. APScheduler's max_instances=1 covers the scheduled job,
# but the UI can also trigger a sweep by hand, and the two must not interleave:
# both write prices, and two concurrent passes over the same product would each
# compare against a baseline the other has already moved.
_sweep_lock = asyncio.Lock()


def _note(message: str):
    """Record a line for the UI's activity log and for the server log at once."""
    logger.info(f"[scan] {message}")
    state.log.append(f"{_now()} {message}")
    # Bounded because nothing drains it: the process can sweep for months.
    del state.log[:-200]


def _should_notify_user(
    price_max: float | None, require_amazon: bool, new_price: float, is_amazon: bool
) -> bool:
    """Decide whether this user wants an alert for the new price.

    price_max is a ceiling: with one set, only prices at or below it notify.
    With none set, every price change notifies (the original behaviour).

    Ported verbatim apart from the parameter name. The bot called this argument
    `alert_price` while passing it `price_max` — see
    tracking.get_users_for_product_notification().
    """
    if require_amazon and not is_amazon:
        return False

    if price_max is not None and new_price > price_max:
        return False

    return True


def _deliver_alert(
    user_id: int,
    product: dict,
    last_price: float,
    new_price: float,
    price_max: float | None,
):
    """Where a matched alert would go out — currently nowhere.

    The site has no delivery channel yet, by decision rather than oversight, so
    a match is logged and dropped. Everything needed to send one is already in
    this signature: adding email, a webhook or an in-page feed should mean
    writing this function's body and touching nothing above it.
    """
    direction = "dropped" if new_price < last_price else "rose"
    target = "" if price_max is None else f" (at or below target {price_max:,.2f})"
    name = product["name"] or product["url"]
    _note(
        f"ALERT (undelivered) user={user_id} '{name[:40]}' "
        f"{direction} {last_price:,.2f} -> {new_price:,.2f}{target}"
    )


async def check_prices() -> ScanState:
    """One sweep over every tracked product."""
    if _sweep_lock.locked():
        _note("Sweep already running — skipping this trigger")
        return state

    async with _sweep_lock:
        state.running = True
        state.started_at = _now()
        state.finished_at = None
        state.products_checked = 0
        state.products_changed = 0
        state.alerts_matched = 0
        state.fetch_failures = 0
        state.last_error = None

        try:
            await _sweep()
        except Exception as e:
            state.last_error = str(e)
            logger.exception(f"[scan] Sweep failed: {e}")
            _note(f"Sweep failed: {type(e).__name__}: {e}")
        finally:
            state.running = False
            state.finished_at = _now()

        return state


async def _sweep():
    products = tracking.get_all_products()
    if not products:
        _note("No products to check")
        return

    _note(f"Checking {len(products)} product(s)")

    blocked = []
    for i, product in enumerate(products):
        if i:
            await _pause()
        state.products_checked += 1
        if await _check_one(product, retry_later=True) == fetch_web.BLOCKED:
            blocked.append(product)

    if blocked:
        _note(
            f"Retrying {len(blocked)} product(s) that hit a bot check, "
            f"after a {RETRY_COOLDOWN_SECONDS}s cooldown"
        )
        await asyncio.sleep(RETRY_COOLDOWN_SECONDS)
        for i, product in enumerate(blocked):
            if i:
                await _pause()
            await _check_one(product, retry_later=False)

    _note(f"Sweep complete: {state.products_changed} product(s) with price changes")


async def _check_one(product: dict, retry_later: bool) -> str:
    """Check one product and act on its price. Returns the fetch reason.

    With `retry_later`, a bot check is not reported as a failure: the sweep
    collects those and tries them once more after a cooldown.
    """
    url = product["url"]
    product_name = product["name"] or url
    last_price = product["last_price"]
    # Rows added before the `available` column existed read as None, which
    # has to count as "was available" or the first sweep after an upgrade
    # reports every item as newly back in stock.
    was_available = product["available"] in (None, 1)

    # Blocking network I/O — keep it off the event loop so the API stays
    # responsive while a sweep is running.
    fetched = await asyncio.to_thread(fetch_web.fetch, url)
    result = fetched.product

    if result is None:
        if retry_later and fetched.reason == fetch_web.BLOCKED:
            logger.info(f"[scan] '{product_name}' hit a bot check — retrying at the end of the sweep")
            return fetched.reason
        state.fetch_failures += 1
        _note(f"Could not fetch '{product_name}': {fetched.detail}")
        return fetched.reason

    # Any price still shown on a sold-out page is stale or belongs to
    # another seller's offer, so it is neither alerted on nor stored —
    # otherwise the site alerts on a price nobody can pay and then treats
    # that phantom price as the baseline for the next comparison.
    if not result.get("available", True):
        if was_available:
            _note(f"'{product_name}' is now unavailable — holding last known price")
        set_product_availability(url, False)
        return fetched.reason

    if not was_available:
        _note(f"'{product_name}' is available again")
        set_product_availability(url, True)

    tracking.backfill_product_image(url, result.get("image"))

    new_price = result["price"]
    is_amazon = result.get("is_amazon", True)

    if last_price is None:
        update_price(url, new_price)
        _note(f"Initial price set for '{product_name}': {new_price:,.2f}")
        return fetched.reason

    if new_price == last_price:
        return fetched.reason

    price_percent = ((last_price - new_price) / last_price) * 100
    users = tracking.get_users_for_product_notification(product["id"])

    if not users:
        _note(f"No accounts tracking product {product['id']}")
        update_price(url, new_price)
        return fetched.reason

    matched = 0
    for user in users:
        if not _should_notify_user(
            user["price_max"],
            bool(user["require_amazon_merchant"]),
            new_price,
            is_amazon,
        ):
            logger.debug(
                f"[scan] Skipping alert for user {user['user_id']}: "
                f"price={new_price}, price_max={user['price_max']}, "
                f"is_amazon={is_amazon}, require_amazon={user['require_amazon_merchant']}"
            )
            continue

        _deliver_alert(user["user_id"], product, last_price, new_price, user["price_max"])
        matched += 1

    # Written only after the alert decisions, so a delivery channel added
    # later still sees the old baseline while it runs.
    update_price(url, new_price)
    state.products_changed += 1
    state.alerts_matched += matched
    _note(
        f"Price changed for '{product_name}': {last_price:,.2f} -> {new_price:,.2f} "
        f"({price_percent:+.1f}%) [{matched} alert(s) matched]"
    )

    return fetched.reason


async def refresh_product(product_id: int) -> tuple[dict | None, str]:
    """Re-scrape one product on demand, for the UI's refresh button.

    Shares the sweep's rules on purpose — an unavailable page must not overwrite
    the baseline here either — but raises no alerts: this is someone asking what
    the price is right now, not a scheduled comparison.
    """
    product = tracking.get_product(product_id)
    if product is None:
        return None, "That product is not being tracked."

    fetched = await asyncio.to_thread(fetch_web.fetch, product["url"])
    if fetched.product is None:
        return None, fetched.detail

    if not fetched.product.get("available", True):
        set_product_availability(product["url"], False)
        return tracking.get_product(product_id), fetched.detail

    tracking.backfill_product_image(product["url"], fetched.product.get("image"))
    update_price(product["url"], fetched.product["price"])
    return tracking.get_product(product_id), ""


_scheduler: AsyncIOScheduler | None = None


def start_scheduler(interval_minutes: int) -> AsyncIOScheduler:
    global _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        check_prices,
        trigger="interval",
        minutes=interval_minutes,
        id=PRICE_CHECK_JOB_ID,
        name="Amazon Price Checker",
        coalesce=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info(f"[scan] Scheduler started - checking every {interval_minutes} minute(s).")
    return _scheduler


def shutdown_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[scan] Scheduler stopped")


def reschedule_check(interval_minutes: int) -> bool:
    """Apply a new check interval to the running scheduler.

    Returns False when there is no live job to retime (the setting is still
    stored, so it takes effect on the next start).
    """
    if _scheduler is None or _scheduler.get_job(PRICE_CHECK_JOB_ID) is None:
        logger.warning("[scan] No running price_check job to reschedule")
        return False

    _scheduler.reschedule_job(PRICE_CHECK_JOB_ID, trigger="interval", minutes=interval_minutes)
    logger.info(f"[scan] Rescheduled - now checking every {interval_minutes} minute(s).")
    return True


def next_run_at() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job(PRICE_CHECK_JOB_ID)
    if job is None or job.next_run_time is None:
        return None
    return job.next_run_time.isoformat(timespec="seconds")


def status() -> dict:
    return {**asdict(state), "next_run_at": next_run_at()}
