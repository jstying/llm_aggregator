"""White-box tests for the image-history CRUD functions in auth/db.py (2026-07-04).

These mirror tests/test_auth_whitebox.py's TestSaveChatHistory/TestGetChatHistoryList/
TestGetChatHistoryById/TestDeleteChatHistory/TestUpdateChatHistoryTitle/
TestTogglePinChatHistory one-for-one, but exercise the parallel image_history collection
functions (save_image_history/get_image_history_list/get_image_history_by_id/
delete_image_history/update_image_history_title/toggle_pin_image_history) instead of the
chat ones -- see auth/db.py's comment above save_image_history for why these write to an
independent Firestore collection rather than reusing 'history'.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


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


class TestSaveImageHistory(unittest.TestCase):

    def _build_mock_db(self, doc_id='imghist123'):
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc_ref.id = doc_id
        mock_db.collection.return_value.add.return_value = (None, mock_doc_ref)
        return mock_db, mock_doc_ref

    def test_returns_dict_with_id(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db('imghist123')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_image_history('uid1', 'a red apple on a table', [])

        self.assertEqual(result['id'], 'imghist123')

    def test_title_is_first_15_chars_plus_ellipsis(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_image_history('uid1', 'a red apple on a wooden table', [])

        self.assertEqual(result['title'], 'a red apple on ...')

    def test_title_equals_prompt_verbatim_when_not_longer_than_limit(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_image_history('uid1', 'cat', [])

        self.assertEqual(result['title'], 'cat')

    def test_title_has_no_ellipsis_when_prompt_exactly_15_chars(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()
        prompt = 'x' * 15

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_image_history('uid1', prompt, [])

        self.assertEqual(result['title'], prompt)

    def test_is_pinned_defaults_to_false(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_image_history('uid1', 'prompt', [])

        self.assertFalse(result['is_pinned'])

    def test_results_preserved_verbatim(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()
        results = [{'provider': 'PollinationsImage', 'success': True, 'url': 'https://x/a.png',
                    'b64_json': None, 'error': '', 'response_time': 1.0, 'model': 'auto',
                    'type': 'g4f_image'}]

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.save_image_history('uid1', 'prompt', results)

        self.assertEqual(result['results'], results)

    def test_writes_to_image_history_collection_not_history(self):
        from auth import db as auth_db
        mock_db, _ = self._build_mock_db()

        with patch.object(auth_db, 'db', mock_db):
            auth_db.save_image_history('uid1', 'prompt', [])

        mock_db.collection.assert_called_once_with('image_history')

    def test_returns_none_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.save_image_history('uid1', 'prompt', [])

        self.assertIsNone(result)


class TestGetImageHistoryList(unittest.TestCase):
    """Same single-field-equality-query + Python-layer sort/paginate contract as
    get_chat_history_list -- see its docstring in test_auth_whitebox.py for why a Firestore
    composite query (.where + double .order_by) is deliberately avoided."""

    def _make_mock_doc(self, data, doc_id='imghist1'):
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
                                    'created_at': self._ts(10)}, 'imghist1')
        mock_db = self._build_mock_db([doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_image_history_list('uid1')

        self.assertEqual(len(result), 1)

    def test_includes_id_key(self):
        from auth import db as auth_db
        doc = self._make_mock_doc({'user_id': 'uid1', 'title': 'a...', 'prompt': 'a',
                                    'results': [], 'is_pinned': False,
                                    'created_at': self._ts(10)}, 'imghist_xyz')
        mock_db = self._build_mock_db([doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_image_history_list('uid1')

        self.assertEqual(result[0]['id'], 'imghist_xyz')

    def test_query_filters_by_user_id(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_image_history_list('uid1')

        mock_db.collection.assert_called_once_with('image_history')
        _assert_where_called_with_field_filter(
            mock_db.collection.return_value.where, 'user_id', '==', 'uid1'
        )

    def test_query_does_not_chain_order_by_avoiding_composite_index(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_image_history_list('uid1')

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
            result = auth_db.get_image_history_list('uid1')

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
            result = auth_db.get_image_history_list('uid1')

        self.assertEqual([item['id'] for item in result], ['newer', 'older'])

    def test_sorts_pinned_items_by_pinned_at_ascending_oldest_pin_first(self):
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
            result = auth_db.get_image_history_list('uid1')

        self.assertEqual([item['id'] for item in result], ['pinned_first', 'pinned_second'])

    def test_missing_pinned_at_for_pinned_item_does_not_crash_sort(self):
        from auth import db as auth_db
        legacy_pinned = self._make_mock_doc({'user_id': 'uid1', 'title': 'legacy', 'prompt': '',
                                              'results': [], 'is_pinned': True,
                                              'created_at': self._ts(5)}, 'legacy')
        mock_db = self._build_mock_db([legacy_pinned])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_image_history_list('uid1')

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
            result = auth_db.get_image_history_list('uid1', limit=2, offset=1)

        self.assertEqual([item['id'] for item in result], ['h1', 'h2'])

    def test_missing_created_at_does_not_crash_sort(self):
        from auth import db as auth_db
        doc = self._make_mock_doc({'user_id': 'uid1', 'title': 'no timestamp', 'prompt': '',
                                    'results': [], 'is_pinned': False}, 'no_ts')
        mock_db = self._build_mock_db([doc])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_image_history_list('uid1')

        self.assertEqual(len(result), 1)

    def test_returns_empty_list_when_no_results(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db([])

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_image_history_list('uid1')

        self.assertEqual(result, [])

    def test_returns_empty_list_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.get_image_history_list('uid1')

        self.assertEqual(result, [])


class TestGetImageHistoryById(unittest.TestCase):

    def _build_mock_db(self, exists=True, owner_id='uid1', extra_fields=None):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = exists
        mock_doc.id = 'imghist1'
        data = {'user_id': owner_id, 'prompt': 'a red apple', 'results': []}
        if extra_fields:
            data.update(extra_fields)
        mock_doc.to_dict.return_value = data
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock_db

    def test_returns_entry_when_owned(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='uid1')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_image_history_by_id('uid1', 'imghist1')

        self.assertIsNotNone(result)
        self.assertEqual(result['prompt'], 'a red apple')

    def test_result_includes_id_key(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='uid1')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_image_history_by_id('uid1', 'imghist1')

        self.assertEqual(result['id'], 'imghist1')

    def test_returns_none_when_owned_by_another_user(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='someone_else')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_image_history_by_id('uid1', 'imghist1')

        self.assertIsNone(result)

    def test_returns_none_when_document_does_not_exist(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.get_image_history_by_id('uid1', 'ghost')

        self.assertIsNone(result)

    def test_returns_none_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.get_image_history_by_id('uid1', 'imghist1')

        self.assertIsNone(result)

    def test_reads_from_image_history_collection_not_history(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='uid1')

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_image_history_by_id('uid1', 'imghist1')

        mock_db.collection.assert_called_once_with('image_history')

    def test_does_not_mutate_the_stored_document(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='uid1')

        with patch.object(auth_db, 'db', mock_db):
            auth_db.get_image_history_by_id('uid1', 'imghist1')

        mock_db.collection.return_value.document.return_value.update.assert_not_called()
        mock_db.collection.return_value.document.return_value.delete.assert_not_called()


class TestDeleteImageHistory(unittest.TestCase):

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
            result = auth_db.delete_image_history('uid1', 'imghist1')

        self.assertTrue(result)
        mock_db.collection.return_value.document.return_value.delete.assert_called_once()

    def test_denied_when_owned_by_another_user(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='someone_else')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.delete_image_history('uid1', 'imghist1')

        self.assertFalse(result)
        mock_db.collection.return_value.document.return_value.delete.assert_not_called()

    def test_denied_when_document_does_not_exist(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.delete_image_history('uid1', 'ghost')

        self.assertFalse(result)

    def test_returns_false_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.delete_image_history('uid1', 'imghist1')

        self.assertFalse(result)


class TestUpdateImageHistoryTitle(unittest.TestCase):

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
            result = auth_db.update_image_history_title('uid1', 'imghist1', 'New Title')

        self.assertTrue(result)
        mock_db.collection.return_value.document.return_value.update.assert_called_once_with(
            {'title': 'New Title'}
        )

    def test_denied_when_owned_by_another_user(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=True, owner_id='someone_else')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.update_image_history_title('uid1', 'imghist1', 'New Title')

        self.assertFalse(result)
        mock_db.collection.return_value.document.return_value.update.assert_not_called()

    def test_denied_when_document_does_not_exist(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.update_image_history_title('uid1', 'ghost', 'New Title')

        self.assertFalse(result)

    def test_returns_false_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.update_image_history_title('uid1', 'imghist1', 'New Title')

        self.assertFalse(result)


class TestTogglePinImageHistory(unittest.TestCase):

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
            result = auth_db.toggle_pin_image_history('uid1', 'imghist1')

        self.assertTrue(result)
        mock_db.collection.return_value.document.return_value.update.assert_called_once_with(
            {'is_pinned': True, 'pinned_at': auth_db.firestore.SERVER_TIMESTAMP}
        )

    def test_toggle_unpins_when_currently_pinned(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(is_pinned=True)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.toggle_pin_image_history('uid1', 'imghist1')

        self.assertFalse(result)
        mock_db.collection.return_value.document.return_value.update.assert_called_once_with(
            {'is_pinned': False, 'pinned_at': auth_db.firestore.DELETE_FIELD}
        )

    def test_denied_when_owned_by_another_user_returns_none(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(owner_id='someone_else')

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.toggle_pin_image_history('uid1', 'imghist1')

        self.assertIsNone(result)
        mock_db.collection.return_value.document.return_value.update.assert_not_called()

    def test_denied_when_document_does_not_exist(self):
        from auth import db as auth_db
        mock_db = self._build_mock_db(exists=False)

        with patch.object(auth_db, 'db', mock_db):
            result = auth_db.toggle_pin_image_history('uid1', 'ghost')

        self.assertIsNone(result)

    def test_returns_none_when_firebase_unavailable(self):
        from auth import db as auth_db

        with patch.object(auth_db, 'FIREBASE_AVAILABLE', False):
            result = auth_db.toggle_pin_image_history('uid1', 'imghist1')

        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
