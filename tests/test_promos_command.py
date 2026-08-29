import argparse
import contextlib
import io
import unittest
from pathlib import Path

from PizzaBot import cmd_promos
from pizzabot import db


class TestPromosCommand(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("test_pizzabot_promos.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.conn = db.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        if self.db_path.exists():
            self.db_path.unlink()

    def _add_account(self, account_id: int, email: str) -> None:
        db.upsert_account(
            self.conn,
            {
                "id": account_id,
                "email": email,
                "first_name": "First",
                "last_name": "Last",
                "status": "verified",
            },
        )

    def _run(self, ids: str = "") -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cmd_promos(argparse.Namespace(db=self.db_path, ids=ids))
        return output.getvalue()

    def test_prints_multiple_stored_promos_for_selected_ids(self):
        account_id = db.ACTIVE_ACCOUNT_MIN_ID
        self._add_account(account_id, "youraccount+33@gmail.com")
        db.mark_promo(
            self.conn,
            account_id,
            name="Offer One",
            status="active",
            expiry="Expires in 2 days!",
            offers=[
                {
                    "name": "Offer One",
                    "status": "active",
                    "expiry": "Expires in 2 days!",
                },
                {
                    "name": "Offer Two",
                    "status": "active",
                    "expiry": "Expiring in 5 days!",
                },
            ],
        )

        text = self._run("33")

        self.assertIn("Promos for youraccount+33@gmail.com:", text)
        self.assertIn("  Offer One\n    Expires in 2 days!", text)
        self.assertIn("  Offer Two\n    Expiring in 5 days!", text)
        self.assertNotIn("No stored promos.", text)

    def test_prints_clear_message_when_account_has_no_stored_promos(self):
        account_id = db.ACTIVE_ACCOUNT_MIN_ID
        self._add_account(account_id, "youraccount+33@gmail.com")

        text = self._run("33")

        self.assertIn("Promos for youraccount+33@gmail.com:", text)
        self.assertIn("No stored promos.", text)

    def test_defaults_to_all_active_pool_accounts(self):
        first_id = db.ACTIVE_ACCOUNT_MIN_ID
        second_id = db.ACTIVE_ACCOUNT_MIN_ID + 1
        self._add_account(first_id, "youraccount+33@gmail.com")
        self._add_account(second_id, "youraccount+34@gmail.com")
        db.mark_promo(
            self.conn,
            first_id,
            name="Offer A",
            expiry="Expires in 3 days!",
        )

        text = self._run()

        self.assertIn("Promos for youraccount+33@gmail.com:", text)
        self.assertIn("  Offer A\n    Expires in 3 days!", text)
        self.assertIn("Promos for youraccount+34@gmail.com:", text)
        self.assertIn("No stored promos.", text)

    def test_excludes_accounts_below_active_pool_minimum(self):
        old_id = db.ACTIVE_ACCOUNT_MIN_ID - 1
        self._add_account(old_id, "old+32@gmail.com")
        db.mark_promo(
            self.conn,
            old_id,
            name="Old Hidden Offer",
            expiry="Expires in 1 day!",
        )

        text = self._run("32")

        self.assertIn("No matching active accounts.", text)
        self.assertNotIn("old+32@gmail.com", text)
        self.assertNotIn("Old Hidden Offer", text)

    def test_no_matching_active_accounts_prints_message(self):
        text = self._run("33,34")
        self.assertEqual(text.strip(), "No matching active accounts.")


if __name__ == "__main__":
    unittest.main()
