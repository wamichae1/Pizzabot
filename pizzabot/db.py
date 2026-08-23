"""SQLite persistence for generated Pizza Hut accounts."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    birthday TEXT,
    phone TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT,
    verified_at TEXT,
    check_promotions INTEGER NOT NULL DEFAULT 1,
    last_promo_checked_at TEXT,
    promotion_name TEXT,
    promotion_status TEXT,
    promotion_expiry TEXT,
    promotion_used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alias_tracker (
    alias_number INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | str = "pizzabot.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT OR IGNORE INTO alias_tracker (alias_number, email)
        SELECT id, email FROM accounts WHERE id IS NOT NULL
        """
    )
    conn.commit()
    return conn


def next_alias_id(conn: sqlite3.Connection) -> int:
    """Return the smallest unused +N alias number.

    The alias_tracker table keeps every allocated number permanently, so
    an alias is never reused even if the account row is later removed.
    """
    n = 1
    while conn.execute(
        """
        SELECT 1
        FROM accounts
        WHERE id = ?
        UNION
        SELECT 1
        FROM alias_tracker
        WHERE alias_number = ?
        """,
        (n, n),
    ).fetchone():
        n += 1
    return n


def register_alias(conn: sqlite3.Connection, alias_number: int, email: str) -> None:
    """Persist an allocated alias number so it can never be reused."""
    conn.execute(
        "INSERT OR IGNORE INTO alias_tracker (alias_number, email) VALUES (?, ?)",
        (alias_number, email),
    )
    conn.commit()


def upsert_account(conn: sqlite3.Connection, account: dict[str, Any]) -> int:
    """Insert a new account. Returns the account id."""
    register_alias(conn, account["id"], account["email"])
    cur = conn.execute(
        """
        INSERT INTO accounts (
            id, email, first_name, last_name, birthday, phone, status,
            created_at, check_promotions
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account["id"],
            account["email"],
            account["first_name"],
            account["last_name"],
            account.get("birthday", ""),
            account.get("phone", ""),
            account.get("status", "created"),
            account.get("created_at") or utc_now_iso(),
            int(account.get("check_promotions", True)),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_accounts(conn: sqlite3.Connection, statuses: Iterable[str] | None = None) -> list[sqlite3.Row]:
    if statuses is None:
        rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    else:
        placeholders = ",".join("?" for _ in statuses)
        rows = conn.execute(
            f"SELECT * FROM accounts WHERE status IN ({placeholders}) ORDER BY id",
            tuple(statuses),
        ).fetchall()
    return list(rows)


def get_account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def count_active(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM accounts WHERE status = 'verified' AND promotion_used = 0"
    ).fetchone()
    return int(row["c"])


def get_unverified(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return get_accounts(conn, statuses=("created", "manual_review"))


def get_promo_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT * FROM accounts
        WHERE status = 'verified' AND check_promotions = 1 AND promotion_used = 0
        ORDER BY id
        """
    ).fetchall()
    return list(rows)


def mark_verified(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute(
        "UPDATE accounts SET status = 'verified', verified_at = ? WHERE id = ?",
        (utc_now_iso(), account_id),
    )
    conn.commit()


def mark_status(conn: sqlite3.Connection, account_id: int, status: str) -> None:
    conn.execute("UPDATE accounts SET status = ? WHERE id = ?", (status, account_id))
    conn.commit()


def mark_promo(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    name: str | None,
    status: str | None = "active",
    expiry: str | None = None,
    used: bool = False,
) -> None:
    conn.execute(
        """
        UPDATE accounts
        SET promotion_name = ?,
            promotion_status = ?,
            promotion_expiry = ?,
            promotion_used = ?,
            last_promo_checked_at = ?
        WHERE id = ?
        """,
        (name, status, expiry, int(used), utc_now_iso(), account_id),
    )
    conn.commit()


def mark_promo_checked(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute(
        "UPDATE accounts SET last_promo_checked_at = ? WHERE id = ?",
        (utc_now_iso(), account_id),
    )
    conn.commit()
