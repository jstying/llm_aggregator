"""Regression coverage for the 2026-07-05 GAE deploy bug fix.

Bug report after a real deploy: Claude/Gemini both failed with "invalid/missing
API key" while ChatGPT worked, even though the user uploaded a real .env. Root
cause chain:

1. .gcloudignore had its ".env" ignore rule accidentally commented out, so .env
   was bundled into every `gcloud app deploy`.
2. app.yaml's env_variables pre-set ANTHROPIC_API_KEY/GEMINI_API_KEY to literal
   placeholder strings like "${ANTHROPIC_API_KEY}" whenever the deployer forgets
   to hand-replace them before deploying.
3. load_dotenv() (main.py) does not override already-set environment variables
   by default, so the real values from the bundled .env never took effect for
   those two -- only OPENAI_API_KEY worked, because app.yaml never declared it
   at all so nothing blocked .env from supplying it.

Separately, blind peer review intermittently disappeared entirely in production:
gunicorn's default sync worker timeout (30s) is shorter than the peer review/
image-generation worst-case formulas in main.py, so gunicorn was killing the
worker mid-request under real cloud latency.

These are plain text-file assertions (no YAML dependency in requirements.txt),
scoped to the two config files touched by this fix.
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(relative_path):
    with open(os.path.join(REPO_ROOT, relative_path), encoding='utf-8') as f:
        return f.read()


class TestGcloudignoreExcludesEnv(unittest.TestCase):
    def test_env_is_an_active_ignore_rule(self):
        lines = [line.strip() for line in _read('.gcloudignore').splitlines()]
        self.assertIn(
            '.env', lines,
            msg='.env must be an uncommented .gcloudignore entry, otherwise local '
                'secrets get bundled into every `gcloud app deploy`'
        )

    def test_env_is_not_commented_out(self):
        for line in _read('.gcloudignore').splitlines():
            stripped = line.strip()
            self.assertFalse(
                stripped == '# .env',
                msg='.env ignore rule must not be commented out'
            )


class TestAppYamlEnvVariables(unittest.TestCase):
    def setUp(self):
        self.app_yaml = _read('app.yaml')

    def test_openai_api_key_declared(self):
        self.assertIn(
            'OPENAI_API_KEY:', self.app_yaml,
            msg='OPENAI_API_KEY must be declared in env_variables like the other '
                'three frontier provider keys, otherwise ChatGPT has no supported '
                'way to receive a real key through the documented deploy workflow'
        )

    def test_all_four_provider_keys_use_placeholder_form(self):
        for key in ('SECRET_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'OPENAI_API_KEY'):
            match = re.search(rf'{key}:\s*"(\$\{{{key}\}})"', self.app_yaml)
            self.assertIsNotNone(
                match, msg=f'{key} must keep the "${{{key}}}" placeholder form in committed app.yaml'
            )


class TestGunicornTimeout(unittest.TestCase):
    def test_entrypoint_sets_an_explicit_timeout(self):
        app_yaml = _read('app.yaml')
        match = re.search(r'entrypoint:\s*gunicorn.*--timeout[= ](\d+)', app_yaml)
        self.assertIsNotNone(
            match,
            msg='gunicorn entrypoint must set an explicit --timeout; the default '
                '30s sync worker timeout is shorter than this app\'s own peer '
                'review/image generation worst-case budgets and silently kills '
                'those requests in production'
        )
        timeout_seconds = int(match.group(1))
        # Must be comfortably above the image generation worst case (AnyProvider:
        # 2 * 70s advisory + 5s buffer = 145s, see IMAGE advisory overrides in main.py).
        self.assertGreaterEqual(timeout_seconds, 150)


if __name__ == '__main__':
    unittest.main()
