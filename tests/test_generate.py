import re
import unittest
from datetime import date
from unittest.mock import patch

from pizzabot.generate import generate_birthday, make_alias, random_phone


class TestMakeAlias(unittest.TestCase):
    def test_basic_alias(self):
        self.assertEqual(make_alias("test@gmail.com", 3), "test+3@gmail.com")

    def test_existing_plus_is_stripped(self):
        self.assertEqual(make_alias("test+old@gmail.com", 4), "test+4@gmail.com")

    def test_invalid_email_raises(self):
        with self.assertRaises(ValueError):
            make_alias("not-an-email", 1)


class TestRandomPhone(unittest.TestCase):
    def test_format_and_length(self):
        phone = random_phone("416")
        self.assertRegex(phone, r"^416[2-9]\d{6}$")
        self.assertEqual(len(phone), 10)

    def test_invalid_area_code_raises(self):
        with self.assertRaises(ValueError):
            random_phone("12")


class TestGenerateBirthday(unittest.TestCase):
    def _generate(self, rule, today, day):
        with patch("pizzabot.generate._sysrand.randint", return_value=day):
            return generate_birthday({"birthday": rule}, today=today)

    def test_fixed_birthday_is_returned_unchanged(self):
        self.assertEqual(
            generate_birthday({"birthday": "2005-05-05"}, today=date(2026, 8, 15)),
            "2005-05-05",
        )

    def test_next_month_generates_configured_day_range(self):
        generated = self._generate("next_month:1-10", date(2026, 8, 15), 7)
        self.assertEqual(generated, "2026-09-07")

    def test_january_advances_to_february(self):
        generated = self._generate("next_month:1-10", date(2026, 1, 20), 2)
        self.assertEqual(generated, "2026-02-02")

    def test_november_advances_to_december(self):
        generated = self._generate("next_month:1-10", date(2026, 11, 20), 10)
        self.assertEqual(generated, "2026-12-10")

    def test_december_advances_to_january_next_year(self):
        generated = self._generate("next_month:1-10", date(2026, 12, 15), 1)
        self.assertEqual(generated, "2027-01-01")

    def test_leap_year_next_month_february_still_accepts_first_ten_days(self):
        generated = self._generate("next_month:1-10", date(2024, 1, 31), 9)
        self.assertEqual(generated, "2024-02-09")

    def test_range_is_inclusive_upper_bound(self):
        generated = self._generate("next_month:5-8", date(2026, 1, 1), 8)
        self.assertEqual(generated, "2026-02-08")

    def test_empty_rule_raises(self):
        with self.assertRaisesRegex(ValueError, "required"):
            generate_birthday({"birthday": ""}, today=date(2026, 8, 15))

    def test_invalid_fixed_date_raises(self):
        with self.assertRaises(ValueError):
            generate_birthday({"birthday": "05-05-2005"}, today=date(2026, 8, 15))

    def test_rule_exceeding_next_month_days_raises(self):
        with self.assertRaisesRegex(ValueError, "invalid for next month"):
            self._generate("next_month:28-31", date(2026, 1, 15), 28)


if __name__ == "__main__":
    unittest.main()
