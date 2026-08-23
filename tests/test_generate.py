import re
import unittest

from pizzabot.generate import make_alias, random_phone


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


if __name__ == "__main__":
    unittest.main()
