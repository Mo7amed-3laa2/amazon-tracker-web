"""Runtime settings. Currently just the scan interval."""

import logging
import os

from fastapi import APIRouter, Depends

import auth
import database
import scan
from schemas import IntervalChange

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


def _env_default() -> int:
    """What CHECK_INTERVAL_MINUTES says, or 60.

    The database setting takes precedence; this is only the fallback for an
    install where nobody has touched the interval yet.
    """
    try:
        return int(os.getenv("CHECK_INTERVAL_MINUTES", "60"))
    except ValueError:
        logger.warning("[settings] CHECK_INTERVAL_MINUTES is not a number — using 60")
        return 60


def _payload() -> dict:
    return {
        "check_interval_minutes": database.get_check_interval(_env_default()),
        "min_interval_minutes": scan.MIN_CHECK_INTERVAL_MINUTES,
        "max_interval_minutes": scan.MAX_CHECK_INTERVAL_MINUTES,
        "next_run_at": scan.next_run_at(),
    }


@router.get("")
async def get_settings(_: dict = Depends(auth.current_user)) -> dict:
    return _payload()


@router.put("")
async def set_interval(
    body: IntervalChange, _: dict = Depends(auth.require_admin)
) -> dict:
    """Store the interval and retime the live job.

    Admin-only because it is shared state: the sweep is one job for the whole
    site, not one per account.
    """
    database.set_check_interval(body.check_interval_minutes)
    rescheduled = scan.reschedule_check(body.check_interval_minutes)
    return {**_payload(), "applied_now": rescheduled}
