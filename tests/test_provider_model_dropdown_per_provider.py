"""Tests for the 2026-07-09 refactor of the free-model dropdown/checkbox wiring in
index.html: the single "Select free models" dropdown (compare form and image form) now
binds to whichever free provider was most recently checked, instead of merging the model
lists of every checked provider into one union. Each provider's own current model choice is
mirrored into the small label under its own checkbox ("Default: X" / "Selected: Y"). There is
no frontend test framework in this project (see CLAUDE.md section 9) -- these tests pin down
what IS verifiable from the server-rendered HTML: the JS functions/state this behavior
depends on are present and wired the way the design requires.
"""
import unittest

import main


def _render_index_as_guest():
    with main.app.test_client() as client:
        with client.session_transaction() as sess:
            sess['is_guest'] = True
        return client.get('/').data.decode()


class TestCompareFormPerProviderModelDropdown(unittest.TestCase):

    def test_dropdown_binds_to_most_recently_checked_provider(self):
        html = _render_index_as_guest()
        self.assertIn('function activeModelProvider()', html)
        self.assertIn(
            'return providerCheckOrder.length ? providerCheckOrder[providerCheckOrder.length - 1] : null;',
            html,
        )

    def test_dropdown_only_shows_active_providers_own_models_not_a_union(self):
        html = _render_index_as_guest()
        self.assertIn(
            "const availableModels = activeProvider ? (providerModelsMap[activeProvider] || []) : [];",
            html,
        )
        # The old union-of-all-checked-providers logic must be gone.
        self.assertNotIn('Object.values(providerModelsMap).forEach(models => {', html)

    def test_checkbox_click_handlers_record_check_order(self):
        html = _render_index_as_guest()
        self.assertIn('setProviderCheckOrder(this.value, this.checked);', html)
        self.assertIn('setProviderCheckOrder(checkbox.value, checkbox.checked);', html)

    def test_selecting_a_model_updates_only_the_active_providers_label(self):
        html = _render_index_as_guest()
        self.assertIn('function refreshProviderModelLabel(name)', html)
        self.assertIn(
            "small.textContent = (selected && selected !== defaultModel)\n"
            "                ? `Selected: ${selected}`\n"
            "                : `Default: ${defaultModel}`;",
            html,
        )
        # The click handler on a custom option must route the choice through the active
        # provider, not apply it globally.
        self.assertIn('const targetProvider = activeModelProvider();', html)
        self.assertIn('refreshProviderModelLabel(targetProvider);', html)

    def test_no_provider_checked_means_dropdown_locked_with_only_default_option(self):
        html = _render_index_as_guest()
        self.assertIn(
            'const locked = frontierOnlyActive || !activeModelProvider();', html
        )

    def test_submit_sends_provider_models_dict_not_single_global_model(self):
        html = _render_index_as_guest()
        self.assertIn('provider_models: selectedProviders.reduce((acc, name) => {', html)
        self.assertIn('if (providerModelSelections[name]) acc[name] = providerModelSelections[name];', html)
        # The old single global "model" field derived from modelSelect.value at submit
        # time must be gone from the compare-form submit handler.
        self.assertNotIn("const targetModel = modelSelect.value || null;", html)


class TestImageFormPerProviderModelDropdown(unittest.TestCase):

    def test_dropdown_binds_to_most_recently_checked_provider(self):
        html = _render_index_as_guest()
        self.assertIn('function activeImageModelProvider()', html)
        self.assertIn(
            'return imageProviderCheckOrder.length ? imageProviderCheckOrder[imageProviderCheckOrder.length - 1] : null;',
            html,
        )

    def test_checkbox_click_handlers_record_check_order(self):
        html = _render_index_as_guest()
        self.assertIn('setImageProviderCheckOrder(this.value, this.checked);', html)
        self.assertIn('setImageProviderCheckOrder(checkbox.value, checkbox.checked);', html)

    def test_selecting_a_model_updates_only_the_active_providers_label(self):
        html = _render_index_as_guest()
        self.assertIn('function refreshImageProviderModelLabel(name)', html)
        self.assertIn('const targetProvider = activeImageModelProvider();', html)
        self.assertIn('refreshImageProviderModelLabel(targetProvider);', html)

    def test_submit_sends_provider_models_dict_not_single_global_model(self):
        html = _render_index_as_guest()
        self.assertIn(
            'if (imageProviderModelSelections[name]) acc[name] = imageProviderModelSelections[name];',
            html,
        )
        self.assertNotIn("const targetModel = imageModelSelect.value || null;", html)


class TestClearResetsPerProviderModelState(unittest.TestCase):

    def test_clear_results_wipes_check_order_and_selections_for_both_forms(self):
        html = _render_index_as_guest()
        self.assertIn(
            "Object.keys(providerModelSelections).forEach(name => delete providerModelSelections[name]);",
            html,
        )
        self.assertIn(
            "Object.keys(imageProviderModelSelections).forEach(name => delete imageProviderModelSelections[name]);",
            html,
        )


if __name__ == '__main__':
    unittest.main()
