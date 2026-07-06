"""Covers the 2026-07-05 provider registry change: removed BlackForestLabs_Flux1Dev/
StabilityAI_SD35Large (HuggingFace ZeroGPU quota permanently exhausted, no free
replacement found) and added three new free text providers found via a full g4f
availability re-scan: CohereForAI_C4AI_Command, Groq, OpenRouterFree.

Scope is limited to this change (registry wiring), not a full re-test of shared
logic (determine_actual_model/error classification/etc.) which already has its own
whitebox coverage elsewhere and is provider-agnostic.
"""
import unittest

import main


class TestG4FProviderRegistryIntegrity(unittest.TestCase):
    """Every entry in G4F_PROVIDERS must have a matching non-empty PROVIDER_MODELS_MAP
    list, and vice versa -- guards against orphaned config on either side."""

    def test_every_provider_has_models_entry(self):
        for provider in main.G4F_PROVIDERS:
            name = provider.__name__
            self.assertIn(name, main.PROVIDER_MODELS_MAP, msg=f"{name} missing from PROVIDER_MODELS_MAP")
            self.assertTrue(main.PROVIDER_MODELS_MAP[name], msg=f"{name} has an empty model list")

    def test_every_models_entry_has_provider(self):
        provider_names = {p.__name__ for p in main.G4F_PROVIDERS}
        for name in main.PROVIDER_MODELS_MAP:
            self.assertIn(name, provider_names, msg=f"PROVIDER_MODELS_MAP has orphaned key {name}")

    def test_new_text_providers_present(self):
        provider_names = {p.__name__ for p in main.G4F_PROVIDERS}
        self.assertIn('CohereForAI_C4AI_Command', provider_names)
        self.assertIn('Groq', provider_names)
        self.assertIn('OpenRouterFree', provider_names)
        self.assertEqual(main.PROVIDER_MODELS_MAP['Groq'], ['openai/gpt-oss-120b'])
        self.assertEqual(main.PROVIDER_MODELS_MAP['OpenRouterFree'], ['openrouter/free'])
        self.assertIn('command-a-03-2025', main.PROVIDER_MODELS_MAP['CohereForAI_C4AI_Command'])


class TestImageProviderRegistryIntegrity(unittest.TestCase):
    """Same shape of check for the image-generation side, plus a regression guard on
    the two removed ZeroGPU-backed providers so they can't silently reappear without
    re-verifying availability."""

    def test_every_provider_has_models_entry(self):
        for provider in main.IMAGE_PROVIDERS:
            name = provider.__name__
            self.assertIn(name, main.IMAGE_PROVIDER_MODELS_MAP, msg=f"{name} missing from IMAGE_PROVIDER_MODELS_MAP")
            self.assertTrue(main.IMAGE_PROVIDER_MODELS_MAP[name], msg=f"{name} has an empty model list")

    def test_every_models_entry_has_provider(self):
        provider_names = {p.__name__ for p in main.IMAGE_PROVIDERS}
        for name in main.IMAGE_PROVIDER_MODELS_MAP:
            self.assertIn(name, provider_names, msg=f"IMAGE_PROVIDER_MODELS_MAP has orphaned key {name}")

    def test_zerogpu_exhausted_providers_removed(self):
        provider_names = {p.__name__ for p in main.IMAGE_PROVIDERS}
        self.assertNotIn('BlackForestLabs_Flux1Dev', provider_names)
        self.assertNotIn('StabilityAI_SD35Large', provider_names)
        self.assertNotIn('BlackForestLabs_Flux1Dev', main.IMAGE_PROVIDER_MODELS_MAP)
        self.assertNotIn('StabilityAI_SD35Large', main.IMAGE_PROVIDER_MODELS_MAP)


class TestNewProviderRouteAndJudgePrompts(unittest.TestCase):
    """ROUTE_PROMPTS_MAP/PEER_REVIEW_PROMPTS_MAP entries are optional per provider
    (CLAUDE.md section 10), but for the three added here they were included -- this
    checks structural shape only (not exact wording, which is free to change)."""

    def test_route_prompt_exists_for_each_new_provider_model_pair(self):
        for name, models in [
            ('CohereForAI_C4AI_Command', main.PROVIDER_MODELS_MAP['CohereForAI_C4AI_Command']),
            ('Groq', main.PROVIDER_MODELS_MAP['Groq']),
            ('OpenRouterFree', main.PROVIDER_MODELS_MAP['OpenRouterFree']),
        ]:
            for model in models:
                key = (name, model)
                self.assertIn(key, main.ROUTE_PROMPTS_MAP, msg=f"missing ROUTE_PROMPTS_MAP entry for {key}")
                self.assertTrue(main.ROUTE_PROMPTS_MAP[key].startswith('\n\n[System:'))

    def test_judge_prompt_exists_for_each_new_model(self):
        for model in ('command-a-03-2025', 'command-r-08-2024', 'openai/gpt-oss-120b', 'openrouter/free'):
            self.assertIn(model, main.PEER_REVIEW_PROMPTS_MAP, msg=f"missing PEER_REVIEW_PROMPTS_MAP entry for {model}")
            self.assertIn('"score"', main.PEER_REVIEW_PROMPTS_MAP[model])


class TestDetermineActualModelForNewProviders(unittest.TestCase):
    """determine_actual_model() is provider-agnostic, but pins rule A/B behavior
    against the real (non-mocked) registry entries for the new providers."""

    def test_rule_a_requested_model_supported(self):
        self.assertEqual(
            main.determine_actual_model('Groq', 'openai/gpt-oss-120b'),
            'openai/gpt-oss-120b',
        )

    def test_rule_b_unsupported_model_falls_back_to_first(self):
        self.assertEqual(
            main.determine_actual_model('CohereForAI_C4AI_Command', 'not-a-real-model'),
            'command-a-03-2025',
        )


class TestProvidersEndpointExposesNewProviders(unittest.TestCase):
    """Black-box: GET /api/providers must expose the three new providers with the
    same field contract as existing ones."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_new_providers_present_with_required_fields(self):
        import json
        response = self.client.get('/api/providers')
        data = json.loads(response.data)
        by_name = {p['name']: p for p in data}
        for name in ('CohereForAI_C4AI_Command', 'Groq', 'OpenRouterFree'):
            self.assertIn(name, by_name, msg=f"{name} not exposed by /api/providers")
            self.assertEqual(by_name[name]['type'], 'g4f')
            self.assertEqual(by_name[name]['status'], 'available')
            self.assertIn(by_name[name]['default_model'], by_name[name]['models'])


if __name__ == '__main__':
    unittest.main()
