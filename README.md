# PizzaBot

Python + Playwright CLI that creates and tracks Pizza Hut Canada accounts using
Gmail plus-address aliases (`youraccount+N@gmail.com`), then checks those
accounts for Hut Rewards limited-time offers.

> Warning: this automates account creation and promotion checks on Pizza Hut
> Canada. It may violate Pizza Hut's Terms of Service. Use it responsibly, at
> your own risk, and only for legitimate personal testing.

---

## Features

- Creates Pizza Hut Canada accounts from one Gmail base address.
- Uses Gmail plus-addressing (`+N`) so verification/sign-in emails arrive in the
  same inbox.
- Persistently tracks allocated aliases in SQLite (`pizzabot.db`) so aliases are
  not reused after failures or deletion.
- Logs in through Pizza Hut's passwordless email login link.
- Completes the `/complete-profile` flow with:
  - first name
  - last name
  - mobile number
  - birthday generated from the configured `base_profile.birthday` rule
  - Terms & Conditions agreed
  - Email/SMS marketing checkboxes left unchecked
- Checks Hut Rewards and records limited-time offers.
- Displays the currently stored Hut Rewards limited-time offers for active
  accounts with `promos`, including each promo's name and expiry, without
  performing a live Pizza Hut login or promo check.
- Handles the Pizza Hut "Warm, web cookies" consent dialog when it appears.
- Keeps separate flows for account creation, account verification, and
  promotion checking.

---

## Requirements

- Python 3.13+
- Chromium (installed through Playwright)
- A Gmail account that supports IMAP and plus-addressing

---

## Setup

```powershell
cd PizzaBot

pip install -r requirements.txt
python -m playwright install chromium
```

Create or update `config.json`:

```powershell
python PizzaBot.py init-config
```

`config.json` and `pizzabot.db` are gitignored. Do not commit your IMAP app
password.

---

## Configuration

`config.example.json` documents the current configuration shape:

```json
{
  "target_account_pool": 3,
  "promo_check_frequency_days": 1,
  "base_profile": {
    "first_name": "Jordan",
    "last_name": "Smith",
    "birthday": "next_month:1-10",
    "area_code": "905",
    "phone": "",
    "base_email": "youraccount@gmail.com"
  },
  "imap": {
    "host": "imap.gmail.com",
    "username": "youraccount@gmail.com",
    "password": "your_gmail_app_password",
    "mailbox": "INBOX"
  },
  "browser": {
    "headless": false,
    "slow_mo_ms": 300
  },
  "selectors": {}
}
```

### `target_account_pool`

How many active accounts `create` should try to maintain. Active accounts are
`id >= 33`, `status = 'verified'`, and `promotion_used = 0`.

### `promo_check_frequency_days`

Used by `run --loop`; the delay is clamped to at least one day.

### `base_profile`

- `first_name`, `last_name`: used for all generated accounts.
- `birthday`: either a fixed `YYYY-MM-DD` date or a `next_month:START-END`
  rule.
- `area_code`: used when `phone` is empty.
- `phone`: optional fixed phone number; leave empty to generate a random local
  number from `area_code`.
- `base_email`: the Gmail address that is converted into `local+N@gmail.com`
  aliases.

### Birthday generation

- Fixed `YYYY-MM-DD`: the account stores and uses that exact date.
- `next_month:START-END`: generates one random day in the inclusive range of the
  month immediately following the current month. Example:
  `next_month:1-10` in August generates `YYYY-09-01` through `YYYY-09-10`.

The generated birthday is stored on the account row as `YYYY-MM-DD`. The Stats
view displays it as `MM-DD`.

### `imap`

Used to poll Gmail for Pizza Hut login emails.

- `host`
- `username`
- `password` (Gmail app password)
- `mailbox` (defaults to `INBOX` when absent)

### `browser`

- `headless`: default browser mode. Individual CLI commands can override this
  with `--headless`.
- `slow_mo_ms`: Playwright slow-motion delay in milliseconds.

### `selectors`

Optional CSS- or locator-override map. Supported keys currently used by the
page flows are:

- `close_popup`
- `login_email`
- `login_submit`
- `first_name_input`
- `last_name_input`
- `phone_input`
- `birthday_input`
- `profile_submit`
- `view_profile`

Leaving it as `{}` uses the built-in default locators.

---

## Account pool and alias tracking

Accounts live in the `accounts` table in `pizzabot.db`. Alias tracking lives in
the `alias_tracker` table.

Current active-pool rules:

- The active account pool starts at account ID **33**.
- Database rows with `id < 33` are kept for history but are excluded from all
  active-pool queries, Stats output, and automated operations.
- New alias allocation starts at **33** and walks upward to find the smallest
  unused ID.
- `alias_tracker` permanently records every allocated alias, so an alias is not
  reused even if an account row is later deleted or a flow fails partway.
- Account selection for `verify`, `check-promos`, and Stats all use the
  active-pool filter (`id >= 33`).

Example alias sequence:

```text
youraccount+33@gmail.com
youraccount+34@gmail.com
youraccount+35@gmail.com
...
```

---

## CLI commands

Global options are available before the subcommand:

```text
python PizzaBot.py [--config CONFIG] [--db DB] COMMAND [command options]
```

- `--config CONFIG`: path to the config file; default is `config.json`
- `--db DB`: path to the SQLite database; default is `pizzabot.db`

The supported subcommands are:

### `init-config`

```powershell
python PizzaBot.py init-config
```

Interactively creates or updates `config.json`.

No command-specific options. The global `--config CONFIG` option changes which
file is written.

### `create`

```powershell
python PizzaBot.py create [--count COUNT] [--timeout TIMEOUT] [--headless]
```

Creates new Pizza Hut accounts and completes their profiles.

Behavior:

- Calculates `needed` as `target_account_pool - active_count`. If
  `--count COUNT` is supplied, it uses that override instead.
- If `needed <= 0`, it prints that the active pool is already at target and
  exits. Use `--count` to force creation anyway.
- For each account:
  1. Allocates the next active alias ID (`id >= 33`).
  2. Generates the email alias and profile fields.
  3. Inserts the account row immediately with status `created`.
  4. Opens `/login`, accepts cookies when present, and submits the email.
  5. Polls IMAP for the "Log in to Your Pizza Hut Account" email.
  6. Opens the Pizza Hut login link from the email.
  7. Completes `/complete-profile` when redirected there.
  8. Marks the account `verified` after profile completion.
- A Pizza Hut flow error marks the account `manual_review` and records a concise
  error action in Stats. Unexpected non-flow errors are re-raised.

Options:

- `--count COUNT`: optional override for the number of accounts to create this
  run.
- `--timeout TIMEOUT`: seconds to wait for each verification email; default is
  `180`.
- `--headless`: run Chromium headless for this command.

Example:

```powershell
python PizzaBot.py create --count 3 --timeout 180
```

### `verify`

```powershell
python PizzaBot.py verify [--ids IDS] [--timeout TIMEOUT] [--headless]
```

Retries login/email verification for unverified active accounts.

Behavior:

- With no `--ids`, selects active accounts with status `created` or
  `manual_review`.
- With `--ids`, selects only active-pool rows whose IDs match the supplied
  comma-separated IDs.
- For each selected account:
  1. Submits its stored email on `/login`.
  2. Polls IMAP for the Pizza Hut login email.
  3. Opens the email login link.
  4. If the landing URL contains `/complete-profile`, completes the profile.
  5. If the landing URL is not `/complete-profile` or `/order/deals`, marks the
     account `manual_review`.
  6. Marks the account `verified` after successful profile completion or after
     landing on `/order/deals` without needing profile completion.
- If no login email is received, the account status is left unchanged.

Options:

- `--ids IDS`: comma-separated account IDs, e.g. `33,34`.
- `--timeout TIMEOUT`: seconds to wait for each login email; default is `180`.
- `--headless`: run Chromium headless for this command.

Example:

```powershell
python PizzaBot.py verify --ids 33,34 --timeout 180
```

### `check-promos`

```powershell
python PizzaBot.py check-promos [--ids IDS] [--timeout TIMEOUT] [--headless]
```

Logs into active, promo-enabled accounts and checks Hut Rewards limited-time
offers.

Behavior:

- With no `--ids`, selects active rows matching:
  - `id >= 33`
  - `status = 'verified'`
  - `check_promotions = 1`
  - `promotion_used = 0`
- With `--ids`, selects active-pool rows whose IDs match the supplied
  comma-separated IDs. IDs below 33 are excluded.
- For each selected account:
  1. Submits its stored email on `/login`.
  2. Polls IMAP for the Pizza Hut login email.
  3. Opens the login link.
  4. Waits for `/order/deals`.
  5. Closes the "Change Carryout Time" modal if present.
  6. Clicks **View Profile** / the account icon.
  7. Navigates to **My Details** if needed.
  8. Clicks the exact **Hut Rewards** menu option.
  9. Waits for Hut Rewards content to load.
  10. Extracts `Limited time offers` from the page and any embedded iframes.
- If no login email is received, the account is skipped with an error action
  recorded in Stats.
- If no limited-time offers are found, that is recorded as a valid zero-offer
  promo check.
- When offers are found, the first offer's name/status/expiry and the total
  offer count are recorded in the database.

Options:

- `--ids IDS`: comma-separated account IDs, e.g. `33,35`.
- `--timeout TIMEOUT`: seconds to wait for each login email; default is `180`.
- `--headless`: run Chromium headless for this command.

Example:

```powershell
python PizzaBot.py check-promos --ids 33,34 --timeout 180
```

### `promos`

```powershell
python PizzaBot.py promos [--ids IDS]
```

Displays promotions already stored in the database.

Behavior:

- Does **not** log into Pizza Hut or perform a live promotion check.
- With no `--ids`, shows stored promotions for every active-pool account
  (`id >= 33`).
- With `--ids`, shows stored promotions only for active-pool account IDs whose
  IDs match the supplied comma-separated IDs. IDs below 33 are excluded.
- For each account, prints the account email and every stored promotion name
  and expiry. Accounts with no stored promotions are marked clearly.

Options:

- `--ids IDS`: comma-separated account IDs, e.g. `33,34`.

Example:

```powershell
python PizzaBot.py promos --ids 33,34
```

Example output:

```text
Promos for youraccount+33@gmail.com:
  Welcome to Hut Rewards: Free Regular Breadsticks
    Expires in 29 days!

Promos for youraccount+34@gmail.com:
  Welcome to Hut Rewards: Free Regular Breadsticks
    Expires in 29 days!
```

### `run`

```powershell
python PizzaBot.py run [--count COUNT] [--timeout TIMEOUT] [--headless] [--loop]
```

Runs `create`, then `verify`, then `check-promos`.

Options:

- `--count COUNT`: optional override passed to the `create` step.
- `--timeout TIMEOUT`: seconds to wait for each email link; default is `180`.
- `--headless`: run Chromium headless for this run.
- `--loop`: repeat the sequence forever, sleeping
  `promo_check_frequency_days` days between runs.

Example:

```powershell
python PizzaBot.py run --count 1 --timeout 180
```

### `stats`

```powershell
python PizzaBot.py stats
```

Prints the active account-pool summary and account table.

Behavior:

- Reads only active rows with `id >= 33`.
- Aggregate summary includes:
  - `Total`
  - `Active`
  - `Verified`
  - `Manual Review`
  - `Promo Used`
  - other non-standard statuses, if present
- Account table columns:
  - `ID`
  - `EMAIL`
  - `STATUS`
  - `BIRTHDAY` as `MM-DD`
  - `LAST ACTION`
  - `PROMOS`
  - `LAST CHECK`
  - `CREATED / VERIFIED`
- The birthday shown is the value actually stored on each account, not the
  configured generation rule.

No command-specific options.

---

## Project structure

```text
PizzaBot.py            # CLI entrypoint
pizzabot/
  browser.py           # Playwright browser session wrapper
  config.py            # config.json loader + interactive setup
  db.py                # SQLite schema + active-pool account helpers
  generate.py          # +N aliases, birthdays, and phone numbers
  mail.py              # IMAP polling + Pizza Hut email link extraction
  pizzahut.py          # Pizza Hut page flows (create + promotions)
tests/                 # unit tests
```

---

## Testing

Run the full unit test suite:

```powershell
python -m unittest discover -s tests -v
```

The tests cover:

- alias generation and non-reuse
- birthday generation
- phone generation
- SQLite account-pool and active-ID filtering
- stored promotion persistence and the `promos` display command
- Stats dashboard formatting and filtering
- email link extraction
- account profile checkbox/terms behavior
- promotion navigation and offer extraction

Live flows against pizzahut.ca should be run separately, for example:

```powershell
python PizzaBot.py create --count 1 --timeout 180
python PizzaBot.py check-promos --ids 33 --timeout 180
```

---

## Disclaimer

This project is an automation tool for personal testing. Account creation and
promotion checking can break whenever Pizza Hut changes its site. Use at your
own risk.
