# Reference only — not part of this app

These two files are copied verbatim from the Telegram bot. Nothing here is
imported by the site, and nothing here should be: `python-telegram-bot` is
deliberately absent from `requirements.txt`.

They are kept because they are the specification for behaviour the site still
has to reproduce, and reading them is cheaper than rediscovering the rules.

## `scheduler_telegram.py`

The scan loop. Port these, leave the delivery behind:

- `_should_notify_user()` — the whole alert-decision rule (threshold, min/max
  band, "Amazon merchant only"). Pure logic, no Telegram. Reusable as-is.
- `check_prices()` — the loop shape: fetch each product, compare, write
  history, decide per user. The `await bot.send_message(...)` at the end is the
  only part that must be replaced.
- `get_localized_notification()` — EN/AR message copy. The wording is worth
  keeping even though the HTML markup is Telegram's.

## `bot_telegram.py`

1,735 lines of Telegram UI. Not portable, but it is the complete inventory of
features the site needs: add/remove products, per-user alert thresholds, price
min/max bands, merchant filtering, price history, language switching, user
authorization and admin controls. Skim it when scoping a screen so a feature
that already exists is not accidentally dropped.
