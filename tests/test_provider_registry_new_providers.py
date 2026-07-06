"""Covers the provider registry changes:

- 2026-07-05: removed BlackForestLabs_Flux1Dev/StabilityAI_SD35Large (HuggingFace
  ZeroGPU quota permanently exhausted, no free replacement found) and added
  CohereForAI_C4AI_Command/Groq/OpenRouterFree, found via a full g4f availability
  re-scan.
- 2026-07-05 (post-GAE-deploy): removed Groq/OpenRouterFree again -- both return
  "Error 403: Access from cloud provider blocked" 100% of the time when g4f runs
  from a cloud IP (GAE), unrelated to the original local-environment scan that
  added them. CohereForAI_C4AI_Command is unaffected and stays.

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
        self.assertIn('command-a-03-2025', main.PROVIDER_MODELS_MAP['CohereForAI_C4AI_Command'])

    def test_cloud_blocked_providers_removed(self):
        """Groq/OpenRouterFree were pulled after real GAE deploy logs showed a 100%
        "Access from cloud provider blocked" 403 from a cloud IP."""
        provider_names = {p.__name__ for p in main.G4F_PROVIDERS}
        self.assertNotIn('Groq', provider_names)
        self.assertNotIn('OpenRouterFree', provider_names)
        self.assertNotIn('Groq', main.PROVIDER_MODELS_MAP)
        self.assertNotIn('OpenRouterFree', main.PROVIDER_MODELS_MAP)
        self.assertNotIn(('Groq', 'openai/gpt-oss-120b'), main.ROUTE_PROMPTS_MAP)
        self.assertNotIn(('OpenRouterFree', 'openrouter/free'), main.ROUTE_PROMPTS_MAP)
        self.assertNotIn('openai/gpt-oss-120b', main.PEER_REVIEW_PROMPTS_MAP)
        self.assertNotIn('openrouter/free', main.PEER_REVIEW_PROMPTS_MAP)


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
    (CLAUDE.md section 10), but for the ones still active this checks structural
    shape only (not exact wording, which is free to change)."""

    def test_route_prompt_exists_for_each_active_provider_model_pair(self):
        for name, models in [
            ('CohereForAI_C4AI_Command', main.PROVIDER_MODELS_MAP['CohereForAI_C4AI_Command']),
        ]:
            for model in models:
                key = (name, model)
                self.assertIn(key, main.ROUTE_PROMPTS_MAP, msg=f"missing ROUTE_PROMPTS_MAP entry for {key}")
                self.assertTrue(main.ROUTE_PROMPTS_MAP[key].startswith('\n\n[System:'))

    def test_judge_prompt_exists_for_each_active_model(self):
        for model in ('command-a-03-2025', 'command-r-08-2024'):
            self.assertIn(model, main.PEER_REVIEW_PROMPTS_MAP, msg=f"missing PEER_REVIEW_PROMPTS_MAP entry for {model}")
            self.assertIn('"score"', main.PEER_REVIEW_PROMPTS_MAP[model])


class TestDetermineActualModelForNewProviders(unittest.TestCase):
    """determine_actual_model() is provider-agnostic, but pins rule A/B behavior
    against the real (non-mocked) registry entries for the new providers."""

    def test_rule_a_requested_model_supported(self):
        self.assertEqual(
            main.determine_actual_model('CohereForAI_C4AI_Command', 'command-r-08-2024'),
            'command-r-08-2024',
        )

    def test_rule_b_unsupported_model_falls_back_to_first(self):
        self.assertEqual(
            main.determine_actual_model('CohereForAI_C4AI_Command', 'not-a-real-model'),
            'command-a-03-2025',
        )


class TestProvidersEndpointExposesNewProviders(unittest.TestCase):
    """Black-box: GET /api/providers must expose the surviving new provider with the
    same field contract as existing ones, and must not expose the removed ones."""

    def setUp(self):
        main.app.config['TESTING'] = True
        self.client = main.app.test_client()

    def test_new_provider_present_with_required_fields(self):
        import json
        response = self.client.get('/api/providers')
        data = json.loads(response.data)
        by_name = {p['name']: p for p in data}
        self.assertIn('CohereForAI_C4AI_Command', by_name, msg="CohereForAI_C4AI_Command not exposed by /api/providers")
        self.assertEqual(by_name['CohereForAI_C4AI_Command']['type'], 'g4f')
        self.assertEqual(by_name['CohereForAI_C4AI_Command']['status'], 'available')
        self.assertIn(by_name['CohereForAI_C4AI_Command']['default_model'], by_name['CohereForAI_C4AI_Command']['models'])

    def test_cloud_blocked_providers_not_exposed(self):
        import json
        response = self.client.get('/api/providers')
        data = json.loads(response.data)
        by_name = {p['name']: p for p in data}
        self.assertNotIn('Groq', by_name)
        self.assertNotIn('OpenRouterFree', by_name)


if __name__ == '__main__':
    unittest.main()
