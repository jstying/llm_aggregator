import re
import sys
import os
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main

VOID_ELEMENTS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'source', 'track', 'wbr',
}


class _TagBalanceChecker(HTMLParser):
    """Every non-void end tag must close the most recently opened tag. Nothing may be
    left open at the end of the document."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.mismatches = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.mismatches.append((self.getpos(), tag, list(self.stack[-3:])))


def assert_html_tag_balance(test_case, html):
    """Structural sanity check that plain `assertIn('some text', html)` markup tests
    cannot catch: a dropped/misplaced opening or closing tag can still leave all the
    same substrings present in the response body while silently reparenting half the
    page in the browser's HTML parser error-recovery algorithm.

    2026-07-04 incident this guards against: an edit to templates/index.html's
    `.sidebar-top` block replaced its opening `<div class="sidebar-top">` with an HTML
    comment but left the matching `</div>` in place. Every existing assertIn-based
    markup test (button ids, header text, etc.) still passed, because all of that text
    was still present verbatim in the response -- but the stray `</div>` had no open
    `<div>` inside `<aside>` to close, so per the HTML5 "any other end tag" parsing
    algorithm the browser walked back up the open-element stack to the nearest `<div>`
    it *could* find (`.app-layout`) and closed everything back to and including it,
    collapsing `.main-content` out of its intended flex layout. The user only saw the
    sidebar buttons. `<script>`/`<style>` bodies are stripped before parsing since their
    contents can legitimately contain `<`/`>` characters that are not markup (e.g. JS
    comparison operators, Jinja expressions)."""
    stripped = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
    stripped = re.sub(r'<style\b[^>]*>.*?</style>', '', stripped, flags=re.S)
    checker = _TagBalanceChecker()
    checker.feed(stripped)
    test_case.assertEqual(
        checker.mismatches, [], f'mismatched closing tag(s) found: {checker.mismatches}'
    )
    test_case.assertEqual(
        checker.stack, [], f'unclosed tag(s) left open at end of document: {checker.stack}'
    )


class TestIndexPageHtmlTagBalance(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_logged_in_index_page_tags_balance(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        assert_html_tag_balance(self, response.data.decode())

    def test_guest_index_page_tags_balance(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/')
        assert_html_tag_balance(self, response.data.decode())

    def test_anonymous_home_page_tags_balance(self):
        with main.app.test_client() as client:
            response = client.get('/')
        assert_html_tag_balance(self, response.data.decode())


class TestHistoryPageHtmlTagBalance(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    @patch('main.get_chat_history_by_id')
    def test_logged_in_history_page_tags_balance(self, mock_get):
        mock_get.return_value = {
            'id': 'hist1', 'user_id': 'uid1', 'prompt': 'hi', 'title': 'hi',
            'results': [], 'is_pinned': False,
        }
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/history/hist1')
        assert_html_tag_balance(self, response.data.decode())

    def test_guest_history_page_tags_balance(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/history/some-id')
        assert_html_tag_balance(self, response.data.decode())


class TestImageHistoryPageHtmlTagBalance(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    @patch('main.get_image_history_by_id')
    def test_logged_in_image_history_page_tags_balance(self, mock_get):
        mock_get.return_value = {
            'id': 'imghist1', 'user_id': 'uid1', 'prompt': 'a cat', 'title': 'a cat',
            'results': [], 'is_pinned': False,
        }
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/imghist1')
        assert_html_tag_balance(self, response.data.decode())


if __name__ == '__main__':
    unittest.main()
