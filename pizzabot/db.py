"""SQLite persistence for generated Pizza Hut accounts."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ACTIVE_ACCOUNT_MIN_ID = 33


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
    last_action TEXT,
    promotion_name TEXT,
    promotion_status TEXT,
    promotion_expiry TEXT,
    promotion_used INTEGER NOT NULL DEFAULT 0,
    promotion_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS promotions (
    account_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT,
    expiry TEXT,
    PRIMARY KEY (account_id, position),
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
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
    _add_column_if_missing(conn, "accounts", "last_action", "TEXT")
    _add_column_if_missing(
        conn, "accounts", "promotion_count", "INTEGER NOT NULL DEFAULT 0"
    )
    # Older databases recorded only the first offer name. Preserve a useful
    # count for rows created before promotion_count existed.
    conn.execute(
        """
        UPDATE accounts
        SET promotion_count = 1
        WHERE promotion_name IS NOT NULL AND promotion_count = 0
        """
    )
    # Backfill the first stored offer from older account rows into the new
    # detailed promotions table. This preserves existing promo results for the
    # new `promos` command without requiring a fresh live promotion check.
    conn.execute(
        """
        INSERT OR IGNORE INTO promotions (account_id, position, name, status, expiry)
        SELECT id, 0, promotion_name, promotion_status, promotion_expiry
        FROM accounts
        WHERE promotion_name IS NOT NULL
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO alias_tracker (alias_number, email)
        SELECT id, email FROM accounts WHERE id IS NOT NULL
        """
    )
    conn.commit()
    return conn


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def next_alias_id(conn: sqlite3.Connection) -> int:
    """Return the smallest unused +N alias number.

    The alias_tracker table keeps every allocated number permanently, so
    an alias is never reused even if the account row is later removed.
    Old pre-ACTIVE_ACCOUNT_MIN_ID test accounts are no longer eligible for
    automated pool operations, so new allocations must also start at or
    above the active-pool minimum.
    """
    n = ACTIVE_ACCOUNT_MIN_ID
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
            created_at, check_promotions, last_action
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            account.get("last_action"),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_accounts(conn: sqlite3.Connection, statuses: Iterable[str] | None = None) -> list[sqlite3.Row]:
    where = ["id >= ?"]
    params: list[Any] = [ACTIVE_ACCOUNT_MIN_ID]
    if statuses is not None:
        statuses = tuple(statuses)
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        where.append(f"status IN ({placeholders})")
        params.extend(statuses)
    rows = conn.execute(
        f"SELECT * FROM accounts WHERE {' AND '.join(where)} ORDER BY id",
        params,
    ).fetchall()
    return list(rows)


def get_account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def count_active(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM accounts
        WHERE id >= ? AND status = 'verified' AND promotion_used = 0
        """,
        (ACTIVE_ACCOUNT_MIN_ID,),
    ).fetchone()
    return int(row["c"])


def get_unverified(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return get_accounts(conn, statuses=("created", "manual_review"))


def get_promo_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT * FROM accounts
        WHERE id >= ?
          AND status = 'verified'
          AND check_promotions = 1
          AND promotion_used = 0
        ORDER BY id
        """,
        (ACTIVE_ACCOUNT_MIN_ID,),
    ).fetchall()
    return list(rows)


def _offers_from_promo_args(
    *,
    name: str | None,
    status: str | None,
    expiry: str | None,
    offers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if offers is not None:
        return list(offers)
    if not name:
        return []
    return [{"name": name, "status": status or "active", "expiry": expiry}]


def replace_promotions(
    conn: sqlite3.Connection,
    account_id: int,
    offers: list[dict[str, Any]],
) -> None:
    """Replace the detailed stored offers for one account.

    The caller is responsible for deciding when a promotion check result should
    clear old stored offers. ``check-promos`` passes the complete current offer
    list on every successful check.
    """
    conn.execute("DELETE FROM promotions WHERE account_id = ?", (account_id,))
    for position, offer in enumerate(offers):
        conn.execute(
            """
            INSERT INTO promotions (account_id, position, name, status, expiry)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                account_id,
                position,
                offer.get("name") or "",
                offer.get("status"),
                offer.get("expiry"),
            ),
        )
    conn.commit()


def get_promotions(
    conn: sqlite3.Connection,
    account_ids: Iterable[int] | None = None,
) -> list[sqlite3.Row]:
    """Return stored promotions for active-pool accounts, ordered by account/position.

    ``account_ids`` is optional. When omitted, promotions for every active-pool
    account are returned. Account IDs below ``ACTIVE_ACCOUNT_MIN_ID`` are always
    excluded, matching every other active-pool query.
    """
    where = ["a.id >= ?"]
    params: list[Any] = [ACTIVE_ACCOUNT_MIN_ID]
    if account_ids is not None:
        account_ids = tuple(account_ids)
        if not account_ids:
            return []
        placeholders = ",".join("?" for _ in account_ids)
        where.append(f"p.account_id IN ({placeholders})")
        params.extend(account_ids)

    rows = conn.execute(
        f"""
        SELECT p.*, a.email, a.status AS account_status
        FROM promotions p
        JOIN accounts a ON a.id = p.account_id
        WHERE {' AND '.join(where)}
        ORDER BY p.account_id, p.position
        """,
        params,
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


def mark_action(conn: sqlite3.Connection, account_id: int, action: str) -> None:
    """Record the latest non-sensitive flow stage for the stats dashboard."""
    conn.execute(
        "UPDATE accounts SET last_action = ? WHERE id = ?",
        (action, account_id),
    )
    conn.commit()


def mark_error(conn: sqlite3.Connection, account_id: int, error: Exception | str) -> None:
    """Store a concise error description without changing account status."""
    message = str(error).replace("\r", " ").replace("\n", " ")
    mark_action(conn, account_id, f"error: {message[:200]}")


def mark_promo(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    name: str | None,
    status: str | None = "active",
    expiry: str | None = None,
    used: bool = False,
    count: int | None = None,
    offers: list[dict[str, Any]] | None = None,
) -> None:
    stored_offers = _offers_from_promo_args(
        name=name,
        status=status,
        expiry=expiry,
        offers=offers,
    )
    first_offer = stored_offers[0] if stored_offers else None
    summary_name = first_offer.get("name") if first_offer else None
    summary_status = (first_offer.get("status") or status) if first_offer else status
    summary_expiry = (first_offer.get("expiry") or expiry) if first_offer else expiry
    if count is None:
        count = len(stored_offers) if stored_offers else (1 if name else 0)
    if offers is not None:
        count = len(stored_offers)
    conn.execute(
        """
        UPDATE accounts
        SET promotion_name = ?,
            promotion_status = ?,
            promotion_expiry = ?,
            promotion_used = ?,
            last_promo_checked_at = ?,
            promotion_count = ?
        WHERE id = ?
        """,
        (
            summary_name,
            summary_status,
            summary_expiry,
            int(used),
            utc_now_iso(),
            int(count),
            account_id,
        ),
    )
    replace_promotions(conn, account_id, stored_offers)
    conn.commit()


def mark_promo_checked(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute(
        "UPDATE accounts SET last_promo_checked_at = ? WHERE id = ?",
        (utc_now_iso(), account_id),
    )
    conn.commit()
