"""AppTest coverage for Workspace's generation-scoped state flows.

These tests use the isolated seeded DB fixture. Save/overwrite flows will
use mutable_db when added; the initial preset-switch and Quick Type cases
only read the fixture database.
"""

import pytest
import database as db_module
from streamlit.testing.v1 import AppTest


@pytest.fixture
def workspace(db):
    app = AppTest.from_file("pages/workspace.py")
    app.run()
    assert not app.exception
    return app


@pytest.fixture
def mutable_workspace(mutable_db):
    app = AppTest.from_file("pages/workspace.py")
    app.run()
    assert not app.exception
    return app


def _select_preset(app, label):
    app.selectbox(key="preset_select").set_value(label).run()
    assert not app.exception


def _preset_label(app, short_code):
    return next(option for option in app.selectbox(key="preset_select").options if option.endswith(f"({short_code})"))


def _select_appendix_with_case_id(app, case_id):
    _select_preset(app, _preset_label(app, "dai"))
    generation = app.session_state["_form_generation"]
    app.text_input(key=f"case_id_{generation}").set_value(case_id).run()
    return generation


def _button_by_label(app, label):
    return next(widget for widget in app.button if widget.label == label)


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


class TestSaveAndSafetyGates:
    def test_save_pending_resets_generation_and_persists_isolated_case(self, mutable_workspace):
        first_generation = _select_appendix_with_case_id(mutable_workspace, "PENDING-1")

        _button_by_label(mutable_workspace, "💾 Save as Pending").click().run()

        assert mutable_workspace.session_state["_form_generation"] == first_generation + 1
        assert mutable_workspace.selectbox(key="preset_select").value == "Appendice (dai)"
        assert mutable_workspace.text_input(key=f"case_id_{first_generation + 1}").value == ""
        saved = db_module.get_case_by_number("PENDING-1")
        assert saved["status"] == "pending"
        assert saved["pending_reason"] == "IHC"
        assert any("PENDING-1" in message.value for message in mutable_workspace.success)

    def test_existing_case_disables_save_until_overwrite_is_confirmed(self, mutable_workspace):
        dai = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        assert db_module.save_case("DUP-1", dai["id"], "", {}, "", status="pending", pending_reason="IHC")
        _select_appendix_with_case_id(mutable_workspace, "DUP-1")

        save_button = _button_by_label(mutable_workspace, "💾 Save as Pending")
        assert save_button.disabled is True
        assert any("already exists" in message.value for message in mutable_workspace.warning)

        overwrite = next(
            widget for widget in mutable_workspace.checkbox
            if widget.label == "I understand — overwrite the existing case anyway"
        )
        overwrite.set_value(True).run()
        assert _button_by_label(mutable_workspace, "💾 Save as Pending").disabled is False

    def test_inconsistent_appendix_requires_confirmation_before_save(self, mutable_workspace):
        _select_appendix_with_case_id(mutable_workspace, "CONSISTENT-1")
        false_membranes = next(
            widget for widget in mutable_workspace.checkbox if widget.label == "Fausses membranes"
        )
        false_membranes.set_value(True).run()

        save_button = _button_by_label(mutable_workspace, "💾 Save as Pending")
        assert save_button.disabled is True
        assert any("Fausses membranes cochées" in message.value for message in mutable_workspace.warning)

        confirmation = next(
            widget for widget in mutable_workspace.checkbox
            if widget.label == "Je comprends — poursuivre malgré l'incohérence signalée"
        )
        confirmation.set_value(True).run()
        assert _button_by_label(mutable_workspace, "💾 Save as Pending").disabled is False
