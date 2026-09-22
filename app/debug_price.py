"""Dump every price candidate on a product page, so a wrong pick is visible.

Usage: python debug_price.py <amazon-url>

Prints, for each price element the scraper could match: the selector that
found it, the text, and whether it sits in a struck-through / list-price
wrapper. The line marked "CHOSEN" is what fetch_product would report.
"""
import sys
import logging

from bs4 import BeautifulSoup

import scraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main(url: str) -> int:
    html = scraper.fetch_html(url)
    if html is None:
        print("Could not fetch the page (blocked or network error).")
        return 1

    soup = BeautifulSoup(html, "lxml")
    print(f"\nname        : {scraper._extract_name(soup)[:70]}")
    print(f"available   : {scraper._extract_availability(soup)}")

    core = soup.select_one("#corePriceDisplay_desktop_feature_div")
    print(f"core block  : {'present' if core else 'MISSING (mobile/alternate layout?)'}")
    if core:
        print("--- core price block text ---")
        print(core.get_text(" | ", strip=True)[:400])

    print("\n--- every a-price / a-offscreen candidate in the offer area ---")
    scope = soup.select_one("#ppd") or soup.select_one("#centerCol") or soup
    for el in scope.select("span.a-offscreen, span.a-color-price, span.a-price-whole"):
        chain = " < ".join(
            f"{p.name}#{p.get('id') or ''}.{'.'.join(p.get('class') or [])}"
            for p in list(el.parents)[:3]
        )
        flags = []
        if scraper._is_list_price(el):
            flags.append("LIST/STRIKE")
        print(f"  {el.get_text(strip=True)[:24]:<24} {'|'.join(flags) or 'offer':<12} {chain[:110]}")

    print(f"\nCHOSEN price: {scraper._extract_price(soup, html)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
