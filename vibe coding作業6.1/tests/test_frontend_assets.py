import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendAccessibilityAssetTests(unittest.TestCase):
    def test_navigation_has_disclosure_attributes_and_keyboard_close(self):
        header = (ROOT / "templates" / "_site_header.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn('aria-controls="primary-navigation"', header)
        self.assertIn('id="primary-navigation"', header)
        self.assertIn("event.key === 'Escape'", script)
        self.assertIn("event.target instanceof Element", script)
        self.assertIn("document.documentElement.classList.add('js-enabled')", script)
        self.assertIn("form.dataset.submitting === 'true'", script)

    def test_css_includes_focus_touch_wrap_and_reduced_motion_rules(self):
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn("a:focus-visible", css)
        self.assertIn("button:focus-visible", css)
        self.assertIn("min-height:44px", css)
        self.assertIn("overflow-wrap:anywhere", css)
        self.assertIn("@media(prefers-reduced-motion:reduce)", css)
        self.assertNotRegex(css, r"outline\s*:\s*(?:none|0)(?:\s*[;}])")

    def test_obsolete_coming_soon_and_disabled_save_rules_are_removed(self):
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertNotIn(".button-soon", css)
        self.assertNotIn(".save-button", css)
        self.assertNotIn("coming-soon", script)


if __name__ == "__main__":
    unittest.main()
