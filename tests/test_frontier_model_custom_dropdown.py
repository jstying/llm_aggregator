"""Tests for the 2026-07-14 rebuild of the Claude/ChatGPT/Gemini (text) and Gemini/ChatGPT
(image) model dropdowns: native <select>/<option> popups are rendered by the OS and can't be
made to show an immediate black-selected/white-unselected/green-hover scheme (the previous
appearance:none + option{background-color} patch only got partway there and still showed the
platform's blue highlight). These five dropdowns are now built from the same
.custom-select-wrapper/.custom-select-trigger/.custom-options/.custom-option component already
used by the free-model dropdown (#customSelectWrapper), with the original <select> kept as a
hidden (display:none) value store so existing .value/.disabled-based JS is untouched.

No frontend test framework in this project (per CLAUDE.md section 9) -- these pin down what's
verifiable from the server-rendered HTML/inline JS.
"""
import unittest

import main

FRONTIER_MODEL_SELECTS = [
    ('claudeModelSelect', 'claudeModelWrapper', 'claudeModelTrigger', 'claudeModelOptions'),
    ('chatgptModelSelect', 'chatgptModelWrapper', 'chatgptModelTrigger', 'chatgptModelOptions'),
    ('geminiTextModelSelect', 'geminiTextModelWrapper', 'geminiTextModelTrigger', 'geminiTextModelOptions'),
    ('geminiModelSelect', 'geminiModelWrapper', 'geminiModelTrigger', 'geminiModelOptions'),
    ('chatgptImageModelSelect', 'chatgptImageModelWrapper', 'chatgptImageModelTrigger', 'chatgptImageModelOptions'),
]


def _render_index_as_guest():
    with main.app.test_client() as client:
        with client.session_transaction() as sess:
            sess['is_guest'] = True
        return client.get('/').data.decode()


class TestFrontierModelSelectMarkupIsCustomDropdown(unittest.TestCase):

    def test_every_frontier_select_has_a_custom_wrapper_and_is_hidden(self):
        html = _render_index_as_guest()
        for select_id, wrapper_id, trigger_id, options_id in FRONTIER_MODEL_SELECTS:
            self.assertIn(f'id="{wrapper_id}"', html)
            self.assertIn('class="custom-select-wrapper frontier-model-select"', html)
            self.assertIn(f'id="{trigger_id}"', html)
            self.assertIn(f'id="{options_id}"', html)
            select_pos = html.index(f'id="{select_id}"')
            select_tag_end = html.index('>', select_pos)
            select_open_tag = html[select_pos:select_tag_end]
            self.assertIn('style="display: none;"', select_open_tag)
            # The visible wrapper must precede the now-hidden <select> in document order.
            self.assertLess(html.index(f'id="{wrapper_id}"'), select_pos)

    def test_every_option_value_has_a_matching_custom_option(self):
        html = _render_index_as_guest()
        for select_id, wrapper_id, trigger_id, options_id in FRONTIER_MODEL_SELECTS:
            options_start = html.index(f'id="{options_id}"')
            options_end = html.index('</div>\n                    </div>', options_start)
            options_block = html[options_start:options_end]
            select_start = html.index(f'id="{select_id}"')
            select_end = html.index('</select>', select_start)
            select_block = html[select_start:select_end]
            import re
            values = re.findall(r'<option value="([^"]+)">', select_block)
            self.assertTrue(values, f'no <option> values found for {select_id}')
            for value in values:
                self.assertIn(f'data-value="{value}"', options_block)
            # Exactly one option starts pre-selected, matching the <select>'s default value.
            self.assertEqual(options_block.count('custom-option selected'), 1)

    def test_native_select_no_longer_carries_visual_styling_rules(self):
        html = _render_index_as_guest()
        self.assertNotIn('.claude-model-select-group select {', html)
        self.assertNotIn('.claude-model-select-group select:disabled', html)
        self.assertNotIn('appearance: none', html)


class TestFrontierModelDropdownJSWiring(unittest.TestCase):

    def test_setup_function_defined_and_invoked_for_all_five_selects(self):
        html = _render_index_as_guest()
        self.assertIn(
            'function setupFrontierModelDropdown(selectId, wrapperId, triggerId, optionsId)',
            html,
        )
        for select_id, wrapper_id, trigger_id, options_id in FRONTIER_MODEL_SELECTS:
            self.assertIn(
                f"setupFrontierModelDropdown('{select_id}', '{wrapper_id}', '{trigger_id}', '{options_id}')",
                html,
            )

    def test_setup_function_reuses_shared_positioning_helper(self):
        html = _render_index_as_guest()
        setup_start = html.index('function setupFrontierModelDropdown(')
        setup_end = html.index('setupFrontierModelDropdown(\'claudeModelSelect\'')
        block = html[setup_start:setup_end]
        self.assertIn('positionCustomOptions(trigger, optionsPanel)', block)

    def test_setup_function_syncs_disabled_state_via_mutation_observer(self):
        html = _render_index_as_guest()
        setup_start = html.index('function setupFrontierModelDropdown(')
        setup_end = html.index('setupFrontierModelDropdown(\'claudeModelSelect\'')
        block = html[setup_start:setup_end]
        self.assertIn('new MutationObserver(', block)
        self.assertIn("attributeFilter: ['disabled']", block)
        self.assertIn("wrapper.classList.toggle('is-locked', select.disabled)", block)

    def test_option_click_updates_select_value_and_fires_change_event(self):
        html = _render_index_as_guest()
        setup_start = html.index('function setupFrontierModelDropdown(')
        setup_end = html.index('setupFrontierModelDropdown(\'claudeModelSelect\'')
        block = html[setup_start:setup_end]
        self.assertIn("select.value = opt.getAttribute('data-value')", block)
        self.assertIn("select.dispatchEvent(new Event('change'))", block)


class TestFrontierModelDropdownHighlightDelay(unittest.TestCase):
    """2026-07-15 fix: clicking an option must not flip the black .selected highlight to the
    new option immediately -- mirrors the free-model dropdown's MODEL_HIGHLIGHT_DELAY_MS so
    the highlight only updates after the close animation finishes, avoiding a jarring
    mid-click color jump. Trigger label/select.value/change event still fire immediately;
    only the .selected class toggle is deferred."""

    def _setup_block(self):
        html = _render_index_as_guest()
        setup_start = html.index('function setupFrontierModelDropdown(')
        setup_end = html.index('setupFrontierModelDropdown(\'claudeModelSelect\'')
        return html[setup_start:setup_end]

    def test_reuses_shared_highlight_delay_constant(self):
        html = _render_index_as_guest()
        # The constant must be declared once, above setupFrontierModelDropdown, and shared
        # with the free-model dropdown rather than a second hardcoded 150.
        self.assertEqual(html.count('const MODEL_HIGHLIGHT_DELAY_MS = 150;'), 1)
        self.assertLess(
            html.index('const MODEL_HIGHLIGHT_DELAY_MS = 150;'),
            html.index('function setupFrontierModelDropdown('),
        )

    def test_option_click_defers_selected_class_toggle(self):
        block = self._setup_block()
        # syncTriggerLabel() (text only) must run synchronously on click...
        self.assertIn('syncTriggerLabel();\n                    wrapper.classList.remove', block)
        # ...while the .selected-class sync is scheduled via setTimeout, not called directly.
        self.assertIn('highlightTimer = setTimeout(syncSelectedHighlight, MODEL_HIGHLIGHT_DELAY_MS)', block)
        click_handler_start = block.index("opt.addEventListener('click'")
        click_handler_end = block.index('});', click_handler_start)
        click_handler_block = block[click_handler_start:click_handler_end]
        self.assertNotIn('syncSelectedHighlight();', click_handler_block)

    def test_reopening_immediately_resyncs_highlight_without_waiting(self):
        block = self._setup_block()
        trigger_click_start = block.index("trigger.addEventListener('click'")
        trigger_click_end = block.index("options.forEach(opt =>", trigger_click_start)
        trigger_click_block = block[trigger_click_start:trigger_click_end]
        self.assertIn('clearTimeout(highlightTimer);', trigger_click_block)
        self.assertIn('syncSelectedHighlight();', trigger_click_block)


if __name__ == '__main__':
    unittest.main()
