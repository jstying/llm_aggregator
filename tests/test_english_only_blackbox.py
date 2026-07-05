"""2026-07-04: the project adopted an "English-only UI text" policy -- see CLAUDE.md
section 9 ("English-only UI text policy"). No Chinese character may ever reach the end
user: not in rendered page text/attributes, not in JSON error messages returned by the
API, and not in the prompt-engineering strings sent to LLM providers (which is a
separate concern from rendered UI, covered by TestPromptEngineeringIsEnglish below).

Internal code comments (Python `#`, JS `//`, CSS/HTML `/* */`/`<!-- -->`) are explicitly
OUT of scope -- they are never sent to a browser's rendered view and are the project's
established engineering documentation language (CLAUDE.md itself is written in
Chinese). This file therefore strips <script>/<style> bodies and HTML comments before
scanning for CJK characters, mirroring the stripping approach in
test_html_structure_blackbox.py's assert_html_tag_balance().
"""
import re
import sys
import os
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

CJK_PATTERN = re.compile(r'[一-鿿]')

# Attributes a user can actually see without opening dev tools/view-source: rendered
# text, native tooltips, placeholder text, and visible option/input values.
_CHECKED_ATTRS = ('title', 'placeholder', 'value', 'alt', 'aria-label')
_SKIP_TEXT_TAGS = {'script', 'style'}


class _VisibleTextChineseScanner(HTMLParser):

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.hits = []

    def _check_attrs(self, tag, attrs):
        for name, value in attrs:
            if name in _CHECKED_ATTRS and value and CJK_PATTERN.search(value):
                self.hits.append((tag, name, value))

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth += 1
        self._check_attrs(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._check_attrs(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _SKIP_TEXT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and CJK_PATTERN.search(data):
            self.hits.append(('#text', None, data.strip()[:80]))


def assert_no_chinese_in_visible_html(test_case, html):
    scanner = _VisibleTextChineseScanner()
    scanner.feed(html)
    test_case.assertEqual(
        scanner.hits, [],
        f'Chinese characters found in user-visible HTML (text node or {_CHECKED_ATTRS} '
        f'attribute): {scanner.hits}'
    )


class TestIndexPageIsEnglishOnly(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_logged_in_index_page_has_no_visible_chinese(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        assert_no_chinese_in_visible_html(self, response.data.decode())

    def test_guest_index_page_has_no_visible_chinese(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/')
        assert_no_chinese_in_visible_html(self, response.data.decode())

    def test_anonymous_home_page_has_no_visible_chinese(self):
        with main.app.test_client() as client:
            response = client.get('/')
        assert_no_chinese_in_visible_html(self, response.data.decode())


class TestHistoryPageIsEnglishOnly(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    @patch('main.get_chat_history_by_id')
    def test_logged_in_history_page_has_no_visible_chinese(self, mock_get):
        mock_get.return_value = {
            'id': 'hist1', 'user_id': 'uid1', 'prompt': 'hi', 'title': 'hi',
            'results': [], 'is_pinned': False,
        }
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/history/hist1')
        assert_no_chinese_in_visible_html(self, response.data.decode())

    def test_guest_history_page_has_no_visible_chinese(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/history/some-id')
        assert_no_chinese_in_visible_html(self, response.data.decode())


class TestImageHistoryPageIsEnglishOnly(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    @patch('main.get_image_history_by_id')
    def test_logged_in_image_history_page_has_no_visible_chinese(self, mock_get):
        mock_get.return_value = {
            'id': 'imghist1', 'user_id': 'uid1', 'prompt': 'a cat', 'title': 'a cat',
            'results': [], 'is_pinned': False,
        }
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/imghist1')
        assert_no_chinese_in_visible_html(self, response.data.decode())


class TestApikeyConfigPageIsEnglishOnly(unittest.TestCase):
    """2026-07-04: this page previously had the most user-visible Chinese in the
    project (title, heading, body copy, all three key-field labels/placeholders, the
    Save/Clear buttons, and the JS-driven save-status messages) -- it is the page most
    at risk of regressing back to Chinese."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_anonymous_apikey_config_page_has_no_visible_chinese(self):
        with main.app.test_client() as client:
            response = client.get('/apikey-config')
        assert_no_chinese_in_visible_html(self, response.data.decode())

    def test_logged_in_apikey_config_page_has_no_visible_chinese(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
                sess['username'] = 'alice'
            response = client.get('/apikey-config')
        assert_no_chinese_in_visible_html(self, response.data.decode())

    def test_save_button_reads_save(self):
        with main.app.test_client() as client:
            response = client.get('/apikey-config')
        self.assertIn('>Save</button>', response.data.decode())

    def test_clear_buttons_read_clear(self):
        html = None
        with main.app.test_client() as client:
            response = client.get('/apikey-config')
            html = response.data.decode()
        self.assertEqual(html.count('>Clear</button>'), 3)


class TestAuthPagesAreEnglishOnly(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main.app.config['SECRET_KEY'] = 'test-secret'

    def test_login_page_has_no_visible_chinese(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True):
            with main.app.test_client() as client:
                response = client.get('/login')
        assert_no_chinese_in_visible_html(self, response.data.decode())

    def test_register_page_has_no_visible_chinese(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True):
            with main.app.test_client() as client:
                response = client.get('/register')
        assert_no_chinese_in_visible_html(self, response.data.decode())

    def test_profile_page_has_no_visible_chinese(self):
        fake_user = {'id': 'uid123', 'username': 'alice',
                     'email': 'alice@test.com', 'created_at': None}
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_id', return_value=fake_user):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid123'
                    sess['username'] = 'alice'
                response = client.get('/profile')
        assert_no_chinese_in_visible_html(self, response.data.decode())


class TestClaudeServerCreditsExhaustedMessageIsEnglish(unittest.TestCase):
    """2026-07-04: the SERVER_CREDITS_EXHAUSTED friendly message returned by
    POST /api/claude-chat used to be Chinese ("开发者账户余额不足，请配置您的个人 API
    Key 继续使用") -- this is rendered directly onto a failed result card in the
    browser (see CLAUDE.md section 6), so it is very much user-visible text, not an
    internal log message. Locks down the exact English replacement text documented in
    CLAUDE.md."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_server_credits_exhausted_message_has_no_chinese(self):
        fake_result = {
            'provider': 'Claude', 'success': False, 'response': '', 'error': 'billing',
            'response_time': 0.1, 'model': 'claude-sonnet-5', 'type': 'anthropic',
            'error_code': 'SERVER_CREDITS_EXHAUSTED',
        }
        with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
             patch.object(main, 'call_claude_model', return_value=fake_result):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid1'
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5'
                })
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(CJK_PATTERN.search(data['message']))

    def test_server_credits_exhausted_message_matches_documented_english_text(self):
        fake_result = {
            'provider': 'Claude', 'success': False, 'response': '', 'error': 'billing',
            'response_time': 0.1, 'model': 'claude-sonnet-5', 'type': 'anthropic',
            'error_code': 'SERVER_CREDITS_EXHAUSTED',
        }
        with patch.object(main, 'get_claude_free_tier_usage', return_value=0), \
             patch.object(main, 'call_claude_model', return_value=fake_result):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid1'
                resp = client.post('/api/claude-chat', json={
                    'prompt': 'hello', 'model': 'claude-sonnet-5'
                })
        data = resp.get_json()
        self.assertEqual(
            data['message'],
            "Your free trial quota still has uses left, but the developer's Claude "
            "API account has run out of credits. Please contact the developer to "
            "restore access."
        )


class TestGeminiServerQuotaExhaustedMessageIsEnglish(unittest.TestCase):
    """Mirrors TestClaudeServerCreditsExhaustedMessageIsEnglish for the Gemini
    ("Nano Banana") image-generation integration's own SERVER_QUOTA_EXHAUSTED friendly
    message returned by POST /api/gemini-image -- this was written in English from the
    start (Gemini was added after the English-only policy already existed), but is
    tested here for the same reason as its Claude counterpart: it's rendered directly
    onto a failed result card in the browser, so a future edit reintroducing Chinese
    here would be a real, user-visible regression."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_server_quota_exhausted_message_has_no_chinese(self):
        fake_result = {
            'provider': 'Gemini', 'success': False, 'url': None, 'b64_json': None,
            'error': 'quota', 'response_time': 0.1, 'model': 'nano-banana-pro',
            'type': 'google_genai', 'error_code': 'SERVER_QUOTA_EXHAUSTED',
        }
        with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
             patch.object(main, 'call_gemini_image_model', return_value=fake_result):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid1'
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro'
                })
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(CJK_PATTERN.search(data['message']))

    def test_server_quota_exhausted_message_matches_documented_english_text(self):
        fake_result = {
            'provider': 'Gemini', 'success': False, 'url': None, 'b64_json': None,
            'error': 'quota', 'response_time': 0.1, 'model': 'nano-banana-pro',
            'type': 'google_genai', 'error_code': 'SERVER_QUOTA_EXHAUSTED',
        }
        with patch.object(main, 'get_gemini_free_tier_usage', return_value=0), \
             patch.object(main, 'call_gemini_image_model', return_value=fake_result):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid1'
                resp = client.post('/api/gemini-image', json={
                    'prompt': 'a cat', 'model': 'nano-banana-pro'
                })
        data = resp.get_json()
        self.assertEqual(
            data['message'],
            "Your free trial quota still has uses left, but the developer's Gemini "
            "API account has run out of quota. Please contact the developer to "
            "restore access."
        )


class TestPromptEngineeringIsEnglish(unittest.TestCase):
    """The 'prompt engineering' half of the 2026-07-04 English-only policy: the hidden
    style-routing prompts (ROUTE_PROMPTS_MAP) appended to user prompts and the peer-
    review judge prompts (PEER_REVIEW_PROMPTS_MAP) sent to LLM providers must contain no
    Chinese instructions -- a Chinese instruction embedded in the prompt text itself
    (as opposed to a Chinese comment describing it) could cause a provider to respond in
    Chinese, which the user would then see rendered as a result card. These maps were
    already English-only prior to this task; this test exists to catch any future
    regression."""

    def test_route_prompts_map_values_contain_no_chinese(self):
        if not main.G4F_AVAILABLE:
            self.skipTest('g4f not available; ROUTE_PROMPTS_MAP is empty in this env')
        for key, prompt_text in main.ROUTE_PROMPTS_MAP.items():
            self.assertFalse(
                CJK_PATTERN.search(prompt_text),
                f'ROUTE_PROMPTS_MAP[{key!r}] contains Chinese text: {prompt_text!r}'
            )

    def test_peer_review_prompts_map_values_contain_no_chinese(self):
        if not main.G4F_AVAILABLE:
            self.skipTest(
                'g4f not available; PEER_REVIEW_PROMPTS_MAP is empty in this env'
            )
        for key, prompt_text in main.PEER_REVIEW_PROMPTS_MAP.items():
            self.assertFalse(
                CJK_PATTERN.search(prompt_text),
                f'PEER_REVIEW_PROMPTS_MAP[{key!r}] contains Chinese text: '
                f'{prompt_text!r}'
            )


if __name__ == '__main__':
    unittest.main()
