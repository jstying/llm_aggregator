import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from werkzeug.security import check_password_hash, generate_password_hash


def _assert_where_called_with_field_filter(mock_where, field_path, op_string, value):
    """auth/db.py builds queries with .where(filter=FieldFilter(...)) (the
    firebase-admin-recommended form) instead of the deprecated positional
    .where(field, op, value). FieldFilter has no __eq__, so assert_called_with
    can't compare instances directly -- inspect the captured filter's attributes."""
    mock_where.assert_called_once()
    _, kwargs = mock_where.call_args
    field_filter = kwargs['filter']
    assert field_filter.field_path == field_path
    assert field_filter.op_string == op_string
    assert field_filter.value == value


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
        _assert_where_called_with_field_filter(
            mock_db.collection.return_value.where, 'username', '==', 'bob'
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

        _assert_where_called_with_field_filter(
            mock_db.collection.return_value.where, 'email', '==', 'x@y.com'
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


class TestSaveChatHistory(unittest.TestCase):

    def _build_mock_db(self, doc_id='hist123'):
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc_ref.id = doc_id
        mock_db.collection.return_value.add.return_value = (None, mock_doc_ref)
        return mock_db, mock_doc_ref

    def test_returns_dict_with_id(self):
        from auth import db as auth_db
        mock_db, mock_doc_ref = self._build_mock_db('hist123')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_chat_history('uid1', 'What is the capital of France?', [])

        self.assertEqual(result['id'], 'hist123')

    def test_title_is_first_15_chars_plus_ellipsis(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_chat_history('uid1', 'What is the capital of France?', [])

        self.assertEqual(result['title'], 'What is the cap...')

    def test_title_equals_prompt_verbatim_when_not_longer_than_limit(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_chat_history('uid1', 'hi', [])

        self.assertEqual(result['title'], 'hi')

    def test_title_has_no_ellipsis_when_prompt_exactly_15_chars(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()
        prompt = 'x' * 15

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_chat_history('uid1', prompt, [])

        self.assertEqual(result['title'], prompt)

    def test_is_pinned_defaults_to_false(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_chat_history('uid1', 'prompt', [])

        self.assertFalse(result['is_pinned'])

    def test_results_preserved_verbatim(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()
        results = [{'provider': 'Yqcloud', 'success': True, 'response': 'hi',
                    'error': '', 'response_time': 1.0, 'model': 'gpt-3.5-turbo',
                    'type': 'g4f', 'peer_reviews': []}]

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_chat_history('uid1', 'prompt', results)

        self.assertEqual(result['results'], results)

    def test_writes_to_history_collection(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()

        with patch.object(auth_db, 'db', mock_db):
            auth_db.save_chat_history('uid1', 'prompt', [])

        mock_db.collection.assert_called_once_with('history')

    def test_returns_none_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.save_chat_history('uid1', 'prompt', [])

        self.assertIsNone(result)


class TestGetChatHistoryList(unittest.TestCase):
    """`get_chat_history_list` deliberately issues only a single-field equality query
    (`.where('user_id', '==', ...)`) against Firestore and does NOT chain `.order_by()`
    twice on it. A `.where()` + double `.order_by()` combination is a Firestore composite
    query, which requires a manually-created composite index per Firebase project (not
    something that ships with the code) — omitting it makes every call raise
    `FAILED_PRECONDITION: The query requires an index` in any environment where that index
    hasn't been created (2026-07-02 production incident). Sorting (pinned first, then by
    `created_at` descending) and pagination (`offset`/`limit`) are therefore done in Python
    on the full result set instead of pushed down into the Firestore query."""

    def _make_mock_doc(self, data, doc_id='hist1'):
        doc = MagicMock()
        doc.to_dict.return_value = dict(data)
        doc.id = doc_id
        return doc

    def _build_mock_db(self, docs):
        mock_db = MagicMock()
        mock_db.collection.return_value.where.return_value.stream.return_value = iter(docs)
        return mock_db

    def _ts(self, seconds_ago):
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)

    def test_returns_list_of_history_items(self):
        from auth import db as auth_db
        doc = self._make_mock_doc({'user_id': 'uid1', 'title': 'a...', 'prompt': 'a',
                                    'results': [], 'is_pinned': False,
                                    'created_at': self._ts(10)}, 'hist1')
        mock_db = self._build_mock_db([doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_list('uid1')

        self.assertEqual(len(result), 1)

    def test_includes_id_key(self):
        from auth import db as auth_db
        doc = self._make_mock_doc({'user_id': 'uid1', 'title': 'a...', 'prompt': 'a',
                                    'results': [], 'is_pinned': False,
                                    'created_at': self._ts(10)}, 'hist_xyz')
        mock_db = self._build_mock_db([doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_list('uid1')

        self.assertEqual(result[0]['id'], 'hist_xyz')

    def test_query_filters_by_user_id(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_chat_history_list('uid1')

        _assert_where_called_with_field_filter(
            mock_db.collection.return_value.where, 'user_id', '==', 'uid1'
        )

    def test_query_does_not_chain_order_by_avoiding_composite_index(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_chat_history_list('uid1')

        mock_db.collection.return_value.where.return_value.order_by.assert_not_called()

    def test_sorts_pinned_items_before_unpinned_regardless_of_recency(self):
        from auth import db as auth_db
        old_pinned = self._make_mock_doc({'user_id': 'uid1', 'title': 'old pinned', 'prompt': '',
                                           'results': [], 'is_pinned': True,
                                           'created_at': self._ts(9999)}, 'pinned')
        new_unpinned = self._make_mock_doc({'user_id': 'uid1', 'title': 'new unpinned', 'prompt': '',
                                             'results': [], 'is_pinned': False,
                                             'created_at': self._ts(1)}, 'unpinned')
        mock_db = self._build_mock_db([new_unpinned, old_pinned])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_list('uid1')

        self.assertEqual([item['id'] for item in result], ['pinned', 'unpinned'])

    def test_sorts_by_created_at_descending_within_unpinned_block(self):
        from auth import db as auth_db
        older = self._make_mock_doc({'user_id': 'uid1', 'title': 'older', 'prompt': '',
                                      'results': [], 'is_pinned': False,
                                      'created_at': self._ts(100)}, 'older')
        newer = self._make_mock_doc({'user_id': 'uid1', 'title': 'newer', 'prompt': '',
                                      'results': [], 'is_pinned': False,
                                      'created_at': self._ts(1)}, 'newer')
        mock_db = self._build_mock_db([older, newer])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_list('uid1')

        self.assertEqual([item['id'] for item in result], ['newer', 'older'])

    def test_sorts_pinned_items_by_pinned_at_ascending_oldest_pin_first(self):
        # The item pinned FIRST (smaller/earlier pinned_at) must stay on top; a later pin
        # must land below it — even if the later-pinned item's *conversation* (created_at)
        # is more recent. This is the 2026-07-02 user-reported bug: pinning a second item
        # used to jump above the first because pinned items were sorted by created_at, not
        # by when they were actually pinned.
        from auth import db as auth_db
        pinned_first = self._make_mock_doc({'user_id': 'uid1', 'title': 'pinned first', 'prompt': '',
                                             'results': [], 'is_pinned': True,
                                             'created_at': self._ts(5),
                                             'pinned_at': self._ts(100)}, 'pinned_first')
        pinned_second = self._make_mock_doc({'user_id': 'uid1', 'title': 'pinned second', 'prompt': '',
                                              'results': [], 'is_pinned': True,
                                              'created_at': self._ts(9999),
                                              'pinned_at': self._ts(10)}, 'pinned_second')
        mock_db = self._build_mock_db([pinned_second, pinned_first])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_list('uid1')

        self.assertEqual([item['id'] for item in result], ['pinned_first', 'pinned_second'])

    def test_missing_pinned_at_for_pinned_item_does_not_crash_sort(self):
        from auth import db as auth_db
        legacy_pinned = self._make_mock_doc({'user_id': 'uid1', 'title': 'legacy', 'prompt': '',
                                              'results': [], 'is_pinned': True,
                                              'created_at': self._ts(5)}, 'legacy')
        mock_db = self._build_mock_db([legacy_pinned])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_list('uid1')

        self.assertEqual(len(result), 1)

    def test_applies_limit_and_offset_in_python_after_sorting(self):
        from auth import db as auth_db
        docs = [
            self._make_mock_doc({'user_id': 'uid1', 'title': str(i), 'prompt': '',
                                  'results': [], 'is_pinned': False,
                                  'created_at': self._ts(i)}, f'h{i}')
            for i in range(5)
        ]
        mock_db = self._build_mock_db(docs)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_list('uid1', limit=2, offset=1)

        # sorted by created_at descending -> h0, h1, h2, h3, h4; offset=1, limit=2 -> h1, h2
        self.assertEqual([item['id'] for item in result], ['h1', 'h2'])

    def test_missing_created_at_does_not_crash_sort(self):
        from auth import db as auth_db
        doc = self._make_mock_doc({'user_id': 'uid1', 'title': 'no timestamp', 'prompt': '',
                                    'results': [], 'is_pinned': False}, 'no_ts')
        mock_db = self._build_mock_db([doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_list('uid1')

        self.assertEqual(len(result), 1)

    def test_returns_empty_list_when_no_results(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_list('uid1')

        self.assertEqual(result, [])

    def test_returns_empty_list_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.get_chat_history_list('uid1')

        self.assertEqual(result, [])


class TestGetChatHistoryById(unittest.TestCase):
    """Backs the read-only /history/<id> page (2026-07-03): a single-document fetch with
    the same ownership-check contract as delete/update/toggle-pin (doc must exist AND belong
    to the requesting user), returning the fully hydrated dict (with 'id') on success or None
    otherwise — the route layer treats None as "not found" and can't distinguish "doesn't
    exist" from "belongs to someone else" from "Firebase unavailable", same as the other
    three CRUD functions' contract."""

    def _build_mock_db(self, exists=True, owner_id='uid1', extra_fields=None):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = exists
        mock_doc.id = 'hist1'
        data = {'user_id': owner_id, 'prompt': 'hello', 'results': []}
        if extra_fields:
            data.update(extra_fields)
        mock_doc.to_dict.return_value = data
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock_db

    def test_returns_entry_when_owned(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='uid1')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_by_id('uid1', 'hist1')

        self.assertIsNotNone(result)
        self.assertEqual(result['prompt'], 'hello')

    def test_result_includes_id_key(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='uid1')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_by_id('uid1', 'hist1')

        self.assertEqual(result['id'], 'hist1')

    def test_returns_none_when_owned_by_another_user(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='someone_else')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_by_id('uid1', 'hist1')

        self.assertIsNone(result)

    def test_returns_none_when_document_does_not_exist(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_chat_history_by_id('uid1', 'ghost')

        self.assertIsNone(result)

    def test_returns_none_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.get_chat_history_by_id('uid1', 'hist1')

        self.assertIsNone(result)

    def test_does_not_mutate_the_stored_document(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='uid1')

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_chat_history_by_id('uid1', 'hist1')

        mock_db.collection.return_value.document.return_value.update.assert_not_called()
        mock_db.collection.return_value.document.return_value.delete.assert_not_called()


class TestDeleteChatHistory(unittest.TestCase):

    def _build_mock_db(self, exists=True, owner_id='uid1'):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = exists
        mock_doc.to_dict.return_value = {'user_id': owner_id}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock_db

    def test_delete_succeeds_when_owned(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='uid1')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.delete_chat_history('uid1', 'hist1')

        self.assertTrue(result)
        mock_db.collection.return_value.document.return_value.delete.assert_called_once()

    def test_denied_when_owned_by_another_user(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='someone_else')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.delete_chat_history('uid1', 'hist1')

        self.assertFalse(result)
        mock_db.collection.return_value.document.return_value.delete.assert_not_called()

    def test_denied_when_document_does_not_exist(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.delete_chat_history('uid1', 'ghost')

        self.assertFalse(result)

    def test_returns_false_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.delete_chat_history('uid1', 'hist1')

        self.assertFalse(result)


class TestUpdateChatHistoryTitle(unittest.TestCase):

    def _build_mock_db(self, exists=True, owner_id='uid1'):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = exists
        mock_doc.to_dict.return_value = {'user_id': owner_id}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock_db

    def test_update_succeeds_when_owned(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='uid1')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.update_chat_history_title('uid1', 'hist1', 'New Title')

        self.assertTrue(result)
        mock_db.collection.return_value.document.return_value.update.assert_called_once_with(
            {'title': 'New Title'}
        )

    def test_denied_when_owned_by_another_user(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='someone_else')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.update_chat_history_title('uid1', 'hist1', 'New Title')

        self.assertFalse(result)
        mock_db.collection.return_value.document.return_value.update.assert_not_called()

    def test_denied_when_document_does_not_exist(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.update_chat_history_title('uid1', 'ghost', 'New Title')

        self.assertFalse(result)

    def test_returns_false_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.update_chat_history_title('uid1', 'hist1', 'New Title')

        self.assertFalse(result)


class TestTogglePinChatHistory(unittest.TestCase):

    def _build_mock_db(self, exists=True, owner_id='uid1', is_pinned=False):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = exists
        mock_doc.to_dict.return_value = {'user_id': owner_id, 'is_pinned': is_pinned}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock_db

    def test_toggle_pins_when_currently_unpinned(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(is_pinned=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.toggle_pin_chat_history('uid1', 'hist1')

        self.assertTrue(result)
        mock_db.collection.return_value.document.return_value.update.assert_called_once_with(
            {'is_pinned': True, 'pinned_at': auth_db.firestore.SERVER_TIMESTAMP}
        )

    def test_toggle_unpins_when_currently_pinned(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(is_pinned=True)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.toggle_pin_chat_history('uid1', 'hist1')

        self.assertFalse(result)
        mock_db.collection.return_value.document.return_value.update.assert_called_once_with(
            {'is_pinned': False, 'pinned_at': auth_db.firestore.DELETE_FIELD}
        )

    def test_pin_sets_server_timestamp_sentinel_not_a_python_datetime(self):
        # Regression guard: pinned_at must be the firestore.SERVER_TIMESTAMP sentinel (resolved
        # server-side on write), not e.g. datetime.now() computed here — a client-clock value
        # would be inconsistent with created_at (which is also SERVER_TIMESTAMP) and could drift
        # across app-server instances.
        from auth import db as auth_db
        mock_db = self._build_mock_db(is_pinned=False)

        with patch.object(auth_db, 'db', mock_db):
            auth_db.toggle_pin_chat_history('uid1', 'hist1')

        call_kwargs = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        self.assertIs(call_kwargs['pinned_at'], auth_db.firestore.SERVER_TIMESTAMP)

    def test_unpin_clears_pinned_at_with_delete_field_sentinel(self):
        # A later re-pin should get a fresh pinned_at rather than resurrecting a stale one, so
        # unpinning must actually remove the field (DELETE_FIELD), not just leave it stale.
        from auth import db as auth_db
        mock_db = self._build_mock_db(is_pinned=True)

        with patch.object(auth_db, 'db', mock_db):
            auth_db.toggle_pin_chat_history('uid1', 'hist1')

        call_kwargs = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        self.assertIs(call_kwargs['pinned_at'], auth_db.firestore.DELETE_FIELD)

    def test_denied_when_owned_by_another_user_returns_none(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(owner_id='someone_else')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.toggle_pin_chat_history('uid1', 'hist1')

        self.assertIsNone(result)
        mock_db.collection.return_value.document.return_value.update.assert_not_called()

    def test_denied_when_document_does_not_exist(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.toggle_pin_chat_history('uid1', 'ghost')

        self.assertIsNone(result)

    def test_returns_none_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.toggle_pin_chat_history('uid1', 'hist1')

        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
