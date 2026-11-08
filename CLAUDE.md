# Amazon Price Tracker — Web

A local website that tracks Amazon product prices and alerts on drops. It does
what the Telegram bot does, without Telegram.

## Provenance

Forked from `../amazon-tracker-bot` at commit `cc3bc6f` ("fixing the bot
reporting Amazon's pre-discount price"). This is a **hard fork**: there is no
shared code, no path dependency and no submodule. The two repos are related
only by history.

`app/scraper.py` and `app/database.py` are byte-identical copies of the bot's
versions. Keeping them that way is worth some discipline — see "Scraper drift".

## Layout

    app/scraper.py      Amazon scraping. Copied unchanged. No Telegram anywhere in it.
    app/database.py     SQLite schema + queries. Copied unchanged. No Telegram either.
    app/debug_price.py  CLI: dump every price candidate on a page. See "Debugging prices".
    app/accounts.py     The accounts table. Site-only, so database.py stays identical.
    app/tracking.py     Site-only user_products queries + dict views of database.py's rows.
    app/fetch_web.py    Site-only fetching: a transport amazon.eg allows, plus a failure reason.
    app/auth.py         PBKDF2 hashing and signed cookie sessions.
    app/scan.py         The price sweep, ported from reference/scheduler_telegram.py.
    app/schemas.py      Request/response models.
    app/api/            The JSON API: session, products, settings, sweep.
    app/main.py         FastAPI app, scheduler lifespan, serves web/dist.
    web/                React + TypeScript frontend (Vite).
    reference/          The bot's Telegram code, for reading only. See reference/README.md.

## Running it

    pip install -r requirements.txt
    cd web && npm install && npm run build      # once, or after frontend changes
    cd ../app && uvicorn main:app --host 0.0.0.0 --port 8000

Then open the port and create the first account, which becomes the admin.

Run it from `app/` — the ported modules import each other by plain name, as they
do in the bot, so that directory has to be on `sys.path`.

For frontend work, run the API as above and `npm run dev` in `web/` alongside it.
Vite proxies `/api` to port 8000, which keeps the dev server same-origin with the
API so the session cookie needs no CORS special-casing.

## Decisions that were open, and how they were settled

**Stack: FastAPI + React (Vite, TypeScript).** A JSON API over the existing
modules, with a real component model for the UI to grow into. FastAPI is async,
matching the ported `asyncio` sweep. The Node build step is paid ahead of
deployment: `web/dist` is built on a dev machine and served as static files, so
the Pi never runs Node. The bundle is ~54 kB gzipped.

**Accounts: a real accounts table.** `app/accounts.py` owns it, and
`user_products.user_id` is now a plain foreign key into it. Sessions are signed
cookies (itsdangerous), passwords are PBKDF2-SHA256 at 600k iterations
(`hashlib`, so no wheel to compile on a Pi). The first account to register
becomes admin and registration then closes — after that only an admin can add
accounts, so a site on the LAN cannot have accounts added by whoever finds the
port. `authorized_users` is left untouched and unused, so the copy of
database.py stays identical to the bot's.

**Alert delivery: not built.** `scan._deliver_alert()` is the seam where
`bot.send_message()` used to be. The decision logic still runs and a matched
alert is logged, but nothing is sent. Adding email, a webhook, browser push or
an in-page feed should mean writing that function's body and touching nothing
above it.

## The user_id problem (resolved)

`database.py` keys everything on `user_id`. In the bot that integer was a
Telegram chat id, which was simultaneously the account identity, the
authorization record and the delivery address.

Here it is identity and authorization only, both owned by `accounts`. **Nothing
about a `user_id` says how to reach a user** — there is no delivery address in
the system at all, by the decision above. Keep it that way: when a channel is
added, the address belongs in its own column or table, not in `user_id`.

## Dead columns — do not build controls on them

Three ported fields look live and are not. They are kept because removing them
would mean editing `database.py`:

- **`user_products.price_min`** is written by the API and read by nothing.
  `_should_notify_user()` only ever consulted `price_max`; the bot's scheduler
  unpacked `price_min` from its query and never passed it on.
- **`user_products.alert_threshold`** predates `price_min`/`price_max` and is
  read by nothing in either repo.
- **`accounts.language`** (and `database.get_user_language`). `fetch_product`
  takes a `lang` argument, but `_extract_name`, `_extract_description` and
  `_extract_specs` all ignore it, so the value changes nothing. In the bot it
  selected notification copy, which is not ported.

The UI deliberately exposes none of these, rather than shipping controls that
do nothing. Note which column is the alert ceiling: it is `price_max`, which the
bot's scheduler read under the name `alert_price`.

## Scraper drift

`app/scraper.py` is a fork of a file that breaks whenever Amazon changes its
markup, so the same bug will need fixing in both repos. Keep the two copies
byte-identical so a fix can be diffed and cherry-picked across:

    diff app/scraper.py ../amazon-tracker-bot/scraper.py
    diff app/database.py ../amazon-tracker-bot/database.py

Both are identical as of this commit. If the site ever needs scraper or database
behaviour the bot does not, add it in a separate module — `accounts.py`,
`tracking.py` and `fetch_web.py` exist for exactly that reason — so those
diffs stay clean.

## Fetching: why the site does not call the scraper's transport directly

`app/fetch_web.py` fetches the page and hands the HTML to `scraper.py` to parse.
Nothing calls `scraper.fetch_product()` straight any more. Two reasons, both
found by testing against live amazon.eg:

- **cloudscraper draws Akamai's bot check on amazon.eg.** The response is a
  4.4 KB `bm-verify` interstitial served with HTTP 200, about one request in
  three. All three of `fetch_html()`'s attempts share that fingerprint, so whole
  fetches fail often enough to look like a broken feature. A plain `requests`
  session with an ordinary Chrome header set was 3/3 where cloudscraper was 2/3,
  and sometimes 0/3. `fetch_web` tries direct first and falls back to the
  scraper's own transport, so a host where it works the other way round — the Pi
  on a residential line — still gets a result.
- **Amazon's 404 page trips `_is_blocked()`.** Its body contains "To discuss
  automated access to Amazon data", which is in `BOT_BLOCK_MARKERS`. So a link
  to an ASIN that is not in the amazon.eg catalogue — very common, since an
  amazon.com ASIN usually is not — gets retried three times with sleeps and then
  reported as "Amazon is blocking this host's IP". `fetch_web` checks the status
  code *before* the markers, which is the whole trick, and returns a reason the
  API turns into a message that names the real cause. This bug is in the bot too.

**The sweep is paced.** Fetched back-to-back, a 42-item sweep ran in about a
minute and Akamai challenged a few random items on every run. `scan.py` now
waits 4–8 s between products and gives anything still `blocked` one more try
after a 60 s cooldown at the end of the sweep. A sweep therefore takes roughly
`items × 6 s` plus the retry pass, which matters when choosing an interval.

`fetch_web.fetch()` returns a `FetchResult` carrying `reason` (`ok`,
`not_found`, `blocked`, `unavailable`, `no_price`, `transport_error`) and a
`detail` sentence. `not_found` answers 400 — it is the caller's bad link; the
rest answer 502.

## Debugging prices

When a product reports the wrong price, do not guess at selectors — dump the
page and look:

    cd app && python debug_price.py https://www.amazon.eg/dp/<ASIN>

It prints every price candidate with its wrapper chain, flags the
struck-through list-price elements, and prints the final choice.

Two things already learned the hard way, both live in `scraper.py`:

- Amazon renders the struck-through list price with the **same** `a-price` /
  `a-offscreen` markup as the real price, often earlier in the DOM.
  `_is_list_price()` exists to skip it. Do not bypass it.
- On amazon.eg the price-to-pay `a-offscreen` is **empty**; the digits are only
  in the `aria-hidden` whole/fraction spans. Selectors must read
  `span.a-price-whole` + `span.a-price-fraction`, not just `a-offscreen`.

## Conventions

- Python 3.11+ (`str | None` unions are used throughout).
- Comments explain *why*, not what. The ported modules are written that way;
  match them.
- The scraper does blocking network I/O with retries and sleeps. Call it off
  the event loop (`asyncio.to_thread`), as the bot did. The same applies to
  password hashing, which is ~0.3s of CPU.
- An unavailable listing's price is never stored and never alerted on. Its
  remaining prices are stale or belong to another seller's offer, so treating
  one as a baseline makes the next comparison wrong. `scan.py` and the add /
  refresh endpoints all hold this line.
- Charts follow the palette and mark specs in `web/src/styles.css`; the price
  history chart is hand-rolled SVG rather than a charting dependency.
