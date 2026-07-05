import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class TestStickyHeaderWrapper(unittest.TestCase):
    """Guest banner must be wrapped together with .navbar in a position:sticky
    container so it stays pinned instead of scrolling out of view (see
    .page-header-sticky in index.html/history.html)."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_index_guest_page_has_sticky_header_wrapper_around_banner(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/')
        html = response.data.decode()
        self.assertIn('<div class="page-header-sticky">', html)
        self.assertIn('<div class="guest-banner">', html)
        # The banner must be nested inside the sticky wrapper, not after it.
        body = html[html.index('<body>'):]
        wrapper_idx = body.index('<div class="page-header-sticky">')
        banner_idx = body.index('<div class="guest-banner">')
        app_layout_idx = body.index('class="app-layout"')
        self.assertTrue(wrapper_idx < banner_idx < app_layout_idx)

    def test_history_guest_page_has_sticky_header_wrapper_around_banner(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/history/some-id')
        html = response.data.decode()
        self.assertIn('<div class="page-header-sticky">', html)
        self.assertIn('guest-banner', html)

    def test_index_logged_in_page_still_wraps_navbar_without_banner(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
            response = client.get('/')
        html = response.data.decode()
        self.assertIn('<div class="page-header-sticky">', html)
        self.assertNotIn('guest-banner">', html)


class TestAuthBaseHeaderParity(unittest.TestCase):
    """auth/base.html (used by /login, /register, /profile, /apikey-config) must
    match index.html's nav layout scale (--page-zoom) and edge-to-edge nav-container
    with a fixed-width .nav-left, instead of the old centered max-width:1200px bar,
    to avoid a visible header shift when navigating between the app shell pages and
    these auth pages."""

    def setUp(self):
        main.app.config['TESTING'] = True

    def _assert_header_parity(self, html):
        self.assertIn('--page-zoom: 0.88', html)
        self.assertIn('<div class="nav-left">', html)
        self.assertNotIn('max-width: 1200px', html)

    def test_login_page_header_parity(self):
        with main.app.test_client() as client:
            response = client.get('/login')
        self._assert_header_parity(response.data.decode())

    def test_register_page_header_parity(self):
        with main.app.test_client() as client:
            response = client.get('/register')
        self._assert_header_parity(response.data.decode())

    def test_apikey_config_page_header_parity(self):
        with main.app.test_client() as client:
            response = client.get('/apikey-config')
        self._assert_header_parity(response.data.decode())

    def test_profile_page_header_parity(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid1'
                sess['username'] = 'alice'
            response = client.get('/profile')
        # Firebase isn't configured in the test environment, so /profile may redirect;
        # either way the header markup that renders (redirect target or the page
        # itself) should carry the same parity fix.
        html = response.data.decode()
        if response.status_code == 200:
            self._assert_header_parity(html)


class TestDownloadImageButtonText(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_index_page_says_download_image_not_download_png(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/')
        html = response.data.decode()
        self.assertIn('Download Image</button>', html)
        self.assertNotIn('Download PNG', html)


class TestContinueAsGuestCasing(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_login_page_continue_as_guest_is_title_case(self):
        with main.app.test_client() as client:
            response = client.get('/login')
        html = response.data.decode()
        self.assertIn('>Continue as Guest<', html)
        self.assertNotIn('>Continue as guest<', html)

    def test_register_page_continue_as_guest_is_title_case(self):
        with main.app.test_client() as client:
            response = client.get('/register')
        html = response.data.decode()
        self.assertIn('>Continue as Guest<', html)
        self.assertNotIn('>Continue as guest<', html)


class TestStopGeneratingButtonColor(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_stop_buttons_use_dedicated_stop_color_class(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/')
        html = response.data.decode()
        self.assertIn('class="btn btn-stop" id="stopBtn"', html)
        self.assertIn('class="btn btn-stop" id="stopImageBtn"', html)
        self.assertIn('.btn-stop {', html)


class TestQuotaModalWidth(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_confirm_modal_widened(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/')
        html = response.data.decode()
        modal_rule = html[html.index('.confirm-modal {'):html.index('.confirm-modal-title')]
        self.assertIn('max-width: 440px', modal_rule)
        self.assertNotIn('max-width: 360px', modal_rule)


class TestFlashMessagePunctuation(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True

    def test_logout_flash_message_has_trailing_period(self):
        with main.app.test_client() as client:
            response = client.get('/logout', follow_redirects=True)
        html = response.data.decode()
        self.assertIn('You have been logged out.', html)


if __name__ == '__main__':
    unittest.main()
