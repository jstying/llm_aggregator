"""
Black-box HTTP tests for the API Key config page:

1. A persistent "API Keys" nav link (-> /apikey-config) inserted between "Profile" and
   "Logout" in every logged-in navbar (index.html, history.html, image_history.html,
   auth/base.html-derived pages), so logged-in users can reach the page from anywhere
   in the app rather than only via a direct URL. (2026-07-04)
2. Per-field "clear" buttons on apikey-config.html so a user can immediately wipe a
   previously-saved key (privacy / "stop being charged on my key" flow) without having
   to blank the input and re-submit the form. (2026-07-04, Claude only at the time)
3. Gemini and ChatGPT (2026-07-06: text + image frontier integrations) joined Claude
   as real, wired-up fields -- all three inputs/clear buttons are enabled and bound to
   their own localStorage keys ('user_gemini_key'/'user_chatgpt_key'), mirroring
   Claude's 'user_claude_key' wiring exactly. Gemini's key covers both its image and
   text scenarios; ChatGPT's key covers both its text and image scenarios (see
   CLAUDE.md) -- there is no longer an inert placeholder field on this page.
"""
import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


def _assert_link_between_profile_and_logout(test_case, html):
    """Profile / API Keys / Logout must appear in that left-to-right order in the
    rendered nav markup -- the whole point of this feature is the link sitting between
    the other two, not merely being present somewhere on the page."""
    test_case.assertIn('Profile</a>', html)
    test_case.assertIn('API Keys</a>', html)
    test_case.assertIn('Logout</a>', html)
    profile_index = html.index('Profile</a>')
    apikey_index = html.index('API Keys</a>')
    logout_index = html.index('Logout</a>')
    test_case.assertLess(
        profile_index, apikey_index,
        'API Keys link must come after Profile in the nav'
    )
    test_case.assertLess(
        apikey_index, logout_index,
        'API Keys link must come before Logout in the nav'
    )
    test_case.assertIn('href="/apikey-config"', html)


class TestNavApikeyLinkLoggedIn(unittest.TestCase):
    """The link is only meaningful in the logged-in nav branch (Profile/Logout only
    ever render for session['user_id']); guests/anonymous get no Profile/Logout either,
    so they should not see this link in the nav (they can still reach the page directly
    -- the route itself has no login guard, see TestNavApikeyLinkAbsentForOthers)."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_index_page_logged_in(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
                sess['username'] = 'alice'
            resp = client.get('/')
        _assert_link_between_profile_and_logout(self, resp.data.decode())

    @patch('main.get_chat_history_by_id')
    def test_history_page_logged_in(self, mock_get):
        mock_get.return_value = {
            'id': 'hist1', 'user_id': 'uid1', 'prompt': 'hi', 'title': 'hi',
            'results': [], 'is_pinned': False,
        }
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
                sess['username'] = 'alice'
            resp = client.get('/history/hist1')
        _assert_link_between_profile_and_logout(self, resp.data.decode())

    @patch('main.get_image_history_by_id')
    def test_image_history_page_logged_in(self, mock_get):
        mock_get.return_value = {
            'id': 'imghist1', 'user_id': 'uid1', 'prompt': 'a cat', 'title': 'a cat',
            'results': [], 'is_pinned': False,
        }
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
                sess['username'] = 'alice'
            resp = client.get('/image-history/imghist1')
        _assert_link_between_profile_and_logout(self, resp.data.decode())

    def test_profile_page_logged_in(self):
        fake_user = {'id': 'uid1', 'username': 'alice',
                     'email': 'alice@test.com', 'created_at': None}
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_id', return_value=fake_user):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid1'
                    sess['username'] = 'alice'
                resp = client.get('/profile')
        _assert_link_between_profile_and_logout(self, resp.data.decode())

    def test_apikey_config_page_itself_shows_the_link_when_logged_in(self):
        """Dogfooding: a logged-in user sitting on the config page should still see the
        same persistent nav (consistent chrome across every logged-in page)."""
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
                sess['username'] = 'alice'
            resp = client.get('/apikey-config')
        _assert_link_between_profile_and_logout(self, resp.data.decode())


class TestNavApikeyLinkAbsentForOthers(unittest.TestCase):
    """Guests and anonymous visitors never see Profile/Logout, so they never see the
    API Keys nav link either -- but the route itself stays reachable by direct URL
    (no login guard), which is unchanged, pre-existing behavior."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_index_page_guest_has_no_apikey_nav_link(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            resp = client.get('/')
        self.assertNotIn('API Keys</a>', resp.data.decode())

    def test_login_page_has_no_apikey_nav_link(self):
        with main.app.test_client() as client:
            resp = client.get('/login')
        self.assertNotIn('API Keys</a>', resp.data.decode())

    def test_apikey_config_route_still_reachable_by_anonymous_direct_url(self):
        with main.app.test_client() as client:
            resp = client.get('/apikey-config')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('API Keys</a>', resp.data.decode())


class TestApikeyConfigClearButtons(unittest.TestCase):
    """The per-field clear buttons: Claude's, Gemini's, and (2026-07-06) ChatGPT's are
    all live -- wired to localStorage.removeItem for immediate effect, independent of
    the Save button."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _get_html(self):
        with main.app.test_client() as client:
            resp = client.get('/apikey-config')
        return resp.data.decode()

    def test_all_three_clear_buttons_present(self):
        html = self._get_html()
        self.assertIn('id="clearChatgptKeyBtn"', html)
        self.assertIn('id="clearGeminiKeyBtn"', html)
        self.assertIn('id="clearClaudeKeyBtn"', html)

    def test_all_three_clear_buttons_are_not_disabled(self):
        import re
        html = self._get_html()
        for btn_id in ('clearClaudeKeyBtn', 'clearGeminiKeyBtn', 'clearChatgptKeyBtn'):
            match = re.search(r'<button[^>]*id="%s"[^>]*>' % btn_id, html)
            self.assertIsNotNone(match, f'{btn_id} button not found')
            self.assertNotIn('disabled', match.group(0))

    def test_claude_clear_button_wired_to_remove_stored_key(self):
        html = self._get_html()
        self.assertIn("getElementById('clearClaudeKeyBtn')", html)
        self.assertIn("localStorage.removeItem('user_claude_key')", html)

    def test_gemini_clear_button_wired_to_remove_stored_key(self):
        html = self._get_html()
        self.assertIn("getElementById('clearGeminiKeyBtn')", html)
        self.assertIn("localStorage.removeItem('user_gemini_key')", html)

    def test_chatgpt_clear_button_wired_to_remove_stored_key(self):
        html = self._get_html()
        self.assertIn("getElementById('clearChatgptKeyBtn')", html)
        self.assertIn("localStorage.removeItem('user_chatgpt_key')", html)

    def test_all_three_inputs_are_not_disabled(self):
        import re
        html = self._get_html()
        for input_id in ('claudeKeyInput', 'geminiKeyInput', 'chatgptKeyInput'):
            match = re.search(r'<input[^>]*id="%s"[^>]*>' % input_id, html)
            self.assertIsNotNone(match)
            self.assertNotIn('disabled', match.group(0))

    def test_save_flow_still_works_alongside_new_clear_buttons(self):
        html = self._get_html()
        self.assertIn('apikeyConfigForm', html)
        self.assertIn("localStorage.setItem('user_claude_key'", html)
        self.assertIn("localStorage.setItem('user_gemini_key'", html)
        self.assertIn("localStorage.setItem('user_chatgpt_key'", html)


if __name__ == '__main__':
    unittest.main()
