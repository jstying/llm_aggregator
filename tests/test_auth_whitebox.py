import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from werkzeug.security import check_password_hash, generate_password_hash


class TestPasswordHashing(unittest.TestCase):

    def test_hash_is_not_plaintext(self):
        hashed = generate_password_hash('secret123')
        self.assertNotEqual(hashed, 'secret123')

    def test_correct_password_verifies(self):
        hashed = generate_password_hash('correct')
        self.assertTrue(check_password_hash(hashed, 'correct'))

    def test_wrong_password_fails(self):
        hashed = generate_password_hash('correct')
        self.assertFalse(check_password_hash(hashed, 'wrong'))

    def test_empty_password_does_not_match_nonempty(self):
        hashed = generate_password_hash('')
        self.assertFalse(check_password_hash(hashed, 'x'))

    def test_two_hashes_of_same_password_are_different(self):
        h1 = generate_password_hash('same')
        h2 = generate_password_hash('same')
        self.assertNotEqual(h1, h2)

    def test_hash_result_is_string(self):
        hashed = generate_password_hash('test')
        self.assertIsInstance(hashed, str)

    def test_longer_password_hashes_correctly(self):
        pw = 'a' * 64
        hashed = generate_password_hash(pw)
        self.assertTrue(check_password_hash(hashed, pw))


class TestGetUserByUsername(unittest.TestCase):

    def _make_mock_doc(self, data, doc_id='uid123'):
        doc = MagicMock()
        doc.to_dict.return_value = dict(data)
        doc.id = doc_id
        return doc

    def _build_mock_db(self, docs):
        mock_db = MagicMock()
        (mock_db.collection.return_value
                .where.return_value
                .limit.return_value
                .stream.return_value) = iter(docs)
        return mock_db

    def test_returns_user_dict_when_found(self):
        from auth import db as auth_db
        user_data = {'username': 'alice', 'email': 'alice@example.com',
                     'password_hash': 'hash'}
        mock_doc = self._make_mock_doc(user_data, 'uid_alice')
        mock_db = self._build_mock_db([mock_doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_user_by_username('alice')

        self.assertIsNotNone(result)
        self.assertEqual(result['username'], 'alice')

    def test_returns_none_when_not_found(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_user_by_username('ghost')

        self.assertIsNone(result)

    def test_result_includes_id_key(self):
        from auth import db as auth_db
        user_data = {'username': 'alice', 'email': 'alice@example.com',
                     'password_hash': 'h'}
        mock_doc = self._make_mock_doc(user_data, 'myid')
        mock_db = self._build_mock_db([mock_doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_user_by_username('alice')

        self.assertIn('id', result)
        self.assertEqual(result['id'], 'myid')

    def test_query_filters_on_username_field(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_user_by_username('bob')

        mock_db.collection.assert_called_once_with('users')
        mock_db.collection.return_value.where.assert_called_once_with(
            'username', '==', 'bob'
        )

    def test_query_applies_limit_of_one(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_user_by_username('bob')

        mock_db.collection.return_value.where.return_value.limit.assert_called_once_with(1)

    def test_returns_first_doc_only(self):
        from auth import db as auth_db
        doc1 = self._make_mock_doc({'username': 'alice', 'email': 'a@a.com',
                                    'password_hash': 'h'}, 'id1')
        doc2 = self._make_mock_doc({'username': 'alice', 'email': 'b@b.com',
                                    'password_hash': 'h'}, 'id2')
        mock_db = self._build_mock_db([doc1, doc2])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_user_by_username('alice')

        self.assertEqual(result['id'], 'id1')


class TestGetUserByEmail(unittest.TestCase):

    def _make_mock_doc(self, data, doc_id='uid_email'):
        doc = MagicMock()
        doc.to_dict.return_value = dict(data)
        doc.id = doc_id
        return doc

    def _build_mock_db(self, docs):
        mock_db = MagicMock()
        (mock_db.collection.return_value
                .where.return_value
                .limit.return_value
                .stream.return_value) = iter(docs)
        return mock_db

    def test_returns_user_dict_when_email_found(self):
        from auth import db as auth_db
        user_data = {'username': 'bob', 'email': 'bob@test.com',
                     'password_hash': 'hash'}
        mock_doc = self._make_mock_doc(user_data, 'uid_bob')
        mock_db = self._build_mock_db([mock_doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_user_by_email('bob@test.com')

        self.assertIsNotNone(result)
        self.assertEqual(result['email'], 'bob@test.com')

    def test_returns_none_when_email_not_found(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_user_by_email('nobody@test.com')

        self.assertIsNone(result)

    def test_result_includes_id_key(self):
        from auth import db as auth_db
        user_data = {'username': 'carol', 'email': 'carol@x.com',
                     'password_hash': 'h'}
        mock_doc = self._make_mock_doc(user_data, 'carol_id')
        mock_db = self._build_mock_db([mock_doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_user_by_email('carol@x.com')

        self.assertIn('id', result)
        self.assertEqual(result['id'], 'carol_id')

    def test_query_filters_on_email_field(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_user_by_email('x@y.com')

        mock_db.collection.return_value.where.assert_called_once_with(
            'email', '==', 'x@y.com'
        )

    def test_query_applies_limit_of_one(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_user_by_email('x@y.com')

        mock_db.collection.return_value.where.return_value.limit.assert_called_once_with(1)

    def test_id_field_equals_firestore_doc_id(self):
        from auth import db as auth_db
        user_data = {'username': 'dan', 'email': 'dan@d.com', 'password_hash': 'h'}
        mock_doc = self._make_mock_doc(user_data, 'doc_999')
        mock_db = self._build_mock_db([mock_doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_user_by_email('dan@d.com')

        self.assertEqual(result['id'], 'doc_999')


if __name__ == '__main__':
    unittest.main(verbosity=2)
