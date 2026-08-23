#!/usr/bin/env python3
"""Pizza Hut Canada account bot CLI.

Commands:
  init-config   Create or update config.json interactively.
  create        Top up the active account pool with new +n email accounts.
  verify        Read Pizza Hut email links from IMAP and click them.
  check-promos  Log into verified accounts and record active Hut Rewards offers.
  run           Run create -> verify -> check-promos (optionally on a loop).
  stats         Print a summary of the SQLite account pool.
"""

from __future__ import annotations

import argparse
import sys
import time
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

    p_run = sub.add_parser("run", help="Create, verify, and check promotions.")
    p_run.add_argument("--count", type=int, default=None, help="Override the number to create this run.")
    p_run.add_argument("--timeout", type=int, default=180, help="Seconds to wait for each email link.")
    p_run.add_argument("--headless", action="store_true", help="Run browser headless.")
    p_run.add_argument("--loop", action="store_true", help="Repeat based on promo_check_frequency_days.")

    sub.add_parser("stats", help="Show account pool summary.")
    return parser.parse_args(argv)


def _split_ids(raw: str) -> list[int] | None:
    if not raw.strip():
        return None
    return [int(x) for x in raw.split(",") if x.strip()]


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
            print(f"  Creating {email} ...")
            signup_profile = {
                "email": email,
                "first_name": profile["first_name"],
                "last_name": profile["last_name"],
                "birthday": profile["birthday"],
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
                    "birthday": profile["birthday"],
                    "phone": signup_profile["phone"],
                    "status": "created",
                },
            )
            try:
                pizzahut.login_with_email(
                    session, email, selectors, flow="create", stage="login"
                )
                print("    Waiting for verification email ...")
                link = pizzahut.wait_for_verification_email(
                    imap,
                    email,
                    timeout_seconds=getattr(args, "timeout", 180),
                )
                if link is None:
                    raise pizzahut.PizzahutError("No verification email received")
                print("    Opening verification login link ...")
                pizzahut.open_verification_login_link(session, link, selectors)
                pizzahut.complete_profile(session, signup_profile, selectors)
                db_mod.mark_verified(conn, alias_id)
                print(f"    Account created and verified for {email}.")
            except pizzahut.PizzahutError as exc:
                print(f"    Error: {exc}")
                db_mod.mark_status(conn, alias_id, "manual_review")
                print(f"    Marked {email} as manual_review.")
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
            pizzahut.login_with_email(
                session, email, selectors, flow="verify", stage="login"
            )
            link = pizzahut.wait_for_verification_email(
                imap, email, timeout_seconds=args.timeout
            )
            if not link:
                print(f"  No verification email received for {email}; leaving status unchanged.")
                continue
            pizzahut.open_verification_login_link(session, link, selectors)
            current_url = session.page.url
            if "/complete-profile" in current_url:
                pizzahut.complete_profile(session, profile_for_verify, selectors)
            elif "/order/deals" not in current_url:
                print(f"  Unexpected landing URL: {current_url}; marking manual_review.")
                db_mod.mark_status(conn, row["id"], "manual_review")
                continue
            db_mod.mark_verified(conn, row["id"])
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
            pizzahut.login_with_email(
                session, email, selectors, flow="promo", stage="login"
            )
            link = pizzahut.wait_for_verification_email(
                imap, email, timeout_seconds=args.timeout
            )
            if not link:
                print(f"  No sign-in link received for {email}; skipping promo check.")
                continue
            pizzahut.open_verification_login_link(session, link, selectors)
            offers = pizzahut.check_promotion(session, selectors)
            if offers:
                offer = offers[0]
                db_mod.mark_promo(
                    conn,
                    row["id"],
                    name=offer["name"],
                    status=offer["status"],
                    expiry=offer["expiry"],
                )
                print(f"  Found {len(offers)} limited-time offer(s):")
                for item in offers:
                    print(f"    {item['name']} ({item['expiry']})")
            else:
                db_mod.mark_promo(conn, row["id"], name=None, status=None, expiry=None)
                print(f"  No limited-time offers detected for {email} (valid result).")
    conn.close()
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
    print(f"Total accounts: {len(all_accounts)}")
    print(f"Active (verified & promo unused): {db_mod.count_active(conn)}")
    print(f"Promotion used: {used}")
    for status, count in sorted(statuses.items()):
        print(f"  {status}: {count}")
    conn.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    handlers = {
        "init-config": cmd_init_config,
        "create": cmd_create,
        "verify": cmd_verify,
        "check-promos": cmd_check_promos,
        "run": cmd_run,
        "stats": cmd_stats,
    }
    return handlers[args.command](args) or 0


if __name__ == "__main__":
    sys.exit(main())
