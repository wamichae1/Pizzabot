# PizzaBot

Python + Playwright bot that creates and tracks Pizza Hut Canada accounts using
Gmail plus-address aliases (`youraccount+N@gmail.com`), then checks accounts for
Hut Rewards limited-time offers.

> ⚠️ This automates account creation and promotion checks on Pizza Hut Canada.
> It may violate Pizza Hut’s Terms of Service. Use it responsibly, at your own
> risk, and only for legitimate personal testing/use.

---

## Features

- Create multiple Pizza Hut Canada accounts from a single base Gmail address.
- Uses Gmail plus-addressing (`+1`, `+2`, `+3`, ...) so the same inbox receives
  every verification/sign-in email.
- Persistently tracks alias numbers in SQLite so an alias is **never reused**.
- Logs into each account via Pizza Hut’s passwordless email login link.
- Completes `/complete-profile` with:
  - first name
  - last name
  - mobile number
  - birthday (entered as `MMDD`; generated from the `birthday` config)
  - Terms & Conditions agreed
  - marketing checkboxes left **unchecked**
- Checks Hut Rewards and records limited-time offers (offer name + expiry).
- Handles the Pizza Hut "Warm, web cookies" consent dialog if it appears.
- Keeps separate flows for account creation and promotion checking.

---

## Requirements

- Python 3.13+
- Chromium browser (installed via Playwright)
- A Gmail account that supports IMAP plus-addressing

---

## Setup

```powershell
cd C:\Users\mwang\Desktop\PROJECTS\PizzaBot

pip install -r requirements.txt
python -m playwright install chromium
```

Create your config:

```powershell
python PizzaBot.py init-config
```

This writes `config.json`. It will ask for:

- target account pool size
- promotion check frequency (days)
- default first/last name
- birthday rule (`YYYY-MM-DD` fixed date or `next_month:START-END`)
- phone area code (used to generate random phone numbers, or a fixed phone)
- base Gmail address
- IMAP host / username / app password
- headed or headless browser mode

`config.json` and `pizzabot.db` are gitignored. Don’t commit your IMAP
password.

The default birthday config is `next_month:1-10`. It generates a random day
from the first 10 days of the month immediately following the current month.
For example, if today is in August it generates `YYYY-09-01` through
`YYYY-09-10`. Set a fixed `YYYY-MM-DD` value to keep the previous behavior.

---

## Usage

### `python PizzaBot.py create [--count N] [--timeout SECONDS]`

Runs the full account-creation flow:

1. Open `/login`
2. Accept cookies if the "Warm, web cookies" dialog appears
3. Enter the generated `base+N@gmail.com` email
4. Wait for the **"Log in to Your Pizza Hut Account"** email
5. Open the Login link from the email
6. If redirected to `/complete-profile`, fill:
   - first name
   - last name
   - mobile number
   - birthday (`MMDD`; generated from the `birthday` config)
   - accept Terms & Conditions
   - leave marketing unchecked
7. Mark the account as verified

Example:

```powershell
python PizzaBot.py create --count 3 --timeout 180
```

### `python PizzaBot.py verify [--ids 1,2] [--timeout SECONDS]`

Re-runs the login/email flow for accounts still in `created` or
`manual_review` status, and completes their profile if needed.

### `python PizzaBot.py check-promos [--ids 1,2] [--timeout SECONDS]`

Checks verified accounts for Hut Rewards limited-time offers:

1. Open `/login`
2. Accept cookies if needed
3. Enter the account email
4. Wait for the **"Log in to Your Pizza Hut Account"** email
5. Open the Login link
6. Go to `/order/deals`
7. Click **View Profile** (or the profile icon/account control)
8. Click **Hut Rewards**
9. Wait for limited-time offers to load
10. Extract each offer’s name + expiry text

If **"Limited time offers"** is not present, the account is still considered
successfully checked with **zero offers** (not an error).

### `python PizzaBot.py run [--timeout SECONDS] [--loop]`

Runs `create` → `verify` → `check-promos`.

Use `--loop` to repeat forever, sleeping `promo_check_frequency_days` days
between runs.

### `python PizzaBot.py stats`

Prints a summary of the account pool:

```text
Total accounts: 7
Active (verified & promo unused): 1
Promotion used: 0
  manual_review: 6
  verified: 1
```

---

## How `+N` alias tracking works

Each generated account is stored in SQLite (`pizzabot.db`).

There are two tables:

- `accounts` — the live account rows
- `alias_tracker` — permanently records every used `+N` alias

Alias allocation always picks the **smallest unused positive number** and never
reuses a number, even if the account row is later deleted or fails partway.

Example:

```text
youraccount+1@gmail.com
youraccount+2@gmail.com
youraccount+3@gmail.com
...
```

---

## Project structure

```text
PizzaBot.py            # CLI entrypoint
pizzabot/
  browser.py           # Playwright browser session wrapper
  config.py            # config.json loader + interactive setup
  db.py                # SQLite schema + account pool helpers
  generate.py          # +N aliases, random birthdays, and random phone numbers
  mail.py              # IMAP polling + Pizza Hut email link extraction
  pizzahut.py          # Pizza Hut page flows (create + promotions)
tests/                 # unit tests
```

---

## Testing

Run the unit tests:

```powershell
python -m unittest discover -s tests -v
```

The current suite covers:

- `+N` alias generation
- random phone number generation
- SQLite alias tracking / duplicate prevention
- email link extraction

Live flows against pizzahut.ca should be tested separately:

```powershell
python PizzaBot.py create --count 1
python PizzaBot.py check-promos
```

---

## Disclaimer

This project is an automation tool for personal testing. Account creation and
promotion checking may break when Pizza Hut changes their site. Use at your own
risk.
