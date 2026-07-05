"""Tests for the 2026-07-05 "Trial Quota" feature.

Three things changed together and are covered here:
1. `CLAUDE_FREE_TIER_LIMIT`/`GEMINI_FREE_TIER_LIMIT` raised from 1 to 10 (see
   `tests/test_claude_integration.py`/`tests/test_gemini_integration.py` for the
   pre-existing free-tier-exhausted/first-request-succeeds flows, which were updated in
   place to read the limit off the constant rather than hardcoding 1).
2. A new `GET /api/quota-status` endpoint (auth-guarded like the Claude/Gemini/history
   routes) that reports `{claude: {used, limit}, gemini: {used, limit}}` for the current
   user, used by the frontend to refresh the nav-bar badge after every Claude/Gemini call
   without having to guess client-side whether that call actually consumed a use (own-key
   calls and failed calls don't).
3. A nav-bar "Trial Quota: X/10" badge injected by `index()` via
   `_get_frontier_quota_context()`, rendered only for logged-in users (Claude/Gemini are
   completely locked for guests/anonymous -- there's no quota to show them) and mode-aware
   (a separate Claude-backed badge for the compare/text form, a separate Gemini-backed
   badge for the image form, toggled by the existing switchToImageMode()/
   switchToCompareMode() mode switch).

Split mirrors the existing Claude/Gemini integration test files' own split:
- White-box/unit: the raised constants themselves, `_get_frontier_quota_context()`'s
  logged-in vs. guest/anonymous branching.
- Black-box/integration: `GET /api/quota-status` auth guard + response shape, and the
  rendered `index.html` badge markup/values/positioning.
"""
import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


# ==========================================================================
# 白盒/单元测试
# ==========================================================================

class TestFreeTierLimitsRaisedToTen(unittest.TestCase):
    """2026-07-05: both limits raised from 1 to 10. Pinning the exact value (rather than
    just "greater than 1") so a future accidental edit back towards 1 fails loudly, the
    same way CLAUDE.md's other "pinned constant" tests do (e.g. the image timeout
    constants in test_main_graybox.py)."""

    def test_claude_free_tier_limit_is_ten(self):
        self.assertEqual(main.CLAUDE_FREE_TIER_LIMIT, 10)

    def test_gemini_free_tier_limit_is_ten(self):
        self.assertEqual(main.GEMINI_FREE_TIER_LIMIT, 10)

    def test_limits_are_independent_constants(self):
        """The two limits happen to share the same value today, but they must remain two
        separate constants (not one shared LIMIT reused for both, e.g.
        `GEMINI_FREE_TIER_LIMIT = CLAUDE_FREE_TIER_LIMIT`) -- Claude and Gemini trial
        usage is tracked in two separate Firestore fields (claude_free_tier_usage/
        gemini_free_tier_usage) and must be independently adjustable in the future.
        Patching one in isolation and confirming the other is untouched is the only way
        to actually detect that kind of aliasing (a plain equality check can't, since both
        constants legitimately equal 10 today)."""
        with patch.object(main, 'CLAUDE_FREE_TIER_LIMIT', 3):
            self.assertEqual(main.GEMINI_FREE_TIER_LIMIT, 10)


class TestGetFrontierQuotaContext(unittest.TestCase):
    """_get_frontier_quota_context() -- the helper index() calls to build the Jinja
    context the Trial Quota badge renders from. Needs an active Flask request context
    since it reads `session` directly (mirrors how the rest of the codebase's whitebox
    tests exercise session-reading helpers)."""

    def test_logged_in_user_gets_both_quotas_populated(self):
        with main.app.test_request_context('/'):
            from flask import session
            session['user_id'] = 'uid1'
            with patch.object(main, 'get_claude_free_tier_usage', return_value=3), \
                 patch.object(main, 'get_gemini_free_tier_usage', return_value=7):
                ctx = main._get_frontier_quota_context()

        self.assertEqual(ctx['claude_quota'], {'used': 3, 'limit': 10})
        self.assertEqual(ctx['gemini_quota'], {'used': 7, 'limit': 10})

    def test_guest_gets_no_quota_populated(self):
        """Guests have session['is_guest'] but no session['user_id'] -- Claude/Gemini are
        completely locked for them (no degraded/partial experience, per CLAUDE.md), so
        there must be nothing to show, not a 0/10 badge."""
        with main.app.test_request_context('/'):
            from flask import session
            session['is_guest'] = True
            with patch.object(main, 'get_claude_free_tier_usage') as mock_claude, \
                 patch.object(main, 'get_gemini_free_tier_usage') as mock_gemini:
                ctx = main._get_frontier_quota_context()

        self.assertIsNone(ctx['claude_quota'])
        self.assertIsNone(ctx['gemini_quota'])
        mock_claude.assert_not_called()
        mock_gemini.assert_not_called()

    def test_anonymous_gets_no_quota_populated(self):
        with main.app.test_request_context('/'):
            with patch.object(main, 'get_claude_free_tier_usage') as mock_claude, \
                 patch.object(main, 'get_gemini_free_tier_usage') as mock_gemini:
                ctx = main._get_frontier_quota_context()

        self.assertIsNone(ctx['claude_quota'])
        self.assertIsNone(ctx['gemini_quota'])
        mock_claude.assert_not_called()
        mock_gemini.assert_not_called()

    def test_limit_constants_always_injected_regardless_of_login(self):
        """The upgrade modals' "N free uses" copy needs these even for guests (they still
        see the Claude/Gemini provider cards, just locked -- the modal text itself is
        static markup, not something only logged-in users' browsers load)."""
        with main.app.test_request_context('/'):
            ctx = main._get_frontier_quota_context()

        self.assertEqual(ctx['claude_free_tier_limit'], 10)
        self.assertEqual(ctx['gemini_free_tier_limit'], 10)


# ==========================================================================
# 黑盒/集成测试
# ==========================================================================

class TestQuotaStatusEndpoint(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _login(self, client, user_id='uid1'):
        with client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = 'alice'

    def test_guest_gets_401(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            resp = client.get('/api/quota-status')
        self.assertEqual(resp.status_code, 401)

    def test_anonymous_gets_401(self):
        with main.app.test_client() as client:
            resp = client.get('/api/quota-status')
        self.assertEqual(resp.status_code, 401)

    def test_logged_in_user_gets_both_counters(self):
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=2), \
                 patch.object(main, 'get_gemini_free_tier_usage', return_value=9):
                resp = client.get('/api/quota-status')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['claude'], {'used': 2, 'limit': 10})
        self.assertEqual(data['gemini'], {'used': 9, 'limit': 10})

    def test_claude_and_gemini_counts_are_independent_not_swapped(self):
        """Regression guard against a copy-paste mistake wiring claude's usage into the
        gemini field or vice versa -- the two counters track completely separate
        Firestore fields (claude_free_tier_usage/gemini_free_tier_usage)."""
        with main.app.test_client() as client:
            self._login(client)
            with patch.object(main, 'get_claude_free_tier_usage', return_value=1), \
                 patch.object(main, 'get_gemini_free_tier_usage', return_value=5):
                resp = client.get('/api/quota-status')

        data = resp.get_json()
        self.assertEqual(data['claude']['used'], 1)
        self.assertEqual(data['gemini']['used'], 5)


class TestIndexPageTrialQuotaBadgeMarkup(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def _get_index_html(self, logged_in=True, guest=False):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                if logged_in:
                    sess['user_id'] = 'uid1'
                    sess['username'] = 'alice'
                elif guest:
                    sess['is_guest'] = True
            response = client.get('/')
        return response.data.decode()

    def test_guest_sees_no_badge_element(self):
        html = self._get_index_html(logged_in=False, guest=True)
        self.assertNotIn('id="trialQuotaBadges"', html)
        self.assertNotIn('id="textQuotaBadge"', html)
        self.assertNotIn('id="imageQuotaBadge"', html)

    def test_anonymous_sees_no_badge_element(self):
        """Anonymous visitors get home.html, not index.html at all -- included for
        completeness against the three-state identity table in CLAUDE.md."""
        html = self._get_index_html(logged_in=False, guest=False)
        self.assertNotIn('id="trialQuotaBadges"', html)

    def test_logged_in_user_sees_badge_with_correct_remaining_counts(self):
        with patch.object(main, 'get_claude_free_tier_usage', return_value=3), \
             patch.object(main, 'get_gemini_free_tier_usage', return_value=7):
            html = self._get_index_html(logged_in=True)

        self.assertIn('id="trialQuotaBadges"', html)
        # Claude backs the text/compare form's badge: 10 - 3 = 7 remaining.
        self.assertIn('<span id="textQuotaValue">7</span>', html)
        self.assertIn('<span id="textQuotaLimit">10</span>', html)
        # Gemini backs the image form's badge: 10 - 7 = 3 remaining.
        self.assertIn('<span id="imageQuotaValue">3</span>', html)
        self.assertIn('<span id="imageQuotaLimit">10</span>', html)

    def test_text_badge_visible_image_badge_hidden_by_default(self):
        """Page always loads in chat/text mode first (sidebarMode = 'chat'), so the
        Claude-backed badge must be the one visible on initial render."""
        with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
             patch.object(main, 'get_gemini_free_tier_usage', return_value=0):
            html = self._get_index_html(logged_in=True)

        text_start = html.index('id="textQuotaBadge"')
        text_tag_end = html.index('>', text_start)
        image_start = html.index('id="imageQuotaBadge"')
        image_tag_end = html.index('>', image_start)
        self.assertNotIn('display: none', html[text_start:text_tag_end])
        self.assertIn('display: none', html[image_start:image_tag_end])

    def test_badge_positioned_between_nav_left_and_nav_links(self):
        """Product requirement: the badge sits just right of the "LLM Aggregator" logo
        (inside .nav-left) rather than being centered or pushed to the far right where
        Profile/Logout live."""
        with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
             patch.object(main, 'get_gemini_free_tier_usage', return_value=0):
            html = self._get_index_html(logged_in=True)

        nav_left_end = html.index('</div>', html.index('class="nav-left"'))
        badge_pos = html.index('id="trialQuotaBadges"')
        nav_links_pos = html.index('<ul class="nav-links">')
        self.assertTrue(nav_left_end < badge_pos < nav_links_pos)

    def test_limit_constants_injected_into_js_regardless_of_login(self):
        """Covers both states that actually render index.html (logged-in and guest --
        anonymous visitors get home.html instead, which has no such JS constants at all,
        see test_anonymous_sees_no_badge_element above)."""
        for logged_in, guest in ((True, False), (False, True)):
            with self.subTest(logged_in=logged_in, guest=guest):
                html = self._get_index_html(logged_in=logged_in, guest=guest)
                self.assertIn('const CLAUDE_FREE_TIER_LIMIT = 10;', html)
                self.assertIn('const GEMINI_FREE_TIER_LIMIT = 10;', html)

    def test_quota_js_variables_null_for_guest(self):
        html = self._get_index_html(logged_in=False, guest=True)
        self.assertIn('let claudeQuota = null;', html)
        self.assertIn('let geminiQuota = null;', html)

    def test_quota_js_variables_populated_for_logged_in_user(self):
        with patch.object(main, 'get_claude_free_tier_usage', return_value=4), \
             patch.object(main, 'get_gemini_free_tier_usage', return_value=6):
            html = self._get_index_html(logged_in=True)

        self.assertIn('let claudeQuota = {"limit": 10, "used": 4};', html)
        self.assertIn('let geminiQuota = {"limit": 10, "used": 6};', html)


class TestHealthCheckReportsAllThreeGeminiModels(unittest.TestCase):
    """/health's gemini_models list must reflect all three Nano Banana tiers now that
    Nano Banana 2/Lite are wired up (2026-07-05) -- this endpoint doesn't need any code
    change to pick them up (it just does list(GEMINI_IMAGE_MODELS.keys())), but the
    behavior itself is worth pinning so a future edit to GEMINI_IMAGE_MODELS can't
    silently drop a tier without a test noticing."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_gemini_models_lists_all_three_tiers(self):
        with main.app.test_client() as client:
            resp = client.get('/health')

        data = resp.get_json()
        self.assertEqual(
            data['gemini_models'],
            ['nano-banana-pro', 'nano-banana-2', 'nano-banana-lite']
        )


if __name__ == '__main__':
    unittest.main()
