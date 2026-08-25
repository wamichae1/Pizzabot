"""Minimal IMAP helper to find Pizza Hut sign-in/verification links."""

from __future__ import annotations

import email
import html
import imaplib
import re
import time
from email.header import decode_header, make_header
from email.message import Message
from typing import Iterable


LINK_RE = re.compile(r"https?://[^\s<>\"']+")


def _decode_header(value) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _message_subject(msg: Message) -> str:
    return _decode_header(msg.get("Subject", ""))


def _extract_payload(msg: Message) -> Iterable[str]:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        yield payload.decode(charset, errors="replace")
                    except LookupError:
                        yield payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            yield payload.decode("utf-8", errors="replace")


def extract_links_from_message(msg: Message, allowed_domain: str | None = None) -> list[str]:
    links: list[str] = []
    for raw in _extract_payload(msg):
        for link in LINK_RE.findall(raw):
            link = html.unescape(link.rstrip(".,;:)\\]}"))
            if link not in links:
                if not allowed_domain or allowed_domain.lower() in link.lower():
                    links.append(link)
    return links


def _fetch_latest_matching(conn: imaplib.IMAP4, to_email: str, limit: int = 50) -> list[Message]:
    """Search by To header, with a recent-message fallback."""
    messages: list[Message] = []
    try:
        typ, data = conn.search(None, f'(TO "{to_email}")')
        if typ == "OK":
            ids = data[0].split()
            # IMAP IDs are returned in ascending order; iterate from newest to
            # oldest so poll_for_link() prefers the most recent matching email.
            for num in reversed(ids[-limit:]):
                typ, msg_data = conn.fetch(num, "(RFC822)")
                if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                    messages.append(email.message_from_bytes(msg_data[0][1]))
        if messages:
            return messages
    except Exception:
        pass

    # Fallback: scan the newest N messages and match the To header.
    try:
        typ, data = conn.search(None, "ALL")
        if typ == "OK":
            ids = reversed(data[0].split()[-limit:])
            for num in ids:
                typ, msg_data = conn.fetch(num, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                if not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                to = _decode_header(msg.get("To", ""))
                if to_email.lower() in to.lower():
                    messages.append(msg)
    except Exception:
        pass
    return messages


def poll_for_link(
    imap_config: dict,
    to_email: str,
    *,
    allowed_domain: str | None = "pizzahut.ca",
    subject_contains: str | None = None,
    timeout_seconds: int = 180,
    poll_seconds: int = 10,
) -> str | None:
    """Poll inbox until a Pizza Hut link addressed to this alias appears."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            conn = imaplib.IMAP4_SSL(imap_config["host"])
            conn.login(imap_config["username"], imap_config["password"])
            conn.select(imap_config.get("mailbox", "INBOX"))
            messages = _fetch_latest_matching(conn, to_email)
            conn.logout()
            for msg in messages:
                if subject_contains and subject_contains.lower() not in _message_subject(msg).lower():
                    continue
                links = extract_links_from_message(msg, allowed_domain=allowed_domain)
                if links:
                    return links[0]
        except Exception as exc:
            # Transient network/auth errors should not kill the whole run on first try.
            print(f"  (mail poll failed for {to_email}: {exc})")
        time.sleep(poll_seconds)
    return None
