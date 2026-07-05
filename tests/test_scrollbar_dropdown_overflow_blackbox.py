import re
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class TestModelDropdownClosedStateHasZeroOverflowFootprint(unittest.TestCase):
    """2026-07-04 bug fix: the page's right-hand custom scrollbar (#pageScrollThumb)
    used to visibly shift position/size the first time a user checked a provider
    checkbox or submitted Compare, and stayed "unlocked" (kept jittering) before that.

    Root cause: `.custom-options` (the custom "Select Model" dropdown panel -- shared
    by both #customOptions in chat mode and #imageCustomOptions in image mode) was
    hidden while closed only via `opacity:0`/`visibility:hidden`, while its box itself
    stayed `display:block`, `position:absolute` at up to `max-height:250px`. Even
    though invisible, that box still counted toward document.documentElement's
    scrollable overflow, and its size tracked however many <option> children
    updateModelDropdown()/updateImageModelDropdown() had most recently populated it
    with (the union of every *selected* provider's models -- the largest possible set
    when none are checked, which is the default state on page load). So the page's
    true scrollHeight -- and therefore #pageScrollThumb's math, which reads
    document.documentElement.scrollHeight directly -- silently depended on the current
    provider selection even though the dropdown was never visibly open. Checking a
    provider box (narrows the pooled set) or submitting Compare (adds enough real
    #results content to make the leftover phantom height negligible) is what made it
    look like the scrollbar "locked" only after interacting.

    Verified directly against a running instance with headless Chromium: forcing
    `display:none` on the closed panel dropped document.documentElement.scrollHeight
    from 987 to 968 -- an exact match for the gap observed when toggling a provider
    checkbox before this fix, and toggling checkboxes after this fix no longer moves
    scrollHeight (or #pageScrollThumb's computed top/height) at all. That kind of
    real-browser layout verification has no automated test harness in this project
    (see CLAUDE.md's front-end testing note -- no JS test framework, Playwright runs
    are manual/undocumented-as-code), so this test instead locks down the specific CSS
    declarations the fix depends on: the *closed* state must clamp `max-height`/
    `overflow` to a value that cannot vary with pooled option count (i.e. can't be a
    large fixed pixel value like the old `max-height:250px`), while the `.open` state
    must still restore a workable scrollable max-height so the dropdown remains usable
    (a long, unioned model list must still be reachable via internal scroll) once
    actually opened."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _get_index_html(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        return response.data.decode()

    def _extract_rule_body(self, html, selector_line_regex):
        match = re.search(
            selector_line_regex + r'\s*\{([^}]*)\}', html, flags=re.MULTILINE
        )
        self.assertIsNotNone(
            match, f"could not find CSS rule matching {selector_line_regex!r} in rendered HTML"
        )
        return match.group(1)

    def test_closed_state_clamps_max_height_to_zero(self):
        html = self._get_index_html()
        # Anchored on start-of-line (ignoring leading whitespace) so this can't
        # accidentally match the more specific ".custom-select-wrapper.open
        # .custom-options" descendant selector, which also contains this substring.
        body = self._extract_rule_body(html, r'^\s*\.custom-options')
        self.assertIn('max-height: 0', body)
        self.assertIn('overflow: hidden', body)
        # The old, buggy value -- must not have been merely moved around.
        self.assertNotIn('max-height: 250px', body)

    def test_open_state_restores_scrollable_max_height(self):
        html = self._get_index_html()
        body = self._extract_rule_body(
            html, r'^\s*\.custom-select-wrapper\.open \.custom-options'
        )
        self.assertIn('max-height: 250px', body)
        self.assertIn('overflow-y: auto', body)

    def test_both_chat_and_image_dropdown_panels_share_the_fixed_class(self):
        """A single shared `.custom-options` CSS rule is what makes the fix apply to
        both dropdowns at once -- if either panel ever stopped using this class (e.g.
        got a bespoke one, mirroring the .provider-checkbox/.image-provider-checkbox
        split elsewhere in this file for an unrelated reason), it would silently drop
        out of this fix's coverage."""
        html = self._get_index_html()
        self.assertIn('class="custom-options" id="customOptions"', html)
        self.assertIn('class="custom-options" id="imageCustomOptions"', html)


if __name__ == '__main__':
    unittest.main()
