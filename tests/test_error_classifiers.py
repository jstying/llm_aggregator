"""Shared fixture suite for all three frontier vendor error classifiers.

Exercises _classify_anthropic_error(), _classify_google_genai_error(), and
_classify_openai_error() against one fixture table. Every fixture marked LIVE was captured
from a real vendor API response (the capture date and method are in each fixture's note);
the rest are docs-derived defensive fallbacks. The aggregate test at the bottom asserts
100% classification accuracy over the whole table, and separately over the live-verified
subset, which is the committed, repeatable version of the ad hoc classifier benchmark in
CLAUDE.md section 12.

Live-captured shapes and where they came from:
- Anthropic credit-balance exhaustion: 2026-07-05, real zero-credit Anthropic account
  (400 + type 'invalid_request_error' + 'credit balance' in the message -- NOT the 429 or
  403+billing_error the docs imply). See tests/test_claude_integration.py.
- Anthropic invalid key: 2026-08-28, real call with a deliberately invalid key
  (401 + type 'authentication_error').
- Gemini quota exhaustion: 2026-07-05, real zero-quota GEMINI_API_KEY fired through all
  three Nano Banana models (429 + private-module RateLimitError, .status absent). See
  tests/test_gemini_integration.py.
- Gemini invalid key: 2026-08-28, real call with a deliberately invalid key -- a 400
  BadRequestError embedding 'API key not valid' / 'INVALID_ARGUMENT' / reason
  API_KEY_INVALID, NOT the 403 the troubleshooting docs imply.
- OpenAI invalid key: 2026-08-28, real call with a deliberately invalid key
  (401 + code 'invalid_api_key' + type 'invalid_request_error').
- OpenAI unsupported parameter: 2026-08-28, real call with the account key
  (400 + code 'unsupported_parameter', must pass through unclassified).
"""

import unittest

import main


class FakeVendorError(Exception):
    """Duck-typed stand-in carrying the same attributes the real SDK exceptions expose.

    All three classifiers read attributes via getattr(), never isinstance(), precisely so
    shapes like this one classify identically to the private-module SDK exception classes.
    """

    def __init__(self, message='', status_code=None, code=None, error_type=None, status=None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        if error_type is not None:
            self.type = error_type
        if status is not None:
            self.status = status


# One row per fixture: (vendor, note, live_verified, exception, expected classification,
# substring the returned message must contain -- None to skip the message check).
FIXTURES = [
    # ---- Anthropic (_classify_anthropic_error) ----
    ('anthropic', 'LIVE 2026-07-05 real zero-credit account', True,
     FakeVendorError(
         message='Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.',
         status_code=400, error_type='invalid_request_error'),
     'SERVER_CREDITS_EXHAUSTED', 'credit balance'),
    ('anthropic', 'LIVE 2026-08-28 invalid key probe', True,
     FakeVendorError(
         message="Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'API key is invalid.'}, 'request_id': None}",
         status_code=401, error_type='authentication_error'),
     'PERMISSION_DENIED', 'Invalid or missing Claude API key'),
    ('anthropic', 'docs-derived billing_error fallback', False,
     FakeVendorError(message='Billing problem.', status_code=403, error_type='billing_error'),
     'SERVER_CREDITS_EXHAUSTED', 'Billing problem'),
    ('anthropic', 'adversarial: 429 rate limit must NOT be read as credits exhausted', False,
     FakeVendorError(message='Rate limit exceeded.', status_code=429, error_type='rate_limit_error'),
     None, 'Error 429'),
    ('anthropic', 'generic 5xx passthrough', False,
     FakeVendorError(message='Overloaded.', status_code=529, error_type='overloaded_error'),
     None, 'Error 529'),

    # ---- Google (_classify_google_genai_error) ----
    ('google', 'LIVE 2026-07-05 real zero-quota key, all three Nano Banana models', True,
     FakeVendorError(
         message="Error code: 429 - {'error': {'message': 'You do not have enough quota to make this request.', 'code': 'too_many_requests'}}",
         status_code=429),
     'QUOTA_EXHAUSTED', 'quota'),
    ('google', 'LIVE 2026-08-28 invalid key probe: 400 INVALID_ARGUMENT, not the documented 403', True,
     FakeVendorError(
         message="Error code: 400 - [{'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.', 'status': 'INVALID_ARGUMENT', 'details': [{'@type': 'type.googleapis.com/google.rpc.ErrorInfo', 'reason': 'API_KEY_INVALID', 'domain': 'googleapis.com'}]}}]",
         status_code=400),
     'PERMISSION_DENIED', 'API key not valid'),
    ('google', 'docs-derived RESOURCE_EXHAUSTED status string fallback', False,
     FakeVendorError(message='Resource has been exhausted.', status='RESOURCE_EXHAUSTED'),
     'QUOTA_EXHAUSTED', None),
    ('google', 'docs-derived 403 permission denied', False,
     FakeVendorError(message='The caller does not have permission.', status_code=403),
     'PERMISSION_DENIED', None),
    ('google', 'generic 5xx passthrough', False,
     FakeVendorError(message='Internal error.', status_code=500),
     None, 'Error 500'),

    # ---- OpenAI (_classify_openai_error) ----
    ('openai', 'LIVE 2026-08-28 invalid key probe', True,
     FakeVendorError(
         message="Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-inval********-key.', 'type': 'invalid_request_error', 'code': 'invalid_api_key', 'param': None}, 'status': 401}",
         status_code=401, code='invalid_api_key', error_type='invalid_request_error'),
     'PERMISSION_DENIED', 'Incorrect API key'),
    ('openai', 'LIVE 2026-08-28 unsupported_parameter shape must pass through unclassified', True,
     FakeVendorError(
         message="Error code: 400 - {'error': {'message': \"Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.\", 'type': 'invalid_request_error', 'param': 'max_tokens', 'code': 'unsupported_parameter'}}",
         status_code=400, code='unsupported_parameter', error_type='invalid_request_error'),
     None, 'Error 400'),
    ('openai', 'docs-derived insufficient_quota error code', False,
     FakeVendorError(message='You exceeded your current quota, please check your plan and billing details.',
                     status_code=429, code='insufficient_quota'),
     'QUOTA_EXHAUSTED', 'quota'),
    ('openai', 'docs-derived quota keyword in message without the code', False,
     FakeVendorError(message='You have exceeded your current quota.', status_code=429),
     'QUOTA_EXHAUSTED', None),
    ('openai', 'generic 5xx passthrough', False,
     FakeVendorError(message='The server had an error.', status_code=500),
     None, 'Error 500'),
]

CLASSIFIERS = {
    'anthropic': main._classify_anthropic_error,
    'google': main._classify_google_genai_error,
    'openai': main._classify_openai_error,
}


class TestErrorClassifierFixtures(unittest.TestCase):
    def _run_fixture(self, fixture):
        vendor, note, _live, exc, expected_cls, expected_substring = fixture
        classification, message = CLASSIFIERS[vendor](exc)
        self.assertEqual(
            classification, expected_cls,
            f'[{vendor}] {note}: expected {expected_cls}, got {classification} ({message!r})'
        )
        if expected_substring is not None:
            self.assertIn(
                expected_substring.lower(), message.lower(),
                f'[{vendor}] {note}: message {message!r} missing {expected_substring!r}'
            )

    def test_every_fixture_classifies_correctly(self):
        for fixture in FIXTURES:
            with self.subTest(vendor=fixture[0], note=fixture[1]):
                self._run_fixture(fixture)

    def test_100_percent_accuracy_over_live_verified_subset(self):
        """The resume-facing number: every live-captured vendor shape classifies correctly."""
        live = [f for f in FIXTURES if f[2]]
        self.assertGreaterEqual(len(live), 6)
        for fixture in live:
            self._run_fixture(fixture)

    def test_every_vendor_has_a_live_verified_fixture(self):
        vendors_with_live = {f[0] for f in FIXTURES if f[2]}
        self.assertEqual(vendors_with_live, {'anthropic', 'google', 'openai'})

    def test_naive_status_code_only_classifier_would_misfile_the_live_shapes(self):
        """Documents the concrete gap these classifiers close: a {429: quota, 403: permission}
        status-code table misfiles Anthropic's real 400 credit-balance shape and Gemini's real
        400 invalid-key shape, both of which the real classifiers handle."""
        def naive(exc):
            return {429: 'QUOTA_EXHAUSTED', 403: 'PERMISSION_DENIED'}.get(
                getattr(exc, 'status_code', None))

        anthropic_credit = FIXTURES[0][3]
        gemini_bad_key = FIXTURES[6][3]
        self.assertIsNone(naive(anthropic_credit))
        self.assertIsNone(naive(gemini_bad_key))
        self.assertEqual(main._classify_anthropic_error(anthropic_credit)[0], 'SERVER_CREDITS_EXHAUSTED')
        self.assertEqual(main._classify_google_genai_error(gemini_bad_key)[0], 'PERMISSION_DENIED')


if __name__ == '__main__':
    unittest.main()
