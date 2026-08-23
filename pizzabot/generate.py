"""Helpers for generating email aliases and random phone numbers."""

from __future__ import annotations

import random
import re


_sysrand = random.SystemRandom()


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
