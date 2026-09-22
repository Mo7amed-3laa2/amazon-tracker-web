"""The web app: JSON API, the scan loop, and the built frontend.

Run it from this directory so the ported modules keep importing each other by
plain name, exactly as they do in the bot:

    cd app && uvicorn main:app --host 0.0.0.0 --port 8000
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

import accounts  # noqa: E402  — after load_dotenv, so DB_PATH is set when
import auth  # noqa: E402       database.py reads it at import time.
import database  # noqa: E402
import scan  # noqa: E402
from api import router as api_router  # noqa: E402

# web/dist, relative to this file. Absent during development, when Vite serves
# the frontend itself on DEV_ORIGIN.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"


def _check_interval() -> int:
    try:
        env_default = int(os.getenv("CHECK_INTERVAL_MINUTES", "60"))
    except ValueError:
        logger.warning("[main] CHECK_INTERVAL_MINUTES is not a number — using 60")
        env_default = 60

    interval = database.get_check_interval(env_default)
    if interval < scan.MIN_CHECK_INTERVAL_MINUTES:
        # A sweep takes minutes; anything shorter overlaps itself and gets the
        # host IP blocked. Clamped rather than rejected so a bad setting cannot
        # stop the site from starting.
        logger.warning(
            f"[main] Check interval {interval}m is below the {scan.MIN_CHECK_INTERVAL_MINUTES}m "
            f"floor — using the floor"
        )
        return scan.MIN_CHECK_INTERVAL_MINUTES
    return interval


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init_db()
    accounts.init_accounts_db()
    auth.init_auth()

    interval = _check_interval()
    scan.start_scheduler(interval)
    logger.info("[main] Ready")

    yield

    scan.shutdown_scheduler()


app = FastAPI(title="Amazon Price Tracker", lifespan=lifespan)

# Only needed while Vite serves the frontend from its own port. Once web/dist is
# built and served below, the frontend is same-origin and this does nothing.
dev_origin = os.getenv("DEV_ORIGIN", "http://localhost:5173").strip()
if dev_origin:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[dev_origin],
        # The session is a cookie, so the dev server's fetches have to be
        # allowed to carry credentials — which is also why the origin is an
        # explicit list and not "*".
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(api_router)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


if FRONTEND_DIR.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIR / "assets"),
        name="assets",
    )

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        """Serve the built frontend, falling back to index.html.

        The router is client-side, so a deep link like /products/12 has to be
        answered with the app shell rather than a 404. Real files are still
        served when they exist, so favicons and the like work.
        """
        candidate = FRONTEND_DIR / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIR / "index.html")
else:
    logger.info(
        f"[main] No built frontend at {FRONTEND_DIR} — serving the API only. "
        f"Run `npm run dev` in web/ during development, or `npm run build` for deployment."
    )
