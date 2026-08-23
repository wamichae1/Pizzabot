"""Configuration loading, validation, and interactive setup."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("config.json")
DEFAULT_DB_PATH = Path("pizzabot.db")

DEFAULT_CONFIG: dict[str, Any] = {
    "target_account_pool": 1,
    "promo_check_frequency_days": 1,
    "base_profile": {
        "first_name": "",
        "last_name": "",
        "birthday": "",
        "area_code": "",
        "phone": "",
        "base_email": "",
    },
    "imap": {
        "host": "imap.gmail.com",
        "username": "",
        "password": "",
        "mailbox": "INBOX",
    },
    "browser": {
        "headless": False,
        "slow_mo_ms": 300,
    },
    "selectors": {},
}


def default_config() -> dict:
    return copy.deepcopy(DEFAULT_CONFIG)


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    """Load config.json, merging with defaults so new keys are safe."""
    cfg = default_config()
    p = Path(path)
    if not p.exists():
        return cfg
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    _deep_merge(cfg, data)
    return cfg


def save_config(config: dict, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
    p = Path(path)
    with p.open("w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")


def _deep_merge(base: dict, override: dict) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _ask(prompt: str, default: str = "") -> str:
    if default:
        prompt = f"{prompt} [{default}] "
    else:
        prompt = f"{prompt} "
    raw = input(prompt).strip()
    return raw if raw else default


def interactive_init(path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    """Create or update config.json by asking the user interactively."""
    cfg = load_config(path)
    print(f"Config file: {Path(path).resolve()}")

    cfg["target_account_pool"] = int(
        _ask("Target account pool (how many active accounts to keep)", str(cfg["target_account_pool"]))
    )
    cfg["promo_check_frequency_days"] = int(
        _ask(
            "Promotion check frequency in days",
            str(cfg["promo_check_frequency_days"]),
        )
    )

    profile = cfg["base_profile"]
    profile["first_name"] = _ask("Default first name", profile.get("first_name", ""))
    profile["last_name"] = _ask("Default last name", profile.get("last_name", ""))
    profile["birthday"] = _ask("Birthday (YYYY-MM-DD)", profile.get("birthday", ""))
    profile["area_code"] = _ask("Phone area code (3 digits)", profile.get("area_code", ""))
    profile["phone"] = _ask(
        "Fixed phone number (blank to generate from area code)", profile.get("phone", "")
    )
    profile["base_email"] = _ask("Base Gmail address (we add +n aliases)", profile.get("base_email", ""))

    imap = cfg["imap"]
    imap["host"] = _ask("IMAP host", imap.get("host", "imap.gmail.com"))
    imap["username"] = _ask("IMAP username", imap.get("username", ""))
    imap["password"] = _ask("IMAP password/app password", imap.get("password", ""))

    browser = cfg["browser"]
    headless = _ask("Run browser headless? (yes/no)", "no" if not browser.get("headless") else "yes").strip().lower()
    browser["headless"] = headless in {"yes", "y", "true", "1"}

    save_config(cfg, path)
    return cfg


def validate_birthday(value: str) -> None:
    if not value:
        return
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"birthday must be YYYY-MM-DD, got {value!r}") from exc


def ensure_base_profile(config: dict, path: Path | str = DEFAULT_CONFIG_PATH, prompt: bool = True) -> dict:
    """Fill missing base profile values, prompting only when interactive is wanted."""
    profile = config["base_profile"]
    changed = False

    if not profile.get("base_email"):
        profile["base_email"] = _ask("Base Gmail address (we add +n aliases)", profile.get("base_email", ""))
        changed = True
    if not profile.get("first_name"):
        profile["first_name"] = _ask("Default first name", "")
        changed = True
    if not profile.get("last_name"):
        profile["last_name"] = _ask("Default last name", "")
        changed = True
    if not profile.get("birthday"):
        profile["birthday"] = _ask("Birthday (YYYY-MM-DD)", "")
        changed = True
    if not profile.get("area_code"):
        profile["area_code"] = _ask("Phone area code (3 digits)", "")
        changed = True

    validate_birthday(profile.get("birthday", ""))
    if changed:
        save_config(config, path)
    return profile


def require_imap_config(config: dict) -> dict:
    imap = config.get("imap", {})
    missing = [k for k in ("host", "username", "password") if not imap.get(k)]
    if missing:
        raise RuntimeError(f"IMAP config missing required key(s): {', '.join(missing)}")
    return imap
