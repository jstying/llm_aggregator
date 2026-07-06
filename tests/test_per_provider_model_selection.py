"""Tests for the 2026-07-09 per-provider model selection refactor: the free-model dropdown
(compare form and image form) no longer applies one global model to every checked provider --
each provider now keeps its own model choice, sent to the backend as a `provider_models`
dict ({provider_name: model_name}) instead of a single `model` string. Providers absent from
the dict fall back to the legacy `model` field (if present) or their own default model.
"""
import json
import unittest
from unittest.mock import patch, MagicMock

import main


class TestCompareProviderModelsDict(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    @patch('main.g4f.ChatCompletion.create')
    def test_provider_models_dict_applies_distinct_model_per_provider(self, mock_create):
        seen_models = {}

        def _capture(*args, **kwargs):
            seen_models[kwargs['provider'].__name__] = kwargs['model']
            return 'Mock response'

        mock_create.side_effect = _capture

        payload = {
            'prompt': 'Hello',
            'providers': ['Yqcloud', 'OperaAria'],
            'provider_models': {'Yqcloud': 'gpt-4'},
        }
        response = self.client.post(
            '/api/compare', data=json.dumps(payload), content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        # Yqcloud got the explicit override.
        self.assertEqual(seen_models['Yqcloud'], 'gpt-4')
        # OperaAria wasn't in provider_models, so it falls back to its own default
        # (there's no legacy `model` field in this payload either).
        self.assertEqual(seen_models['OperaAria'], 'aria')

    @patch('main.g4f.ChatCompletion.create')
    def test_provider_absent_from_dict_falls_back_to_legacy_model_field(self, mock_create):
        seen_models = {}

        def _capture(*args, **kwargs):
            seen_models[kwargs['provider'].__name__] = kwargs['model']
            return 'Mock response'

        mock_create.side_effect = _capture

        payload = {
            'prompt': 'Hello',
            'providers': ['Yqcloud'],
            'model': 'gpt-4',
            'provider_models': {},
        }
        response = self.client.post(
            '/api/compare', data=json.dumps(payload), content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen_models['Yqcloud'], 'gpt-4')

    @patch('main.g4f.ChatCompletion.create')
    def test_missing_provider_models_key_does_not_break_request(self, mock_create):
        mock_create.return_value = 'Mock response'

        payload = {'prompt': 'Hello', 'providers': ['Yqcloud']}
        response = self.client.post(
            '/api/compare', data=json.dumps(payload), content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)


class TestGenerateImagesProviderModelsDict(unittest.TestCase):

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_provider_models_dict_applies_distinct_model_per_image_provider(self):
        seen_models = {}

        def _fake_client(*_args, **_kwargs):
            fake = MagicMock()

            def _generate(**kwargs):
                seen_models[kwargs['provider'].__name__] = kwargs.get('model')
                data_item = MagicMock()
                data_item.url = '/media/fake.png?url=x'
                data_item.b64_json = None
                response = MagicMock()
                response.data = [data_item]
                return response

            fake.images.generate.side_effect = _generate
            return fake

        with patch('main.G4FImageClient', side_effect=_fake_client):
            payload = {
                'prompt': 'a cat',
                'providers': ['AnyProvider', 'OperaAria'],
                'provider_models': {'OperaAria': 'aria'},
            }
            response = self.client.post(
                '/api/generate-images', data=json.dumps(payload), content_type='application/json'
            )

        self.assertEqual(response.status_code, 200)
        # AnyProvider has no override -> falls back to its own default ('flux').
        self.assertEqual(seen_models['AnyProvider'], 'flux')
        self.assertEqual(seen_models['OperaAria'], 'aria')


if __name__ == '__main__':
    unittest.main()
