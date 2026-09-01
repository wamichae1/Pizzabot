import argparse
import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PizzaBot import _parse_args, cmd_open_account
from pizzabot import db, pizzahut


class TestOpenAccountCommand(unittest.TestCase):
    def setUp(self):
        self.db_path = Path("test_pizzabot_open_account.db")
        if self.db_path.exists():
            self.db_path.unlink()
        self.conn = db.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        if self.db_path.exists():
            self.db_path.unlink()

    def _add_account(self, account_id: int, email: str, status: str = "verified") -> None:
        db.upsert_account(
            self.conn,
            {
                "id": account_id,
                "email": email,
                "first_name": "First",
                "last_name": "Last",
                "status": status,
            },
        )

    def _args(self, account_id: int, timeout: int = 17) -> argparse.Namespace:
        return argparse.Namespace(
            config="unused-config",
            db=self.db_path,
            id=account_id,
            timeout=timeout,
            headless=True,
        )

    def _run(self, args) -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cmd_open_account(args)
        return output.getvalue()

    def _input_assert_not_closed(self, session):
        def _input(*_args, **_kwargs):
            session.close.assert_not_called()
            return ""

        return _input

    def test_parse_open_account_requires_id(self):
        args = _parse_args(["open-account", "--id", "45"])

        self.assertEqual(args.command, "open-account")
        self.assertEqual(args.id, 45)
        self.assertEqual(args.timeout, 180)
        self.assertFalse(args.headless)

    def test_below_active_pool_minimum_is_rejected_without_browser(self):
        self._add_account(db.ACTIVE_ACCOUNT_MIN_ID - 1, "old+32@gmail.com")
        cfg = {"browser": {"headless": False, "slow_mo_ms": 0}, "selectors": {}}
        imap = {"host": "imap.example", "username": "u", "password": "p"}

        with patch("PizzaBot.config_mod.load_config", return_value=cfg), \
             patch("PizzaBot.config_mod.require_imap_config", return_value=imap), \
             patch("pizzabot.browser.BrowserSession") as browser_cls, \
             patch("pizzabot.pizzahut.login_with_email") as login, \
             patch("pizzabot.pizzahut.wait_for_verification_email") as wait:
            text = self._run(self._args(db.ACTIVE_ACCOUNT_MIN_ID - 1))

        self.assertIn("outside the active account pool", text)
        self.assertIn("minimum ID 33", text)
        browser_cls.assert_not_called()
        login.assert_not_called()
        wait.assert_not_called()

    def test_missing_active_pool_account_prints_message_without_browser(self):
        cfg = {"browser": {"headless": False, "slow_mo_ms": 0}, "selectors": {}}
        imap = {"host": "imap.example", "username": "u", "password": "p"}

        with patch("PizzaBot.config_mod.load_config", return_value=cfg), \
             patch("PizzaBot.config_mod.require_imap_config", return_value=imap), \
             patch("pizzabot.browser.BrowserSession") as browser_cls, \
             patch("pizzabot.pizzahut.login_with_email") as login, \
             patch("pizzabot.pizzahut.wait_for_verification_email") as wait:
            text = self._run(self._args(45))

        self.assertIn("No active-pool account with ID 45.", text)
        browser_cls.assert_not_called()
        login.assert_not_called()
        wait.assert_not_called()

    def test_submits_email_waits_for_link_opens_link_and_prints_current_url(self):
        account_id = 45
        email = "youraccount+45@gmail.com"
        self._add_account(account_id, email)

        cfg = {
            "browser": {"headless": False, "slow_mo_ms": 0},
            "selectors": {"login_email": "input[type='email']"},
        }
        imap = {"host": "imap.example", "username": "u", "password": "p"}
        link = "https://www.pizzahut.ca/login?token=abc"
        final_url = "https://www.pizzahut.ca/order/deals"

        session = Mock()
        session.page.url = link
        open_link = Mock(
            side_effect=lambda _session, _link, _selectors: setattr(
                _session.page, "url", final_url
            )
        )

        with patch("PizzaBot.config_mod.load_config", return_value=cfg), \
             patch("PizzaBot.config_mod.require_imap_config", return_value=imap), \
             patch("pizzabot.browser.BrowserSession", return_value=session) as browser_cls, \
             patch("pizzabot.pizzahut.login_with_email") as login, \
             patch("pizzabot.pizzahut.wait_for_verification_email", return_value=link) as wait, \
             patch("pizzabot.pizzahut.open_verification_login_link", open_link), \
             patch("pizzabot.pizzahut._wait_for_url_contains") as wait_url, \
             patch("builtins.input", side_effect=self._input_assert_not_closed(session)) as input_mock:
            text = self._run(self._args(account_id, timeout=23))

        self.assertIn(f"Opening login for {email} ...", text)
        self.assertIn(final_url, text)
        lines = text.splitlines()
        self.assertIn(final_url, lines)
        self.assertEqual(lines[-1], "Press Enter to close the browser...")
        browser_cls.assert_called_once_with(headless=True, slow_mo_ms=0)
        login.assert_called_once_with(
            session,
            email,
            cfg["selectors"],
            flow="promo",
            stage="login",
        )
        wait.assert_called_once_with(imap, email, timeout_seconds=23)
        open_link.assert_called_once_with(session, link, cfg["selectors"])
        wait_url.assert_called_once_with(
            session.page,
            "/order/deals",
            "promo",
            "deals_page",
            "open account verification link",
            timeout_seconds=8,
        )
        input_mock.assert_called_once_with()
        session.close.assert_called_once()

    def test_lands_on_root_still_prints_url_and_leaves_browser_open(self):
        account_id = 45
        email = "youraccount+45@gmail.com"
        self._add_account(account_id, email)

        cfg = {"browser": {"headless": False, "slow_mo_ms": 0}, "selectors": {}}
        imap = {"host": "imap.example", "username": "u", "password": "p"}
        link = "https://www.pizzahut.ca/login?token=old"
        final_url = "https://www.pizzahut.ca/"

        session = Mock()
        session.page.url = link
        open_link = Mock(
            side_effect=lambda _session, _link, _selectors: setattr(
                _session.page, "url", final_url
            )
        )
        wait_url = Mock(
            side_effect=pizzahut.PizzahutError(
                "[promo/deals_page] open account verification link "
                f"did not reach the expected URL. Current URL: {final_url}"
            )
        )

        with patch("PizzaBot.config_mod.load_config", return_value=cfg), \
             patch("PizzaBot.config_mod.require_imap_config", return_value=imap), \
             patch("pizzabot.browser.BrowserSession", return_value=session) as browser_cls, \
             patch("pizzabot.pizzahut.login_with_email") as login, \
             patch("pizzabot.pizzahut.wait_for_verification_email", return_value=link) as wait, \
             patch("pizzabot.pizzahut.open_verification_login_link", open_link), \
             patch("pizzabot.pizzahut._wait_for_url_contains", wait_url), \
             patch("builtins.input", side_effect=self._input_assert_not_closed(session)) as input_mock:
            text = self._run(self._args(account_id))

        self.assertIn(final_url, text)
        self.assertIn("Press Enter to close the browser...", text)
        lines = text.splitlines()
        self.assertIn(final_url, lines)
        self.assertEqual(lines[-1], "Press Enter to close the browser...")
        login.assert_called_once_with(
            session,
            email,
            cfg["selectors"],
            flow="promo",
            stage="login",
        )
        wait.assert_called_once_with(imap, email, timeout_seconds=17)
        open_link.assert_called_once_with(session, link, cfg["selectors"])
        wait_url.assert_called_once_with(
            session.page,
            "/order/deals",
            "promo",
            "deals_page",
            "open account verification link",
            timeout_seconds=8,
        )
        input_mock.assert_called_once_with()
        session.close.assert_called_once()

    def test_missing_login_link_prints_diagnostic_and_keeps_browser_open(self):
        account_id = 45
        email = "youraccount+45@gmail.com"
        self._add_account(account_id, email)

        cfg = {"browser": {"headless": False, "slow_mo_ms": 0}, "selectors": {}}
        imap = {"host": "imap.example", "username": "u", "password": "p"}
        session = Mock()

        with patch("PizzaBot.config_mod.load_config", return_value=cfg), \
             patch("PizzaBot.config_mod.require_imap_config", return_value=imap), \
             patch("pizzabot.browser.BrowserSession", return_value=session) as browser_cls, \
             patch("pizzabot.pizzahut.login_with_email"), \
             patch("pizzabot.pizzahut.wait_for_verification_email", return_value=None), \
             patch("pizzabot.pizzahut.open_verification_login_link") as open_link, \
             patch("pizzabot.pizzahut._wait_for_url_contains") as wait_url, \
             patch("builtins.input", side_effect=self._input_assert_not_closed(session)) as input_mock:
            text = self._run(self._args(account_id))

        self.assertIn("No Pizza Hut login email received", text)
        self.assertIn("leaving the browser open", text)
        self.assertIn("Press Enter to close the browser...", text)
        open_link.assert_not_called()
        wait_url.assert_not_called()
        input_mock.assert_called_once_with()
        session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
