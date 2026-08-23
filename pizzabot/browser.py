"""Playwright browser session wrapper."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


class BrowserSession:
    def __init__(self, *, headless: bool = False, slow_mo_ms: int = 300) -> None:
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(
            headless=headless,
            slow_mo=int(slow_mo_ms),
            args=["--disable-blink-features=AutomationControlled", "--disable-http2"],
        )
        context = self.browser.new_context(
            viewport={"width": 1366, "height": 900},
            locale="en-CA",
            timezone_id="America/Toronto",
        )
        self.page: Page = context.new_page()

    @property
    def page(self) -> Page:
        return self._page

    @page.setter
    def page(self, value: Page) -> None:
        self._page = value

    def goto(self, url: str, timeout_ms: int = 60_000) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def screenshot(self, name: str) -> Path:
        p = Path(f"{name}.screenshot.png")
        self.page.screenshot(path=str(p), full_page=True)
        return p

    def close(self) -> None:
        try:
            self.browser.close()
        finally:
            self._playwright.stop()

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def visible_locator(page: Page, locators: list[str]) -> any:
    for locator in locators:
        try:
            loc = page.locator(locator).first
            if loc.is_visible(timeout=1500):
                return loc
        except (PlaywrightTimeoutError, Exception):
            continue
    return None


def click_first(page: Page, locators: list[str], fallback_texts: list[str] | None = None) -> bool:
    loc = visible_locator(page, locators)
    if loc is not None:
        loc.click(timeout=3000)
        return True
    if fallback_texts:
        for text in fallback_texts:
            try:
                loc = page.get_by_text(text, exact=False).first
                if loc.is_visible(timeout=1000):
                    loc.click(timeout=3000)
                    return True
            except Exception:
                continue
    return False
