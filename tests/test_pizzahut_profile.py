import unittest
from unittest.mock import Mock, patch

from pizzabot import pizzahut


class FakePage:
    url = "https://www.pizzahut.ca/complete-profile"

    def evaluate(self, _script):
        return []

    def get_by_role(self, role, name=None):
        return FakeRoleBoxes([])

    def locator(self, _selector):
        return FakeLabelQuery([])

    def evaluate_handle(self, _script):
        return FakeElementHandle(None)


class TestTermsCheckbox(unittest.TestCase):
    def test_missing_terms_raises_stage_specific_error(self):
        page = FakePage()

        with self.assertRaisesRegex(
            pizzahut.PizzahutError,
            r"\[create/complete_profile\].*Terms & Conditions checkbox",
        ):
            pizzahut._ensure_terms_checked(page, "create", "complete_profile")


    def test_complete_profile_checks_terms_after_marketing_and_before_submit(self):
        page = FakePage()
        session = Mock(page=page)
        profile = {
            "first_name": "First",
            "last_name": "Last",
            "phone": "6135551234",
            "birthday": "2005-05-05",
        }

        with patch.object(pizzahut, "accept_cookies"), \
             patch.object(pizzahut, "_fill_visible", return_value=True), \
             patch.object(pizzahut, "_fill_birthday", return_value=True), \
             patch.object(pizzahut, "_fill_terms_and_marketing") as fill_terms_and_marketing, \
             patch.object(pizzahut, "click_first", return_value=True) as submit:
            manager = Mock()
            manager.attach_mock(fill_terms_and_marketing, "fill_terms_and_marketing")
            manager.attach_mock(submit, "submit")

            pizzahut.complete_profile(session, profile)

        method_names = [call_item[0] for call_item in manager.method_calls]
        self.assertEqual(method_names, ["fill_terms_and_marketing", "submit"])
        submit_locator_options = submit.call_args.args[1]
        self.assertEqual(submit_locator_options[0], "button:has-text('Create Account')")


class TestTermsAndMarketingOrder(unittest.TestCase):
    def test_all_checkbox_states_are_verified_before_submission_helper(self):
        page = Mock(url="https://www.pizzahut.ca/complete-profile")
        with patch.object(pizzahut, "_ensure_marketing_unchecked") as marketing, \
             patch.object(pizzahut, "_ensure_terms_checked") as terms:
            manager = Mock()
            manager.attach_mock(marketing, "marketing")
            manager.attach_mock(terms, "terms")
            pizzahut._fill_terms_and_marketing(page, "create", "complete_profile")

        self.assertEqual(
            [method[0] for method in manager.method_calls],
            ["marketing", "terms"],
        )
        marketing.assert_called_once_with(page, "create", "complete_profile")
        terms.assert_called_once_with(page, "create", "complete_profile")


class FakeCheckbox:
    def __init__(self, checked=False):
        self.checked = checked

    def is_visible(self, timeout=0):
        return True

    def evaluate(self, _script):
        return True

    def is_checked(self):
        return self.checked


class FakeRoleBoxes:
    def __init__(self, boxes):
        self.boxes = boxes

    def count(self):
        return len(self.boxes)

    def nth(self, index):
        return self.boxes[index]


class SelectorFakePage:
    url = "https://www.pizzahut.ca/complete-profile"

    def __init__(self):
        self.role_name_patterns = []
        self.role_queries = []
        self.email_boxes = FakeRoleBoxes([FakeCheckbox()])
        self.sms_boxes = FakeRoleBoxes([FakeCheckbox()])
        self.terms_boxes = FakeRoleBoxes([FakeCheckbox()])

    def get_by_role(self, role, name=None):
        assert role == "checkbox"
        self.role_name_patterns.append(name.pattern)
        if name.pattern == r"^\s*Email\s*$":
            return self.email_boxes
        if name.pattern == r"^\s*SMS\s*$":
            return self.sms_boxes
        raise AssertionError(f"Unexpected accessible-name pattern: {name.pattern!r}")


class FakeElementHandle:
    def __init__(self, element):
        self.element = element

    def as_element(self):
        return self.element


class CheckedTermsInput(FakeCheckbox):
    def evaluate(self, script):
        if "getAttribute('data-testid')" in script or 'getAttribute("data-testid")' in script:
            return "termsAndConditions"
        return True


class ContainerFakePage:
    def __init__(self):
        self.checkbox = object()
        self.script = None

    def evaluate_handle(self, script):
        self.script = script
        return FakeElementHandle(self.checkbox)


class TermsControlFakePage(ContainerFakePage):
    url = "https://www.pizzahut.ca/complete-profile"

    def __init__(self):
        super().__init__()
        self.checkbox = CheckedTermsInput(checked=True)
        self.wrapper = Mock()
        self.wrapper_locator = Mock()
        self.wrapper_locator.first = self.wrapper
        self.wrapper_selector = None

    def evaluate(self, _script):
        return "termsAndConditions"

    def locator(self, selector):
        self.wrapper_selector = selector
        return self.wrapper_locator


class FakeLabelQuery:
    def __init__(self, labels):
        self.labels = labels

    def filter(self, has_text=None):
        return self

    def count(self):
        return len(self.labels)

    def nth(self, index):
        return self.labels[index]


class TestCheckboxSelectors(unittest.TestCase):
    def test_email_and_sms_are_targeted_separately_and_left_unchecked(self):
        page = SelectorFakePage()
        pizzahut._ensure_marketing_unchecked(page, "create", "complete_profile")
        self.assertEqual(
            page.role_name_patterns,
            [r"^\s*Email\s*$", r"^\s*SMS\s*$"],
        )

    def test_terms_uses_nearest_shared_container_not_assumed_label(self):
        page = ContainerFakePage()
        box = pizzahut._find_terms_checkbox(page)
        self.assertIs(box, page.checkbox)
        self.assertIn("by ticking this box", page.script)
        self.assertIn("privacy policy", page.script)
        self.assertIn('input[type="checkbox"]', page.script)
        self.assertNotIn("data-testid", page.script)

    def test_terms_clicks_discovered_square_and_verifies_refreshed_state(self):
        page = TermsControlFakePage()
        pizzahut._ensure_terms_checked(page, "create", "complete_profile")
        self.assertEqual(page.wrapper_selector, '[data-testid="termsAndConditions"]')
        page.wrapper.click.assert_called_once_with(timeout=5000)


if __name__ == "__main__":
    unittest.main()
