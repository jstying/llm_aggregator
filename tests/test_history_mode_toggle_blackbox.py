import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class TestHistoryPageModeAwareButtons(unittest.TestCase):
    """2026-07-04: templates/history.html's sidebar-top used to be a single fixed-label
    "+ New Chat" button with no way to jump to image mode at all -- unlike
    templates/image_history.html, which already had a second button. This brought
    history.html's sidebar-top up to parity with templates/index.html's mode-aware button
    pair (icon+label spans, "+ New" wording) and gave it the previously-missing
    #generateImageBtn entry point into image mode. Since this read-only page has no live
    compare form to clear in place, both buttons just navigate elsewhere rather than
    branching on a client-side sidebarMode the way index.html's copy does."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _fake_entry(self, **overrides):
        entry = {
            'id': 'hist1', 'user_id': 'uid1', 'prompt': 'hi', 'title': 'hi',
            'results': [], 'is_pinned': False,
        }
        entry.update(overrides)
        return entry

    def _get_html_logged_in(self):
        with patch('main.get_chat_history_by_id', return_value=self._fake_entry()):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid1'
                response = client.get('/history/hist1')
        return response.data.decode()

    def _get_html_guest(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/history/guest-abc-123')
        return response.data.decode()

    def test_new_button_markup_reads_plus_new_logged_in(self):
        html = self._get_html_logged_in()
        self.assertIn(
            '<span class="btn-icon">+</span><span class="btn-label">New</span>', html
        )
        button_start = html.index('id="newChatBtn"')
        self.assertNotIn('New Chat', html[button_start:button_start + 120])

    def test_new_button_markup_reads_plus_new_guest(self):
        html = self._get_html_guest()
        self.assertIn(
            '<span class="btn-icon">+</span><span class="btn-label">New</span>', html
        )

    def test_generate_image_button_now_present_logged_in(self):
        """This button did not exist at all before 2026-07-04 -- history.html previously
        had no way to reach image mode from its sidebar."""
        html = self._get_html_logged_in()
        self.assertIn('id="generateImageBtn"', html)
        self.assertIn(
            '<span class="btn-icon">\U0001f3a8</span><span class="btn-label">Generate Image</span>',
            html,
        )

    def test_generate_image_button_now_present_guest(self):
        html = self._get_html_guest()
        self.assertIn('id="generateImageBtn"', html)
        self.assertIn(
            '<span class="btn-icon">\U0001f3a8</span><span class="btn-label">Generate Image</span>',
            html,
        )

    def test_new_button_click_handler_navigates_to_root(self):
        html = self._get_html_logged_in()
        handler_start = html.index("getElementById('newChatBtn').addEventListener")
        handler_snippet = html[handler_start:handler_start + 300]
        self.assertIn("window.location.href = '/';", handler_snippet)

    def test_generate_image_button_click_handler_navigates_to_image_mode(self):
        html = self._get_html_logged_in()
        handler_start = html.index("getElementById('generateImageBtn').addEventListener")
        handler_snippet = html[handler_start:handler_start + 300]
        self.assertIn("window.location.href = '/?mode=image';", handler_snippet)


class TestImageHistoryPageModeAwareButtons(unittest.TestCase):
    """2026-07-04: templates/image_history.html's two sidebar-top buttons were plain-text
    ("+ New Chat" / "🎨 Generate Image") and their semantics predated index.html's
    mode-aware redesign: "+ New Chat" jumped back to chat mode and "Generate Image"
    re-entered image mode, i.e. the *toggle* button was the one that stayed in the
    current (image) context. Brought in line with index.html's rule that "+ New" never
    force-switches modes: on this page (always image context) "+ New" now stays in image
    mode ('/?mode=image') and the mode-toggle button now offers the *other* mode
    ("✍️"+"Generate Text" -> '/'), mirroring history.html's toggle button pointing the
    other way."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _fake_entry(self, **overrides):
        entry = {
            'id': 'imghist1', 'user_id': 'uid1', 'prompt': 'a cat', 'title': 'a cat',
            'results': [], 'is_pinned': False,
        }
        entry.update(overrides)
        return entry

    def _get_html(self):
        with patch('main.get_image_history_by_id', return_value=self._fake_entry()):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid1'
                response = client.get('/image-history/imghist1')
        return response.data.decode()

    def test_new_button_markup_reads_plus_new(self):
        html = self._get_html()
        self.assertIn(
            '<span class="btn-icon">+</span><span class="btn-label">New</span>', html
        )
        button_start = html.index('id="newChatBtn"')
        self.assertNotIn('New Chat', html[button_start:button_start + 120])

    def test_generate_image_button_markup_now_reads_generate_text(self):
        """Previously this button's own rendered text was "🎨 Generate Image" -- since this
        page is always in image-history context, it must now offer the *other* mode."""
        html = self._get_html()
        self.assertIn(
            '<span class="btn-icon">✍️</span><span class="btn-label">Generate Text</span>',
            html,
        )
        self.assertNotIn('Generate Image</span>', html)

    def test_new_button_click_handler_stays_in_image_mode(self):
        """Previously this navigated to '/' (chat mode) -- must now stay in image mode."""
        html = self._get_html()
        handler_start = html.index("getElementById('newChatBtn').addEventListener")
        handler_snippet = html[handler_start:handler_start + 300]
        self.assertIn("window.location.href = '/?mode=image';", handler_snippet)

    def test_generate_image_button_click_handler_switches_to_chat_mode(self):
        """Previously this navigated to '/?mode=image' (re-entering image mode) -- must now
        offer the switch away to chat mode instead."""
        html = self._get_html()
        handler_start = html.index("getElementById('generateImageBtn').addEventListener")
        handler_snippet = html[handler_start:handler_start + 300]
        self.assertIn("window.location.href = '/';", handler_snippet)


if __name__ == '__main__':
    unittest.main()
