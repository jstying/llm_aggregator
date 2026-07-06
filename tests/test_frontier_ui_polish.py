"""Tests for the 2026-07-09 UI polish pass: Frontier-only mode also locks the free-model
dropdown (not just the checkboxes), the Trial Quota pill grays out once a provider hits
0 remaining, frontier model <select> dropdowns are restyled off the browser default
blue/white, and blind-review score badges are colored by a 3-tier scale. The dropdown-lock
and native-select-restyle pieces are pure CSS/JS (no automated frontend test framework in
this project, per CLAUDE.md section 9) -- these tests pin down what IS verifiable from the
server-rendered HTML: the CSS rules exist, and the quota badge's is-exhausted class is wired
to real usage numbers rather than being frontend-guessed.
"""
import re
import unittest
from unittest.mock import patch

import main


def _render_index_as_guest():
    with main.app.test_client() as client:
        with client.session_transaction() as sess:
            sess['is_guest'] = True
        return client.get('/').data.decode()


def _render_index_as_user():
    with main.app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 'uid1'
        return client.get('/').data.decode()


class TestTrialQuotaExhaustedClass(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _badge_exhausted_map(self, html):
        """Maps each pill's visible label ("Claude", "ChatGPT", ...) to whether its
        <span class="trial-quota-badge ..."> carries is-exhausted, without relying on a
        fixed slice window that can bleed into a neighboring pill."""
        pattern = re.compile(r'<span class="trial-quota-badge( is-exhausted)?">(\w+):')
        return {label: bool(exhausted) for exhausted, label in pattern.findall(html)}

    def test_fully_exhausted_provider_gets_is_exhausted_class(self):
        with patch.object(main, 'get_claude_free_tier_usage', return_value=main.CLAUDE_FREE_TIER_LIMIT), \
             patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
             patch.object(main, 'get_free_tier_usage', return_value=0):
            html = _render_index_as_user()
        self.assertTrue(self._badge_exhausted_map(html)['Claude'])

    def test_provider_with_remaining_quota_has_no_is_exhausted_class(self):
        with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
             patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
             patch.object(main, 'get_free_tier_usage', return_value=0):
            html = _render_index_as_user()
        self.assertFalse(self._badge_exhausted_map(html)['Claude'])

    def test_exhausted_state_is_per_provider_not_global(self):
        """Only Claude is exhausted -- ChatGPT still has quota left, so only Claude's pill
        should carry the class."""
        with patch.object(main, 'get_claude_free_tier_usage', return_value=main.CLAUDE_FREE_TIER_LIMIT), \
             patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
             patch.object(main, 'get_free_tier_usage', return_value=0):
            html = _render_index_as_user()
        badges = self._badge_exhausted_map(html)
        self.assertTrue(badges['Claude'])
        self.assertFalse(badges['ChatGPT'])

    def test_js_toggles_is_exhausted_from_live_quota_refresh(self):
        html = _render_index_as_user()
        self.assertIn("pillEl.classList.toggle('is-exhausted', remaining <= 0)", html)


class TestFrontierOnlyDropdownLockMarkup(unittest.TestCase):
    """Frontier-only mode must also gray out the free-model dropdown itself (not just the
    provider checkboxes) and disable it until the mode is switched off again."""

    def test_index_html_defines_locked_dropdown_style(self):
        html = _render_index_as_guest()
        self.assertIn('.custom-select-wrapper.is-locked .custom-select-trigger', html)

    def test_dropdown_trigger_click_handlers_check_lock_state(self):
        html = _render_index_as_guest()
        self.assertIn("customWrapper.classList.contains('is-locked')", html)
        self.assertIn("imageCustomWrapper.classList.contains('is-locked')", html)

    def test_frontier_only_toggles_lock_the_dropdown_wrapper(self):
        # 2026-07-09: the dropdown's lock state is now centralized in
        # updateModelSelectLockState()/updateImageModelSelectLockState() (locked when
        # frontier-only mode is on OR no free provider is checked to bind the dropdown
        # to), rather than the toggle handler setting the class directly. Both toggle
        # handlers must still end up calling that recompute after flipping the flag.
        html = _render_index_as_guest()
        self.assertIn("const locked = frontierOnlyActive || !activeModelProvider();", html)
        self.assertIn(
            "const locked = frontierOnlyImageActive || !activeImageModelProvider();", html
        )
        self.assertIn("if (frontierOnlyActive) providerCheckOrder = [];\n                updateModelDropdown();", html)
        self.assertIn(
            "if (frontierOnlyImageActive) imageProviderCheckOrder = [];\n                updateImageModelDropdown();",
            html,
        )

    def test_clear_buttons_unlock_the_dropdown_wrapper(self):
        # Clear Results no longer removes 'is-locked' directly either -- it resets
        # providerCheckOrder/imageProviderCheckOrder to empty and calls
        # updateModelDropdown()/updateImageModelDropdown(), which re-locks the wrapper
        # (nothing checked to bind to) via the same centralized lock-state functions.
        html = _render_index_as_guest()
        self.assertIn("providerCheckOrder = [];", html)
        self.assertIn("imageProviderCheckOrder = [];", html)
        clear_btn_start = html.index("document.getElementById('clearBtn').addEventListener")
        clear_btn_block = html[clear_btn_start:clear_btn_start + 1200]
        self.assertIn('updateModelDropdown();', clear_btn_block)
        clear_images_btn_start = html.index("document.getElementById('clearImagesBtn').addEventListener")
        clear_images_btn_block = html[clear_images_btn_start:clear_images_btn_start + 1200]
        self.assertIn('updateImageModelDropdown();', clear_images_btn_block)



class TestPeerReviewScoreColorTiers(unittest.TestCase):

    def test_index_defines_three_score_tier_css_classes(self):
        html = _render_index_as_guest()
        self.assertIn('.review-score-badge.score-low', html)
        self.assertIn('#E8F5E9', html)
        self.assertIn('.review-score-badge.score-mid', html)
        self.assertIn('#66BB6A', html)
        self.assertIn('.review-score-badge.score-high', html)
        self.assertIn('#1B5E20', html)

    def test_index_render_peer_reviews_applies_score_color_class(self):
        html = _render_index_as_guest()
        self.assertIn(
            '<span class="review-score-badge ${scoreColorClass(r.score)}">${r.score} pts</span>',
            html,
        )

    def test_history_page_defines_same_three_score_tier_css_classes(self):
        # Guest view renders history.html without needing a real Firestore entry -- the
        # CSS block itself is static markup, unaffected by which entry is shown.
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            html = client.get('/history/anything').data.decode()
        self.assertIn('.review-score-badge.score-low', html)
        self.assertIn('.review-score-badge.score-mid', html)
        self.assertIn('.review-score-badge.score-high', html)


if __name__ == '__main__':
    unittest.main()
