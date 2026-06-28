import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from werkzeug.security import generate_password_hash

import main


def _fake_user(user_id='uid123', username='alice', email='alice@test.com',
               password='password123'):
    return {
        'id': user_id,
        'username': username,
        'email': email,
        'password_hash': generate_password_hash(password),
        'created_at': None,
    }


class TestLoginPageRendering(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main.app.config['SECRET_KEY'] = 'test-secret'
        self.client = main.app.test_client()

    def test_get_login_returns_200(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True):
            response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)

    def test_get_login_page_contains_form(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True):
            response = self.client.get('/login')
        self.assertIn(b'username', response.data.lower())

    def test_get_login_unavailable_firebase_returns_503(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', False):
            response = self.client.get('/login')
        self.assertEqual(response.status_code, 503)


class TestRegisterPageRendering(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main.app.config['SECRET_KEY'] = 'test-secret'
        self.client = main.app.test_client()

    def test_get_register_returns_200(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True):
            response = self.client.get('/register')
        self.assertEqual(response.status_code, 200)

    def test_get_register_page_contains_form(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True):
            response = self.client.get('/register')
        self.assertIn(b'username', response.data.lower())

    def test_get_register_unavailable_firebase_returns_503(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', False):
            response = self.client.get('/register')
        self.assertEqual(response.status_code, 503)


class TestLoginPost(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main.app.config['SECRET_KEY'] = 'test-secret'
        self.client = main.app.test_client()

    def test_login_success_redirects(self):
        fake_user = _fake_user(password='password123')
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=fake_user):
            response = self.client.post('/login', data={
                'username': 'alice',
                'password': 'password123',
            })
        self.assertEqual(response.status_code, 302)

    def test_login_success_sets_user_id_in_session(self):
        fake_user = _fake_user(user_id='uid123', password='password123')
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=fake_user):
            with main.app.test_client() as client:
                client.post('/login', data={
                    'username': 'alice',
                    'password': 'password123',
                })
                with client.session_transaction() as sess:
                    self.assertEqual(sess.get('user_id'), 'uid123')

    def test_login_success_sets_username_in_session(self):
        fake_user = _fake_user(username='alice', password='password123')
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=fake_user):
            with main.app.test_client() as client:
                client.post('/login', data={
                    'username': 'alice',
                    'password': 'password123',
                })
                with client.session_transaction() as sess:
                    self.assertEqual(sess.get('username'), 'alice')

    def test_login_success_clears_is_guest(self):
        fake_user = _fake_user(password='password123')
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=fake_user):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['is_guest'] = True
                client.post('/login', data={
                    'username': 'alice',
                    'password': 'password123',
                })
                with client.session_transaction() as sess:
                    self.assertNotIn('is_guest', sess)

    def test_login_failure_wrong_password_returns_200(self):
        fake_user = _fake_user(password='correct_password')
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=fake_user):
            response = self.client.post('/login', data={
                'username': 'alice',
                'password': 'wrong_password',
            })
        self.assertEqual(response.status_code, 200)

    def test_login_failure_user_not_found_returns_200(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=None):
            response = self.client.post('/login', data={
                'username': 'ghost',
                'password': 'any',
            })
        self.assertEqual(response.status_code, 200)

    def test_login_failure_does_not_set_user_id(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=None):
            with main.app.test_client() as client:
                client.post('/login', data={
                    'username': 'ghost',
                    'password': 'any',
                })
                with client.session_transaction() as sess:
                    self.assertNotIn('user_id', sess)

    def test_login_missing_fields_returns_200(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True):
            response = self.client.post('/login', data={
                'username': '',
                'password': '',
            })
        self.assertEqual(response.status_code, 200)

    def test_login_redirect_points_to_index(self):
        fake_user = _fake_user(password='password123')
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=fake_user):
            response = self.client.post('/login', data={
                'username': 'alice',
                'password': 'password123',
            })
        self.assertIn('/', response.headers.get('Location', ''))


class TestRegisterPost(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main.app.config['SECRET_KEY'] = 'test-secret'

    def test_register_success_clears_is_guest(self):
        fake_user = {'id': 'new_uid', 'username': 'newuser',
                     'email': 'new@test.com', 'password_hash': 'h'}
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=None), \
             patch('auth.routes.get_user_by_email', return_value=None), \
             patch('auth.routes.create_user', return_value=fake_user):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['is_guest'] = True
                client.post('/register', data={
                    'username': 'newuser',
                    'email': 'new@test.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                })
                with client.session_transaction() as sess:
                    self.assertNotIn('is_guest', sess)

    def test_register_success_sets_user_id(self):
        fake_user = {'id': 'new_uid', 'username': 'newuser',
                     'email': 'new@test.com', 'password_hash': 'h'}
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=None), \
             patch('auth.routes.get_user_by_email', return_value=None), \
             patch('auth.routes.create_user', return_value=fake_user):
            with main.app.test_client() as client:
                client.post('/register', data={
                    'username': 'newuser',
                    'email': 'new@test.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                })
                with client.session_transaction() as sess:
                    self.assertEqual(sess.get('user_id'), 'new_uid')

    def test_register_success_sets_username(self):
        fake_user = {'id': 'new_uid', 'username': 'newuser',
                     'email': 'new@test.com', 'password_hash': 'h'}
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=None), \
             patch('auth.routes.get_user_by_email', return_value=None), \
             patch('auth.routes.create_user', return_value=fake_user):
            with main.app.test_client() as client:
                client.post('/register', data={
                    'username': 'newuser',
                    'email': 'new@test.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                })
                with client.session_transaction() as sess:
                    self.assertEqual(sess.get('username'), 'newuser')

    def test_register_success_redirects(self):
        fake_user = {'id': 'new_uid', 'username': 'newuser',
                     'email': 'new@test.com', 'password_hash': 'h'}
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=None), \
             patch('auth.routes.get_user_by_email', return_value=None), \
             patch('auth.routes.create_user', return_value=fake_user):
            with main.app.test_client() as client:
                response = client.post('/register', data={
                    'username': 'newuser',
                    'email': 'new@test.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                })
        self.assertEqual(response.status_code, 302)

    def test_register_password_mismatch_returns_200(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=None), \
             patch('auth.routes.get_user_by_email', return_value=None):
            with main.app.test_client() as client:
                response = client.post('/register', data={
                    'username': 'newuser',
                    'email': 'new@test.com',
                    'password': 'abc123',
                    'confirm_password': 'different',
                })
        self.assertEqual(response.status_code, 200)

    def test_register_short_password_returns_200(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=None), \
             patch('auth.routes.get_user_by_email', return_value=None):
            with main.app.test_client() as client:
                response = client.post('/register', data={
                    'username': 'newuser',
                    'email': 'new@test.com',
                    'password': 'abc',
                    'confirm_password': 'abc',
                })
        self.assertEqual(response.status_code, 200)

    def test_register_duplicate_username_returns_200(self):
        existing = _fake_user(username='taken')
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_username', return_value=existing):
            with main.app.test_client() as client:
                response = client.post('/register', data={
                    'username': 'taken',
                    'email': 'new@test.com',
                    'password': 'password123',
                    'confirm_password': 'password123',
                })
        self.assertEqual(response.status_code, 200)


class TestProfileAccessControl(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main.app.config['SECRET_KEY'] = 'test-secret'

    def test_unauthenticated_user_is_redirected(self):
        with main.app.test_client() as client:
            response = client.get('/profile')
        self.assertEqual(response.status_code, 302)

    def test_unauthenticated_redirect_points_to_login(self):
        with main.app.test_client() as client:
            response = client.get('/profile')
        self.assertIn('/login', response.headers.get('Location', ''))

    def test_guest_user_is_redirected_to_login(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            response = client.get('/profile')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers.get('Location', ''))

    def test_authenticated_user_gets_200(self):
        fake_user = {'id': 'uid123', 'username': 'alice',
                     'email': 'alice@test.com', 'created_at': None}
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_id', return_value=fake_user):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid123'
                    sess['username'] = 'alice'
                response = client.get('/profile')
        self.assertEqual(response.status_code, 200)

    def test_authenticated_user_profile_contains_username(self):
        fake_user = {'id': 'uid123', 'username': 'alice',
                     'email': 'alice@test.com', 'created_at': None}
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_id', return_value=fake_user):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'uid123'
                    sess['username'] = 'alice'
                response = client.get('/profile')
        self.assertIn(b'alice', response.data)

    def test_user_not_found_in_db_redirects_to_login(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_id', return_value=None):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'stale_id'
                response = client.get('/profile')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers.get('Location', ''))

    def test_user_not_found_clears_user_id_from_session(self):
        with patch('auth.routes.FIREBASE_AVAILABLE', True), \
             patch('auth.routes.get_user_by_id', return_value=None):
            with main.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_id'] = 'stale_id'
                client.get('/profile')
                with client.session_transaction() as sess:
                    self.assertNotIn('user_id', sess)


class TestLogout(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main.app.config['SECRET_KEY'] = 'test-secret'

    def test_logout_redirects(self):
        with main.app.test_client() as client:
            response = client.get('/logout')
        self.assertEqual(response.status_code, 302)

    def test_logout_clears_user_id(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'uid123'
                sess['username'] = 'alice'
            client.get('/logout')
            with client.session_transaction() as sess:
                self.assertNotIn('user_id', sess)

    def test_logout_clears_is_guest(self):
        with main.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['is_guest'] = True
            client.get('/logout')
            with client.session_transaction() as sess:
                self.assertNotIn('is_guest', sess)


class TestGuestRoute(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        main.app.config['SECRET_KEY'] = 'test-secret'

    def test_guest_login_returns_200(self):
        with main.app.test_client() as client:
            response = client.post('/api/auth/guest')
        self.assertEqual(response.status_code, 200)

    def test_guest_login_sets_is_guest_in_session(self):
        with main.app.test_client() as client:
            client.post('/api/auth/guest')
            with client.session_transaction() as sess:
                self.assertTrue(sess.get('is_guest'))

    def test_guest_login_does_not_set_user_id(self):
        with main.app.test_client() as client:
            client.post('/api/auth/guest')
            with client.session_transaction() as sess:
                self.assertNotIn('user_id', sess)


if __name__ == '__main__':
    unittest.main(verbosity=2)
