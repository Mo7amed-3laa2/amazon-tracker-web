import cloudscraper
from bs4 import BeautifulSoup
import re
import logging
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

def _supported_encodings() -> str:
    """Only advertise brotli if we can actually decode it.

    Requesting "br" without the brotli package installed makes Amazon return a
    brotli-compressed body that requests cannot decompress, so response.text is
    binary garbage and every selector silently misses.
    """
    try:
        import brotli  # noqa: F401
    except ImportError:
        try:
            import brotlicffi  # noqa: F401
        except ImportError:
            return "gzip, deflate"
    return "gzip, deflate, br"


# Deliberately no User-Agent here: cloudscraper generates a UA that matches the
# TLS/cipher fingerprint it presents. Overriding the UA creates a mismatch
# (Chrome header over a different fingerprint), which is itself a bot signal.
BASE_HEADERS = {
    # Ask for English so prices come back in Western digits.
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": _supported_encodings(),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Connection": "keep-alive",
}

# Amazon serves a captcha/interstitial instead of the product page when it
# decides the caller is a bot; these markers identify that page.
BOT_BLOCK_MARKERS = (
    "Enter the characters you see below",
    "api-services-support@amazon.com",
    "To discuss automated access to Amazon data",
    "Type the characters you see in this image",
    "/errors/validateCaptcha",
    "Sorry, we just need to make sure you're not a robot",
)

# A genuine product page is several hundred KB. Anything tiny is a block or
# error interstitial even when it returns HTTP 200 and carries no marker.
MIN_PRODUCT_PAGE_BYTES = 60_000

# Rotated across attempts so a blocked fingerprint is not retried unchanged.
BROWSER_PROFILES = (
    {"browser": "chrome", "platform": "windows", "desktop": True},
    {"browser": "firefox", "platform": "windows", "desktop": True},
    {"browser": "chrome", "platform": "linux", "desktop": True},
)

ASIN_PATH_RE = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?#]|$)", re.IGNORECASE
)
ASIN_QUERY_RE = re.compile(r"[?&]asin=([A-Z0-9]{10})", re.IGNORECASE)

DEFAULT_ORIGIN = "https://www.amazon.eg"


def _new_session(attempt: int = 0):
    profile = BROWSER_PROFILES[attempt % len(BROWSER_PROFILES)]
    return cloudscraper.create_scraper(browser=profile)


def _origin(url: str) -> str:
    parts = urlparse(url)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return DEFAULT_ORIGIN


def _extract_asin(url: str) -> str | None:
    match = ASIN_PATH_RE.search(url) or ASIN_QUERY_RE.search(url)
    return match.group(1).upper() if match else None


def _canonical_url(url: str) -> str | None:
    """Rebuild a bare /dp/<ASIN> URL, dropping all tracking parameters.

    Share links carry `ref=cm_sw_r_cso_...` and `social_share=...`, which are a
    strong bot signal; requesting the clean canonical URL avoids them.
    """
    asin = _extract_asin(url)
    if not asin:
        return None
    return f"{_origin(url)}/dp/{asin}"


def canonical_product_url(url: str) -> str:
    """The clean /dp/<ASIN> form of a URL, or the URL unchanged if it has no ASIN.

    Used for the links shown in Telegram: share URLs carry a long tail of
    tracking parameters that only make the message longer and the link more
    fragile.
    """
    return _canonical_url(url) or url


def _headers_for(origin: str) -> dict:
    headers = dict(BASE_HEADERS)
    headers["Referer"] = origin + "/"
    return headers


def _warm_up(session, origin: str) -> None:
    """Fetch the storefront first so the session carries real Amazon cookies.

    A cold request straight to /dp/<ASIN> with no session cookies is one of the
    most reliable ways to trigger the bot check.
    """
    try:
        session.get(origin + "/", headers=_headers_for(origin), timeout=20)
        logger.debug(f"[scraper] Warmed session on {origin} ({len(session.cookies)} cookies)")
    except Exception as e:
        logger.debug(f"[scraper] Warm-up on {origin} failed (continuing): {e}")


def _is_blocked(html: str) -> bool:
    if any(marker in html for marker in BOT_BLOCK_MARKERS):
        return True
    return len(html) < MIN_PRODUCT_PAGE_BYTES and "productTitle" not in html


def _resolve_short_link(session, url: str) -> str:
    """Follow amzn.eu / amzn.to / a.co redirects to the real product URL."""
    try:
        response = session.get(
            url, headers=_headers_for(_origin(url)), timeout=25, allow_redirects=True
        )
        return str(response.url)
    except Exception as e:
        logger.debug(f"[scraper] Could not resolve short link {url}: {e}")
        return url


def fetch_html(url: str) -> str | None:
    """Fetch a product page's HTML, rotating fingerprints across retries.

    Split out of fetch_product so diagnostics can look at exactly the page the
    scraper saw without re-implementing the warm-up and retry logic.
    """
    MAX_ATTEMPTS = 3

    # Resolve short links once, then request the clean canonical /dp/<ASIN> URL
    # so no share-tracking parameters are ever sent.
    target = url
    if not _extract_asin(target):
        with _new_session(0) as resolver_session:
            target = _resolve_short_link(resolver_session, target)
    canonical = _canonical_url(target)
    if canonical:
        if canonical != target:
            logger.info(f"[scraper] Using canonical URL {canonical}")
        target = canonical
    else:
        logger.warning(f"[scraper] No ASIN found in {url}; requesting as-is")

    html = None
    for attempt in range(MAX_ATTEMPTS):
        # Each session opens its own connection pool (sockets = file descriptors);
        # without closing it, a long-running process leaks fds on every attempt
        # until it can't open any file at all — including the sqlite database.
        with _new_session(attempt) as session:
            origin = _origin(target)
            _warm_up(session, origin)

            try:
                response = session.get(target, headers=_headers_for(origin), timeout=30)
                response.raise_for_status()
            except Exception as e:
                logger.warning(f"[scraper] Attempt {attempt + 1}/{MAX_ATTEMPTS} request failed: {e}")
                html = None
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(2 ** attempt)
                continue

            body = response.text
            logger.info(
                f"[scraper] Attempt {attempt + 1}/{MAX_ATTEMPTS} GET {response.status_code} | "
                f"encoding={response.headers.get('Content-Encoding') or 'none'} | "
                f"bytes={len(body)} | final_url={response.url}"
            )

            if _is_blocked(body):
                logger.warning(
                    f"[scraper] Attempt {attempt + 1}/{MAX_ATTEMPTS} hit a bot check "
                    f"({len(body)} bytes) — rotating browser fingerprint"
                )
                html = None
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(3 * (attempt + 1))
                continue

            if not _looks_like_html(body):
                logger.error(
                    f"[scraper] Response body is not readable HTML "
                    f"(encoding={response.headers.get('Content-Encoding')}). "
                    f"First 80 chars: {body[:80]!r}"
                )
                html = None
                continue

            html = body
            break

    if html is None:
        logger.error(
            f"[scraper] Giving up on {url} after {MAX_ATTEMPTS} attempts — "
            f"Amazon is blocking this host's IP."
        )
    return html


def fetch_product(url: str, lang: str = "en", retry_count: int = 0) -> dict | None:
    """
    Scrape an Amazon product page and return name, price, specs, and image.
    Includes retry logic with exponential backoff.

    Args:
        url: Amazon product URL
        lang: Language code ('en' or 'ar')
        retry_count: Internal retry counter (don't set manually)

    Returns None if the page could not be parsed.
    """
    html = fetch_html(url)
    if html is None:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")

        name = _extract_name(soup, lang)
        available = _extract_availability(soup)

        # An unavailable listing is reported rather than parsed further: its
        # remaining prices are stale or belong to someone else's offer, so
        # callers must be able to tell "sold out" apart from "fetch failed".
        if not available:
            logger.info(f"[scraper] '{name[:40]}' is unavailable — reporting without a price")
            return {
                "name": name,
                "price": None,
                "image": _extract_image(soup),
                "description": None,
                "specs": None,
                "merchant_name": None,
                "is_amazon": True,
                "available": False,
                "language": lang,
            }

        price = _extract_price(soup, html)
        image_url = _extract_image(soup)
        description = _extract_description(soup, lang)
        specs = _extract_specs(soup, lang)
        merchant_name, is_amazon = _extract_merchant(soup)

        if price is None:
            has_title = soup.find(id="productTitle") is not None
            logger.error(
                f"[scraper] Could not find price for {url} "
                f"(productTitle present={has_title}, bytes={len(html)}). "
                f"Page layout may have changed or the item is unavailable."
            )
            return None

        logger.info(f"[scraper] OK '{name[:40]}' -> {price:,.2f} (merchant={merchant_name or 'Unknown'}, is_amazon={is_amazon})")
        return {
            "name": name,
            "price": price,
            "image": image_url,
            "description": description,
            "specs": specs,
            "merchant_name": merchant_name,
            "is_amazon": is_amazon,
            "available": True,
            "language": lang,
        }
    except Exception as e:
        logger.exception(f"[scraper] Error parsing page for {url}: {e}")
        return None


def _looks_like_html(body: str) -> bool:
    head = body[:2048].lstrip().lower()
    return "<html" in head or head.startswith("<!doctype") or "<head" in head


def _extract_name(soup: BeautifulSoup, lang: str = "en") -> str:
    """Extract product name from the page."""
    name_tag = soup.find(id="productTitle")
    return name_tag.get_text(strip=True) if name_tag else "Unknown Product"


def _extract_image(soup: BeautifulSoup) -> str | None:
    """Extract product image URL from the page."""
    img_tag = soup.find("img", {"id": "landingImage"})
    if img_tag and img_tag.get("src"):
        return img_tag["src"]

    img_tag = soup.find("img", {"class": "a-dynamic-image"})
    if img_tag and img_tag.get("src"):
        return img_tag["src"]

    return None


def _extract_description(soup: BeautifulSoup, lang: str = "en") -> str | None:
    """Extract product description from the page."""
    desc_selectors = [
        ("div", {"id": "feature-bullets"}),
        ("div", {"class": "a-section a-spacing-medium a-spacing-top-medium"}),
        ("div", {"data-feature-name": "featurebullets"}),
    ]

    for tag, attrs in desc_selectors:
        el = soup.find(tag, attrs)
        if el:
            text = el.get_text(strip=True, separator="\n")
            if text:
                return text[:500]

    return None


def _extract_specs(soup: BeautifulSoup, lang: str = "en") -> dict | None:
    """Extract key product specifications from the page."""
    specs = {}

    details_table = soup.find("table", {"class": "a-keyvalue"})
    if details_table:
        rows = details_table.find_all("tr")
        for row in rows[:5]:
            cells = row.find_all("td")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True)
                value = cells[1].get_text(strip=True)
                specs[key] = value

    return specs if specs else None


_AMAZON_SELLER_NAMES = ("amazon", "amazon.eg", "amazon.com")


def _extract_merchant(soup: BeautifulSoup) -> tuple[str | None, bool]:
    """Extract merchant info and determine if sold by Amazon.

    Only trusts elements that specifically carry the seller name — never a
    whole-page or whole-buybox text blob, which almost always contains the
    word "Amazon" somewhere (badges, disclaimers, footer links) regardless of
    who the actual seller is, and would otherwise mark every product as
    Amazon-sold.

    Returns tuple of (merchant_name, is_amazon). is_amazon defaults to True
    when no seller line is found at all, matching Amazon's own convention of
    omitting "Sold by" for its first-party listings.
    """
    # The seller name link in the buy box, e.g. <a id="sellerProfileTriggerId">ABC Trading</a>
    seller_link = soup.find(id="sellerProfileTriggerId")
    if seller_link:
        name = seller_link.get_text(strip=True)
        if name:
            is_amazon = name.lower() in _AMAZON_SELLER_NAMES
            return (None if is_amazon else name), is_amazon

    # "Ships from and sold by ..." / "Sold by ... and Fulfilled by Amazon"
    merchant_info = soup.find(id="merchant-info")
    if merchant_info:
        text = merchant_info.get_text(" ", strip=True)
        match = re.search(r"sold by\s+([^.,]+?)(?:\s+and\s+(?:ships|fulfilled)|[.,]|$)", text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            is_amazon = name.lower() in _AMAZON_SELLER_NAMES
            return (None if is_amazon else name), is_amazon

    # Newer tabular buy box: a row labelled "Sold by" with the seller in the next cell
    for row in soup.select("#tabular-buybox tr, table.a-keyvalue tr"):
        label = row.find(["th", "td"])
        if label and "sold by" in label.get_text(strip=True).lower():
            cells = row.find_all("td")
            if cells:
                name = cells[-1].get_text(strip=True)
                if name:
                    is_amazon = name.lower() in _AMAZON_SELLER_NAMES
                    return (None if is_amazon else name), is_amazon

    # No seller line found at all — Amazon's own listings commonly omit it.
    return None, True


# Amazon keeps rendering prices on a listing nobody can buy: the struck-through
# list price, the "other sellers" box and the related-items carousels all use
# the same `a-price` markup. These markers identify such a page so a sold-out
# item is not reported as a price change.
UNAVAILABLE_MARKERS = (
    "currently unavailable",
    "out of stock",
    "temporarily out of stock",
    "we don't know when or if this item will be back",
    "غير متوفر",
    "غير متاح",
    "نفدت الكمية",
)

# A listing that can actually be bought renders one of these; an unavailable
# one renders none of them, even while it still shows a price.
BUYABLE_SELECTORS = (
    "#add-to-cart-button",
    "#buy-now-button",
    "input[name='submit.add-to-cart']",
    "#buybox-see-all-buying-choices",
    "#dynamic-aod-ingress-box",
)

OUT_OF_STOCK_SELECTORS = ("#outOfStock", "#outOfStockBuyBox_feature_div")

AVAILABILITY_SELECTORS = ("#availability", "#availability_feature_div")


def _extract_availability(soup: BeautifulSoup) -> bool:
    """Whether the page is actually offering this item for sale right now."""
    for selector in OUT_OF_STOCK_SELECTORS:
        if soup.select_one(selector):
            logger.info(f"[scraper] Item unavailable: '{selector}' present")
            return False

    for selector in AVAILABILITY_SELECTORS:
        el = soup.select_one(selector)
        if not el:
            continue
        text = _normalize_digits(el.get_text(" ", strip=True)).lower()
        if any(marker in text for marker in UNAVAILABLE_MARKERS):
            logger.info(f"[scraper] Item unavailable: availability reads '{text[:60]}'")
            return False

    if any(soup.select_one(selector) for selector in BUYABLE_SELECTORS):
        return True

    # No buy affordance and no explicit notice. Treating this as unavailable
    # costs at most a missed notification, whereas trusting it means alerting
    # on a price that cannot be paid.
    logger.warning("[scraper] No add-to-cart/buying-options element found — treating as unavailable")
    return False


# Price markup in these blocks belongs to other products (recommendation
# carousels, sponsored strips, comparison tables), not to this listing.
NON_OFFER_BLOCK_MARKERS = (
    "carousel",
    "sims",
    "similarities",
    "sponsored",
    "related",
    "comparison",
    "bundle",
    "shopping-aids",
)

# Holds the title, price and buy box and nothing from further down the page.
OFFER_SCOPE_SELECTORS = ("#ppd", "#centerCol")


def _in_non_offer_block(el) -> bool:
    for parent in el.parents:
        ident = f"{parent.get('id') or ''} {' '.join(parent.get('class') or [])}".lower()
        if any(marker in ident for marker in NON_OFFER_BLOCK_MARKERS):
            return True
    return False


# On a discounted listing Amazon renders the struck-through list price ("was
# EGP1,200.00") with exactly the same `a-price` / `a-offscreen` markup as the
# price being charged, and frequently earlier in the DOM. A selector that only
# asks for `a-price` therefore reports the pre-discount price. These markers
# identify the list-price wrapper so its contents are never taken as the price.
LIST_PRICE_CLASSES = {
    "basisprice",
    "a-text-strike",
    "a-price-strike",
    "pricestrikethrough",
    "priceblockstrikepricestring",
    "a-text-price-strike",
}

LIST_PRICE_IDS = {
    "listprice",
    "priceblock_listprice",
    "print-list-price",
}

# Climbing past the price block teaches nothing more, so stop there.
PRICE_BLOCK_IDS = {
    "corePriceDisplay_desktop_feature_div",
    "corePrice_feature_div",
    "apex_desktop",
    "ppd",
    "centerCol",
}


def _is_list_price(el) -> bool:
    """Whether this price element is the struck-through list/"was" price."""
    node = el
    while node is not None and hasattr(node, "get"):
        if node.get("data-a-strike") == "true":
            return True
        if {c.lower() for c in (node.get("class") or [])} & LIST_PRICE_CLASSES:
            return True
        if (node.get("id") or "").lower() in LIST_PRICE_IDS:
            return True
        if node.get("id") in PRICE_BLOCK_IDS:
            break
        node = node.parent
    return False


# Ordered most-specific first: the buy-box / core price blocks come before the
# generic ones, because the first `.a-offscreen` on a page is frequently the
# struck-through list price rather than the price actually being charged.
PRICE_SELECTORS = (
    "#corePriceDisplay_desktop_feature_div span.priceToPay span.a-offscreen",
    "#corePriceDisplay_desktop_feature_div span.apexPriceToPay span.a-offscreen",
    "#corePriceDisplay_desktop_feature_div span.apex-pricetopay-value span.a-offscreen",
    # amazon.eg leaves the price-to-pay `a-offscreen` empty and renders the
    # digits only in the aria-hidden whole/fraction spans, so those have to be
    # read before falling back to any generic `a-price` on the page.
    "#corePriceDisplay_desktop_feature_div span.priceToPay span.a-price-whole",
    "#corePriceDisplay_desktop_feature_div span.apex-pricetopay-value span.a-price-whole",
    "#corePriceDisplay_desktop_feature_div span.a-price span.a-offscreen",
    "#corePrice_feature_div span.priceToPay span.a-offscreen",
    "#corePrice_feature_div span.apexPriceToPay span.a-offscreen",
    "#corePrice_feature_div span.a-price span.a-offscreen",
    "#corePrice_desktop span.a-price span.a-offscreen",
    "#apex_desktop span.priceToPay span.a-offscreen",
    "#apex_desktop span.apexPriceToPay span.a-offscreen",
    "#apex_desktop span.a-price span.a-offscreen",
    "span.priceToPay span.a-offscreen",
    "span.apexPriceToPay span.a-offscreen",
    "span.apex-pricetopay-value span.a-offscreen",
    "span.priceToPay span.a-price-whole",
    "span.apex-pricetopay-value span.a-price-whole",
    "span.reinventPricePriceToPayMargin span.a-offscreen",
    "#price_inside_buybox",
    "#newBuyBoxPrice",
    "#buybox span.a-price span.a-offscreen",
    "#tp_price_block_total_price_ww span.a-offscreen",
    "#sns-base-price",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#priceblock_saleprice",
    "#usedBuySection span.a-color-price",
    # Whole/fraction pair — handled specially below.
    "#corePriceDisplay_desktop_feature_div span.a-price-whole",
    "span.a-price span.a-price-whole",
    # Last resort: any price on the page.
    "span.a-price span.a-offscreen",
    "span.a-color-price",
)

# Amazon embeds the authoritative price in its inline JSON payloads.
PRICE_JSON_PATTERNS = (
    r'"priceAmount"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
    r'"displayPrice"\s*:\s*"[^"0-9]*([0-9][0-9.,]*)"',
    r'"buyingPrice"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
)


def _extract_price(soup: BeautifulSoup, html: str | None = None) -> float | None:
    """Find the price being charged for *this* listing.

    Searched inside the offer area first (title/price/buy box) and, only if
    that finds nothing, across the whole page while skipping recommendation
    and comparison blocks — their `a-price` markup belongs to other products
    and is what made an unavailable item look like a huge price change.
    """
    scope = next(
        (el for selector in OFFER_SCOPE_SELECTORS if (el := soup.select_one(selector))),
        None,
    )
    for root in (scope, soup) if scope is not None else (soup,):
        price = _price_from(root, root is soup)
        if price is not None:
            return _corrected_for_discount(soup, html, price)

    return _price_from_json(html)


def _price_from_json(html: str | None) -> float | None:
    """The price Amazon embeds in its inline JSON payloads."""
    if not html:
        return None
    for pattern in PRICE_JSON_PATTERNS:
        match = re.search(pattern, html)
        if match:
            price = _parse_price(match.group(1))
            if price is not None:
                logger.debug(f"[scraper] Price matched via JSON pattern: {price}")
                return price
    return None


def _corrected_for_discount(soup: BeautifulSoup, html: str | None, price: float) -> float:
    """Catch the case where the price picked is the pre-discount list price.

    A page that shows a savings badge ("-18%") alongside a struck-through list
    price is by definition charging less than that list price, so a match equal
    to it means the discounted price was rendered with markup this scraper does
    not recognise. Amazon's inline JSON carries the real one.
    """
    basis = _basis_price(soup)
    if basis is None or abs(price - basis) > 0.01:
        return price
    if not soup.select_one("span.savingsPercentage, span.savingPriceOverride, #dealBadge"):
        return price

    json_price = _price_from_json(html)
    if json_price is not None and json_price < basis:
        logger.warning(
            f"[scraper] Matched the list price {basis:,.2f} on a discounted listing; "
            f"using the JSON price {json_price:,.2f} instead"
        )
        return json_price

    logger.warning(
        f"[scraper] Listing shows a discount but the only price found is the "
        f"list price {basis:,.2f} — reporting it; price markup may have changed"
    )
    return price


def _basis_price(soup: BeautifulSoup) -> float | None:
    """The struck-through "was" price, if the page shows one."""
    for el in soup.select("span.basisPrice span.a-offscreen, span[data-a-strike='true'] span.a-offscreen"):
        price = _parse_price(el.get_text(strip=True))
        if price is not None:
            return price
    return None


def _price_from(root, skip_non_offer_blocks: bool) -> float | None:
    for selector in PRICE_SELECTORS:
        for el in root.select(selector):
            if skip_non_offer_blocks and _in_non_offer_block(el):
                continue

            if _is_list_price(el):
                continue

            raw = el.get_text(strip=True)

            # `a-price-whole` holds only the integer part; the decimals live in a
            # sibling `a-price-fraction`. Without stitching them together a price of
            # 1,299.99 would be read as 1299 — or as 129999 once separators are
            # stripped, depending on the trailing separator Amazon renders.
            if "a-price-whole" in (el.get("class") or []):
                fraction = el.find_next("span", {"class": "a-price-fraction"})
                whole = re.sub(r"[^\d]", "", _normalize_digits(raw))
                if whole:
                    if fraction:
                        frac_digits = re.sub(r"[^\d]", "", _normalize_digits(fraction.get_text(strip=True)))
                        raw = f"{whole}.{frac_digits or '0'}"
                    else:
                        raw = whole

            price = _parse_price(raw)
            if price is not None:
                logger.debug(f"[scraper] Price matched via '{selector}': {price}")
                return price

    return None


# Amazon's Arabic locale renders prices in Arabic-Indic digits, which float()
# cannot parse.
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _normalize_digits(raw: str) -> str:
    return raw.translate(_ARABIC_INDIC)


def _parse_price(raw: str) -> float | None:
    """Extract a float from a string like 'EGP 1,299.00', '1,299' or '١٢٩٩'."""
    cleaned = _normalize_digits(raw)
    cleaned = re.sub(r"[^\d.,]", "", cleaned)      # keep digits, dot, comma
    cleaned = cleaned.strip(".,")                   # drop dangling separators

    if not cleaned:
        return None

    # Decide which separator is the decimal point: whichever comes last, and
    # only when it is followed by 1-2 digits (thousands groups are always 3).
    last_dot, last_comma = cleaned.rfind("."), cleaned.rfind(",")
    decimal_sep = None
    if last_dot > last_comma and len(cleaned) - last_dot - 1 in (1, 2):
        decimal_sep = "."
    elif last_comma > last_dot and len(cleaned) - last_comma - 1 in (1, 2):
        decimal_sep = ","

    if decimal_sep:
        whole, _, frac = cleaned.rpartition(decimal_sep)
        cleaned = re.sub(r"[^\d]", "", whole) + "." + frac
    else:
        cleaned = re.sub(r"[^\d]", "", cleaned)

    try:
        price = float(cleaned)
    except ValueError:
        return None

    # Guard against parsing something that is not a price at all.
    if price <= 0 or price > 100_000_000:
        return None
    return price
