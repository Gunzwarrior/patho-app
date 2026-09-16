"""AppTest coverage for Workspace's generation-scoped state flows.

These tests use the isolated seeded DB fixture. Save/overwrite flows will
use mutable_db when added; the initial preset-switch and Quick Type cases
only read the fixture database.
"""

import pytest
import content_changes
import content_editing
import content_snapshot
import content_studio
import database as db_module
import composition
from test_stage5_packages import graph, run
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


def _snapshot_hash(path):
    return content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(path))


def _reopen_case(app, case_number):
    app.session_state["_reopen_case_number"] = case_number
    app.session_state["_do_case_reopen"] = True
    app.run()
    assert not app.exception


def test_reopen_archived_pending_preset_is_case_local_not_new_choice(mutable_workspace, mutable_db):
    preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
    assert db_module.save_case("26PR300004", preset["id"], "", {}, "<p>draft</p>")
    content_editing.record_initial_snapshot("a" * 64)
    review = content_studio.review(
        [content_studio.operation("archive", "Presets", "dai")], _snapshot_hash(mutable_db),
        summary="archive pending preset", db_name=mutable_db,
    )
    content_changes.apply_review(review, db_name=mutable_db)

    _reopen_case(mutable_workspace, "26PR300004")
    selector = mutable_workspace.selectbox(key="preset_select")
    assert selector.value == preset["id"] and "Appendice (dai)" in selector.options
    mutable_workspace.session_state["_do_workspace_reset"] = True
    mutable_workspace.run()
    assert "Appendice (dai)" not in mutable_workspace.selectbox(key="preset_select").options


def test_reopen_pending_case_resolves_archived_ad_hoc_block_under_active_preset(mutable_workspace, mutable_db):
    """Saved composition, rather than Preset lifecycle state, authorizes legacy resolution."""
    dai = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
    ad_hoc = next(block for block in db_module.get_preset_blocks(_preset_id("vb"))
                  if block["key"] == "vesicule_biliaire")
    structured = {
        "block_instances": [{"block_id": ad_hoc["block_id"], "instance_no": 700}],
        "blocks": {"vesicule_biliaire#700": {}},
    }
    assert db_module.save_case("26PR300003", dai["id"], "", structured, "<p>draft</p>")
    content_editing.record_initial_snapshot("a" * 64)
    review = content_studio.review(
        [content_studio.operation("archive", "Blocks", "vesicule_biliaire")], _snapshot_hash(mutable_db),
        summary="archive cross-preset ad hoc block", db_name=mutable_db,
    )
    content_changes.apply_review(review, db_name=mutable_db)
    assert db_module.get_preset_by_id(dai["id"])["is_archived"] == 0

    _reopen_case(mutable_workspace, "26PR300003")

    assert mutable_workspace.session_state["_case_block_instances"] == structured["block_instances"]


def test_package_decimal_zero_preview_matches_fresh_workspace(mutable_db):
    operations = graph()
    operations[-1]["values"]["field_overrides"]["synthetic_size"] = 0
    review = run(mutable_db, operations)
    expected = next(
        preset for preset in review.presets if preset["code"] == "synthetic_preset"
    )["after"]["report"]
    content_editing.record_initial_snapshot("a" * 64)
    content_changes.apply_review(review, db_name=mutable_db)

    app = AppTest.from_file("pages/workspace.py").run()
    preset_id = _preset_id("synthetic_preset")
    block = db_module.get_preset_blocks(preset_id)[0]
    _select_preset(app, preset_id)
    generation = app.session_state["_form_generation"]

    assert not app.exception
    assert app.text_input(
        key=f"field_{block['block_id']}_{block['sort_order']}_synthetic_size_{generation}"
    ).value == "0"
    assert app.text_input(key=f"clin_info_{generation}").value == expected["clinical_info"] == "Taille 0"
    assert app.text_area(key=f"final_micro_edit_{generation}").value == expected["micro_plain"]


def test_applied_decimal_lookup_quick_type_seeds_workspace_text_widget_as_text(mutable_db):
    """A typed lookup value must not be placed directly in text_input state."""
    dai = next(preset for preset in db_module.get_all_presets() if preset["short_code"] == "dai")
    app = AppTest.from_file("pages/workspace.py").run()
    # Start with the existing measurement grammar, then replace it with a
    # lookup grammar in the same browser session.  This catches a stale
    # generation/widget-type value surviving the Quick Type reset.
    app.text_input(key="qt_input_0").set_value("dai37").run()
    assert not app.exception
    assert app.session_state["_form_generation"] == 1
    desired = db_module.get_quick_type_tokens(dai["id"])
    desired[1] = {**desired[1], "token_kind": "lookup", "lookup_table": {"x": 7.5}, "digit_width": None}
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai")
    intents = content_studio.quick_type_draft_operations("dai", desired, baseline=baseline)
    content_editing.record_initial_snapshot("a" * 64)
    review = content_studio.review(intents, _snapshot_hash(mutable_db), summary="decimal lookup")
    content_changes.apply_review(review)

    app.text_input(key="qt_input_1").set_value("dai3x").run()
    assert not app.exception
    generation = app.session_state["_form_generation"]
    block = db_module.get_preset_blocks(dai["id"])[0]
    widget_key = f"field_{block['block_id']}_{block['sort_order']}_appendix_size_cm_{generation}"
    assert app.text_input(key=widget_key).value == "7.5"


def test_unlimited_decimal_quick_type_overflow_is_atomic_and_visible(mutable_db):
    dai = next(preset for preset in db_module.get_all_presets() if preset["short_code"] == "dai")
    desired = db_module.get_quick_type_tokens(dai["id"])
    desired[1] = {**desired[1], "digit_width": None}
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai")
    intents = content_studio.quick_type_draft_operations("dai", desired, baseline=baseline)
    content_editing.record_initial_snapshot("a" * 64)
    review = content_studio.review(
        intents, _snapshot_hash(mutable_db), summary="Unlimited decimal measurement",
    )
    content_changes.apply_review(review)

    app = AppTest.from_file("pages/workspace.py").run()
    app.text_input(key="case_id_0").set_value("26PR300016").run()
    app.text_input(key="qt_input_0").set_value("dai3" + "9" * 400).run()

    assert not app.exception
    assert app.session_state.filtered_state.get("_form_generation", 0) == 0
    assert app.text_input(key="case_id_0").value == "26PR300016"
    assert any("finite" in item.value.lower() for item in app.error)
    assert not any("✅ dai" in item.value for item in app.success)


def test_unlimited_decimal_quick_type_accepts_an_ordinary_measurement(mutable_db):
    dai = next(preset for preset in db_module.get_all_presets() if preset["short_code"] == "dai")
    desired = db_module.get_quick_type_tokens(dai["id"])
    desired[1] = {**desired[1], "digit_width": None}
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai")
    intents = content_studio.quick_type_draft_operations("dai", desired, baseline=baseline)
    content_editing.record_initial_snapshot("a" * 64)
    review = content_studio.review(
        intents, _snapshot_hash(mutable_db), summary="Unlimited decimal measurement",
    )
    content_changes.apply_review(review)

    app = AppTest.from_file("pages/workspace.py").run()
    app.text_input(key="qt_input_0").set_value("dai312345").run()
    assert not app.exception
    assert app.session_state["_form_generation"] == 1
    block = db_module.get_preset_blocks(dai["id"])[0]
    widget_key = f"field_{block['block_id']}_{block['sort_order']}_appendix_size_cm_1"
    assert app.text_input(key=widget_key).value == "12345"


def test_queued_quick_type_code_is_reparsed_after_grammar_change(mutable_db):
    """A queued code must not carry decoded values across a content change."""
    dai = next(preset for preset in db_module.get_all_presets()
               if preset["short_code"] == "dai")
    app = AppTest.from_file("pages/workspace.py").run()
    app.text_input(key="case_id_0").set_value("26PR300015").run()
    # This is the callback/application boundary: dai3 was accepted while the
    # original grammar mapped 3 to appendicite_type=periappendicite.
    app.session_state["_pending_quicktype_code"] = "dai3"
    app.session_state["_pending_quicktype_preset_id"] = dai["id"]
    app.session_state["_pending_quicktype_overrides"] = {
        0: {"appendicite_type": "periappendicite"},
    }
    app.session_state["_do_quick_type_apply"] = True

    desired = db_module.get_quick_type_tokens(dai["id"])
    desired[0] = {
        **desired[0], "field_key": "false_membranes",
        "lookup_table": {"3": True},
    }
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai")
    content_editing.record_initial_snapshot("a" * 64)
    review = content_studio.review(
        content_studio.quick_type_draft_operations(
            "dai", desired, baseline=baseline,
        ),
        _snapshot_hash(mutable_db), summary="Retarget queued code",
    )
    content_changes.apply_review(review)

    app.run()
    assert not app.exception
    assert app.session_state["_form_generation"] == 1
    block = db_module.get_preset_blocks(dai["id"])[0]
    checkbox_key = f"field_{block['block_id']}_{block['sort_order']}_false_membranes_1"
    type_key = f"field_{block['block_id']}_{block['sort_order']}_appendicite_type_1"
    assert app.checkbox(key=checkbox_key).value is True
    assert app.selectbox(key=type_key).value != "periappendicite"
    assert app.text_input(key="case_id_1").value == "26PR300015"


@pytest.mark.parametrize("action", ["compose_up_1", "compose_down_0", "compose_remove_1", "compose_add"])
def test_reopened_composition_keeps_its_layout_position_after_first_edit(mutable_workspace, action):
    """A disappearing reopen notice must not remount the unkeyed expander.

    AppTest checks the render-tree position; actual open/closed state belongs
    to the browser in the installed Streamlit version.
    """
    app = mutable_workspace
    _select_preset(app, _preset_id("etc_bi"))
    generation = app.session_state["_form_generation"]
    app.text_input(key=f"case_id_{generation}").set_value("26PR300008").run()
    _button_by_label(app, "💾 Save as Pending").click().run()

    def position():
        return next(index for index, element in app.main.children.items()
                    if element.type == "expander" and element.label == "🧩 Compose specimens")

    # Each reopen previously reintroduced the banner and the first-edit reset.
    for _ in range(2):
        _reopen_case(app, "26PR300008")
        assert any("reopened" in message.value for message in app.success)
        before = position()
        app.button(key=action).click().run()
        assert not app.exception
        assert not any("reopened" in message.value for message in app.success)
        assert position() == before
        app.run()
        assert position() == before


def test_wildcard_targets_duplicate_instance_and_follows_composition(mutable_workspace):
    app = mutable_workspace
    _select_preset(app, _preset_id("etc_bi"))
    generation = app.session_state["_form_generation"]
    app.text_input(key=f"case_id_{generation}").set_value("26PR300025").run()
    instances = app.session_state["_case_block_instances"]
    second = (instances[1]["block_id"], instances[1]["instance_no"])
    target = app.selectbox(key=f"wildcard_target_instance_{generation}")
    assert target.options[0] != target.options[1]
    target.set_value(second).run()
    app.text_area(key="wildcard_text").set_value("NOTE SECOND SPECIMEN").run()
    app.button(key="wildcard_add").click().run()
    assert not app.exception
    assert app.session_state["wildcard_notes"][0]["target_idx"] == 1
    micro = app.text_area(key=f"final_micro_edit_{generation}").value
    assert micro.index("NOTE SECOND SPECIMEN") > micro.index("**2.")
    assert micro.count("NOTE SECOND SPECIMEN") == 1

    app.button(key="compose_up_1").click().run()
    assert app.session_state["wildcard_notes"][0]["target_idx"] == 0
    micro = app.text_area(key=f"final_micro_edit_{generation}").value
    assert micro.index("NOTE SECOND SPECIMEN") < micro.index("**2.")
    _button_by_label(app, "💾 Save as Pending").click().run()
    saved = db_module.get_case_by_number("26PR300025")
    assert saved["structured_input"]["wildcard_notes"][0]["target_idx"] == 0
    _reopen_case(app, "26PR300025")
    generation = app.session_state["_form_generation"]
    micro = app.text_area(key=f"final_micro_edit_{generation}").value
    assert micro.index("NOTE SECOND SPECIMEN") < micro.index("**2.")
    app.button(key="compose_remove_0").click().run()
    assert not app.exception
    assert app.session_state["wildcard_notes"] == []
    assert "NOTE SECOND SPECIMEN" not in app.text_area(key=f"final_micro_edit_{generation}").value


@pytest.mark.parametrize("value,expected", [
    ("", "Liquide clair."), ("0", "Liquide clair de 0 mL."),
    ("2,5", "Liquide clair de 2.5 mL."),
])
def test_thyroid_liquid_volume_workspace_and_reopen(mutable_workspace, value, expected):
    app = mutable_workspace
    _select_preset(app, _preset_id("etc0"))
    generation = app.session_state["_form_generation"]
    app.text_input(key=f"case_id_{generation}").set_value("26PR300020").run()
    volume = next(widget for widget in app.text_input if "liquid_volume_ml" in (widget.key or ""))
    volume.set_value(value).run()
    micro = app.text_area(key=f"final_micro_edit_{generation}").value
    assert expected in micro
    assert "None" not in micro
    _button_by_label(app, "💾 Save as Pending").click().run()
    _reopen_case(app, "26PR300020")
    generation = app.session_state["_form_generation"]
    assert expected in app.text_area(key=f"final_micro_edit_{generation}").value


class TestPresetSwitchReset:
    def test_switching_thyroid_variants_preserves_case_id_and_resets_fields(self, workspace):
        _select_preset(workspace, _preset_id("etc0"))
        first_generation = workspace.session_state["_form_generation"]
        workspace.text_input(key=f"case_id_{first_generation}").set_value("26PR300007").run()

        old_pattern = next(widget for widget in workspace.selectbox if widget.label == "Aspect cytologique")
        old_pattern.set_value("etc3").run()
        _select_preset(workspace, _preset_id("etc5"))

        new_generation = workspace.session_state["_form_generation"]
        assert new_generation == first_generation + 1
        assert workspace.session_state[f"case_id_{new_generation}"] == "26PR300007"
        new_pattern = next(widget for widget in workspace.selectbox if widget.label == "Aspect cytologique")
        assert new_pattern.value == "etc5"


class TestQuickTypeApply:
    def test_success_applies_preset_and_overrides_atomically(self, workspace):
        workspace.text_input(key="case_id_0").set_value("26PR300006").run()
        workspace.text_input(key="qt_input_0").set_value("dai37").run()

        assert workspace.session_state["_form_generation"] == 1
        assert workspace.session_state["case_id_1"] == "26PR300006"
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
        first_generation = _select_appendix_with_case_id(mutable_workspace, "26PR300017")

        _button_by_label(mutable_workspace, "💾 Save as Pending").click().run()

        assert mutable_workspace.session_state["_form_generation"] == first_generation + 1
        assert mutable_workspace.selectbox(key="preset_select").value == _preset_id("dai")
        assert mutable_workspace.text_input(key=f"case_id_{first_generation + 1}").value == ""
        saved = db_module.get_case_by_number("26PR300017")
        assert saved["status"] == "pending"
        assert saved["pending_reason"] == "IHC"
        assert any("26PR300017" in message.value for message in mutable_workspace.success)

    def test_multiblock_case_composition_round_trips_on_reopen(self, mutable_workspace):
        _select_preset(mutable_workspace, _preset_id("gt"))
        generation = mutable_workspace.session_state["_form_generation"]
        mutable_workspace.text_input(key=f"case_id_{generation}").set_value("26PR300012").run()

        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "gt")
        expected_instances = composition.derive_block_instances(
            db_module.get_preset_blocks(preset["id"])
        )
        _button_by_label(mutable_workspace, "💾 Save as Pending").click().run()

        saved = db_module.get_case_by_number("26PR300012")
        assert saved["structured_input"]["block_instances"] == expected_instances

        reopened = AppTest.from_file("pages/workspace.py")
        reopened.run()
        assert not reopened.exception
        reopened.session_state["_reopen_case_number"] = "26PR300012"
        reopened.session_state["_do_case_reopen"] = True
        reopened.run()

        assert not reopened.exception
        assert reopened.session_state["_case_block_instances"] == expected_instances

    def test_reopen_old_case_falls_back_to_preset_instances(self, mutable_workspace):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "gt")
        assert db_module.save_case(
            "26PR300013", preset["id"], "", {"blocks": {}}, "", status="pending"
        )
        expected_instances = composition.derive_block_instances(
            db_module.get_preset_blocks(preset["id"])
        )

        mutable_workspace.session_state["_reopen_case_number"] = "26PR300013"
        mutable_workspace.session_state["_do_case_reopen"] = True
        mutable_workspace.run()

        assert not mutable_workspace.exception
        assert mutable_workspace.session_state["_case_block_instances"] == expected_instances

    def test_reorder_and_remove_composition_round_trip(self, mutable_workspace):
        _select_preset(mutable_workspace, _preset_id("gt"))
        generation = mutable_workspace.session_state["_form_generation"]
        mutable_workspace.text_input(key=f"case_id_{generation}").set_value("26PR300014").run()

        initial = list(mutable_workspace.session_state["_case_block_instances"])
        mutable_workspace.button(key="compose_down_0").click().run()
        assert mutable_workspace.session_state["_case_block_instances"] == [initial[1], initial[0], initial[2]]

        mutable_workspace.button(key="compose_remove_1").click().run()
        expected = [initial[1], initial[2]]
        assert mutable_workspace.session_state["_case_block_instances"] == expected
        _button_by_label(mutable_workspace, "💾 Save as Pending").click().run()

        reopened = AppTest.from_file("pages/workspace.py")
        reopened.run()
        reopened.session_state["_reopen_case_number"] = "26PR300014"
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
        mutable_workspace.text_input(key=f"case_id_{generation}").set_value("26PR300011").run()
        add_widget = mutable_workspace.selectbox(key="compose_add_block")
        appendix = next(block for block in db_module.get_all_blocks() if block["key"] == "appendice")
        add_widget.set_value(appendix["id"]).run()
        mutable_workspace.button(key="compose_add").click().run()

        added = mutable_workspace.session_state["_case_block_instances"][-1]
        assert added["block_id"] == appendix["id"]
        assert added["instance_no"] == 1000
        _button_by_label(mutable_workspace, "💾 Save as Pending").click().run()
        saved = db_module.get_case_by_number("26PR300011")
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
        assert db_module.save_case("26PR300010", dai["id"], "", {}, "", status="pending", pending_reason="IHC")
        _select_appendix_with_case_id(mutable_workspace, "26PR300010")

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
        _select_appendix_with_case_id(mutable_workspace, "26PR300009")
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
        assert db_module.save_case("26PR300024", preset["id"], "", {}, "<p>frozen report</p>", status="validated")
        conn = db_module.get_db_connection()
        conn.execute("UPDATE Blocks SET micro_template = 'CURRENT TEMPLATE MUST NOT RENDER' WHERE key = 'appendice'")
        conn.commit()
        conn.close()
        _reopen_case(mutable_workspace, "26PR300024")

        assert not any(button.label.startswith("💾 Save") for button in mutable_workspace.button)
        return_button = _button_by_label(mutable_workspace, "↩️ Return to Pending")
        assert return_button.disabled is True
        assert any("frozen" in info.value for info in mutable_workspace.info)
        assert any("frozen report" in markdown.value for markdown in mutable_workspace.markdown)
        assert not any("CURRENT TEMPLATE MUST NOT RENDER" in markdown.value for markdown in mutable_workspace.markdown)

    def test_deleted_preset_validated_case_reopens_frozen_and_cannot_return_to_pending(self, mutable_workspace, mutable_db):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        frozen_html = "<p>frozen after preset deletion</p>"
        assert db_module.save_case(
            "26PR300022", preset["id"], "", {}, frozen_html, status="validated"
        )
        content_editing.record_initial_snapshot("a" * 64)
        review = content_studio.review(
            [content_studio.operation("delete", "Presets", "dai")],
            _snapshot_hash(mutable_db), summary="delete validated case preset", db_name=mutable_db,
        )
        content_changes.apply_review(review, db_name=mutable_db)
        assert db_module.get_case_by_number("26PR300022")["preset_id"] is None

        _reopen_case(mutable_workspace, "26PR300022")

        assert any("frozen after preset deletion" in markdown.value for markdown in mutable_workspace.markdown)
        assert not any("references a preset that no longer exists" in error.value for error in mutable_workspace.error)
        generation = mutable_workspace.session_state["_form_generation"]
        mutable_workspace.text_input(key=f"return_pending_reason_{generation}").set_value("needs a live draft").run()
        mutable_workspace.checkbox(key=f"return_pending_confirm_{generation}").set_value(True).run()
        _button_by_label(mutable_workspace, "↩️ Return to Pending").click().run()

        saved = db_module.get_case_by_number("26PR300022")
        assert saved["status"] == "validated"
        assert saved["rendered_html"] == frozen_html
        assert any("Could not return this case to pending" in error.value for error in mutable_workspace.error)

    def test_new_case_button_leaves_frozen_validated_view(self, mutable_workspace):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        assert db_module.save_case("26PR300023", preset["id"], "", {}, "<p>frozen</p>", status="validated")
        _reopen_case(mutable_workspace, "26PR300023")
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
        assert db_module.save_case("26PR300021", preset["id"], "", {}, "<p>frozen</p>", status="validated")
        _select_appendix_with_case_id(mutable_workspace, "26PR300021")

        assert not any("already exists" in warning.value for warning in mutable_workspace.warning)
        assert not any("overwrite the existing case anyway" in checkbox.label for checkbox in mutable_workspace.checkbox)
        assert sum("validated and cannot be overwritten" in error.value for error in mutable_workspace.error) == 1
        assert _button_by_label(mutable_workspace, "💾 Save as Pending").disabled is True
        assert _button_by_label(mutable_workspace, "✅ Save as Validated").disabled is True

    def test_validated_history_shows_return_to_pending_reason(self, mutable_workspace):
        preset = next(p for p in db_module.get_all_presets() if p["short_code"] == "dai")
        assert db_module.save_case("26PR300005", preset["id"], "", {}, "<p>first validation</p>", status="validated")
        assert db_module.return_case_to_pending("26PR300005", "wrong validation status")
        assert db_module.save_case("26PR300005", preset["id"], "", {}, "<p>second validation</p>", status="validated")
        _reopen_case(mutable_workspace, "26PR300005")

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
        assert db_module.save_case("26PR300001", preset["id"], "", structured, "<p>old</p>")
        conn = db_module.get_db_connection()
        conn.execute("UPDATE Blocks SET micro_template = micro_template || ' ' WHERE id = ?", (blocks[0]["block_id"],))
        conn.commit()
        conn.close()
        _reopen_case(mutable_workspace, "26PR300001")

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
        assert db_module.save_case("26PR300002", preset["id"], "", structured, "<p>old</p>")
        conn = db_module.get_db_connection()
        conn.execute("UPDATE Blocks SET micro_template = micro_template || ' first' WHERE id = ?", (blocks[0]["block_id"],))
        conn.commit()
        conn.close()
        _reopen_case(mutable_workspace, "26PR300002")

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
        mutable_workspace.text_input(key=f"case_id_{generation}").set_value("26PR300018").run()
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
        assert mutable_workspace.text_input(key=f"case_id_{generation}").value == "26PR300018"
        assert mutable_workspace.session_state["_preset_display_labels"][preset_id] == original_label

        mutable_workspace.session_state["_do_workspace_reset"] = True
        mutable_workspace.run()
        assert mutable_workspace.session_state["_preset_display_labels"][preset_id] == "Appendice renommé (dai)"


@pytest.mark.parametrize("code,master_lock,context_lock", [
    ("dai", False, False), ("etc_bi", False, False),
    ("etc0", True, False), ("etc0", False, True), ("etc_bi", True, True),
])
def test_saved_case_preview_matches_workspace_reopen(mutable_workspace, code, master_lock, context_lock):
    """The offline candidate preview must reproduce actual Workspace text and HTML."""
    import json
    import editor_preview

    app = mutable_workspace
    preset_id = _preset_id(code)
    blocks = db_module.get_preset_blocks(preset_id)
    instances = composition.derive_block_instances(blocks)
    if code == "etc_bi":
        instances.reverse()
        instances = composition.add_instance(instances, blocks[0]["block_id"])
    structured = {
        "block_instances": instances,
        "blocks": {f"{blocks[0]['key']}#{i['instance_no']}": {"nodule_size_mm": size}
                   for i, size in zip(instances, ["12,5", "", "30"])} if code.startswith("etc") else {},
        "wildcard_notes": [{"target_idx": len(instances)-1, "text": "**NOTE** de contrôle", "target_name": "Spécimen", "note_type": "Autre"}],
        "master_lock": master_lock, "context_title_lock": context_lock,
        "final_micro_edit": "", "final_conc_edit": "Conclusion manuelle", "final_title_edit": "",
    }
    conn = db_module.get_db_connection()
    fingerprint = db_module.compute_case_content_fingerprint(preset_id, structured, conn)
    conn.execute(
        """INSERT INTO Cases(case_number,preset_id,clinical_info,structured_input,rendered_html,status,content_fingerprint)
           VALUES(?,?,?,?,?,'pending',?)""",
        ("26PR300019", preset_id, "Contexte libre", json.dumps(structured), "Previously saved", fingerprint),
    )
    conn.commit()
    case = dict(conn.execute("SELECT * FROM Cases WHERE case_number='26PR300019'").fetchone())
    preview = editor_preview.render_saved_case(conn, case)
    conn.close()
    _reopen_case(app, "26PR300019")
    generation = app.session_state["_form_generation"]
    assert app.text_input(key=f"final_title_edit_{generation}").value == preview["title"]
    assert app.text_input(key=f"clin_info_{generation}").value == preview["clinical_info"]
    assert app.text_area(key=f"final_micro_edit_{generation}").value == preview["micro_plain"]
    assert app.text_area(key=f"final_conc_edit_{generation}").value == preview["conclusion_plain"]
    from streamlit.string_util import clean_text
    from report_presentation import restricted_report_html
    assert any(widget.value == clean_text(restricted_report_html(preview["html"])) for widget in app.markdown)
    assert app.session_state["_case_block_instances"] == instances
