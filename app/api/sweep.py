"""The sweep: its status, and running one by hand."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

import auth
import scan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scan", tags=["scan"])

# Strong references to in-flight manual sweeps. Without one the event loop
# may garbage-collect the task mid-sweep.
_background: set[asyncio.Task] = set()


@router.get("/status")
async def scan_status(_: dict = Depends(auth.current_user)) -> dict:
    return scan.status()


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_now(_: dict = Depends(auth.current_user)) -> dict:
    """Start a sweep and return immediately.

    A sweep can run for minutes, which is far longer than a request should be
    held open, so it goes on the event loop as its own task and the UI follows
    it through /scan/status. The task is deliberately not awaited; scan.state is
    where its outcome lands.
    """
    if scan.state.running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A scan is already running",
        )

    task = asyncio.create_task(scan.check_prices())
    _background.add(task)
    task.add_done_callback(_background.discard)

    logger.info("[scan] Manual sweep requested")
    return {"started": True}

