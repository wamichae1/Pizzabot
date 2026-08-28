"""Helpers for generating email aliases, random birthdays, and phone numbers."""

from __future__ import annotations

import calendar
import random
import re
from datetime import date


_sysrand = random.SystemRandom()


BIRTHDAY_NEXT_MONTH_RE = re.compile(
    r"^next_month:(?P<start>\d{1,2})-(?P<end>\d{1,2})$"
)


def make_alias(base_email: str, n: int) -> str:
    """Turn 'user@gmail.com' into 'user+<n>@gmail.com'.

    Any existing +suffix in the base address is stripped first so aliases
    stay predictable even if the base was already an alias.
    """
    match = re.match(r"^([^@]+)@([^@]+)$", base_email.strip())
    if not match:
        raise ValueError(f"invalid email: {base_email!r}")
    local, domain = match.groups()
    local = local.split("+", 1)[0]
    if not local or not domain:
        raise ValueError(f"invalid email: {base_email!r}")
    return f"{local}+{n}@{domain}"


def random_phone(area_code: str) -> str:
    """Return a 10-digit phone number without formatting.

    Uses the provided area code plus a random North American style
    7-digit local number (exchange starts with 2-9).
    """
    area = re.sub(r"\D", "", area_code)
    if len(area) != 3:
        raise ValueError(f"area_code must be 3 digits, got {area_code!r}")
    exchange = (
        str(_sysrand.randint(2, 9))
        + str(_sysrand.randint(0, 9))
        + str(_sysrand.randint(0, 9))
    )
    line = str(_sysrand.randint(0, 9999)).zfill(4)
    return area + exchange + line


def generate_account_email(config_profile: dict, alias_id: int) -> str:
    return make_alias(config_profile["base_email"], alias_id)


def generate_phone(config_profile: dict) -> str:
    fixed = (config_profile.get("phone") or "").strip()
    if fixed and re.fullmatch(r"\d{10}", re.sub(r"\D", "", fixed)):
        return re.sub(r"\D", "", fixed)
    return random_phone(config_profile["area_code"])


def generate_birthday(config_profile: dict, today: date | None = None) -> str:
    """Return a birthday string from a fixed date or ``next_month:START-END``.

    Fixed values remain backward compatible. The ``next_month`` rule uses the
    month immediately following ``today`` (or the actual current date when
    ``today`` is omitted) and chooses a random day in the inclusive range.
    """
    value = (config_profile.get("birthday") or "").strip()
    if not value:
        raise ValueError("birthday config is required")

    match = BIRTHDAY_NEXT_MONTH_RE.fullmatch(value)
    if not match:
        # Preserve the existing fixed-date behavior and validate the shape.
        date.fromisoformat(value)
        return value

    start = int(match.group("start"))
    end = int(match.group("end"))
    current = today or date.today()
    if current.month == 12:
        year = current.year + 1
        month = 1
    else:
        year = current.year
        month = current.month + 1

    last_day = calendar.monthrange(year, month)[1]
    if not (1 <= start <= end <= last_day):
        raise ValueError(
            f"birthday rule {value!r} is invalid for next month "
            f"{year}-{month:02d} (days 1-{last_day})"
        )

    day = _sysrand.randint(start, end)
    return f"{year:04d}-{month:02d}-{day:02d}"
