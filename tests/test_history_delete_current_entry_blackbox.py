"""Regression test for the 2026-07-05 bug: deleting the history entry currently open on
its own /history/<id> or /image-history/<id> detail page would navigate to '/' immediately
alongside the DELETE fetch, and the browser would sometimes cancel that in-flight fetch on
navigation -- so the entry silently survived while the user was bounced to '/' (which,
for the chat/text detail page, lands back on the default text-generation view). Fix: for
the currently-viewed entry, deleteHistoryItem() now awaits the DELETE response before
navigating away, and only navigates on success.

No JS test framework exists in this project (see CLAUDE.md's front-end testing note), so
-- mirroring tests/test_sidebar_ui_blackbox.py -- this asserts on the exact inline JS source
served by the detail-page routes rather than executing it in a browser."""
import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from test_main_blackbox import (  # noqa: E402
    TestViewHistoryPage as _ViewHistoryPageBase,
)
from test_image_history_blackbox import (  # noqa: E402
    TestViewImageHistoryPage as _ViewImageHistoryPageBase,
)


class TestChatHistoryDeleteCurrentEntryAwaitsBeforeNavigating(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self._fake_entry = _ViewHistoryPageBase()._fake_entry

    @patch('main.get_chat_history_by_id')
    def _get_html(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/history/hist1')
        return response.data.decode()

    def test_current_entry_branch_awaits_delete_fetch_before_navigating(self):
        html = self._get_html()
        fn_start = html.index('async function deleteHistoryItem(id)')
        fn_body = html[fn_start:html.index('function commitRename', fn_start)]
        self.assertIn('isCurrentEntry', fn_body)
        self.assertIn('await fetch(`/api/history/', fn_body)

    def test_navigation_no_longer_races_an_un_awaited_fetch(self):
        # The old, buggy shape: `if (id === targetHistoryId) { window.location.href = '/';
        # return; }` appeared *before* any fetch call, so the DELETE request was either
        # never issued or raced against the navigation. That exact shape must be gone.
        html = self._get_html()
        self.assertNotIn(
            "if (id === targetHistoryId) {\n                window.location.href = '/';",
            html,
        )

    def test_guest_current_entry_removes_from_session_storage_before_navigating(self):
        # Previously guests hit the `id === targetHistoryId` branch and navigated away
        # without ever calling persistGuestHistory(), so the entry silently stayed in
        # sessionStorage. Now the splice + persist must happen ahead of the redirect.
        html = self._get_html()
        guest_branch = html[html.index('if (!isLoggedIn) {', html.index('async function deleteHistoryItem(id)')):]
        guest_branch = guest_branch[:guest_branch.index('if (isCurrentEntry)') + 200]
        self.assertIn('persistGuestHistory()', guest_branch)


class TestImageHistoryDeleteCurrentEntryAwaitsBeforeNavigating(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self._fake_entry = _ViewImageHistoryPageBase()._fake_entry

    @patch('main.get_image_history_by_id')
    def _get_html(self, mock_get):
        mock_get.return_value = self._fake_entry()
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/image-history/imghist1')
        return response.data.decode()

    def test_current_entry_branch_awaits_delete_fetch_before_navigating(self):
        html = self._get_html()
        fn_start = html.index('async function deleteHistoryItem(id)')
        fn_body = html[fn_start:html.index('function commitRename', fn_start)]
        self.assertIn("if (id === targetHistoryId) {", fn_body)
        self.assertIn('await fetch(`/api/image-history/', fn_body)

    def test_navigation_no_longer_races_an_un_awaited_fetch(self):
        html = self._get_html()
        self.assertNotIn(
            "if (id === targetHistoryId) {\n                window.location.href = '/';",
            html,
        )

    def test_success_navigates_to_image_mode_not_default_text_mode(self):
        # Bare '/' lands on the app's default text-generation view -- deleting the
        # currently-viewed image batch must send the user back in image mode instead
        # (2026-07-05 user-reported: delete succeeded but the app bounced to text
        # generation). Mirrors this page's own '+ New' button, which already uses
        # '/?mode=image' for the same reason.
        html = self._get_html()
        fn_start = html.index('async function deleteHistoryItem(id)')
        fn_body = html[fn_start:html.index('function commitRename', fn_start)]
        self.assertIn("window.location.href = '/?mode=image';", fn_body)
        self.assertNotIn("window.location.href = '/';", fn_body)


if __name__ == '__main__':
    unittest.main()
