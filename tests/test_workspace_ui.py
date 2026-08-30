"""AppTest coverage for Workspace's generation-scoped state flows.

These tests use the isolated seeded DB fixture. Save/overwrite flows will
use mutable_db when added; the initial preset-switch and Quick Type cases
only read the fixture database.
"""

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def workspace(db):
    app = AppTest.from_file("pages/workspace.py")
    app.run()
    assert not app.exception
    return app


def _select_preset(app, label):
    app.selectbox(key="preset_select").set_value(label).run()
    assert not app.exception


def _preset_label(app, short_code):
    return next(option for option in app.selectbox(key="preset_select").options if option.endswith(f"({short_code})"))


class TestPresetSwitchReset:
    def test_switching_thyroid_variants_preserves_case_id_and_resets_fields(self, workspace):
        _select_preset(workspace, _preset_label(workspace, "etc0"))
        first_generation = workspace.session_state["_form_generation"]
        workspace.text_input(key=f"case_id_{first_generation}").set_value("CASE-42").run()

        old_pattern = next(widget for widget in workspace.selectbox if widget.label == "Aspect cytologique")
        old_pattern.set_value("etc3").run()
        _select_preset(workspace, _preset_label(workspace, "etc5"))

        new_generation = workspace.session_state["_form_generation"]
        assert new_generation == first_generation + 1
        assert workspace.session_state[f"case_id_{new_generation}"] == "CASE-42"
        new_pattern = next(widget for widget in workspace.selectbox if widget.label == "Aspect cytologique")
        assert new_pattern.value == "etc5"


class TestQuickTypeApply:
    def test_success_applies_preset_and_overrides_atomically(self, workspace):
        workspace.text_input(key="case_id_0").set_value("CASE-37").run()
        workspace.text_input(key="qt_input_0").set_value("dai37").run()

        assert workspace.session_state["_form_generation"] == 1
        assert workspace.session_state["case_id_1"] == "CASE-37"
        assert workspace.selectbox(key="preset_select").value == "Appendice (dai)"
        assert any("dai" in message.value for message in workspace.success)
        assert any(
            widget.value == "periappendicite"
            for widget in workspace.selectbox
            if widget.label == "Type d'appendicite"
        )
        assert any(
            widget.value == "7"
            for widget in workspace.text_input
            if widget.label == "Taille (cm)"
        )

    def test_parse_failure_keeps_existing_selection_and_typed_code(self, workspace):
        workspace.text_input(key="qt_input_0").set_value("dai3x").run()

        assert workspace.selectbox(key="preset_select").value == "-- Select --"
        assert workspace.text_input(key="qt_input_0").value == "dai3x"
        assert any("expected digits" in message.value for message in workspace.error)
