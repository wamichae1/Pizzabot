"""Pizza Hut Canada page flows built on the Playwright BrowserSession.

The live account flow is:
  /login -> email -> verification email -> Login button -> /complete-profile

The live promotion flow is:
  /login -> email -> verification email -> Login button -> /order/deals -> Hut Rewards
"""

from __future__ import annotations

import re
import time
from typing import Any

from pizzabot.browser import BrowserSession, click_first, visible_locator


LOGIN_URL = "https://www.pizzahut.ca/login"
COMPLETE_PROFILE_URL = "https://www.pizzahut.ca/complete-profile"
DEALS_URL = "https://www.pizzahut.ca/order/deals"


class PizzahutError(RuntimeError):
    pass


def _selectors(custom: dict, key: str, defaults: list[str]) -> list[str]:
    value = custom.get(key)
    if not value:
        return list(defaults)
    if isinstance(value, str):
        return [value]
    return list(value)


def _url_contains(page, part: str) -> bool:
    return part in page.url


def _missing(page, flow: str, stage: str, element: str) -> PizzahutError:
    return PizzahutError(
        f"[{flow}/{stage}] element not found: {element!r}. "
        f"Current URL: {page.url}"
    )


def _fill_visible(page, value: str, locators: list[str]) -> bool:
    loc = visible_locator(page, locators)
    if loc is not None:
        loc.fill(value, timeout=3000)
        return True
    return False


# --- Cookie handling ---------------------------------------------------------


def _find_warm_cookie_dialog(page):
    """Find the visible 'Warm, web cookies' dialog across all frames."""
    needle = "warm, web cookies"
    deadline = time.time() + 6.0
    while time.time() < deadline:
        for frame in page.frames:
            try:
                dialogs = frame.get_by_role("dialog")
                count = min(dialogs.count(), 30)
                for i in range(count):
                    dialog = dialogs.nth(i)
                    try:
                        if dialog.is_visible(timeout=300) and needle in (dialog.inner_text(timeout=500) or "").lower():
                            return frame, dialog
                    except Exception:
                        continue
            except Exception:
                continue
        time.sleep(0.3)
    return None, None


def _wait_dialog_gone(dialog) -> bool:
    try:
        dialog.wait_for(state="hidden", timeout=8000)
        return True
    except Exception:
        return False


_ACCEPT_BUTTON_JS = """
() => {
  const needle = 'warm, web cookies';

  function isVisible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function isButton(el) {
    return el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button';
  }

  function hasWarmContext(el) {
    let node = el;
    for (let i = 0; i < 12 && node; i += 1, node = node.parentElement) {
      const text = (node.innerText || node.textContent || '');
      if (text.toLowerCase().includes(needle)) {
        return true;
      }
    }
    return false;
  }

  function findInRoot(root) {
    const candidates = root.querySelectorAll('button, a, [role="button"]');
    for (const el of candidates) {
      if (!isButton(el) || !isVisible(el)) continue;
      const label = (el.innerText || el.getAttribute('aria-label') || '').trim();
      if (/^accept$/i.test(label) && hasWarmContext(el)) {
        return el;
      }
    }
    for (const host of root.querySelectorAll('*')) {
      if (host.shadowRoot) {
        const found = findInRoot(host.shadowRoot);
        if (found) return found;
      }
    }
    return null;
  }

  return findInRoot(document);
}
"""


def _click_accept_via_js(page) -> bool:
    """DOM-based fallback that searches frames and open shadow roots by text."""
    for frame in page.frames:
        try:
            handle = frame.evaluate_handle(_ACCEPT_BUTTON_JS)
            element = handle.as_element()
            if element is not None:
                element.click(timeout=4000)
                handle.dispose()
                return True
            handle.dispose()
        except Exception:
            continue
    return False


def accept_cookies(session: BrowserSession, selectors: dict[str, Any] | None = None) -> None:
    """Accept the visible 'Warm, web cookies' dialog if it exists."""
    page = session.page
    _, dialog = _find_warm_cookie_dialog(page)
    if dialog is None:
        if _click_accept_via_js(page):
            time.sleep(1)
        return

    accept = dialog.get_by_role("button", name="Accept", exact=True).first
    try:
        accept.click(timeout=4000)
    except Exception:
        try:
            dialog.get_by_text("Accept", exact=True).first.click(timeout=4000)
        except Exception:
            if not _click_accept_via_js(page):
                return

    if not _wait_dialog_gone(dialog):
        time.sleep(1)


def close_popups(session: BrowserSession, selectors: dict[str, Any] | None = None) -> None:
    selectors = selectors or {}
    page = session.page
    accept_cookies(session, selectors)
    close_locators = _selectors(
        selectors,
        "close_popup",
        [
            "button[aria-label*='close' i]",
            "[data-testid*='modal'] button",
            ".modal button",
            "button:has-text('X')",
        ],
    )
    click_first(page, close_locators, fallback_texts=["Close", "Dismiss", "No thanks", "Got it"])


# --- Account creation flow ---------------------------------------------------

# Stage 1: enter email on /login
def login_with_email(
    session: BrowserSession,
    email: str,
    selectors: dict[str, Any] | None = None,
    *,
    flow: str = "create",
    stage: str = "login",
) -> None:
    selectors = selectors or {}
    session.goto(LOGIN_URL, timeout_ms=90_000)
    accept_cookies(session, selectors)
    page = session.page

    email_ok = _fill_visible(
        page,
        email,
        _selectors(
            selectors,
            "login_email",
            [
                "input[type='email']",
                "input[name*='email']",
                "input[id*='email']",
                "input[placeholder*='email' i]",
            ],
        ),
    )
    if not email_ok:
        raise _missing(page, flow, stage, "email input")

    submit_ok = click_first(
        page,
        _selectors(
            selectors,
            "login_submit",
            [
                "button[type='submit']",
                "button:has-text('Continue')",
                "button:has-text('Sign In')",
                "button:has-text('Log In')",
                "button:has-text('Submit')",
            ],
        ),
        fallback_texts=["Continue", "Sign In", "Log In", "Submit", "Next"],
    )
    if not submit_ok:
        raise _missing(page, flow, stage, "submit/continue button")

    time.sleep(2)


# Stage 2: wait for the verification/sign-in email
def wait_for_verification_email(
    imap_config: dict,
    email: str,
    *,
    timeout_seconds: int = 180,
    allowed_domain: str = "pizzahut.ca",
) -> str | None:
    from pizzabot import mail as mail_mod

    return mail_mod.poll_for_link(
        imap_config,
        email,
        allowed_domain=allowed_domain,
        timeout_seconds=timeout_seconds,
        subject_contains="Log in to Your Pizza Hut Account",
    )


# Stage 3: open the red "Login" button from the verification email
def open_verification_login_link(
    session: BrowserSession,
    link: str,
    selectors: dict[str, Any] | None = None,
) -> None:
    selectors = selectors or {}
    session.goto(link, timeout_ms=90_000)
    accept_cookies(session, selectors)
    time.sleep(2)


# Stage 4: complete profile on /complete-profile
def _birthday_mmdd(birthday: str) -> str:
    value = birthday.strip()
    if len(value) == 4 and value.isdigit():
        return value
    try:
        year, month, day = value.split("-")
        return f"{int(month):02d}{int(day):02d}"
    except Exception:
        return value


def _fill_birthday(page, mmdd: str, selectors: dict, flow: str, stage: str) -> bool:
    direct_locators = _selectors(
        selectors,
        "birthday_input",
        [
            "input[name*='birth']",
            "input[id*='birth']",
            "input[placeholder*='birth' i]",
            "input[placeholder*='MM' i]",
            "input[aria-label*='birth' i]",
            "input[data-testid*='birth' i]",
            "input[maxlength='4']",
            "input[inputmode='numeric']",
        ],
    )
    if _fill_visible(page, mmdd, direct_locators):
        return True

    # Label-based fallback: find the visible "Birthday" text and use the input
    # associated with it.
    try:
        label = page.get_by_text("Birthday", exact=False).first
        if label.is_visible(timeout=1000):
            for_id = label.get_attribute("for")
            if for_id:
                inp = page.locator(f"#{for_id}").first
                if inp.is_visible(timeout=1000):
                    inp.fill(mmdd, timeout=1500)
                    return True
            inp = label.locator(
                "xpath=ancestor::div[1]//input | ancestor::div[2]//input | following::input[1]"
            ).first
            if inp.is_visible(timeout=1000):
                inp.fill(mmdd, timeout=1500)
                return True
    except Exception:
        pass

    # Some forms split month/day into two maxlength=2 inputs (MM / DD).
    try:
        boxes = page.locator("input[maxlength='2']")
        if boxes.count() >= 2:
            if boxes.nth(0).is_visible(timeout=1000) and boxes.nth(1).is_visible(timeout=1000):
                boxes.nth(0).fill(mmdd[:2], timeout=1500)
                boxes.nth(1).fill(mmdd[2:4], timeout=1500)
                return True
    except Exception:
        pass

    return False


def _ensure_terms_checked(page, flow: str, stage: str) -> None:
    try:
        terms = page.get_by_role("checkbox", name=re.compile("terms", re.IGNORECASE)).first
        if not terms.is_checked():
            terms.check(timeout=3000)
    except Exception:
        # Fallback: look for the checkbox closest to Terms text.
        try:
            label = page.get_by_text("Terms", exact=False).first
            box = label.locator("xpath=ancestor::label//input[type='checkbox'] | ancestor::label//input").first
            if box.is_visible(timeout=1000) and not box.is_checked():
                box.check(timeout=3000)
        except Exception as exc:
            raise _missing(page, flow, stage, "Terms and Conditions checkbox") from exc


def _uncheck_marketing(page) -> None:
    """Ensure SMS/Email marketing checkboxes are unchecked."""
    for term in ("SMS", "Email", "Marketing"):
        try:
            boxes = page.get_by_role("checkbox", name=re.compile(term, re.IGNORECASE))
            for i in range(boxes.count()):
                box = boxes.nth(i)
                if box.is_visible(timeout=300) and box.is_checked():
                    box.uncheck(timeout=1000)
        except Exception:
            continue


def complete_profile(
    session: BrowserSession,
    profile: dict,
    selectors: dict[str, Any] | None = None,
) -> None:
    selectors = selectors or {}
    page = session.page
    flow = "create"
    stage = "complete_profile"

    if not _url_contains(page, "/complete-profile"):
        raise PizzahutError(
            f"[{flow}/{stage}] expected to be on /complete-profile. Current URL: {page.url}"
        )

    accept_cookies(session, selectors)

    first_ok = _fill_visible(
        page,
        profile["first_name"],
        _selectors(
            selectors,
            "first_name_input",
            ["input[name*='first']", "input[id*='first']", "input[placeholder*='first' i]"],
        ),
    )
    if not first_ok:
        raise _missing(page, flow, stage, "first name input")

    last_ok = _fill_visible(
        page,
        profile["last_name"],
        _selectors(
            selectors,
            "last_name_input",
            ["input[name*='last']", "input[id*='last']", "input[placeholder*='last' i]"],
        ),
    )
    if not last_ok:
        raise _missing(page, flow, stage, "last name input")

    phone_ok = _fill_visible(
        page,
        profile["phone"],
        _selectors(
            selectors,
            "phone_input",
            ["input[type='tel']", "input[name*='phone']", "input[id*='phone']", "input[placeholder*='phone' i]"],
        ),
    )
    if not phone_ok:
        raise _missing(page, flow, stage, "mobile number input")

    birthday_ok = _fill_birthday(
        page,
        _birthday_mmdd(profile["birthday"]),
        selectors,
        flow,
        stage,
    )
    if not birthday_ok:
        raise _missing(page, flow, stage, "birthday input (single or MM/DD field)")

    _uncheck_marketing(page)
    _ensure_terms_checked(page, flow, stage)

    submit_ok = click_first(
        page,
        _selectors(
            selectors,
            "profile_submit",
            [
                "button[type='submit']",
                "button:has-text('Continue')",
                "button:has-text('Save')",
                "button:has-text('Submit')",
                "button:has-text('Create Account')",
            ],
        ),
        fallback_texts=["Continue", "Save", "Submit", "Create Account"],
    )
    if not submit_ok:
        raise _missing(page, flow, stage, "profile submit button")

    time.sleep(3)


# --- Promotion checking flow ------------------------------------------------

def navigate_to_hut_rewards(session: BrowserSession, selectors: dict[str, Any] | None = None) -> None:
    selectors = selectors or {}
    page = session.page
    flow = "promo"
    stage = "navigate_to_hut_rewards"

    accept_cookies(session, selectors)
    if not _url_contains(page, "/order/deals"):
        # The passwordless login can land on `/` before the profile session is
        # fully ready; give it a moment, then navigate to the deals page.
        time.sleep(3)
        session.goto(DEALS_URL, timeout_ms=90_000)
        accept_cookies(session, selectors)
        time.sleep(2)

    profile_clicked = click_first(
        page,
        _selectors(
            selectors,
            "view_profile",
            [
                "button:has-text('View Profile')",
                "a:has-text('View Profile')",
                "[aria-label*='profile' i]",
                "[aria-label*='account' i]",
                "[data-testid*='profile']",
                "[data-testid*='account']",
                "button[aria-label*='profile' i]",
                "a[aria-label*='profile' i]",
                "img[alt*='profile' i]",
                "button[class*='profile' i]",
                "a[class*='profile' i]",
                "[class*='profile' i]",
                "[class*='account' i]",
            ],
        ),
        fallback_texts=["View Profile", "Profile", "Account", "My Account"],
    )
    if not profile_clicked:
        raise _missing(page, flow, stage, "View Profile control")

    time.sleep(1)

    rewards_clicked = click_first(
        page,
        _selectors(
            selectors,
            "hut_rewards",
            [
                "button:has-text('Hut Rewards')",
                "a:has-text('Hut Rewards')",
                "[data-testid*='rewards']",
                "button:has-text('Rewards')",
                "a:has-text('Rewards')",
            ],
        ),
        fallback_texts=["Hut Rewards", "Rewards"],
    )
    if not rewards_clicked:
        raise _missing(page, flow, stage, "Hut Rewards menu item")

    time.sleep(5)


def extract_limited_time_offers(session: BrowserSession) -> list[dict]:
    """Return every limited-time offer found, or [] when the section is absent."""
    try:
        body = session.page.inner_text("body", timeout=5000)
    except Exception:
        return []

    marker = body.lower().find("limited time offers")
    if marker == -1:
        return []

    section = body[marker : marker + 5000]
    offers: list[dict] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"([^.!?\n]{4,220}?)\s*(Expires? in [^.!?\n]+!?)",
        section,
        re.IGNORECASE,
    ):
        name = re.sub(r"\s+", " ", match.group(1)).strip(" .:;,-")
        expiry = re.sub(r"\s+", " ", match.group(2)).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        offers.append({"name": name, "status": "active", "expiry": expiry})
    return offers


def check_promotion(session: BrowserSession, selectors: dict[str, Any] | None = None) -> list[dict]:
    navigate_to_hut_rewards(session, selectors)
    return extract_limited_time_offers(session)
