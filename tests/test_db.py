import os
import sqlite3
import unittest
from pathlib import Path

from pizzabot import db


class TestDb(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("test_pizzabot_tmp.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.conn = db.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        try:
            self.db_path.unlink()
        except OSError:
            pass

    def test_add_and_count_active(self):
        acct = {
            "id": db.next_alias_id(self.conn),
            "email": "test+1@gmail.com",
            "first_name": "A",
            "last_name": "B",
            "birthday": "2000-01-01",
            "phone": "4165551234",
            "status": "verified",
        }
        db.upsert_account(self.conn, acct)
        self.assertEqual(db.count_active(self.conn), 1)
        self.assertEqual(len(db.get_accounts(self.conn)), 1)

    def test_next_alias_keeps_incrementing(self):
        first = db.next_alias_id(self.conn)
        db.upsert_account(
            self.conn,
            {
                "id": first,
                "email": "test+1@gmail.com",
                "first_name": "A",
                "last_name": "B",
            },
        )
        self.assertEqual(db.next_alias_id(self.conn), first + 1)

    def test_mark_verified(self):
        row = {
            "id": db.next_alias_id(self.conn),
            "email": "test+1@gmail.com",
            "first_name": "A",
            "last_name": "B",
        }
        db.upsert_account(self.conn, row)
        db.mark_verified(self.conn, row["id"])
        self.assertEqual(db.get_account(self.conn, row["id"])["status"], "verified")

    def test_alias_numbers_are_sequential_and_never_reused(self):
        for expected in (
            db.ACTIVE_ACCOUNT_MIN_ID,
            db.ACTIVE_ACCOUNT_MIN_ID + 1,
            db.ACTIVE_ACCOUNT_MIN_ID + 2,
        ):
            n = db.next_alias_id(self.conn)
            self.assertEqual(n, expected)
            db.upsert_account(
                self.conn,
                {
                    "id": n,
                    "email": f"test+{n}@gmail.com",
                    "first_name": "A",
                    "last_name": "B",
                },
            )

        # Even if an account row is deleted, its alias stays tracked.
        self.conn.execute(
            "DELETE FROM accounts WHERE id = ?",
            (db.ACTIVE_ACCOUNT_MIN_ID + 1,),
        )
        self.conn.commit()
        self.assertEqual(db.next_alias_id(self.conn), db.ACTIVE_ACCOUNT_MIN_ID + 3)

    def test_active_pool_excludes_accounts_below_minimum_id(self):
        db.upsert_account(
            self.conn,
            {
                "id": 32,
                "email": "test+32@gmail.com",
                "first_name": "Old",
                "last_name": "Account",
                "status": "verified",
            },
        )
        db.upsert_account(
            self.conn,
            {
                "id": db.ACTIVE_ACCOUNT_MIN_ID,
                "email": "test+33@gmail.com",
                "first_name": "Active",
                "last_name": "Account",
                "status": "verified",
            },
        )

        self.assertEqual(
            [row["id"] for row in db.get_accounts(self.conn)],
            [db.ACTIVE_ACCOUNT_MIN_ID],
        )
        self.assertEqual(db.count_active(self.conn), 1)
        self.assertEqual(
            [row["id"] for row in db.get_promo_accounts(self.conn)],
            [db.ACTIVE_ACCOUNT_MIN_ID],
        )

    def test_active_pool_includes_accounts_at_and_above_minimum_id(self):
        active_above = db.ACTIVE_ACCOUNT_MIN_ID + 1
        used_account = db.ACTIVE_ACCOUNT_MIN_ID

        db.upsert_account(
            self.conn,
            {
                "id": used_account,
                "email": "test+33@gmail.com",
                "first_name": "Active",
                "last_name": "Account",
                "status": "verified",
            },
        )
        db.upsert_account(
            self.conn,
            {
                "id": active_above,
                "email": "test+34@gmail.com",
                "first_name": "Eligible",
                "last_name": "Account",
                "status": "verified",
                "check_promotions": True,
            },
        )
        db.mark_promo(
            self.conn,
            used_account,
            name="Old Offer",
            status="active",
            expiry="Expires in 1 day",
            used=True,
            count=1,
        )

        self.assertEqual(db.count_active(self.conn), 1)
        self.assertEqual(
            [row["id"] for row in db.get_promo_accounts(self.conn)],
            [active_above],
        )
        self.assertEqual(
            [row["id"] for row in db.get_accounts(self.conn)],
            [used_account, active_above],
        )

    def test_duplicate_email_raises(self):
        row_one = {
            "id": 1,
            "email": "test+1@gmail.com",
            "first_name": "A",
            "last_name": "B",
        }
        db.upsert_account(self.conn, row_one)
        with self.assertRaises(sqlite3.IntegrityError):
            db.upsert_account(
                self.conn,
                {
                    "id": 2,
                    "email": "test+1@gmail.com",
                    "first_name": "A",
                    "last_name": "B",
                },
            )

    def test_mark_promo_persists_every_offer_in_detailed_table(self):
        account_id = db.ACTIVE_ACCOUNT_MIN_ID
        db.upsert_account(
            self.conn,
            {
                "id": account_id,
                "email": "test+33@gmail.com",
                "first_name": "First",
                "last_name": "Last",
                "status": "verified",
            },
        )

        db.mark_promo(
            self.conn,
            account_id,
            name="First Summary Offer",
            status="active",
            expiry="Expires in 1 day!",
            offers=[
                {
                    "name": "First Summary Offer",
                    "status": "active",
                    "expiry": "Expires in 1 day!",
                },
                {
                    "name": "Second Offer",
                    "status": "active",
                    "expiry": "Expiring in 4 days!",
                },
            ],
        )

        rows = db.get_promotions(self.conn, account_ids=[account_id])
        self.assertEqual(
            [row["name"] for row in rows],
            ["First Summary Offer", "Second Offer"],
        )
        self.assertEqual(
            [row["expiry"] for row in rows],
            ["Expires in 1 day!", "Expiring in 4 days!"],
        )

        account = db.get_account(self.conn, account_id)
        self.assertEqual(account["promotion_count"], 2)
        self.assertEqual(account["promotion_name"], "First Summary Offer")

    def test_zero_offer_promo_clears_detailed_offers(self):
        account_id = db.ACTIVE_ACCOUNT_MIN_ID
        db.upsert_account(
            self.conn,
            {
                "id": account_id,
                "email": "test+33@gmail.com",
                "first_name": "First",
                "last_name": "Last",
                "status": "verified",
            },
        )
        db.mark_promo(
            self.conn,
            account_id,
            name="Old Offer",
            expiry="Expires in 1 day!",
        )
        self.assertEqual(len(db.get_promotions(self.conn, account_ids=[account_id])), 1)

        db.mark_promo(self.conn, account_id, name=None, status=None, expiry=None)

        self.assertEqual(db.get_promotions(self.conn, account_ids=[account_id]), [])
        account = db.get_account(self.conn, account_id)
        self.assertIsNone(account["promotion_name"])
        self.assertEqual(account["promotion_count"], 0)

    def test_promotions_respect_active_pool_minimum(self):
        old_id = db.ACTIVE_ACCOUNT_MIN_ID - 1
        active_id = db.ACTIVE_ACCOUNT_MIN_ID
        db.upsert_account(
            self.conn,
            {"id": old_id, "email": "old+32@gmail.com", "first_name": "Old", "last_name": "Account"},
        )
        db.upsert_account(
            self.conn,
            {"id": active_id, "email": "active+33@gmail.com", "first_name": "Active", "last_name": "Account"},
        )
        db.mark_promo(
            self.conn,
            old_id,
            name="Old Offer",
            expiry="Expires in 1 day!",
        )
        db.mark_promo(
            self.conn,
            active_id,
            name="Active Offer",
            expiry="Expires in 2 days!",
        )

        rows = db.get_promotions(self.conn)
        self.assertEqual([row["account_id"] for row in rows], [active_id])
        self.assertEqual([row["name"] for row in rows], ["Active Offer"])


if __name__ == "__main__":
    unittest.main()
