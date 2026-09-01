import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from pizzabot import pizzahut


def _evaluate_extract_offers_js_with_dom_fixture() -> dict:
    """Run the real _EXTRACT_OFFERS_JS against an in-memory DOM fixture via Node.

    The PizzaBot project does not exercise the site's real DOM in unit tests, so
    this helper gives us a focused regression test for the actual extractor JS.
    It is skipped when Node is not installed.
    """
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("node executable not available")

    js_literal = json.dumps(pizzahut._EXTRACT_OFFERS_JS)

    node_script = r"""
function matchOne(el, part) {
  part = part.trim();
  if (part === "*") return true;
  const tagMatch = part.match(/^([a-zA-Z][a-zA-Z0-9]*)/);
  const tag = tagMatch ? tagMatch[1].toUpperCase() : "*";
  if (tag !== "*" && el.tagName !== tag) return false;
  let m;
  const re = /\[([^\]]+)\]/g;
  while ((m = re.exec(part)) !== null) {
    const a = m[1];
    const am = a.match(/^([a-zA-Z-]+)\s*=\s*"?([^"]+)"?$/);
    if (!am) return false;
    if (el.getAttribute(am[1]) !== am[2]) return false;
  }
  return true;
}
function descendants(el) {
  let out = [];
  for (const c of el.children) {
    out.push(c);
    out = out.concat(descendants(c));
  }
  return out;
}
function matchSelector(sel, root) {
  const parts = sel.split(",").map((s) => s.trim()).filter(Boolean);
  return descendants(root).filter((el) => parts.some((p) => matchOne(el, p)));
}
function makeElement(tag, text, attrs) {
  const el = {
    tagName: tag.toUpperCase(),
    _text: text || "",
    _attrs: Object.assign({}, attrs || {}),
    children: [],
    parentElement: null,
    get innerText() {
      return this._text + (this.children.length ? " " : "") +
        this.children.map((c) => c.innerText).join(" ");
    },
    get textContent() {
      return this._text + this.children.map((c) => c.innerText).join("");
    },
    get outerHTML() {
      return "<" + this.tagName + ">" + this.innerText + "</" + this.tagName + ">";
    },
    getBoundingClientRect() {
      return { width: 120, height: 60 };
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this._attrs, name)
        ? this._attrs[name]
        : null;
    },
    querySelectorAll(sel) {
      return matchSelector(sel, this);
    },
  };
  return el;
}
function build(tag, text, attrs, kids) {
  const el = makeElement(tag, text, attrs);
  el.children = (kids || []).map((k) => {
    k.parentElement = el;
    return k;
  });
  return el;
}

const TITLE = "Welcome to Hut Rewards: Free Regular Breadsticks";
const EXP = "Expires in 30 days!";

// Div heading (no h1-h6 / role=heading) and the actual Rewards offer card
// shape: the expiry/redeem text is in a small sub-container, while a slightly
// higher container also includes the offer title. The extractor must climb
// past the sub-container rather than stopping there and discarding the offer.
const headingDiv = build("div", "Limited time offers", { class: "section-title" });
const title = build("div", TITLE, { class: "title" });
const expiry = build("span", EXP, { class: "expiry" });
const emoji = build("span", "\u23F3", { class: "emoji" });
const button = build("button", "Redeem Now", { class: "cta" });
const bottomText = build("div", "", { class: "MuiBox-root css-4utmtb" }, [
  expiry,
  emoji,
  button,
]);
const content = build("div", "", {
  class: "MuiGrid-root MuiGrid-container css-sw91qo",
}, [
  title,
  bottomText,
]);
const card = build("div", "", {
  class: "MuiPaper-root MuiCard-root css-1ynhwnc",
  "data-testid": "reward-offer-19900800",
}, [content]);
const section = build("section", "", {}, [headingDiv, card]);
const body = build("body", "", {}, [section]);
const document = {
  body,
  querySelectorAll(sel) {
    return matchSelector(sel, body);
  },
};

const js = __JS__;
const fn = eval("(" + js + ")");
console.log(JSON.stringify(fn()));
"""
    node_script = node_script.replace("__JS__", js_literal)

    fd, path = tempfile.mkstemp(suffix=".cjs", prefix="pizzabot_extract_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(node_script)
        proc = subprocess.run(
            [node, path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if proc.returncode != 0:
        raise AssertionError(
            "Node DOM fixture failed with stderr:\n" + proc.stderr
        )
    return json.loads(proc.stdout.strip())


class FakePage:
    def __init__(self, url):
        self.url = url

    def wait_for_timeout(self, _ms):
        pass


class FakeRequestedTimeModal:
    def __init__(self, present=True, close_present=True):
        self.present = present
        self.close_present = close_present
        self.click_count = 0
        self.close_click_count = 0
        self.wait_calls = []

    def filter(self, **_kwargs):
        return self

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.present else 0

    def is_visible(self, timeout=None):
        return self.present

    def get_by_role(self, role, name=None):
        assert role == "button"
        assert name.pattern.lower() == r"^\s*(close|dismiss)\s*$"
        return FakeCloseControl(self)

    def locator(self, selector):
        assert "aria-label" in selector
        return FakeCloseControl(self)

    def click(self, timeout=None):
        assert self.close_present
        self.click_count += 1

    def wait_for(self, state, timeout=None):
        self.wait_calls.append((state, timeout))


class FakeCloseControl:
    def __init__(self, modal):
        self.modal = modal

    @property
    def present(self):
        return self.modal.close_present

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.present else 0

    def is_visible(self, timeout=None):
        return self.present

    def click(self, timeout=None):
        assert self.present
        self.modal.close_click_count += 1


class FakeModalPage:
    def __init__(self, modal):
        self.url = "https://www.pizzahut.ca/order/deals"
        self.modal = modal

    def get_by_role(self, role, name=None):
        return f"{role}:heading"

    def locator(self, selector):
        assert selector == '[data-testid="requested-time-modal"]'
        return self.modal


class TestWaitForUrlContains(unittest.TestCase):
    def test_matching_url_returns_immediately(self):
        page = FakePage("https://www.pizzahut.ca/order/deals")
        pizzahut._wait_for_url_contains(
            page,
            "/order/deals",
            "promo",
            "deals_page",
            "verification login link",
            timeout_seconds=0,
        )

    def test_nonmatching_url_raises_with_expected_and_current(self):
        page = FakePage("https://www.pizzahut.ca/")
        with self.assertRaisesRegex(
            pizzahut.PizzahutError,
            r"Expected URL: '/order/deals'\. Current URL: https://www\.pizzahut\.ca/$",
        ):
            pizzahut._wait_for_url_contains(
                page,
                "/order/deals",
                "promo",
                "deals_page",
                "verification login link",
                timeout_seconds=0,
            )


class TestIsPromotionRootPage(unittest.TestCase):
    def test_root_urls(self):
        page = FakePage("https://www.pizzahut.ca/")
        self.assertTrue(pizzahut._is_promotion_root_page(page))

        page = FakePage("https://www.pizzahut.ca")
        self.assertTrue(pizzahut._is_promotion_root_page(page))

    def test_non_root_urls(self):
        page = FakePage("https://www.pizzahut.ca/order/deals")
        self.assertFalse(pizzahut._is_promotion_root_page(page))

        page = FakePage("https://www.pizzahut.ca/login")
        self.assertFalse(pizzahut._is_promotion_root_page(page))

        page = FakePage("https://www.pizzahut.ca/complete-profile")
        self.assertFalse(pizzahut._is_promotion_root_page(page))

        page = FakePage("https://www.pizzahut.ca/?query=1")
        self.assertFalse(pizzahut._is_promotion_root_page(page))


class TestNavigateToHutRewards(unittest.TestCase):
    def test_success_path_reports_stages_and_checks_urls(self):
        profile_control = Mock()
        rewards_control = Mock()
        wait_for_url = Mock()
        session = Mock()
        session.page = FakePage("https://www.pizzahut.ca/order/deals")
        selectors = {}
        stages = []

        with patch.object(pizzahut, "accept_cookies"), \
             patch.object(pizzahut, "_wait_for_url_contains", wait_for_url), \
             patch.object(pizzahut, "_close_requested_time_modal") as close_modal, \
             patch.object(pizzahut, "_wait_for_view_profile", return_value=profile_control), \
             patch.object(pizzahut, "_wait_for_hut_rewards_option", return_value=rewards_control), \
             patch.object(pizzahut, "_wait_for_hut_rewards_content"):
            pizzahut.navigate_to_hut_rewards(
                session,
                selectors,
            report_stage=stages.append,
        )

        close_modal.assert_called_once_with(session.page)
        self.assertEqual(
            stages,
            ["deals_page", "account_page", "rewards_page", "rewards_loaded"],
        )
        profile_control.hover.assert_called_once()
        profile_control.click.assert_called_once()

        expected_parts = ["/order/deals", "/my-account/details", "/rewards"]
        rewards_control.click.assert_called_once()
        actual_parts = [call.args[1] for call in wait_for_url.call_args_list]
        self.assertEqual(actual_parts, expected_parts)


    def test_root_start_continues_same_rewards_flow(self):
        profile_control = Mock()
        rewards_control = Mock()
        wait_for_url = Mock()
        session = Mock()
        session.page = FakePage("https://www.pizzahut.ca/")
        selectors = {}
        stages = []

        with patch.object(pizzahut, "accept_cookies"), \
             patch.object(pizzahut, "_wait_for_url_contains", wait_for_url), \
             patch.object(pizzahut, "_close_requested_time_modal") as close_modal, \
             patch.object(pizzahut, "_wait_for_view_profile", return_value=profile_control), \
             patch.object(pizzahut, "_wait_for_hut_rewards_option", return_value=rewards_control), \
             patch.object(pizzahut, "_wait_for_hut_rewards_content"):
            pizzahut.navigate_to_hut_rewards(
                session,
                selectors,
                report_stage=stages.append,
            )

        close_modal.assert_called_once_with(session.page)
        self.assertEqual(
            stages,
            ["deals_page", "account_page", "rewards_page", "rewards_loaded"],
        )
        profile_control.hover.assert_called_once()
        profile_control.click.assert_called_once()
        rewards_control.click.assert_called_once()

        expected_parts = ["/my-account/details", "/rewards"]
        actual_parts = [call.args[1] for call in wait_for_url.call_args_list]
        self.assertEqual(actual_parts, expected_parts)


class TestCloseRequestedTimeModal(unittest.TestCase):
    def test_closes_only_the_targeted_modal_and_waits_for_hidden(self):
        modal = FakeRequestedTimeModal()
        page = FakeModalPage(modal)

        self.assertTrue(pizzahut._close_requested_time_modal(page))
        self.assertEqual(modal.close_click_count, 1)
        self.assertEqual(modal.wait_calls, [("hidden", 8000)])

    def test_absent_modal_continues_without_clicking_anything(self):
        modal = FakeRequestedTimeModal(present=False)
        page = FakeModalPage(modal)

        self.assertFalse(pizzahut._close_requested_time_modal(page))
        self.assertEqual(modal.click_count, 0)
        self.assertEqual(modal.wait_calls, [])

    def test_missing_close_control_raises_a_stage_specific_error(self):
        modal = FakeRequestedTimeModal(close_present=False)
        page = FakeModalPage(modal)

        with self.assertRaisesRegex(
            pizzahut.PizzahutError,
            r"\[promo/deals_page\] 'Change Carryout Time' modal is open",
        ):
            pizzahut._close_requested_time_modal(page)


class TestCheckPromotion(unittest.TestCase):
    def test_passes_report_stage_to_navigation_and_returns_extracted_offers(self):
        session = Mock()
        extracted = [{"name": "Welcome", "status": "active", "expiry": "Expires in 28 days!"}]
        reporter = Mock()

        with patch.object(pizzahut, "navigate_to_hut_rewards") as nav, \
             patch.object(pizzahut, "extract_limited_time_offers", return_value=extracted) as extract:
            result = pizzahut.check_promotion(session, {}, report_stage=reporter)

        nav.assert_called_once_with(session, {}, report_stage=reporter)
        extract.assert_called_once_with(session)
        self.assertEqual(result, extracted)




class FakeUrlOnlyPage:
    """Page double without Playwright timing APIs.

    Wait loops that call page.wait_for_timeout() will hit AttributeError
    and break immediately -- the intended behaviour for unit tests.
    """
    def __init__(self, url):
        self.url = url


class FakeEmptyLocator:
    """A locator that reports zero matching elements."""
    def count(self):
        return 0

    @property
    def first(self):
        return self

    def is_visible(self, timeout=None):
        return False


class FakeEmptyAccountPage(FakeUrlOnlyPage):
    """Page at /order/deals that has no View Profile / account icon."""
    def get_by_role(self, role, name=None):
        return FakeEmptyLocator()

    def locator(self, selector):
        return FakeEmptyLocator()

    def get_by_text(self, pattern, exact=False):
        return FakeEmptyLocator()

    def evaluate(self, script):
        return {
            "url": self.url,
            "loggedInIcon": False,
            "loggedOutIcon": True,
            "appBarControls": [],
        }


class _FakeFrame:
    """A minimal frame double: evaluate() returns a canned result."""

    def __init__(self, evaluate_result):
        self._result = evaluate_result
        self.evaluate_call_count = 0
        self.evaluate_scripts = []

    def evaluate(self, script):
        self.evaluate_call_count += 1
        self.evaluate_scripts.append(script)
        return self._result


class FakeFramePage:
    """Page double modelling main_frame + iframe iteration.

    The promotion extractor walks every frame (main document first, then
    each iframe) because Pizza Hut renders the Rewards offers inside iframe
    content documents. By default the page has only a main frame; pass
    iframe_results to model offers that live in iframes.
    """

    def __init__(self, main_result, iframe_results=None):
        self.main_frame = _FakeFrame(main_result)
        iframes = [_FakeFrame(r) for r in (iframe_results or [])]
        self.frames = [self.main_frame] + iframes

    @property
    def evaluate_call_count(self):
        return sum(frame.evaluate_call_count for frame in self.frames)


class FakeExtractSession:
    def __init__(self, main_result, iframe_results=None):
        self.page = FakeFramePage(main_result, iframe_results)


class TestExtractLimitedTimeOffers(unittest.TestCase):
    """Unit tests for the promotion-offer extractor wrapper.

    Covers the actual bug: _EXTRACT_OFFERS_JS must be a defined module-level
    constant (otherwise the extractor raises NameError at runtime) and the
    extractor must search every frame, since the Rewards offers render inside
    iframe documents rather than the main frame.
    """

    def _session(self, main_result, iframe_results=None):
        return FakeExtractSession(main_result, iframe_results)

    def test_extract_offers_js_constant_is_defined(self):
        # Regression guard for the NameError bug: the JS must be restored as a
        # module-level constant instead of being referenced-but-undefined.
        js = pizzahut._EXTRACT_OFFERS_JS
        self.assertIsInstance(js, str)
        self.assertGreater(len(js), 0)
        self.assertIn("limited time offers", js)
        self.assertIn("getBoundingClientRect", js)
        # The actual Rewards card has a REDEEM button; name normalization must
        # strip it (currently via \bRedeem(?: Now)?\b) along with older CTAs.
        self.assertIn("Redeem(?: Now)?", js)

    def test_passes_extract_offers_js_to_each_frame_evaluate(self):
        session = self._session(
            {"sectionFound": False, "offers": []},
            iframe_results=[{"sectionFound": True, "offers": []}],
        )
        pizzahut.extract_limited_time_offers(session)
        # The exact same JS constant must be evaluated in every frame.
        for frame in session.page.frames:
            self.assertEqual(frame.evaluate_scripts, [pizzahut._EXTRACT_OFFERS_JS])

    def test_returns_single_offer_with_name_and_expiry(self):
        result = {
            "sectionFound": True,
            "offers": [
                {"name": "Free Pizza", "status": "active", "expiry": "Expires in 7 days!"}
            ],
        }
        offers = pizzahut.extract_limited_time_offers(self._session(result))
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["name"], "Free Pizza")
        self.assertEqual(offers[0]["status"], "active")
        self.assertEqual(offers[0]["expiry"], "Expires in 7 days!")

    def test_returns_multiple_offers_preserving_order(self):
        result = {
            "sectionFound": True,
            "offers": [
                {"name": "Free Pizza", "status": "active", "expiry": "Expires in 7 days!"},
                {"name": "$5 Off Any Pizza", "status": "active", "expiry": "Expiring in 2 days!"},
            ],
        }
        offers = pizzahut.extract_limited_time_offers(self._session(result))
        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0]["name"], "Free Pizza")
        self.assertEqual(offers[1]["name"], "$5 Off Any Pizza")

    def test_no_section_returns_empty_list(self):
        offers = pizzahut.extract_limited_time_offers(
            self._session({"sectionFound": False, "offers": []})
        )
        self.assertEqual(offers, [])

    def test_section_found_but_no_offers(self):
        offers = pizzahut.extract_limited_time_offers(
            self._session({"sectionFound": True, "offers": []})
        )
        self.assertEqual(offers, [])

    def test_returns_empty_list_when_offers_key_missing(self):
        offers = pizzahut.extract_limited_time_offers(
            self._session({"sectionFound": True})
        )
        self.assertEqual(offers, [])

    def test_duplicate_offers_passthrough(self):
        """Dedup is handled in the JS; Python returns exactly what evaluate() returns."""
        result = {
            "sectionFound": True,
            "offers": [
                {"name": "Free Pizza", "status": "active", "expiry": "Expires in 7 days!"},
                {"name": "Free Pizza", "status": "active", "expiry": "Expires in 7 days!"},
            ],
        }
        offers = pizzahut.extract_limited_time_offers(self._session(result))
        self.assertEqual(len(offers), 2)

    def test_calls_page_evaluate_once(self):
        # Only a main frame exists and it has no section, so exactly one
        # evaluate() call happens (the constant) and no iframe fallback runs.
        session = self._session({"sectionFound": False, "offers": []})
        pizzahut.extract_limited_time_offers(session)
        self.assertEqual(session.page.evaluate_call_count, 1)

    def test_extracts_offers_from_iframe_when_main_frame_has_no_section(self):
        # The core fix: offers render inside an iframe document, so the main
        # frame reports no section while the iframe reports the offers.
        iframe_offers = [
            {"name": "Free Pizza", "status": "active", "expiry": "Expires in 7 days!"},
        ]
        session = self._session(
            {"sectionFound": False, "offers": []},
            iframe_results=[{"sectionFound": True, "offers": iframe_offers}],
        )
        offers = pizzahut.extract_limited_time_offers(session)
        self.assertEqual(offers, iframe_offers)
        self.assertEqual(session.page.frames[1].evaluate_call_count, 1)

    def test_prefers_iframe_offers_when_main_frame_finds_section_but_no_offers(self):
        # Realistic frame split: the main document contains the "Limited time
        # offers" heading (so its JS returns sectionFound=True), but the offer
        # cards live inside an embedded iframe. The extractor must not stop at
        # that empty main-frame result; it must continue to later frames.
        iframe_offers = [{
            "name": "Welcome to Hut Rewards: Free Regular Breadsticks",
            "status": "active",
            "expiry": "Expires in 30 days!",
        }]
        session = self._session(
            {"sectionFound": True, "offers": []},
            iframe_results=[{"sectionFound": True, "offers": iframe_offers}],
        )
        offers = pizzahut.extract_limited_time_offers(session)
        self.assertEqual(offers, iframe_offers)
        self.assertEqual(session.page.frames[1].evaluate_call_count, 1)

    @unittest.skipUnless(shutil.which("node"), "node executable not available")
    def test_real_js_extracts_div_heading_offer_card_with_redeem_button(self):
        """Regression: the Rewards offer card has a div title and a REDEEM button, not h1-h6.

        Run the actual _EXTRACT_OFFERS_JS against a DOM fixture matching the
        visually-verified Rewards page. The extractor must find the card even
        when the title is not an h1-h6/[role=heading] element, and the bare
        REDEEM button text must not be included in the offer name.
        """
        result = _evaluate_extract_offers_js_with_dom_fixture()
        self.assertTrue(result["sectionFound"])
        self.assertEqual(
            result["offers"],
            [
                {
                    "name": "Welcome to Hut Rewards: Free Regular Breadsticks",
                    "status": "active",
                    "expiry": "Expires in 30 days!",
                }
            ],
        )

    def test_returns_empty_when_no_frame_has_section(self):
        offers = pizzahut.extract_limited_time_offers(
            self._session(
                {"sectionFound": False, "offers": []},
                iframe_results=[
                    {"sectionFound": False, "offers": []},
                    {"sectionFound": False, "offers": []},
                ],
            )
        )
        self.assertEqual(offers, [])

    def test_stops_at_first_frame_with_section(self):
        # The main frame already contains offers, so the iframe must not be
        # evaluated at all (document order preserved; first match wins).
        main = {
            "sectionFound": True,
            "offers": [
                {"name": "Main Offer", "status": "active", "expiry": "Expires in 1 day!"}
            ],
        }
        iframe = {
            "sectionFound": True,
            "offers": [
                {"name": "Iframe Offer", "status": "active", "expiry": "Expires in 1 day!"}
            ],
        }
        session = self._session(main, [iframe])
        offers = pizzahut.extract_limited_time_offers(session)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["name"], "Main Offer")
        self.assertEqual(session.page.frames[1].evaluate_call_count, 0)



class TestNavigateToHutRewardsErrorPaths(unittest.TestCase):
    """Error-path tests for the promotion navigation flow."""

    def test_unsupported_start_url_raises_deals_page_error(self):
        """Login link landing at an unsupported URL raises [promo/deals_page]."""
        session = Mock()
        session.page = FakeUrlOnlyPage("https://www.pizzahut.ca/login")
        with patch.object(pizzahut, "accept_cookies"):
            with self.assertRaises(pizzahut.PizzahutError) as cm:
                pizzahut.navigate_to_hut_rewards(
                    session, {}, report_stage=lambda s: None
                )
        msg = str(cm.exception)
        self.assertIn("[promo/deals_page]", msg)
        self.assertIn("https://www.pizzahut.ca/login", msg)

    def test_view_profile_unavailable_raises_account_page_error(self):
        """View Profile icon not rendered raises [promo/account_page] with current URL."""
        page = FakeEmptyAccountPage("https://www.pizzahut.ca/order/deals")
        session = Mock()
        session.page = page
        with patch.object(pizzahut, "accept_cookies"), \
             patch.object(pizzahut, "_close_requested_time_modal", return_value=False):
            with self.assertRaises(pizzahut.PizzahutError) as cm:
                pizzahut.navigate_to_hut_rewards(
                    session, {}, report_stage=lambda s: None
                )
        msg = str(cm.exception)
        self.assertIn("[promo/account_page]", msg)
        self.assertIn("order/deals", msg)

    def test_direct_view_profile_wait_raises_with_stage_and_url(self):
        """_wait_for_view_profile directly raises [promo/account_page] with URL info."""
        page = FakeEmptyAccountPage("https://www.pizzahut.ca/order/deals")
        with self.assertRaises(pizzahut.PizzahutError) as cm:
            pizzahut._wait_for_view_profile(
                page, "promo", "account_page", timeout_seconds=0
            )
        msg = str(cm.exception)
        self.assertIn("[promo/account_page]", msg)
        self.assertIn("order/deals", msg)

    def test_modal_close_failure_propagates_through_navigation(self):
        """A failure in _close_requested_time_modal propagates as [promo/deals_page]."""
        session = Mock()
        session.page = FakeUrlOnlyPage("https://www.pizzahut.ca/order/deals")
        with patch.object(pizzahut, "accept_cookies"), \
             patch.object(
                pizzahut, "_close_requested_time_modal",
                side_effect=pizzahut.PizzahutError(
                    "[promo/deals_page] 'Change Carryout Time' modal is open, "
                    "but its close control was not found."
                ),
             ):
            with self.assertRaisesRegex(pizzahut.PizzahutError, r"\[promo/deals_page\]"):
                pizzahut.navigate_to_hut_rewards(
                    session, {}, report_stage=lambda s: None
                )

    def test_hut_rewards_option_missing_raises_with_stage_and_url(self):
        """_wait_for_hut_rewards_option raises [promo/hut_rewards] when absent."""
        page = FakeEmptyAccountPage("https://www.pizzahut.ca/my-account/details")
        with self.assertRaises(pizzahut.PizzahutError) as cm:
            pizzahut._wait_for_hut_rewards_option(
                page, "promo", "hut_rewards", timeout_seconds=0
            )
        msg = str(cm.exception)
        self.assertIn("[promo/hut_rewards]", msg)
        self.assertIn("my-account/details", msg)

if __name__ == "__main__":
    unittest.main()
