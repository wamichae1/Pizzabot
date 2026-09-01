#!/usr/bin/env python3
"""Pizza Hut Canada account bot CLI.

Commands:
  init-config   Create or update config.json interactively.
  create        Top up the active account pool with new +n email accounts.
  verify        Read Pizza Hut email links from IMAP and click them.
  check-promos  Log into verified accounts and record active Hut Rewards offers.
  open-account  Submit one account's email login without running any further flow.
  run           Run create -> verify -> check-promos (optionally on a loop).
  stats         Print a summary of the SQLite account pool.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence

from pizzabot import config as config_mod
from pizzabot import db as db_mod
from pizzabot import generate as gen_mod


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="PizzaBot", description=__doc__)
    parser.add_argument("--config", default=str(config_mod.DEFAULT_CONFIG_PATH), help="Path to config.json.")
    parser.add_argument("--db", default=str(config_mod.DEFAULT_DB_PATH), help="Path to SQLite database.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-config", help="Create/update config.json interactively.")

    p_create = sub.add_parser("create", help="Create new Pizza Hut accounts.")
    p_create.add_argument("--count", type=int, default=None, help="Override the number to create this run.")
    p_create.add_argument("--timeout", type=int, default=180, help="Seconds to wait for each verification email.")
    p_create.add_argument("--headless", action="store_true", help="Run browser headless.")

    p_verify = sub.add_parser("verify", help="Verify unverified accounts via email links.")
    p_verify.add_argument("--ids", default="", help="Comma-separated account ids to verify; default all unverified.")
    p_verify.add_argument("--timeout", type=int, default=180, help="Seconds to wait for each email link.")
    p_verify.add_argument("--headless", action="store_true", help="Run browser headless.")

    p_promos = sub.add_parser("check-promos", help="Check Hut Rewards offers on verified accounts.")
    p_promos.add_argument("--ids", default="", help="Comma-separated account ids; default all promo-enabled accounts.")
    p_promos.add_argument("--timeout", type=int, default=180, help="Seconds to wait for each email link.")
    p_promos.add_argument("--headless", action="store_true", help="Run browser headless.")

    p_open = sub.add_parser("open-account", help="Open one account for manual inspection.")
    p_open.add_argument("--id", type=int, required=True, help="Account id to open.")
    p_open.add_argument("--timeout", type=int, default=180, help="Seconds to wait for the login email link.")
    p_open.add_argument("--headless", action="store_true", help="Run browser headless.")

    p_run = sub.add_parser("run", help="Create, verify, and check promotions.")
    p_run.add_argument("--count", type=int, default=None, help="Override the number to create this run.")
    p_run.add_argument("--timeout", type=int, default=180, help="Seconds to wait for each email link.")
    p_run.add_argument("--headless", action="store_true", help="Run browser headless.")
    p_run.add_argument("--loop", action="store_true", help="Repeat based on promo_check_frequency_days.")

    sub.add_parser("stats", help="Show account pool summary.")
    p_promos = sub.add_parser("promos", help="Show stored promos for active accounts.")
    p_promos.add_argument("--ids", default="", help="Comma-separated account ids; default all active accounts.")
    return parser.parse_args(argv)


def _split_ids(raw: str) -> list[int] | None:
    if not raw.strip():
        return None
    return [int(x) for x in raw.split(",") if x.strip()]


def _format_db_time(value: str | None) -> str:
    if not value:
        return "--"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return value


def _format_birthday(value: str | None) -> str:
    if not value:
        return "--"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "--"
    return parsed.strftime("%m-%d")


def _format_created_verified(row) -> str:
    created = _format_db_time(row["created_at"])
    verified = _format_db_time(row["verified_at"])
    try:
        same_day = (
            row["created_at"]
            and row["verified_at"]
            and _format_db_time(row["created_at"])[:5]
            == _format_db_time(row["verified_at"])[:5]
        )
        if same_day:
            verified_time = datetime.fromisoformat(
                row["verified_at"].replace("Z", "+00:00")
            )
            if verified_time.tzinfo is not None:
                verified_time = verified_time.astimezone()
            verified = verified_time.strftime("%H:%M")
    except (TypeError, ValueError):
        pass
    return f"{created} / {verified}"


def _display_action(action: str | None) -> str:
    if not action:
        return "Unknown"
    if action.lower().startswith("error:"):
        return "Error: " + action[6:].strip()
    labels = {
        "created": "Alias allocated",
        "login": "Login submitted",
        "waiting_for_verification": "Waiting for email",
        "verification_received": "Verification received",
        "complete_profile": "Complete profile",
        "first_name": "First name filled",
        "last_name": "Last name filled",
        "phone": "Phone filled",
        "birthday": "Birthday filled",
        "terms": "Terms checked",
        "account_created": "Account created",
        "deals_page": "Deals page",
        "account_page": "Account page",
        "hut_rewards": "Hut Rewards",
        "rewards_page": "Rewards page",
        "rewards_loaded": "Rewards loaded",
        "promo_checked": "Promo checked",
        "manual_review": "Manual review",
    }
    return labels.get(action, action.replace("_", " ").title())


def _truncate(value: object, width: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= width else text[: max(1, width - 3)] + "..."


def _browser_config(cfg: dict, headless_flag: bool) -> dict:
    browser = cfg.get("browser", {})
    return {
        "headless": bool(headless_flag) or bool(browser.get("headless", False)),
        "slow_mo_ms": int(browser.get("slow_mo_ms", 300)),
    }


def cmd_init_config(args: argparse.Namespace) -> int:
    config_mod.interactive_init(args.config)
    print("Config saved.")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    from pizzabot.browser import BrowserSession
    from pizzabot import pizzahut

    cfg = config_mod.load_config(args.config)
    profile = config_mod.ensure_base_profile(cfg, args.config)
    imap = config_mod.require_imap_config(cfg)
    conn = db_mod.connect(args.db)
    selectors = cfg.get("selectors", {})

    active = db_mod.count_active(conn)
    needed = cfg["target_account_pool"] - active
    if args.count is not None:
        needed = args.count
    if needed <= 0:
        print(f"Active pool is already at target ({active}). Use --count to force create.")
        conn.close()
        return 0

    print(f"Creating {needed} account(s).")
    browser_opts = _browser_config(cfg, args.headless)
    with BrowserSession(**browser_opts) as session:
        for _ in range(needed):
            alias_id = db_mod.next_alias_id(conn)
            email = gen_mod.make_alias(profile["base_email"], alias_id)
            birthday = gen_mod.generate_birthday(profile)
            print(f"  Creating {email} ...")
            signup_profile = {
                "email": email,
                "first_name": profile["first_name"],
                "last_name": profile["last_name"],
                "birthday": birthday,
                "phone": gen_mod.generate_phone(profile),
            }
            # Insert the row up front so the alias is tracked even if a later
            # stage fails. Mistakes get status manual_review.
            db_mod.upsert_account(
                conn,
                {
                    "id": alias_id,
                    "email": email,
                    "first_name": profile["first_name"],
                    "last_name": profile["last_name"],
                    "birthday": birthday,
                    "phone": signup_profile["phone"],
                    "status": "created",
                    "last_action": "created",
                },
            )
            try:
                db_mod.mark_action(conn, alias_id, "login")
                pizzahut.login_with_email(
                    session, email, selectors, flow="create", stage="login"
                )
                print("    Waiting for verification email ...")
                db_mod.mark_action(conn, alias_id, "waiting_for_verification")
                link = pizzahut.wait_for_verification_email(
                    imap,
                    email,
                    timeout_seconds=getattr(args, "timeout", 180),
                )
                if link is None:
                    raise pizzahut.PizzahutError("No verification email received")
                db_mod.mark_action(conn, alias_id, "verification_received")
                print("    Opening verification login link ...")
                pizzahut.open_verification_login_link(session, link, selectors)
                pizzahut.complete_profile(
                    session,
                    signup_profile,
                    selectors,
                    report_stage=lambda stage: db_mod.mark_action(conn, alias_id, stage),
                )
                db_mod.mark_verified(conn, alias_id)
                print(f"    Account created and verified for {email}.")
            except pizzahut.PizzahutError as exc:
                print(f"    Error: {exc}")
                db_mod.mark_error(conn, alias_id, exc)
                db_mod.mark_status(conn, alias_id, "manual_review")
                print(f"    Marked {email} as manual_review.")
            except Exception as exc:
                db_mod.mark_error(conn, alias_id, exc)
                raise
    conn.close()
    return 0


def _get_matching_account_ids(conn, ids: list[int] | None, default_statuses=None):
    if ids is not None:
        return [row for row in db_mod.get_accounts(conn) if row["id"] in ids]
    return db_mod.get_accounts(conn, statuses=default_statuses)


def cmd_verify(args: argparse.Namespace) -> int:
    from pizzabot.browser import BrowserSession
    from pizzabot import pizzahut

    cfg = config_mod.load_config(args.config)
    imap = config_mod.require_imap_config(cfg)
    conn = db_mod.connect(args.db)
    ids = _split_ids(getattr(args, "ids", ""))
    accounts = _get_matching_account_ids(conn, ids, default_statuses=("created", "manual_review"))

    if not accounts:
        print("No accounts need verification.")
        conn.close()
        return 0

    browser_opts = _browser_config(cfg, args.headless)
    selectors = cfg.get("selectors", {})
    with BrowserSession(**browser_opts) as session:
        for row in accounts:
            email = row["email"]
            profile_for_verify = {
                "email": email,
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "birthday": row["birthday"],
                "phone": row["phone"],
            }
            print(f"Verifying {email} ...")
            db_mod.mark_action(conn, row["id"], "login")
            pizzahut.login_with_email(
                session, email, selectors, flow="verify", stage="login"
            )
            db_mod.mark_action(conn, row["id"], "waiting_for_verification")
            link = pizzahut.wait_for_verification_email(
                imap, email, timeout_seconds=args.timeout
            )
            if not link:
                print(f"  No verification email received for {email}; leaving status unchanged.")
                continue
            db_mod.mark_action(conn, row["id"], "verification_received")
            pizzahut.open_verification_login_link(session, link, selectors)
            current_url = session.page.url
            if "/complete-profile" in current_url:
                pizzahut.complete_profile(
                    session,
                    profile_for_verify,
                    selectors,
                    report_stage=lambda stage, account_id=row["id"]: db_mod.mark_action(
                        conn, account_id, stage
                    ),
                )
            elif "/order/deals" not in current_url:
                print(f"  Unexpected landing URL: {current_url}; marking manual_review.")
                db_mod.mark_error(
                    conn,
                    row["id"],
                    f"Unexpected landing URL after verification: {current_url}",
                )
                db_mod.mark_status(conn, row["id"], "manual_review")
                continue
            db_mod.mark_verified(conn, row["id"])
            db_mod.mark_action(conn, row["id"], "account_created")
            print(f"  Marked {email} as verified.")
    conn.close()
    return 0


def cmd_check_promos(args: argparse.Namespace) -> int:
    from pizzabot.browser import BrowserSession
    from pizzabot import pizzahut

    cfg = config_mod.load_config(args.config)
    imap = config_mod.require_imap_config(cfg)
    conn = db_mod.connect(args.db)
    ids = _split_ids(getattr(args, "ids", ""))
    if ids is not None:
        accounts = [row for row in db_mod.get_accounts(conn) if row["id"] in ids]
    else:
        accounts = db_mod.get_promo_accounts(conn)

    if not accounts:
        print("No verified promo-enabled accounts to check.")
        conn.close()
        return 0

    selectors = cfg.get("selectors", {})
    browser_opts = _browser_config(cfg, args.headless)
    with BrowserSession(**browser_opts) as session:
        for row in accounts:
            email = row["email"]
            print(f"Checking promotions for {email} ...")
            try:
                db_mod.mark_action(conn, row["id"], "login")
                pizzahut.login_with_email(
                    session, email, selectors, flow="promo", stage="login"
                )
                db_mod.mark_action(conn, row["id"], "waiting_for_verification")
                link = pizzahut.wait_for_verification_email(
                    imap, email, timeout_seconds=args.timeout
                )
                if not link:
                    print(f"  No sign-in link received for {email}; skipping promo check.")
                    db_mod.mark_error(
                        conn, row["id"],
                        "No sign-in link received for verification/login",
                    )
                    continue
                db_mod.mark_action(conn, row["id"], "verification_received")
                pizzahut.open_verification_login_link(session, link, selectors)
                offers = pizzahut.check_promotion(
                    session,
                    selectors,
                    report_stage=lambda stage, account_id=row["id"]: db_mod.mark_action(
                        conn, account_id, stage
                    ),
                )
                if offers:
                    offer = offers[0]
                    db_mod.mark_promo(
                        conn,
                        row["id"],
                        name=offer["name"],
                        status=offer["status"],
                        expiry=offer["expiry"],
                        offers=offers,
                    )
                    print(f"  Found {len(offers)} limited-time offer(s):")
                    for item in offers:
                        print(f"    {item['name']} ({item['expiry']})")
                    db_mod.mark_action(conn, row["id"], "promo_checked")
                else:
                    db_mod.mark_promo(conn, row["id"], name=None, status=None, expiry=None)
                    db_mod.mark_action(conn, row["id"], "promo_checked")
                    print(f"  No limited-time offers detected for {email} (valid result).")
            except pizzahut.PizzahutError as exc:
                print(f"  Error: {exc}")
                db_mod.mark_error(conn, row["id"], exc)
            except Exception as exc:
                print(f"  Unexpected error: {exc}")
                db_mod.mark_error(conn, row["id"], exc)
    conn.close()
    return 0


def cmd_open_account(args: argparse.Namespace) -> int:
    from pizzabot.browser import BrowserSession
    from pizzabot import pizzahut

    cfg = config_mod.load_config(args.config)
    imap = config_mod.require_imap_config(cfg)
    conn = db_mod.connect(args.db)

    # Use the same active-pool filter as every other account operation. Rows
    # below ACTIVE_ACCOUNT_MIN_ID are intentionally invisible to automated
    # flows, so an explicit --id lower than the minimum is not allowed here.
    account = None
    for row in db_mod.get_accounts(conn):
        if row["id"] == args.id:
            account = row
            break
    conn.close()

    if account is None:
        if args.id < db_mod.ACTIVE_ACCOUNT_MIN_ID:
            print(
                f"Account ID {args.id} is outside the active account pool "
                f"(minimum ID {db_mod.ACTIVE_ACCOUNT_MIN_ID})."
            )
        else:
            print(f"No active-pool account with ID {args.id}.")
        return 0

    selectors = cfg.get("selectors", {})
    browser_opts = _browser_config(cfg, args.headless)
    # This diagnostic command intentionally does not use a context manager or
    # close the session at the end: the browser should remain available for
    # manual inspection.
    session = BrowserSession(**browser_opts)

    email = account["email"]
    print(f"Opening login for {email} ...")
    pizzahut.login_with_email(
        session, email, selectors, flow="promo", stage="login"
    )

    print("Waiting for Pizza Hut login email ...")
    link = pizzahut.wait_for_verification_email(
        imap,
        email,
        timeout_seconds=args.timeout,
    )
    if link is None:
        print("No Pizza Hut login email received; leaving the browser open for inspection.")
        print("Press Enter to close the browser...")
        input()
        session.close()
        return 0

    pizzahut.open_verification_login_link(session, link, selectors)

    # Match check-promos' first post-login wait: a valid login link eventually
    # lands on /order/deals. Keep this diagnostic non-fatal so it still prints
    # whatever URL the account actually reaches when authentication fails.
    try:
        pizzahut._wait_for_url_contains(
            session.page,
            "/order/deals",
            "promo",
            "deals_page",
            "open account verification link",
            timeout_seconds=8,
        )
    except pizzahut.PizzahutError:
        pass

    print(session.page.url)
    print("Press Enter to close the browser...")
    input()
    session.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    while True:
        cmd_create(args)
        cmd_verify(args)
        cmd_check_promos(args)
        if not args.loop:
            break
        cfg = config_mod.load_config(args.config)
        delay_days = max(1, int(cfg.get("promo_check_frequency_days", 1)))
        print(f"Sleeping {delay_days} day(s) before next run. Press Ctrl+C to stop.")
        time.sleep(delay_days * 86400)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    conn = db_mod.connect(args.db)
    all_accounts = db_mod.get_accounts(conn)
    from collections import Counter

    statuses = Counter(a["status"] for a in all_accounts)
    used = sum(1 for a in all_accounts if a["promotion_used"])

    summary_parts = [
        f"Total: {len(all_accounts)}",
        f"Active: {db_mod.count_active(conn)}",
        f"Verified: {statuses.get('verified', 0)}",
        f"Manual Review: {statuses.get('manual_review', 0)}",
        f"Promo Used: {used}",
    ]
    other_statuses = sorted(
        (status, count)
        for status, count in statuses.items()
        if status not in {"verified", "manual_review"}
    )
    for status, count in other_statuses:
        summary_parts.append(f"{status}: {count}")

    print("PizzaBot Account Status")
    print("-" * 100)
    print(" | ".join(summary_parts))
    print("-" * 100)
    print(
        f"{'ID':>2} {'EMAIL':<28} {'STATUS':<13} "
        f"{'BIRTHDAY':<10} "
        f"{'LAST ACTION':<22} {'PROMOS':>6}  {'LAST CHECK':<11} "
        f"{'CREATED / VERIFIED':<20}"
    )
    for row in all_accounts:
        checked = bool(row["last_promo_checked_at"])
        promos = str(row["promotion_count"]) if checked else "-"
        last_check = _format_db_time(row["last_promo_checked_at"]) if checked else "-"
        action = _display_action(row["last_action"])
        if row["last_action"] is None:
            if row["status"] == "manual_review":
                action = "Manual review"
            elif row["status"] == "verified":
                action = "Promo checked" if row["last_promo_checked_at"] else "Account created"
            elif row["status"] == "created":
                action = "Alias allocated"
        if row["last_action"] == "promo_checked" and row["promotion_status"]:
            action = f"{action} ({row['promotion_status']})"
        print(
            f"{row['id']:>2} {_truncate(row['email'], 28):<28} "
            f"{_truncate(row['status'], 13):<13} "
            f"{_format_birthday(row['birthday']):<10} "
            f"{_truncate(action, 22):<22} "
            f"{promos:>6}  {last_check:<11} "
            f"{_truncate(_format_created_verified(row), 20):<20}"
        )
    conn.close()
    return 0


def _print_stored_promos(account: sqlite3.Row, promotions: list[sqlite3.Row]) -> None:
    print(f"Promos for {account['email']}:")
    if not promotions:
        print("  No stored promos.")
        return
    for promo in promotions:
        print(f"  {promo['name']}")
        if promo["expiry"]:
            print(f"    {promo['expiry']}")


def cmd_promos(args: argparse.Namespace) -> int:
    """Print stored promotions for active-pool accounts without logging in."""
    conn = db_mod.connect(args.db)
    ids = _split_ids(getattr(args, "ids", ""))
    if ids is not None:
        accounts = [row for row in db_mod.get_accounts(conn) if row["id"] in ids]
    else:
        accounts = db_mod.get_accounts(conn)

    if not accounts:
        print("No matching active accounts.")
        conn.close()
        return 0

    account_ids = [row["id"] for row in accounts]
    promotions = db_mod.get_promotions(conn, account_ids=account_ids)
    promotions_by_account: dict[int, list[sqlite3.Row]] = {}
    for promo in promotions:
        promotions_by_account.setdefault(promo["account_id"], []).append(promo)

    for account in accounts:
        stored = promotions_by_account.get(account["id"], [])
        _print_stored_promos(account, stored)
        print()

    conn.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    handlers = {
        "init-config": cmd_init_config,
        "create": cmd_create,
        "verify": cmd_verify,
        "check-promos": cmd_check_promos,
        "open-account": cmd_open_account,
        "run": cmd_run,
        "stats": cmd_stats,
        "promos": cmd_promos,
    }
    return handlers[args.command](args) or 0


if __name__ == "__main__":
    sys.exit(main())
