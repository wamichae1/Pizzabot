import email
import unittest

from pizzabot.mail import extract_links_from_message


class TestExtractLinks(unittest.TestCase):
    def test_extracts_link_from_html_and_filters_domain(self):
        msg_text = """\
From: Pizza Hut <noreply@pizzahut.ca>
To: user+1@gmail.com
Subject: Verify your account
Content-Type: text/html

<html><body><a href="https://www.pizzahut.ca/verify?token=abc">Verify</a>
<a href="https://example.com/tracker">tracker</a></body></html>
"""
        msg = email.message_from_string(msg_text)
        links = extract_links_from_message(msg, allowed_domain="pizzahut.ca")
        self.assertEqual(len(links), 1)
        self.assertIn("pizzahut.ca", links[0])

    def test_extracts_plain_text_link(self):
        msg_text = """\
From: Pizza Hut <noreply@pizzahut.ca>
To: user+2@gmail.com
Subject: Sign in
Content-Type: text/plain

Click here to sign in: https://www.pizzahut.ca/signin?token=xyz
"""
        msg = email.message_from_string(msg_text)
        self.assertEqual(len(extract_links_from_message(msg)), 1)


if __name__ == "__main__":
    unittest.main()
