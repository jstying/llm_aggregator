"""Tests for the 2026-07-13 fix: the free-model custom dropdown (#customOptions/
#imageCustomOptions) used position:absolute, which is never clipped by any ancestor --
when no free provider is checked (the union of every provider's models, the longest
possible list) opening it near the bottom of the form inflated document.scrollHeight
beyond .app-layout's own box height, leaving blank space below the sticky .left-sidebar
once scrolled past it. Fixed by switching to position:fixed (excluded from ancestor
layout sizing entirely) with JS computing top/left/width from the trigger's bounding
rect on open.

(The frontier Claude/ChatGPT/Gemini model <select> native-option restyling this file used
to also cover was superseded by the 2026-07-14 rebuild of those dropdowns into the same
custom component -- see test_frontier_model_custom_dropdown.py.)

No frontend test framework in this project (per CLAUDE.md section 9) -- these tests pin down
what's verifiable from the server-rendered HTML/inline JS: the CSS rules exist and the JS
wiring calls the positioning helper before opening either dropdown.
"""
import unittest

import main


def _render_index_as_guest():
    with main.app.test_client() as client:
        with client.session_transaction() as sess:
            sess['is_guest'] = True
        return client.get('/').data.decode()


class TestFreeModelDropdownFixedPositioning(unittest.TestCase):
    """.custom-options must be position:fixed (not absolute) so opening it can never inflate
    document.scrollHeight beyond .app-layout's own box -- and both triggers must compute its
    on-screen position via JS before revealing it."""

    def test_custom_options_is_fixed_not_absolute(self):
        html = _render_index_as_guest()
        block_start = html.index('.custom-options {')
        block_end = html.index('.custom-select-wrapper.open .custom-options')
        block = html[block_start:block_end]
        self.assertIn('position: fixed', block)
        self.assertNotIn('position: absolute', block)

    def test_open_state_contains_scroll_chaining(self):
        html = _render_index_as_guest()
        block_start = html.index('.custom-select-wrapper.open .custom-options {')
        block_end = html.index('.custom-option {')
        block = html[block_start:block_end]
        self.assertIn('overscroll-behavior: contain', block)

    def test_position_helper_defined_and_zoom_adjusted(self):
        html = _render_index_as_guest()
        self.assertIn('function positionCustomOptions(trigger, panel)', html)
        self.assertIn("getPropertyValue('--page-zoom')", html)

    def test_both_triggers_call_the_position_helper_before_opening(self):
        html = _render_index_as_guest()
        self.assertIn('positionCustomOptions(customTrigger, customOptionsContainer)', html)
        self.assertIn(
            'positionCustomOptions(imageCustomTrigger, imageCustomOptionsContainer)', html
        )

    def test_both_dropdowns_close_on_real_page_scroll(self):
        html = _render_index_as_guest()
        self.assertIn("customWrapper.classList.remove('open');\n        }, { passive: true }", html)
        self.assertIn(
            "imageCustomWrapper.classList.remove('open');\n        }, { passive: true }", html
        )


if __name__ == '__main__':
    unittest.main()
