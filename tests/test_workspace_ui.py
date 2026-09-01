"""AppTest coverage for Workspace's generation-scoped state flows.

These tests use the isolated seeded DB fixture. Save/overwrite flows will
use mutable_db when added; the initial preset-switch and Quick Type cases
only read the fixture database.
"""

import pytest
import database as db_module
import composition
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


def _select_preset(app, preset_id):
    app.selectbox(key="preset_select").set_value(preset_id).run()
    assert not app.exception


def _preset_id(short_code):
    return next(preset["id"] for preset in db_module.get_all_presets() if preset["short_code"] == short_code)


def _select_appendix_with_case_id(app, case_id):
    _select_preset(app, _preset_id("dai"))
    generation = app.session_state["_form_generation"]
    app.text_input(key=f"case_id_{generation}").set_value(case_id).run()
    return generation


def _button_by_label(app, label):
    return next(widget for widget in app.button if widget.label == label)


def _reopen_case(app, case_number):
    app.session_state["_reopen_case_number"] = case_number
    app.session_state["_do_case_reopen"] = True
    app.run()
    assert not app.exception


class TestPresetSwitchReset:
    def test_switching_thyroid_variants_preserves_case_id_and_resets_fields(self, workspace):
        _select_preset(workspace, _preset_id("etc0"))
        first_generation = workspace.session_state["_form_generation"]
        workspace.text_input(key=f"case_id_{first_generation}").set_value("CASE-42").run()

        old_pattern = next(widget for widget in workspace.selectbox if widget.label == "Aspect cytologique")
        old_pattern.set_value("etc3").run()
        _select_preset(workspace, _preset_id("etc5"))

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
        assert workspace.selectbox(key="preset_select").value == _preset_id("dai")
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

        assert workspace.selectbox(key="preset_select").value is None
        assert workspace.text_input(key="qt_input_0").value == "dai3x"
        assert any("expected digits" in message.value for message in workspace.error)


class TestSaveAndSafetyGates:
    def test_save_pending_resets_generation_and_persists_isolated_case(self, mutable_workspace):
        first_generation = _select_appendix_with_case_id(mutable_workspace, "PENDING-1")

        _button_by_label(mutable_workspace, "💾 Save as Pending").click().run()

        assert mutable_workspace.session_state["_form_generation"] == first_generation + 1
        assert mutable_workspace.selectbox(key="preset_select").value == _preset_id("dai")
        assert mutable_workspace.text_input(key=f"case_id_{first_generation + 1}").value == ""
        saved = db_module.get_case_by_number("PENDING-1")
        assert saved["status"] == "pending"
        assert saved["pending_reason"] == "IHC"
        assert any("PENDING-1" in message.value for message in mutable_workspace.success)

    def test_multiblock_case_composition_round_trips_on_reopen(self, mutable_workspace):
        _select_preset(mutable_workspace, _preset_id("gt"))
        generation = mutable_workspace.session_state["_form_generation"]
        mutable_workspace.text_input(key=f"case_id_{generation}").set_value("GT-COMPOSE-1").run()

        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "gt")
        expected_instances = composition.derive_block_instances(
            db_module.get_preset_blocks(preset["id"])
        )
        _button_by_label(mutable_workspace, "💾 Save as Pending").click().run()

        saved = db_module.get_case_by_number("GT-COMPOSE-1")
        assert saved["structured_input"]["block_instances"] == expected_instances

        reopened = AppTest.from_file("pages/workspace.py")
        reopened.run()
        assert not reopened.exception
        reopened.session_state["_reopen_case_number"] = "GT-COMPOSE-1"
        reopened.session_state["_do_case_reopen"] = True
        reopened.run()

        assert not reopened.exception
        assert reopened.session_state["_case_block_instances"] == expected_instances

    def test_reopen_old_case_falls_back_to_preset_instances(self, mutable_workspace):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "gt")
        assert db_module.save_case(
            "GT-LEGACY-1", preset["id"], "", {"blocks": {}}, "", status="pending"
        )
        expected_instances = composition.derive_block_instances(
            db_module.get_preset_blocks(preset["id"])
        )

        mutable_workspace.session_state["_reopen_case_number"] = "GT-LEGACY-1"
        mutable_workspace.session_state["_do_case_reopen"] = True
        mutable_workspace.run()

        assert not mutable_workspace.exception
        assert mutable_workspace.session_state["_case_block_instances"] == expected_instances

    def test_reorder_and_remove_composition_round_trip(self, mutable_workspace):
        _select_preset(mutable_workspace, _preset_id("gt"))
        generation = mutable_workspace.session_state["_form_generation"]
        mutable_workspace.text_input(key=f"case_id_{generation}").set_value("GT-REORDER-1").run()

        initial = list(mutable_workspace.session_state["_case_block_instances"])
        mutable_workspace.button(key="compose_down_0").click().run()
        assert mutable_workspace.session_state["_case_block_instances"] == [initial[1], initial[0], initial[2]]

        mutable_workspace.button(key="compose_remove_1").click().run()
        expected = [initial[1], initial[2]]
        assert mutable_workspace.session_state["_case_block_instances"] == expected
        _button_by_label(mutable_workspace, "💾 Save as Pending").click().run()

        reopened = AppTest.from_file("pages/workspace.py")
        reopened.run()
        reopened.session_state["_reopen_case_number"] = "GT-REORDER-1"
        reopened.session_state["_do_case_reopen"] = True
        reopened.run()
        assert not reopened.exception
        assert reopened.session_state["_case_block_instances"] == expected

    def test_composition_rerun_preserves_rendered_field_values(self, workspace):
        _select_preset(workspace, _preset_id("gt"))
        normal = next(widget for widget in workspace.checkbox if widget.label == "Normal ?")
        normal.set_value(False).run()
        generation = workspace.session_state["_form_generation"]
        assert "Anomalie détectée." in workspace.text_area(key=f"final_micro_edit_{generation}").value

        workspace.button(key="compose_down_0").click().run()
        assert "Anomalie détectée." in workspace.text_area(key=f"final_micro_edit_{generation}").value

    def test_add_block_uses_bare_defaults_and_round_trips(self, mutable_workspace):
        _select_preset(mutable_workspace, _preset_id("gt"))
        generation = mutable_workspace.session_state["_form_generation"]
        mutable_workspace.text_input(key=f"case_id_{generation}").set_value("GT-ADD-1").run()
        add_widget = mutable_workspace.selectbox(key="compose_add_block")
        appendix = next(block for block in db_module.get_all_blocks() if block["key"] == "appendice")
        add_widget.set_value(appendix["id"]).run()
        mutable_workspace.button(key="compose_add").click().run()

        added = mutable_workspace.session_state["_case_block_instances"][-1]
        assert added["block_id"] == appendix["id"]
        assert added["instance_no"] == 1000
        _button_by_label(mutable_workspace, "💾 Save as Pending").click().run()
        saved = db_module.get_case_by_number("GT-ADD-1")
        assert saved["structured_input"]["blocks"]["appendice#1000"]["appendicite_type"] == "endo"

    def test_quick_type_resets_composition_to_preset_defaults(self, workspace):
        _select_preset(workspace, _preset_id("gt"))
        workspace.button(key="compose_add").click().run()
        assert len(workspace.session_state["_case_block_instances"]) == 4

        generation = workspace.session_state["_form_generation"]
        workspace.text_input(key=f"qt_input_{generation}").set_value("dai").run()
        dai = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        assert workspace.session_state["_case_block_instances"] == composition.derive_block_instances(
            db_module.get_preset_blocks(dai["id"])
        )

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

    def test_validated_reopen_is_frozen_and_only_offers_audited_return(self, mutable_workspace):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        assert db_module.save_case("VALIDATED-UI-1", preset["id"], "", {}, "<p>frozen report</p>", status="validated")
        conn = db_module.get_db_connection()
        conn.execute("UPDATE Blocks SET micro_template = 'CURRENT TEMPLATE MUST NOT RENDER' WHERE key = 'appendice'")
        conn.commit()
        conn.close()
        _reopen_case(mutable_workspace, "VALIDATED-UI-1")

        assert not any(button.label.startswith("💾 Save") for button in mutable_workspace.button)
        return_button = _button_by_label(mutable_workspace, "↩️ Return to Pending")
        assert return_button.disabled is True
        assert any("frozen" in info.value for info in mutable_workspace.info)
        assert any("frozen report" in markdown.value for markdown in mutable_workspace.markdown)
        assert not any("CURRENT TEMPLATE MUST NOT RENDER" in markdown.value for markdown in mutable_workspace.markdown)

    def test_new_case_button_leaves_frozen_validated_view(self, mutable_workspace):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        assert db_module.save_case("VALIDATED-NEW-CASE-1", preset["id"], "", {}, "<p>frozen</p>", status="validated")
        _reopen_case(mutable_workspace, "VALIDATED-NEW-CASE-1")
        frozen_generation = mutable_workspace.session_state["_form_generation"]

        _button_by_label(mutable_workspace, "➕ New Case").click().run()

        assert mutable_workspace.session_state["_form_generation"] == frozen_generation + 1
        assert not any("This validated case is frozen" in info.value for info in mutable_workspace.info)
        assert not any(button.label == "↩️ Return to Pending" for button in mutable_workspace.button)
        assert mutable_workspace.selectbox(key="preset_select").value is None
        new_generation = mutable_workspace.session_state["_form_generation"]
        assert mutable_workspace.text_input(key=f"case_id_{new_generation}").value == ""

    def test_validated_case_number_shows_only_the_hard_stop_message(self, mutable_workspace):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        assert db_module.save_case("VALIDATED-COLLISION-1", preset["id"], "", {}, "<p>frozen</p>", status="validated")
        _select_appendix_with_case_id(mutable_workspace, "VALIDATED-COLLISION-1")

        assert not any("already exists" in warning.value for warning in mutable_workspace.warning)
        assert not any("overwrite the existing case anyway" in checkbox.label for checkbox in mutable_workspace.checkbox)
        assert sum("validated and cannot be overwritten" in error.value for error in mutable_workspace.error) == 1
        assert _button_by_label(mutable_workspace, "💾 Save as Pending").disabled is True
        assert _button_by_label(mutable_workspace, "✅ Save as Validated").disabled is True

    def test_validated_history_shows_return_to_pending_reason(self, mutable_workspace):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        assert db_module.save_case("AUDIT-UI-1", preset["id"], "", {}, "<p>first validation</p>", status="validated")
        assert db_module.return_case_to_pending("AUDIT-UI-1", "wrong validation status")
        assert db_module.save_case("AUDIT-UI-1", preset["id"], "", {}, "<p>second validation</p>", status="validated")
        _reopen_case(mutable_workspace, "AUDIT-UI-1")

        assert any("wrong validation status" in markdown.value for markdown in mutable_workspace.markdown)
        validation_lines = [
            markdown.value for markdown in mutable_workspace.markdown
            if markdown.value.startswith("Validated —")
        ]
        assert len(validation_lines) == 2

    def test_pending_content_change_requires_current_acknowledgement(self, mutable_workspace):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        blocks = db_module.get_preset_blocks(preset["id"])
        structured = {
            "block_instances": [{"block_id": blocks[0]["block_id"], "instance_no": blocks[0]["sort_order"]}],
            "blocks": {}, "wildcard_notes": [], "master_lock": False,
        }
        assert db_module.save_case("ACK-UI-1", preset["id"], "", structured, "<p>old</p>")
        conn = db_module.get_db_connection()
        conn.execute("UPDATE Blocks SET micro_template = micro_template || ' ' WHERE id = ?", (blocks[0]["block_id"],))
        conn.commit()
        conn.close()
        _reopen_case(mutable_workspace, "ACK-UI-1")

        assert _button_by_label(mutable_workspace, "💾 Save as Pending").disabled is True
        acknowledgement = next(widget for widget in mutable_workspace.checkbox if "acknowledge the content change" in widget.label)
        acknowledgement.set_value(True).run()
        assert _button_by_label(mutable_workspace, "💾 Save as Pending").disabled is False

    def test_open_draft_invalidates_prior_acknowledgement_after_another_content_change(self, mutable_workspace):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        blocks = db_module.get_preset_blocks(preset["id"])
        structured = {
            "block_instances": [{"block_id": blocks[0]["block_id"], "instance_no": blocks[0]["sort_order"]}],
            "blocks": {}, "wildcard_notes": [], "master_lock": False,
        }
        assert db_module.save_case("ACK-UI-2", preset["id"], "", structured, "<p>old</p>")
        conn = db_module.get_db_connection()
        conn.execute("UPDATE Blocks SET micro_template = micro_template || ' first' WHERE id = ?", (blocks[0]["block_id"],))
        conn.commit()
        conn.close()
        _reopen_case(mutable_workspace, "ACK-UI-2")

        acknowledgement = next(widget for widget in mutable_workspace.checkbox if "acknowledge the content change" in widget.label)
        acknowledgement.set_value(True).run()
        assert _button_by_label(mutable_workspace, "💾 Save as Pending").disabled is False
        assert _button_by_label(mutable_workspace, "✅ Save as Validated").disabled is False

        conn = db_module.get_db_connection()
        conn.execute("UPDATE Blocks SET micro_template = micro_template || ' second' WHERE id = ?", (blocks[0]["block_id"],))
        conn.commit()
        conn.close()
        mutable_workspace.run()

        acknowledgement = next(widget for widget in mutable_workspace.checkbox if "acknowledge the content change" in widget.label)
        assert acknowledgement.value is False
        assert _button_by_label(mutable_workspace, "💾 Save as Pending").disabled is True
        assert _button_by_label(mutable_workspace, "✅ Save as Validated").disabled is True

        acknowledgement.set_value(True).run()
        assert _button_by_label(mutable_workspace, "💾 Save as Pending").disabled is False
        assert _button_by_label(mutable_workspace, "✅ Save as Validated").disabled is False

    def test_preset_selection_survives_a_name_change_by_stable_id(self, mutable_workspace):
        preset_id = _preset_id("dai")
        _select_preset(mutable_workspace, preset_id)
        generation = mutable_workspace.session_state["_form_generation"]
        mutable_workspace.text_input(key=f"case_id_{generation}").set_value("RENAME-IN-PROGRESS-1").run()
        original_label = mutable_workspace.session_state["_preset_display_labels"][preset_id]
        conn = db_module.get_db_connection()
        conn.execute("UPDATE Presets SET name = ? WHERE id = ?", ("Appendice renommé", preset_id))
        conn.commit()
        conn.close()
        mutable_workspace.run()

        false_membranes = next(
            widget for widget in mutable_workspace.checkbox if widget.label == "Fausses membranes"
        )
        false_membranes.set_value(True).run()
        consistency_confirmation = next(
            widget for widget in mutable_workspace.checkbox
            if widget.label == "Je comprends — poursuivre malgré l'incohérence signalée"
        )
        consistency_confirmation.set_value(True).run()

        assert mutable_workspace.selectbox(key="preset_select").value == preset_id
        assert mutable_workspace.session_state["_form_generation"] == generation
        assert mutable_workspace.text_input(key=f"case_id_{generation}").value == "RENAME-IN-PROGRESS-1"
        assert mutable_workspace.session_state["_preset_display_labels"][preset_id] == original_label

        mutable_workspace.session_state["_do_workspace_reset"] = True
        mutable_workspace.run()
        assert mutable_workspace.session_state["_preset_display_labels"][preset_id] == "Appendice renommé (dai)"
