import argparse
import contextlib
import io
import sqlite3
import unittest
from pathlib import Path

from PizzaBot import _display_action, cmd_stats
from pizzabot import db


class TestStatsDashboard(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("test_pizzabot_stats.db")
        if self.db_path.exists():
            self.db_path.unlink()

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_connect_adds_dashboard_fields_to_legacy_database(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE accounts (
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
            CREATE TABLE alias_tracker (
                alias_number INTEGER PRIMARY KEY,
                email TEXT UNIQUE NOT NULL
            );
            INSERT INTO accounts (
                id, email, first_name, last_name, status,
                last_promo_checked_at, promotion_name, promotion_status
            ) VALUES (
                1, 'test+1@gmail.com', 'First', 'Last', 'verified',
                '2026-08-23T19:54:29+00:00', 'Old Offer', 'active'
            );
            """
        )
        conn.commit()
        conn.close()

        conn = db.connect(self.db_path)
        row = db.get_account(conn, 1)
        conn.close()

        self.assertEqual(row["last_action"], None)
        self.assertEqual(row["promotion_count"], 1)

    def test_mark_error_truncates_long_messages(self):
        conn = db.connect(self.db_path)
        account_id = db.next_alias_id(conn)
        db.upsert_account(
            conn,
            {
                "id": account_id,
                "email": "test+1@gmail.com",
                "first_name": "First",
                "last_name": "Last",
            },
        )
        db.mark_error(conn, account_id, RuntimeError("x" * 500))
        action = db.get_account(conn, account_id)["last_action"]
        conn.close()

        self.assertTrue(action.startswith("error: xxx"))
        self.assertLessEqual(len(action), len("error: ") + 200)

    def test_stats_renders_account_rows_and_progress(self):
        conn = db.connect(self.db_path)
        account_id = db.next_alias_id(conn)
        db.upsert_account(
            conn,
            {
                "id": account_id,
                "email": "youraccount+7@gmail.com",
                "first_name": "First",
                "last_name": "Last",
                "status": "verified",
                "created_at": "2026-08-23T15:46:26+00:00",
                "verified_at": "2026-08-23T15:47:30+00:00",
                "last_action": "created",
            },
        )
        db.mark_promo(
            conn,
            account_id,
            name=None,
            status=None,
            expiry=None,
            count=0,
        )
        db.mark_action(conn, account_id, "promo_checked")
        conn.close()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cmd_stats(argparse.Namespace(db=self.db_path))
        text = output.getvalue()

        self.assertIn("PizzaBot Account Status", text)
        self.assertIn("Total: 1 | Active: 1 | Verified: 1", text)
        self.assertIn("EMAIL", text)
        self.assertIn("LAST ACTION", text)
        self.assertIn("youraccount+7@gmail.com", text)
        self.assertIn("Promo checked", text)

    def test_stats_renders_stored_birthday_as_mm_dd(self):
        conn = db.connect(self.db_path)
        account_id = db.ACTIVE_ACCOUNT_MIN_ID
        db.upsert_account(
            conn,
            {
                "id": account_id,
                "email": "youraccount+33@gmail.com",
                "first_name": "First",
                "last_name": "Last",
                "birthday": "2026-09-07",
                "status": "verified",
            },
        )
        db.mark_promo(
            conn,
            account_id,
            name=None,
            status=None,
            expiry=None,
            count=0,
        )
        db.mark_action(conn, account_id, "promo_checked")
        conn.close()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cmd_stats(argparse.Namespace(db=self.db_path))
        text = output.getvalue()

        self.assertIn("BIRTHDAY", text)
        self.assertIn("09-07", text)
        self.assertNotIn("2026-09-07", text)

    def test_stats_excludes_accounts_below_minimum_id_from_rows(self):
        conn = db.connect(self.db_path)
        db.upsert_account(
            conn,
            {
                "id": 32,
                "email": "old+32@gmail.com",
                "first_name": "Old",
                "last_name": "Account",
                "status": "verified",
            },
        )
        active_id = db.ACTIVE_ACCOUNT_MIN_ID
        db.upsert_account(
            conn,
            {
                "id": active_id,
                "email": "active+33@gmail.com",
                "first_name": "Active",
                "last_name": "Account",
                "status": "verified",
            },
        )
        conn.close()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cmd_stats(argparse.Namespace(db=self.db_path))
        text = output.getvalue()

        self.assertIn("active+33@gmail.com", text)
        self.assertNotIn("old+32@gmail.com", text)
        self.assertIn("Total: 1 | Active: 1 | Verified: 1", text)

    def test_stats_aggregates_respect_active_pool_filter(self):
        conn = db.connect(self.db_path)
        # Old verified/used account must not contribute to any aggregate.
        db.upsert_account(
            conn,
            {
                "id": 32,
                "email": "old+32@gmail.com",
                "first_name": "Old",
                "last_name": "Account",
                "status": "verified",
            },
        )
        db.mark_promo(
            conn,
            32,
            name="Old Offer",
            status="active",
            expiry="Expires in 1 day",
            used=True,
            count=1,
        )

        active_id = db.ACTIVE_ACCOUNT_MIN_ID
        db.upsert_account(
            conn,
            {
                "id": active_id,
                "email": "active+33@gmail.com",
                "first_name": "Active",
                "last_name": "Account",
                "status": "verified",
            },
        )
        db.upsert_account(
            conn,
            {
                "id": active_id + 1,
                "email": "manual+34@gmail.com",
                "first_name": "Manual",
                "last_name": "Account",
                "status": "manual_review",
            },
        )
        conn.close()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cmd_stats(argparse.Namespace(db=self.db_path))
        text = output.getvalue()

        self.assertIn(
            "Total: 2 | Active: 1 | Verified: 1 | Manual Review: 1 | Promo Used: 0",
            text,
        )
        self.assertNotIn("old+32@gmail.com", text)

    def test_display_action_is_human_readable(self):
        self.assertEqual(_display_action("waiting_for_verification"), "Waiting for email")
        self.assertEqual(_display_action("error: birthday mismatch"), "Error: birthday mismatch")
        self.assertEqual(_display_action(None), "Unknown")


if __name__ == "__main__":
    unittest.main()
