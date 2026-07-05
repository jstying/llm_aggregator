import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class TestModeAwareSidebarButtonsMarkup(unittest.TestCase):
    """2026-07-04: #newChatBtn/#generateImageBtn stopped being fixed-label buttons.
    #newChatBtn always reads "+ New" and now clears whichever mode's results are
    currently on screen (chat or image) instead of unconditionally force-switching to
    chat mode first; #generateImageBtn's icon/label now flip between the "enter image
    mode" pair and the "enter chat mode" pair depending on sidebarMode, rather than
    always meaning "enter image mode". These are markup/inline-JS string assertions --
    there is no JS test framework in this project (see CLAUDE.md's front-end testing
    note), so the interactive behavior itself is verified by asserting the exact JS
    source lines that implement it are present, mirroring how the rest of this file's
    sibling tests (test_main_blackbox.py) assert on inline script contents."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _get_index_html(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        return response.data.decode()

    def test_new_button_markup_reads_plus_new(self):
        html = self._get_index_html()
        self.assertIn('id="newChatBtn"', html)
        self.assertIn(
            '<span class="btn-icon">+</span><span class="btn-label">New</span>', html
        )
        # The button element's own rendered text must be exactly "+ New", not the old
        # "+ New Chat" -- checked on the <button> tag itself rather than the whole page,
        # since explanatory HTML/JS comments elsewhere legitimately mention the old label
        # by name when describing what changed.
        button_start = html.index('id="newChatBtn"')
        button_snippet = html[button_start:button_start + 120]
        self.assertNotIn('New Chat', button_snippet)

    def test_generate_image_button_has_toggleable_icon_and_label_spans(self):
        html = self._get_index_html()
        self.assertIn('id="modeToggleBtnIcon"', html)
        self.assertIn('id="modeToggleBtnLabel"', html)

    def test_generate_image_button_initial_markup_is_image_mode_entry(self):
        html = self._get_index_html()
        self.assertIn('id="modeToggleBtnIcon">\U0001f3a8</span>', html)
        self.assertIn('id="modeToggleBtnLabel">Generate Image</span>', html)

    def test_switch_to_image_mode_sets_toggle_button_to_generate_text(self):
        html = self._get_index_html()
        self.assertIn("modeToggleBtnIcon.textContent = '✍️';", html)
        self.assertIn("modeToggleBtnLabel.textContent = 'Generate Text';", html)

    def test_switch_to_compare_mode_restores_toggle_button_to_generate_image(self):
        html = self._get_index_html()
        self.assertIn("modeToggleBtnIcon.textContent = '\U0001f3a8';", html)
        self.assertIn("modeToggleBtnLabel.textContent = 'Generate Image';", html)

    def test_generate_image_button_click_handler_toggles_direction(self):
        """The click listener must pick a direction off sidebarMode rather than always
        entering image mode -- otherwise there would be no way back to chat mode from the
        button once in image mode."""
        html = self._get_index_html()
        handler_start = html.index("getElementById('generateImageBtn').addEventListener")
        handler_snippet = html[handler_start:handler_start + 300]
        self.assertIn("sidebarMode === 'image'", handler_snippet)
        self.assertIn('switchToCompareMode();', handler_snippet)
        self.assertIn('switchToImageMode();', handler_snippet)

    def test_new_button_click_handler_is_mode_aware_not_forced_to_chat(self):
        """Old behavior always called switchToCompareMode() before clearing, so "+ New"
        while inside image mode would silently discard the in-progress image session and
        jump back to chat. That forced switch must be gone, replaced with a sidebarMode
        branch that clears whichever form is actually active."""
        html = self._get_index_html()
        handler_start = html.index("getElementById('newChatBtn').addEventListener")
        handler_snippet = html[handler_start:handler_start + 400]
        self.assertIn("sidebarMode === 'image'", handler_snippet)
        self.assertIn("getElementById('clearImagesBtn').click();", handler_snippet)
        self.assertIn("getElementById('clearBtn').click();", handler_snippet)
        self.assertNotIn('switchToCompareMode', handler_snippet)


class TestG4FBrandingRemoved(unittest.TestCase):
    """2026-07-04: all user-facing "G4F" branding was removed from index.html,
    history.html and image_history.html (title tags, nav-logo mobile abbreviation, page
    headers, image-mode subtitle). The app is still implemented on top of the g4f
    library internally, but that is an implementation detail that must not leak into
    rendered end-user-facing text."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_index_page_has_no_g4f_text_logged_in(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        self.assertNotIn('G4F', response.data.decode())

    def test_index_page_has_no_g4f_text_guest(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/')
        self.assertNotIn('G4F', response.data.decode())

    def test_index_page_title_tag_dropped_g4f_prefix(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        self.assertIn('<title>LLM Aggregator</title>', response.data.decode())

    @patch('main.get_chat_history_by_id')
    def test_history_page_has_no_g4f_text(self, mock_get):
        mock_get.return_value = {
            'id': 'hist1', 'user_id': 'uid1', 'prompt': 'hi', 'title': 'hi',
            'results': [], 'is_pinned': False,
        }
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/history/hist1')
        self.assertNotIn('G4F', response.data.decode())

    @patch('main.get_chat_history_by_id')
    def test_history_page_title_tag_dropped_g4f_prefix(self, mock_get):
        mock_get.return_value = {
            'id': 'hist1', 'user_id': 'uid1', 'prompt': 'hi', 'title': 'hi',
            'results': [], 'is_pinned': False,
        }
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/history/hist1')
        self.assertIn('<title>History - LLM Aggregator</title>', response.data.decode())

    @patch('main.get_image_history_by_id')
    def test_image_history_page_has_no_g4f_text(self, mock_get):
        mock_get.return_value = {
            'id': 'imghist1', 'user_id': 'uid1', 'prompt': 'a cat', 'title': 'a cat',
            'results': [], 'is_pinned': False,
        }
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/imghist1')
        self.assertNotIn('G4F', response.data.decode())

    @patch('main.get_image_history_by_id')
    def test_image_history_page_title_tag_dropped_g4f_prefix(self, mock_get):
        mock_get.return_value = {
            'id': 'imghist1', 'user_id': 'uid1', 'prompt': 'a cat', 'title': 'a cat',
            'results': [], 'is_pinned': False,
        }
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/imghist1')
        self.assertIn(
            '<title>Image History - LLM Aggregator</title>', response.data.decode()
        )


class TestHeaderRenames(unittest.TestCase):
    """2026-07-04: the two page headers dropped their "G4F" prefixes and were
    shortened -- "G4F LLM Aggregator" -> "Text Generator", "G4F Image Generator" ->
    "Image Generator" -- and the image-mode subtitle dropped its "free, no-key g4f"
    wording. The mobile-width header abbreviation (.header-short) was also unified to
    "LLM Aggregator" for both modes instead of diverging ("LLM Aggregator" for chat,
    "Image Gen" for image)."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _get_index_html(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        return response.data.decode()

    def test_chat_mode_header_is_text_generator(self):
        html = self._get_index_html()
        self.assertIn('<span class="header-full">Text Generator</span>', html)

    def test_image_mode_header_is_image_generator(self):
        html = self._get_index_html()
        self.assertIn('<span class="header-full">Image Generator</span>', html)

    def test_image_mode_subtitle_text_updated(self):
        html = self._get_index_html()
        self.assertIn(
            'Generate images with providers and compare them side by side.', html
        )
        self.assertNotIn('free, no-key g4f providers', html)

    def test_mobile_header_short_unified_to_llm_aggregator_for_both_modes(self):
        html = self._get_index_html()
        self.assertEqual(
            html.count('<span class="header-short">LLM Aggregator</span>'), 2
        )
        self.assertNotIn('Image Gen<', html)


class TestCompareButtonRenamed(unittest.TestCase):
    """2026-07-04: the primary submit button on the chat-comparison form was renamed
    from "Compare Providers" to "Compare Responses" -- the button submits a prompt and
    compares the *responses* returned by each provider, not the providers themselves,
    and the new label was requested to make that clearer."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _get_index_html(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        return response.data.decode()

    def test_compare_button_reads_compare_responses(self):
        html = self._get_index_html()
        button_start = html.index('id="compareBtn"')
        button_snippet = html[button_start:button_start + 120]
        self.assertIn('Compare Responses', button_snippet)

    def test_old_compare_providers_label_is_gone(self):
        html = self._get_index_html()
        self.assertNotIn('Compare Providers', html)


class TestSidebarRecentsEmptyStateText(unittest.TestCase):
    """2026-07-04: the chat-mode Recents sidebar's empty-state copy was shortened from
    "No conversations yet." to "Empty." in both index.html (live sidebar) and
    history.html (read-only detail page's sidebar). This is a markup/inline-JS string
    assertion, not an interactive-behavior test, per this file's established pattern --
    renderHistoryGroups() only returns this string when groups.length === 0, which is
    not exercised by the Flask test client (no JS runtime here)."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_index_page_chat_sidebar_empty_state_is_empty(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        html = response.data.decode()
        self.assertIn('<div class="sidebar-empty-state">Empty.</div>', html)
        self.assertNotIn('No conversations yet.', html)

    def test_history_page_chat_sidebar_empty_state_is_empty(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/history/some-id')
        html = response.data.decode()
        self.assertIn('<div class="sidebar-empty-state">Empty.</div>', html)
        self.assertNotIn('No conversations yet.', html)


if __name__ == '__main__':
    unittest.main()
