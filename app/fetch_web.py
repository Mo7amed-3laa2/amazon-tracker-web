"""Fetching Amazon pages for the site, with a reason when it fails.

`scraper.py` is a byte-identical copy of the bot's file and must not be edited
(CLAUDE.md, "Scraper drift"), but the site needs two things the bot never did:

1. **A transport amazon.eg does not challenge.** The bot fetches through
   cloudscraper. On amazon.eg that draws Akamai's Bot Manager challenge — a
   4.4 KB `bm-verify` interstitial served with HTTP 200 — for roughly one
   request in three, and since all three of the scraper's attempts share the
   same client, a whole fetch fails often enough to look broken. A plain
   `requests` session with an ordinary Chrome header set is not challenged.
2. **A reason it can put in front of the user.** `scraper.fetch_product`
   returns `None` for every kind of failure, so the UI could only ever say
   "could not fetch". Worse, Amazon's ordinary "Page Not Found" body carries
   the string "To discuss automated access to Amazon data", which is one of
   the scraper's `BOT_BLOCK_MARKERS` — so a link to an ASIN that simply is not
   in the amazon.eg catalogue is misread as an IP block, retried three times
   with sleeps, and reported as blocking. Checking the status code *before*
   the markers is what tells those two apart.

Only the transport is replaced. Every extractor — `_is_list_price`, the
`a-price-whole` reading, availability, merchant — stays in `scraper.py`, so a
fix made there for the bot still reaches the site with nothing to port.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass

import requests

import scraper

logger = logging.getLogger(__name__)

# Chrome's own header set, in Chrome's order. Akamai fingerprints the shape of
# the request as much as its contents, so an incomplete set is worse than none.
CHROME_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-EG,en;q=0.9,ar;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Connection": "keep-alive",
}

TIMEOUT_SECONDS = 25

# Reasons, in the vocabulary the API turns into a message.
OK = "ok"
NOT_FOUND = "not_found"
BLOCKED = "blocked"
UNAVAILABLE = "unavailable"
NO_PRICE = "no_price"
TRANSPORT_ERROR = "transport_error"

MESSAGES = {
    NOT_FOUND: (
        "Amazon has no page for that link. Check the ASIN — a product that "
        "exists on amazon.com is often not listed on amazon.eg."
    ),
    BLOCKED: (
        "Amazon served a bot check instead of the page. This usually clears by "
        "itself — try again in a minute."
    ),
    UNAVAILABLE: (
        "That product is unavailable, so Amazon is not showing a price for it."
    ),
    NO_PRICE: (
        "Found the page but no price on it. The listing may have no offer right "
        "now, or Amazon changed its markup — run "
        "`python debug_price.py <url>` to see the candidates."
    ),
    TRANSPORT_ERROR: "Could not reach Amazon. Check this machine's connection.",
}


@dataclass
class FetchResult:
    """What came back, and — when nothing did — something to show the user."""

    product: dict | None
    reason: str

    @property
    def ok(self) -> bool:
        return self.product is not None and self.reason == OK

    @property
    def detail(self) -> str:
        return MESSAGES.get(self.reason, "Could not fetch that product right now.")


# `fetch_product` resolves `fetch_html` as a module global, which makes it the
# one seam that lets the site swap the transport and keep the bot's parsing.
# Held under a lock because the sweep scrapes from `asyncio.to_thread` workers.
_SWAP_LOCK = threading.Lock()


@contextmanager
def _parsing_with(html: str):
    with _SWAP_LOCK:
        original = scraper.fetch_html
        scraper.fetch_html = lambda _url: html
        try:
            yield
        finally:
            scraper.fetch_html = original


def _get(url: str) -> tuple[int | None, str]:
    """One direct GET. Redirects are followed, so short links resolve here too."""
    session = requests.Session()
    session.headers.update(CHROME_HEADERS)
    try:
        response = session.get(url, timeout=TIMEOUT_SECONDS, allow_redirects=True)
    except requests.RequestException as e:
        logger.warning(f"[fetch] {url} — {type(e).__name__}: {e}")
        return None, ""
    return response.status_code, response.text


def fetch(url: str, lang: str = "en") -> FetchResult:
    """Fetch and parse one product page.

    Tries the direct transport first and falls back to the bot's cloudscraper
    path, so a host where the fingerprints work the other way round — the Pi on
    a residential line, say — still gets a result.
    """
    status, html = _get(url)

    if status is None:
        return FetchResult(None, TRANSPORT_ERROR)

    # Before the markers, deliberately: Amazon's 404 body contains one of them.
    if status == 404:
        logger.info(f"[fetch] {url} — Amazon returned 404, no such page")
        return FetchResult(None, NOT_FOUND)

    if status != 200 or scraper._is_blocked(html):
        logger.info(
            f"[fetch] {url} — direct GET gave {status} ({len(html):,} bytes); "
            f"falling back to the scraper's own transport"
        )
        product = scraper.fetch_product(url, lang)
        if product is None:
            return FetchResult(None, BLOCKED)
        return _classify(product)

    with _parsing_with(html):
        product = scraper.fetch_product(url, lang)

    if product is None:
        # The page was fetched and was not a block, so parsing is what failed.
        return FetchResult(None, NO_PRICE)

    return _classify(product)


def _classify(product: dict) -> FetchResult:
    if not product.get("available", True):
        return FetchResult(product, UNAVAILABLE)
    return FetchResult(product, OK)


def fetch_product(url: str, lang: str = "en") -> dict | None:
    """Drop-in replacement for `scraper.fetch_product`, for the sweep."""
    return fetch(url, lang).product
