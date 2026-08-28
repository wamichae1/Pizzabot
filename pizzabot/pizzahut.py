"""Pizza Hut Canada page flows built on the Playwright BrowserSession.

The live account flow is:
  /login -> email -> verification email -> Login button -> /complete-profile

The live promotion flow is:
  /login -> email -> verification email -> Login button -> /order/deals -> Hut Rewards
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from pizzabot.browser import BrowserSession, click_first, visible_locator


LOGIN_URL = "https://www.pizzahut.ca/login"
COMPLETE_PROFILE_URL = "https://www.pizzahut.ca/complete-profile"
DEALS_URL = "https://www.pizzahut.ca/order/deals"
MY_ACCOUNT_DETAILS_URL = "https://www.pizzahut.ca/my-account/details"


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


def _wait_for_url_contains(
    page,
    expected_part: str,
    flow: str,
    stage: str,
    action: str,
    timeout_seconds: float = 8.0,
) -> None:
    """Wait for the browser URL to contain expected_part, raising a stage-specific error."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        if expected_part in page.url:
            return
        if time.monotonic() >= deadline:
            break
        try:
            page.wait_for_timeout(250)
        except AttributeError:
            break
    raise PizzahutError(
        f"[{flow}/{stage}] {action} did not reach the expected URL. "
        f"Expected URL: {expected_part!r}. Current URL: {page.url}"
    )


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
            "input[placeholder*='DD' i]",
            "input[aria-label*='birth' i]",
            "input[data-testid*='birth' i]",
            "input[maxlength='4']",
            "input[maxlength='5']",
            "input[inputmode='numeric']:not([type='tel'])",
        ],
    )

    field = visible_locator(page, direct_locators)

    # Label-based fallback: find the visible "Birthday" text and use the input
    # associated with it.
    if field is None:
        try:
            label = page.get_by_text("Birthday", exact=False).first
            if label.is_visible(timeout=1000):
                for_id = label.get_attribute("for")
                if for_id:
                    candidate = page.locator(f"#{for_id}").first
                    if candidate.is_visible(timeout=1000):
                        field = candidate
                if field is None:
                    candidate = label.locator(
                        "xpath=ancestor::div[1]//input | ancestor::div[2]//input | following::input[1]"
                    ).first
                    if candidate.is_visible(timeout=1000):
                        field = candidate
        except Exception:
            pass

    if field is None:
        # Some forms split month/day into two maxlength=2 inputs (MM / DD).
        try:
            boxes = page.locator("input[maxlength='2']")
            if boxes.count() >= 2 and boxes.nth(0).is_visible(timeout=1000) and boxes.nth(1).is_visible(timeout=1000):
                boxes.nth(0).click()
                boxes.nth(0).press_sequentially(mmdd[:2], delay=100)
                boxes.nth(1).click()
                boxes.nth(1).press_sequentially(mmdd[2:4], delay=100)
                # No simple single-value mask to verify here; assume success.
                return True
        except Exception:
            pass

    if field is None:
        return False

    expected = f"{mmdd[:2]}/{mmdd[2:4]}"
    field.click()
    field.focus()
    try:
        field.fill("", timeout=2000)
    except Exception:
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
    # Send keyboard events to the focused element so the site's input mask
    # receives real key presses (typing exactly 4 digits, no slash).
    field.focus()
    field.press_sequentially(mmdd, delay=100)
    time.sleep(0.4)

    actual = field.input_value()
    if actual != expected:
        # Some masked inputs update a hidden sibling input instead of the
        # editable display field.
        candidate_values = page.evaluate(
            """() => {
                return Array.from(document.querySelectorAll('input')).map((el) => ({
                    name: el.name,
                    id: el.id,
                    type: el.type,
                    placeholder: el.getAttribute('placeholder'),
                    value: el.value,
                }));
            }"""
        )
        if any(item.get("value") == expected for item in candidate_values):
            return True
        raise PizzahutError(
            f"[{flow}/{stage}] birthday input value mismatch. "
            f"Expected {expected!r} after typing {mmdd!r}, got {actual!r}. "
            f"Current URL: {page.url}. Candidate input values: {candidate_values}"
        )
    return True


def _checkbox_is_checked(locator) -> bool:
    try:
        tag_and_type = locator.evaluate(
            "el => el.tagName === 'INPUT' && el.type === 'checkbox'"
        )
        if tag_and_type:
            return locator.is_checked()
        aria = locator.evaluate("el => el.getAttribute('aria-checked')")
        return aria == "true"
    except Exception:
        return False


_FIND_TERMS_CHECKBOX_JS = r"""
() => {
  const normalize = (value) => (value || '')
    .replace(/[\u2018\u2019']/g, "'")
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();

  const candidates = [];
  for (const box of document.querySelectorAll('input[type="checkbox"]')) {
    let container = box.parentElement;
    let distance = 0;
    while (container && container !== document.body) {
      const text = normalize(container.innerText || container.textContent);
      if (text.includes('by ticking this box') && text.includes('privacy policy')) {
        candidates.push({box, distance, length: text.length});
        break;
      }
      container = container.parentElement;
      distance += 1;
    }
  }
  candidates.sort((left, right) =>
    left.distance - right.distance || left.length - right.length
  );
  return candidates[0]?.box || null;
}
"""


def _find_terms_checkbox(page):
    """Locate Terms through its live DOM container, not an assumed label."""
    handle = page.evaluate_handle(_FIND_TERMS_CHECKBOX_JS)
    return handle.as_element()


def _describe_page_checkboxes(page) -> str:
    """Return non-brittle diagnostics for every visible checkbox-like control."""
    diagnostics = page.evaluate(
        r"""
        () => Array.from(
            document.querySelectorAll('input[type="checkbox"], [role="checkbox"]')
        ).map((el) => {
          const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
          const labelTexts = [];
          if (el.id) {
            document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`).forEach((label) => {
              labelTexts.push(clean(label.innerText || label.textContent));
            });
          }
          const wrappingLabel = el.closest('label');
          if (wrappingLabel) {
            labelTexts.push(clean(wrappingLabel.innerText || wrappingLabel.textContent));
          }
          const squareWrapper = el.closest('label')?.querySelector('[data-testid]');
          const labelledby = el.getAttribute('aria-labelledby');
          if (labelledby) {
            labelledby.split(/\s+/).forEach((id) => {
              const node = document.getElementById(id);
              if (node) labelTexts.push(clean(node.innerText || node.textContent));
            });
          }
          labelTexts.push(clean(el.getAttribute('aria-label')));
          let textContainer = el.parentElement;
          while (textContainer && textContainer !== document.body) {
            const text = clean(textContainer.innerText || textContainer.textContent);
            if (text) {
              labelTexts.push(`nearest container: ${text}`);
              break;
            }
            textContainer = textContainer.parentElement;
          }
          return {
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type'),
            name: el.getAttribute('name'),
            id: el.id,
            aria_label: el.getAttribute('aria-label'),
            wrapper_testid: squareWrapper?.getAttribute('data-testid'),
            wrapper_classes: squareWrapper?.getAttribute('class'),
            wrapper_outer_html: squareWrapper?.outerHTML,
            associated_label_text: Array.from(new Set(labelTexts)).filter(Boolean),
            checked: el instanceof HTMLInputElement ? el.checked : el.getAttribute('aria-checked'),
          };
        })
        """
        )
    return json.dumps(diagnostics, indent=2, ensure_ascii=False)


def _wait_for_clickable_terms(page, flow: str, stage: str, timeout_seconds: float = 15.0):
    """Wait for the located input's live MUI square wrapper to finish rendering."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        box = _find_terms_checkbox(page)
        if box is not None:
            testid = box.evaluate(
                "el => el.parentElement?.closest('[data-testid]')"
                "?.getAttribute('data-testid')"
            )
            if testid:
                return box, str(testid)
        if time.monotonic() >= deadline:
            return None, None
        try:
            page.wait_for_timeout(250)
        except AttributeError:
            # Unit-test page doubles do not implement Playwright timing APIs.
            break

    return None, None


def _ensure_terms_checked(page, flow: str, stage: str) -> None:
    box, testid = _wait_for_clickable_terms(page, flow, stage)
    if box is None or not testid:
        raise PizzahutError(
            f"[{flow}/{stage}] Terms & Conditions checkbox was located, but "
            "its clickable square wrapper did not finish rendering. "
            f"Current URL: {page.url}. Page checkbox structure:\n"
            f"{_describe_page_checkboxes(page)}"
        )

    try:
        # A React/MUI re-render can swallow the first pointer event immediately
        # after birthday validation. Retry a genuine wrapper click only while
        # a fresh DOM query says the checkbox remains unchecked.
        for _ in range(3):
            page.locator(f'[data-testid="{testid}"]').first.click(timeout=5000)
            time.sleep(0.5)
            refreshed_box = _find_terms_checkbox(page)
            if refreshed_box is not None and _checkbox_is_checked(refreshed_box):
                break
        else:
            raise RuntimeError("checkbox did not become checked")
    except Exception as exc:
        raise PizzahutError(
            f"[{flow}/{stage}] Terms & Conditions checkbox could not be "
            f"verified as checked. Current URL: {page.url}. "
            f"Page checkbox structure:\n{_describe_page_checkboxes(page)}"
        ) from exc


def _fill_terms_and_marketing(page, flow: str, stage: str) -> None:
    """Apply and verify the final three checkbox states before submission."""
    _ensure_marketing_unchecked(page, flow, stage)
    _ensure_terms_checked(page, flow, stage)


def _marketing_candidates(page, label: str):
    pattern = re.compile(rf"^\s*{label}\s*$", re.IGNORECASE)
    boxes = page.get_by_role("checkbox", name=pattern)
    candidates = [
        boxes.nth(i)
        for i in range(min(boxes.count(), 10))
        if boxes.nth(i).is_visible(timeout=500)
    ]
    if candidates:
        return candidates

    labels = page.locator("label").filter(has_text=pattern)
    for i in range(min(labels.count(), 10)):
        label_locator = labels.nth(i)
        if not label_locator.is_visible(timeout=500):
            continue
        boxes = label_locator.locator("input[type='checkbox']")
        for j in range(min(boxes.count(), 10)):
            box = boxes.nth(j)
            if box.is_visible(timeout=500):
                candidates.append(box)
    return candidates


def _ensure_marketing_unchecked(page, flow: str, stage: str) -> None:
    """Explicitly leave both separate Email and SMS marketing boxes off."""
    required_labels = ("Email", "SMS")
    for label in required_labels:
        boxes = _marketing_candidates(page, label)
        if not boxes:
            raise PizzahutError(
                f"[{flow}/{stage}] {label} marketing checkbox not found. "
                f"Current URL: {page.url}. Page checkbox structure:\n"
                f"{_describe_page_checkboxes(page)}"
            )
        for box in boxes:
            if _checkbox_is_checked(box):
                try:
                    box.uncheck(timeout=3000)
                except Exception:
                    if _checkbox_is_checked(box):
                        box.click(timeout=3000)
            if _checkbox_is_checked(box):
                raise PizzahutError(
                    f"[{flow}/{stage}] {label} marketing checkbox remained "
                    f"checked. Current URL: {page.url}."
                )


def complete_profile(
    session: BrowserSession,
    profile: dict,
    selectors: dict[str, Any] | None = None,
    report_stage=None,
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

    if report_stage:
        report_stage("complete_profile")

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
    if report_stage:
        report_stage("first_name")

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
    if report_stage:
        report_stage("last_name")

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
    if report_stage:
        report_stage("phone")

    birthday_ok = _fill_birthday(
        page,
        _birthday_mmdd(profile["birthday"]),
        selectors,
        flow,
        stage,
    )
    if not birthday_ok:
        raise _missing(page, flow, stage, "birthday input (single or MM/DD field)")
    if report_stage:
        report_stage("birthday")

    _fill_terms_and_marketing(page, flow, stage)
    if report_stage:
        report_stage("terms")

    submit_ok = click_first(
        page,
        _selectors(
            selectors,
            "profile_submit",
            [
                "button:has-text('Create Account')",
                "button[type='submit']",
                "button:has-text('Continue')",
                "button:has-text('Save')",
                "button:has-text('Submit')",
            ],
        ),
        fallback_texts=["Create Account", "Continue", "Save", "Submit"],
    )
    if not submit_ok:
        raise _missing(page, flow, stage, "profile submit button")

    time.sleep(3)
    if report_stage:
        report_stage("account_created")


# --- Promotion checking flow ------------------------------------------------


_VIEW_PROFILE_NAME = re.compile(r"^\s*view\s+profile\s*$", re.IGNORECASE)
_MY_DETAILS_NAME = re.compile(r"^\s*my\s+details\s*$", re.IGNORECASE)
_HUT_REWARDS_NAME = re.compile(r"^\s*hut\s+rewards\s*$", re.IGNORECASE)
_REQUESTED_TIME_TITLE = re.compile(
    r"^\s*change\s+carryout\s+time\s*$",
    re.IGNORECASE,
)
_CLOSE_CONTROL_NAME = re.compile(r"^\s*(close|dismiss)\s*$", re.IGNORECASE)


def _visible_locator_first(locators):
    for locator in locators:
        try:
            if locator.count() > 0 and locator.first.is_visible(timeout=500):
                return locator.first
        except Exception:
            continue
    return None


def _close_requested_time_modal(page) -> bool:
    """Close only Pizza Hut's specific 'Change Carryout Time' modal if shown."""
    modal = page.locator('[data-testid="requested-time-modal"]').filter(
        has=page.get_by_role("heading", name=_REQUESTED_TIME_TITLE),
    ).first
    try:
        if modal.count() == 0 or not modal.is_visible(timeout=500):
            return False
    except Exception:
        return False

    close = _visible_locator_first(
        [
            modal.get_by_role("button", name=_CLOSE_CONTROL_NAME),
            modal.locator("button[aria-label*='close' i]"),
        ]
    )
    if close is None:
        raise PizzahutError(
            "[promo/deals_page] 'Change Carryout Time' modal is open, "
            "but its close control was not found."
        )

    close.click(timeout=3000)
    if not _wait_dialog_gone(modal):
        raise PizzahutError(
            "[promo/deals_page] 'Change Carryout Time' modal did not close "
            f"after clicking its close control. Current URL: {page.url}"
        )
    return True


def _wait_for_view_profile(page, flow: str, stage: str, timeout_seconds: float = 10.0):
    """Wait for the authenticated account icon by its live accessible/DOM identity."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        control = _visible_locator_first(
            [
                page.get_by_role("img", name=_VIEW_PROFILE_NAME),
                page.get_by_role("button", name=_VIEW_PROFILE_NAME),
                page.locator('[data-testid="account-logged-in-icon"]'),
            ]
        )
        if control is not None:
            return control
        if time.monotonic() >= deadline:
            break
        try:
            page.wait_for_timeout(250)
        except AttributeError:
            break

    diagnostics = page.evaluate(
        """
        () => ({
          url: location.href,
          loggedInIcon: Boolean(document.querySelector('[data-testid="account-logged-in-icon"]')),
          loggedOutIcon: Boolean(document.querySelector('[data-testid="account-logged-out-icon"]')),
          appBarControls: Array.from(document.querySelectorAll(
              '[data-testid="account-action"], [data-testid^="account-logged"]'
          )).map((el) => ({
            tag: el.tagName.toLowerCase(),
            testid: el.getAttribute('data-testid'),
            ariaLabel: el.getAttribute('aria-label'),
            title: el.getAttribute('title'),
            text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim(),
          })),
        })
        """
    )
    raise PizzahutError(
        f"[{flow}/{stage}] authenticated View Profile control not found. "
        f"Current page state: {json.dumps(diagnostics, indent=2)}"
    )




def _wait_for_hut_rewards_option(page, flow: str, stage: str, timeout_seconds: float = 5.0):
    """Wait for a Hut Rewards control on the account pages (menu item, button, or link)."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        rewards = _visible_locator_first(
            [
                page.get_by_role("menuitem", name=_HUT_REWARDS_NAME),
                page.get_by_role("button", name=_HUT_REWARDS_NAME),
                page.get_by_role("link", name=_HUT_REWARDS_NAME),
                page.get_by_text(_HUT_REWARDS_NAME, exact=True),
            ]
        )
        if rewards is not None:
            return rewards
        if time.monotonic() >= deadline:
            break
        try:
            page.wait_for_timeout(250)
        except AttributeError:
            break
    diagnostics = page.evaluate(
        """
        () => ({
          url: location.href,
          hutRewards: Array.from(document.querySelectorAll('a, button, [role="menuitem"], [role="button"]'))
            .map((el) => ({
              tag: el.tagName.toLowerCase(),
              role: el.getAttribute('role'),
              testid: el.getAttribute('data-testid'),
              ariaLabel: el.getAttribute('aria-label'),
              text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim(),
            }))
            .filter((el) => /hut\\s+rewards/i.test(el.text || el.ariaLabel || '')),
        })
        """
    )
    raise PizzahutError(
        f"[{flow}/{stage}] Hut Rewards control not found. "
        f"Current page state: {json.dumps(diagnostics, indent=2)}"
    )


def _wait_for_hut_rewards_content(page, flow: str, stage: str, timeout_seconds: float = 10.0):
    """Explicitly distinguish a loaded rewards page from absence of offers."""
    deadline = time.monotonic() + timeout_seconds
    last_state = {}
    while True:
        last_state = page.evaluate(
            """
            () => {
              const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
              const body = clean(document.body.innerText);
              const lowerBody = body.toLowerCase();
              const markers = [
                'limited time offers',
                'my rewards',
                'next tier rewards',
                'your tier is',
                'my progress',
                'earn 1 point',
                'point history',
              ];
              const errors = [
                'error loading reward page',
                'could not load your details',
                'oops! something went wrong!',
              ];
              return {
                url: location.href,
                title: document.title,
                loadedMarkers: markers.filter((marker) => lowerBody.includes(marker)),
                errorMarkers: errors.filter((marker) => lowerBody.includes(marker)),
                hasRewardsHeaderBalance: Boolean(document.querySelector('[data-testid="reward-header-balance"]')),
                bodyStart: body.slice(0, 1500),
              };
            }
            """
        )
        if last_state.get("errorMarkers"):
            break
        if last_state.get("loadedMarkers") or last_state.get("hasRewardsHeaderBalance"):
            return
        if time.monotonic() >= deadline:
            break
        try:
            page.wait_for_timeout(250)
        except AttributeError:
            break

    raise PizzahutError(
        f"[{flow}/{stage}] Hut Rewards content did not finish loading. "
        f"Current page state: {json.dumps(last_state, indent=2, ensure_ascii=False)}"
    )


def navigate_to_hut_rewards(
    session: BrowserSession,
    selectors: dict[str, Any] | None = None,
    report_stage=None,
) -> None:
    selectors = selectors or {}
    page = session.page
    flow = "promo"

    accept_cookies(session, selectors)

    # The login link should land on /order/deals. Give the SPA a moment to
    # redirect, but do NOT auto-navigate if it lands somewhere else: that
    # would mask an authentication/navigation failure.
    _wait_for_url_contains(
        page,
        "/order/deals",
        flow,
        "deals_page",
        "verification login link",
        timeout_seconds=8,
    )
    if report_stage:
        report_stage("deals_page")

    # When the store is closed this specific modal blocks the app behind a
    # backdrop. Leave every other overlay alone.
    _close_requested_time_modal(page)

    # A project-configured selector remains an explicit override, never a
    # generic class/text fallback.
    custom_profile = _selectors(selectors, "view_profile", [])
    if custom_profile:
        click_first(page, custom_profile)
    try:
        # Prefer the live tooltip/accessibly named person icon. Keep the
        # site-discovered account-action test ID only as a focused fallback.
        profile_control = _wait_for_view_profile(page, flow, "account_page")
        profile_control.hover(timeout=3000)
        profile_control.click(timeout=5000)
    except Exception as exc:
        if isinstance(exc, PizzahutError):
            raise
        raise PizzahutError(
            f"[{flow}/account_page] could not click the account icon. "
            f"Current URL: {page.url}"
        ) from exc

    try:
        # Clicking the account icon should navigate to /my-account/details.
        # If it opens a menu instead, fall back to clicking the exact
        # "My Details" option that lives on that menu.
        _wait_for_url_contains(
            page,
            "/my-account/details",
            flow,
            "account_page",
            "account icon click",
            timeout_seconds=5,
        )
    except PizzahutError:
        details = _visible_locator_first(
            [
                page.get_by_role("menuitem", name=_MY_DETAILS_NAME),
                page.get_by_role("button", name=_MY_DETAILS_NAME),
                page.get_by_role("link", name=_MY_DETAILS_NAME),
                page.get_by_text(_MY_DETAILS_NAME, exact=True),
            ]
        )
        if details is None:
            raise PizzahutError(
                f"[{flow}/account_page] account page was not reached after clicking the account icon. "
                f"Expected URL: {MY_ACCOUNT_DETAILS_URL!r}. Current URL: {page.url}. "
                f"Account menu option 'My Details' was not found either."
            )
        details.click(timeout=5000)
        _wait_for_url_contains(
            page,
            "/my-account/details",
            flow,
            "account_page",
            "My Details menu option click",
            timeout_seconds=8,
        )
    if report_stage:
        report_stage("account_page")

    try:
        rewards = _wait_for_hut_rewards_option(page, flow, "hut_rewards")
        rewards.click(timeout=5000)
        _wait_for_url_contains(
            page,
            "/rewards",
            flow,
            "hut_rewards",
            "Hut Rewards click",
            timeout_seconds=10,
        )
        if report_stage:
            report_stage("rewards_page")
    except PizzahutError:
        raise
    except Exception as exc:
        raise PizzahutError(
            f"[{flow}/hut_rewards] could not open Hut Rewards from the account page. "
            f"Current URL: {page.url}"
        ) from exc

    _wait_for_hut_rewards_content(page, flow, "rewards_page")

    # The rewards page skeleton (headers, balance marker, etc.) can render
    # before the Limited Time Offers section is populated by async fetches.
    # Wait briefly for the offer content to settle so extraction does not
    # race ahead and miss visible offers.
    time.sleep(3)

    if report_stage:
        report_stage("rewards_loaded")


_EXTRACT_OFFERS_JS = r"""
        () => {
          const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
          const expiryPattern = /\bExpir(?:es?|ing)\s+in\s+[^.!?\n]+!?/i;
          const findHeading = (selector) => Array.from(
            document.querySelectorAll(selector)
          ).find((el) =>
            clean(el.innerText).toLowerCase() === 'limited time offers'
          );
          const heading =
            findHeading('h1, h2, h3, h4, h5, h6, [role="heading"]') ||
            findHeading('div, section, article, span, p, button, a');
          if (!heading) {
            return {
              sectionFound: false,
              offers: [],
            };
          }

          let section = heading.parentElement;
          while (section && section !== document.body) {
            if (expiryPattern.test(clean(section.innerText))) break;
            section = section.parentElement;
          }
          if (!section || section === document.body) {
            return {
              sectionFound: true,
              offers: [],
            };
          }

          const area = (el) => {
            const rect = el.getBoundingClientRect();
            return rect.width * rect.height;
          };
          const expiryElements = Array.from(section.querySelectorAll('*')).filter((el) => {
            const text = clean(el.innerText || el.textContent);
            if (!expiryPattern.test(text)) return false;
            return !Array.from(el.children).some((child) =>
              expiryPattern.test(clean(child.innerText || child.textContent))
            );
          }).sort((left, right) => area(left) - area(right));

          const records = [];
          for (const [recordIndex, expiryEl] of expiryElements.entries()) {
            let card = expiryEl.parentElement;
            let candidateName = '';
            let candidateExpiry = null;
            while (card && card !== section.parentElement) {
              const text = clean(card.innerText || card.textContent);
              const match = text.match(expiryPattern);
              if (match && card.children.length > 0) {
                let name = text.replace(match[0], ' ');
                name = name.replace(/\b(?:Redeem(?: Now)?|Start an order)\b/gi, ' ');
                name = name.replace(/[⏳⌛]/gu, ' ');
                name = clean(name).replace(/^[-.:;, ]+|[-.:;, ]+$/g, '');
                if (name && name.length >= 4) {
                  candidateName = name;
                  candidateExpiry = clean(match[0]);
                  break;
                }
              }
              card = card.parentElement;
            }
            if (
              !card ||
              card === document.body ||
              !candidateName ||
              !candidateExpiry
            ) {
              continue;
            }
            records.push({
              order: recordIndex,
              key: `${candidateName.toLowerCase()}::${candidateExpiry.toLowerCase()}`,
              name: candidateName,
              expiry: candidateExpiry,
              testid: card.getAttribute('data-testid'),
              outerHTML: card.outerHTML.slice(0, 500),
            });
          }

          records.sort((left, right) => left.order - right.order);

          const seen = new Set();
          const offers = [];
          for (const record of records) {
            if (seen.has(record.key)) continue;
            seen.add(record.key);
            offers.push({
              name: record.name,
              status: 'active',
              expiry: record.expiry,
            });
          }
          return {
            sectionFound: true,
            offers,
          };
        }
        """


def extract_limited_time_offers(session: BrowserSession) -> list[dict]:
    """Extract offer cards from the live section, or [] only after rewards loaded.

    Searches the main document AND every iframe content document for the
    "Limited time offers" heading and offer cards. Pizza Hut may render the
    rewards page content in the main document or inside embedded iframes, so
    keep checking later frames when an earlier frame finds only the section
    heading without offer cards.
    """
    page = session.page

    # Collect all frames: main frame first, then every iframe.
    frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]

    for frame in frames:
        result = frame.evaluate(_EXTRACT_OFFERS_JS)
        offers = list(result.get("offers", []))
        # A frame can contain the "Limited time offers" heading without the
        # offer cards (the heading renders in the main document while the
        # cards live in an embedded iframe). Only stop early when a frame
        # actually produced offers; otherwise keep checking later frames.
        if result.get("sectionFound") and offers:
            return offers

    # No frame produced offers (or no frame even contained the section).
    # Return empty, which callers treat as a valid "no offers" result.
    return []

def check_promotion(
    session: BrowserSession,
    selectors: dict[str, Any] | None = None,
    report_stage=None,
) -> list[dict]:
    navigate_to_hut_rewards(session, selectors, report_stage=report_stage)
    return extract_limited_time_offers(session)
