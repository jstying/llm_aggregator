"""White-box tests for main._delete_local_media_files_for_image_results() (2026-07-05).

Deleting an image_history entry now also deletes the local get_media_dir() files its
g4f results referenced -- safe because once the Firestore record is gone nothing can
still reach those files through the app, and it stops generated_media/ from growing
forever for entries the user explicitly deleted. This only targets files owned by an
explicitly-deleted record; it's not the periodic/blanket cleanup CLAUDE.md's "danger
zone" warns against reintroducing."""
import sys
import os
import tempfile
import shutil
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import main  # noqa: E402


class TestMediaDirRedirectedUnderSystemTempDir(unittest.TestCase):
    """Regression for the 2026-07-09 production bug: GAE Standard's local filesystem is
    read-only everywhere except /tmp (true for the python312 gen2 runtime too, not just
    legacy gen1). g4f's own get_media_dir() defaults to relative paths
    ('./generated_images'/'./generated_media') resolved against the process CWD, which
    is writable in local dev (masking the problem) but not on a real GAE instance --
    every mkdir/open there raised, and this was the actual cause behind both the
    ChatGPT *and* the Gemini image-history "local storage error" showing up together in
    production. main.py now redirects g4f's own images_dir/media_dir module globals to
    tempfile.gettempdir() right after import, since our own
    _persist_image_result_local_copy() and g4f's internal image download both funnel
    through the same get_media_dir()/module globals."""

    def test_get_media_dir_resolves_under_system_temp_dir_not_a_relative_repo_path(self):
        resolved = os.path.abspath(main.get_media_dir())
        temp_root = os.path.abspath(tempfile.gettempdir())
        self.assertTrue(
            resolved.startswith(temp_root),
            f"get_media_dir() must resolve under {temp_root}, got {resolved}"
        )

    def test_g4f_copy_images_module_globals_were_redirected(self):
        import g4f.image.copy_images as g4f_copy_images
        temp_root = os.path.abspath(tempfile.gettempdir())
        self.assertTrue(os.path.abspath(g4f_copy_images.media_dir).startswith(temp_root))
        self.assertTrue(os.path.abspath(g4f_copy_images.images_dir).startswith(temp_root))


class TestDeleteLocalMediaFilesForImageResults(unittest.TestCase):

    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.patcher = patch('main.get_media_dir', return_value=self.media_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.media_dir, ignore_errors=True)

    def _touch(self, filename):
        path = os.path.join(self.media_dir, filename)
        with open(path, 'w') as f:
            f.write('fake image bytes')
        return path

    def test_deletes_file_referenced_by_g4f_image_result_url(self):
        path = self._touch('abc123.jpg')
        results = [{
            'provider': 'PollinationsImage', 'success': True,
            'url': '/media/abc123.jpg?url=https://example.com/a.png',
            'b64_json': None, 'error': '', 'response_time': 1.1,
            'model': 'auto', 'type': 'g4f_image',
        }]
        main._delete_local_media_files_for_image_results(results)
        self.assertFalse(os.path.exists(path))

    def test_ignores_gemini_result_with_no_local_file(self):
        # A Gemini/ChatGPT result whose url is still None (e.g. never went through
        # _persist_image_result_local_copy(), such as a failed call) has nothing to
        # delete -- must not raise or attempt any deletion for it.
        results = [{
            'provider': 'Gemini', 'success': True, 'url': None,
            'b64_json': 'ZmFrZQ==', 'error': '', 'response_time': 1.1,
            'model': 'nano-banana-pro', 'type': 'google_genai',
        }]
        main._delete_local_media_files_for_image_results(results)  # must not raise

    def test_deletes_file_referenced_by_persisted_gemini_result_url(self):
        # 2026-07-06: Gemini/ChatGPT results whose b64_json got persisted to disk via
        # _persist_image_result_local_copy() now carry a real /media/<filename> url and
        # must be cleaned up the same way g4f results are.
        path = self._touch('gemini123.png')
        results = [{
            'provider': 'Gemini', 'success': True, 'url': '/media/gemini123.png',
            'b64_json': None, 'error': '', 'response_time': 1.1,
            'model': 'nano-banana-pro', 'type': 'google_genai',
        }]
        main._delete_local_media_files_for_image_results(results)
        self.assertFalse(os.path.exists(path))

    def test_deletes_file_referenced_by_persisted_chatgpt_result_url(self):
        path = self._touch('chatgpt456.png')
        results = [{
            'provider': 'ChatGPT', 'success': True, 'url': '/media/chatgpt456.png',
            'b64_json': None, 'error': '', 'response_time': 1.1,
            'model': 'gpt-image-2', 'type': 'openai_image',
        }]
        main._delete_local_media_files_for_image_results(results)
        self.assertFalse(os.path.exists(path))

    def test_skips_failed_result_with_no_url(self):
        results = [{
            'provider': 'PollinationsImage', 'success': False, 'url': None,
            'b64_json': None, 'error': 'timeout', 'response_time': 40.0,
            'model': 'auto', 'type': 'g4f_image',
        }]
        main._delete_local_media_files_for_image_results(results)  # must not raise

    def test_missing_file_on_disk_does_not_raise(self):
        results = [{
            'provider': 'PollinationsImage', 'success': True,
            'url': '/media/already-gone.jpg?url=https://example.com/a.png',
            'b64_json': None, 'error': '', 'response_time': 1.1,
            'model': 'auto', 'type': 'g4f_image',
        }]
        main._delete_local_media_files_for_image_results(results)  # must not raise

    def test_only_deletes_the_referenced_file_not_the_whole_directory(self):
        target = self._touch('abc123.jpg')
        unrelated = self._touch('unrelated-other-history.jpg')
        results = [{
            'provider': 'PollinationsImage', 'success': True,
            'url': '/media/abc123.jpg?url=https://example.com/a.png',
            'b64_json': None, 'error': '', 'response_time': 1.1,
            'model': 'auto', 'type': 'g4f_image',
        }]
        main._delete_local_media_files_for_image_results(results)
        self.assertFalse(os.path.exists(target))
        self.assertTrue(os.path.exists(unrelated))

    def test_multiple_g4f_image_results_all_cleaned_up(self):
        path_a = self._touch('a.jpg')
        path_b = self._touch('b.jpg')
        results = [
            {
                'provider': 'PollinationsImage', 'success': True,
                'url': '/media/a.jpg?url=https://example.com/a.png',
                'b64_json': None, 'error': '', 'response_time': 1.1,
                'model': 'auto', 'type': 'g4f_image',
            },
            {
                'provider': 'AnyProviderImage', 'success': True,
                'url': '/media/b.jpg?url=https://example.com/b.png',
                'b64_json': None, 'error': '', 'response_time': 2.2,
                'model': 'auto', 'type': 'g4f_image',
            },
        ]
        main._delete_local_media_files_for_image_results(results)
        self.assertFalse(os.path.exists(path_a))
        self.assertFalse(os.path.exists(path_b))

    def test_none_results_does_not_raise(self):
        main._delete_local_media_files_for_image_results(None)  # must not raise

    def test_empty_results_does_not_raise(self):
        main._delete_local_media_files_for_image_results([])  # must not raise

    def test_path_traversal_filename_is_confined_to_media_dir(self):
        # url's path component could in principle contain "../" segments -- os.path.basename
        # strips directory components entirely, so this must never touch anything outside
        # media_dir (mirrors the same guard already used by serve_generated_media()).
        outside_dir = tempfile.mkdtemp()
        try:
            secret = os.path.join(outside_dir, 'secret.txt')
            with open(secret, 'w') as f:
                f.write('do not delete me')
            results = [{
                'provider': 'PollinationsImage', 'success': True,
                'url': '/media/../../secret.txt?url=https://example.com/a.png',
                'b64_json': None, 'error': '', 'response_time': 1.1,
                'model': 'auto', 'type': 'g4f_image',
            }]
            main._delete_local_media_files_for_image_results(results)
            self.assertTrue(os.path.exists(secret))
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)


class TestPersistImageResultLocalCopy(unittest.TestCase):
    """main._persist_image_result_local_copy() (2026-07-06): converts a Gemini/ChatGPT
    result's b64_json into a local get_media_dir() file before it gets written into
    Firestore's 'results' array. Without this, appending a real (non-trivial-size)
    generated image crashes Firestore with 'Property array contains an invalid nested
    entity' -- confirmed against the live project: a base64 string over roughly 1MB
    embedded inside an array-of-maps field triggers this, and real gpt-image outputs
    routinely exceed that."""

    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.patcher = patch('main.get_media_dir', return_value=self.media_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.media_dir, ignore_errors=True)

    def test_no_b64_json_returns_result_unchanged(self):
        result = {
            'provider': 'Gemini', 'success': False, 'url': None, 'b64_json': None,
            'error': 'timeout', 'response_time': 1.0, 'model': 'nano-banana-pro',
            'type': 'google_genai',
        }
        persisted = main._persist_image_result_local_copy(result)
        self.assertIs(persisted, result)

    def test_writes_decoded_bytes_to_media_dir_and_swaps_url_for_b64_json(self):
        import base64
        raw = b'not a real png, just bytes for the test'
        result = {
            'provider': 'ChatGPT', 'success': True, 'url': None,
            'b64_json': base64.b64encode(raw).decode(),
            'error': '', 'response_time': 2.0, 'model': 'gpt-image-2',
            'type': 'openai_image',
        }
        persisted = main._persist_image_result_local_copy(result)

        self.assertIsNone(persisted['b64_json'])
        self.assertTrue(persisted['url'].startswith('/media/'))
        filename = persisted['url'].split('/media/')[1]
        with open(os.path.join(self.media_dir, filename), 'rb') as f:
            self.assertEqual(f.read(), raw)

    def test_does_not_mutate_the_original_result_dict(self):
        # The route handler still returns the original `result` (with b64_json intact)
        # to the frontend for immediate rendering -- only the persisted copy differs.
        import base64
        result = {
            'provider': 'ChatGPT', 'success': True, 'url': None,
            'b64_json': base64.b64encode(b'abc').decode(),
            'error': '', 'response_time': 2.0, 'model': 'gpt-image-2',
            'type': 'openai_image',
        }
        original_b64 = result['b64_json']
        main._persist_image_result_local_copy(result)
        self.assertEqual(result['b64_json'], original_b64)
        self.assertIsNone(result['url'])

    def test_decode_failure_returns_small_failure_result_not_original(self):
        # 2026-07-08 regression: a persist failure used to return the original result
        # unchanged, still carrying its (potentially multi-MB) b64_json. That oversized
        # dict then blew up append_image_history_result()'s Firestore write with the
        # same 'Property array contains an invalid nested entity' error this whole
        # persistence mechanism exists to avoid -- and because that exception was only
        # logged and swallowed by the caller, the entire record silently never made it
        # into history, even though the frontend had already shown the image as a
        # success. The failure branch must now degrade to a small, safe result instead.
        result = {
            'provider': 'ChatGPT', 'success': True, 'url': None,
            'b64_json': 'not-valid-base64!!!',
            'error': '', 'response_time': 2.0, 'model': 'gpt-image-2',
            'type': 'openai_image',
        }
        persisted = main._persist_image_result_local_copy(result)
        self.assertIsNot(persisted, result)
        self.assertFalse(persisted['success'])
        self.assertIsNone(persisted['url'])
        self.assertIsNone(persisted['b64_json'])
        self.assertTrue(persisted['error'])
        self.assertEqual(persisted['provider'], 'ChatGPT')

    def test_write_failure_falls_back_to_small_result_instead_of_leaking_b64_json(self):
        # Same regression, but from the disk-write side (e.g. a full/read-only volume
        # on a real GAE instance) rather than a decode error: valid base64, but the
        # file write itself raises. The huge b64_json must never survive into the
        # returned result.
        import base64
        raw = os.urandom(1500 * 1024)
        result = {
            'provider': 'ChatGPT', 'success': True, 'url': None,
            'b64_json': base64.b64encode(raw).decode(),
            'error': '', 'response_time': 2.0, 'model': 'gpt-image-2',
            'type': 'openai_image',
        }
        with patch('builtins.open', side_effect=OSError('No space left on device')):
            persisted = main._persist_image_result_local_copy(result)

        self.assertFalse(persisted['success'])
        self.assertIsNone(persisted['url'])
        self.assertIsNone(persisted['b64_json'])
        self.assertTrue(persisted['error'])
        # The original caller-visible dict must still be untouched, same as the
        # success path -- the frontend still renders the image for this request.
        self.assertEqual(result['b64_json'], base64.b64encode(raw).decode())

    def test_large_base64_that_would_overflow_firestore_is_persisted_as_url(self):
        # Regression guard for the actual production bug: a base64 payload well past
        # the ~1MB threshold that crashes a raw Firestore array-of-maps append must end
        # up as a small url reference, not an inline blob.
        import base64
        raw = os.urandom(1500 * 1024)
        result = {
            'provider': 'ChatGPT', 'success': True, 'url': None,
            'b64_json': base64.b64encode(raw).decode(),
            'error': '', 'response_time': 3.0, 'model': 'gpt-image-2',
            'type': 'openai_image',
        }
        persisted = main._persist_image_result_local_copy(result)
        self.assertIsNone(persisted['b64_json'])
        self.assertLess(len(persisted['url']), 200)


if __name__ == '__main__':
    unittest.main()
