"""Unit tests for element normalisation."""

from testgen.normalisation.element_normaliser import normalise_element, get_selector_priority


class TestNormaliseElement:
    def test_role_with_name(self):
        target = {
            "selectors": {"role": {"role": "button", "name": "Submit"}},
            "tag": "BUTTON",
        }
        result = normalise_element(target)
        assert result == 'getByRole("button", { name: "Submit" })'

    def test_role_without_name(self):
        target = {
            "selectors": {"role": {"role": "navigation"}},
            "tag": "NAV",
        }
        result = normalise_element(target)
        assert result == 'getByRole("navigation")'

    def test_testid(self):
        target = {
            "selectors": {"testid": "submit-btn"},
            "tag": "BUTTON",
        }
        result = normalise_element(target)
        assert result == 'getByTestId("submit-btn")'

    def test_aria_label(self):
        target = {
            "selectors": {},
            "tag": "BUTTON",
            "attributes": {"aria-label": "Close dialog"},
        }
        result = normalise_element(target)
        assert result == 'getByLabel("Close dialog")'

    def test_placeholder(self):
        target = {
            "selectors": {"placeholder": "Enter email"},
            "tag": "INPUT",
        }
        result = normalise_element(target)
        assert result == 'getByPlaceholder("Enter email")'

    def test_text_content_for_button(self):
        target = {
            "selectors": {},
            "tag": "BUTTON",
            "text_content": "Save Changes",
        }
        result = normalise_element(target)
        assert result == 'getByText("Save Changes")'

    def test_css_fallback(self):
        target = {
            "selectors": {"css": "#main-content"},
            "tag": "DIV",
        }
        result = normalise_element(target)
        assert result == 'locator("#main-content")'

    def test_tag_fallback(self):
        target = {"selectors": {}, "tag": "DIV"}
        result = normalise_element(target)
        assert result == 'locator("div")'

    def test_textbox_without_name_falls_through_to_placeholder(self):
        """Interaction role without name should fall through to placeholder."""
        target = {
            "selectors": {
                "role": {"role": "textbox"},
                "placeholder": "Enter email",
            },
            "tag": "INPUT",
        }
        result = normalise_element(target)
        assert result == 'getByPlaceholder("Enter email")'

    def test_textbox_without_name_falls_through_to_testid(self):
        """Interaction role without name should fall through to testid."""
        target = {
            "selectors": {
                "role": {"role": "textbox"},
                "testid": "email-input",
            },
            "tag": "INPUT",
        }
        result = normalise_element(target)
        assert result == 'getByTestId("email-input")'

    def test_textbox_without_name_falls_through_to_css(self):
        """Interaction role without name and no other selectors → CSS fallback."""
        target = {
            "selectors": {
                "role": {"role": "textbox"},
                "css": "input#email",
            },
            "tag": "INPUT",
        }
        result = normalise_element(target)
        assert result == 'locator("input#email")'

    def test_structural_role_without_name_still_works(self):
        """Structural/landmark roles remain valid without a name."""
        target = {
            "selectors": {"role": {"role": "main"}},
            "tag": "MAIN",
        }
        result = normalise_element(target)
        assert result == 'getByRole("main")'

    def test_none_target(self):
        assert normalise_element(None) is None

    def test_empty_target(self):
        result = normalise_element({})
        assert result is None

    def test_whitespace_collapsed_in_name(self):
        """Element names with newlines/tabs should be collapsed to single spaces."""
        target = {
            "selectors": {"role": {"role": "link", "name": "TS\n      TestSite"}},
            "tag": "A",
        }
        result = normalise_element(target)
        assert result == 'getByRole("link", { name: "TS TestSite" })'

    def test_whitespace_collapsed_in_placeholder(self):
        target = {
            "selectors": {"placeholder": "Enter\n  your\temail"},
            "tag": "INPUT",
        }
        result = normalise_element(target)
        assert result == 'getByPlaceholder("Enter your email")'

    def test_whitespace_collapsed_in_text(self):
        target = {
            "selectors": {},
            "tag": "BUTTON",
            "text_content": "Submit\n  Form",
        }
        result = normalise_element(target)
        assert result == 'getByText("Submit Form")'


class TestSelectorPriority:
    def test_role_highest(self):
        target = {
            "selectors": {"role": {"role": "button", "name": "Submit"}, "css": "btn"},
            "tag": "BUTTON",
        }
        assert get_selector_priority(target) == 1

    def test_testid_second(self):
        target = {
            "selectors": {"testid": "submit-btn", "css": "btn"},
            "tag": "BUTTON",
        }
        assert get_selector_priority(target) == 2

    def test_none_target(self):
        assert get_selector_priority(None) == 99

    def test_css_low_priority(self):
        target = {"selectors": {"css": ".btn"}, "tag": "BUTTON"}
        assert get_selector_priority(target) == 6

    def test_textbox_without_name_falls_through_to_testid_priority(self):
        """Interaction role without name should not get priority 1."""
        target = {
            "selectors": {
                "role": {"role": "textbox"},
                "testid": "email-input",
            },
            "tag": "INPUT",
        }
        assert get_selector_priority(target) == 2

    def test_structural_role_without_name_gets_priority_1(self):
        target = {
            "selectors": {"role": {"role": "navigation"}},
            "tag": "NAV",
        }
        assert get_selector_priority(target) == 1
