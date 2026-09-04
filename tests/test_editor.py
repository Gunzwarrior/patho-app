"""Regression coverage for the Stage 1 read-only Editor navigator."""

from streamlit.testing.v1 import AppTest

import content_editing
import database as db_module
import editor_preview
from golden_helpers import render_preset_defaults


def _preset(short_code):
    return next(preset for preset in db_module.get_all_presets() if preset["short_code"] == short_code)


def _go_to(app, section):
    app.radio(key="editor_section").set_value(section).run()
    assert app.radio(key="editor_section").value == section
    return app


def test_preset_preview_uses_workspace_rendering_pipeline(db):
    preset = _preset("gt")
    preview = editor_preview.render_preset_defaults(preset["id"])
    expected_micro, expected_conclusion = render_preset_defaults("gt")

    assert preview["micro_plain"] == expected_micro
    assert preview["conclusion_plain"] == expected_conclusion
    assert preview["title"] == "Biopsies gastroduodénales"
    assert "<b>CONCLUSION</b>" in preview["html"]
    assert "BIOPSIES GASTRODUODÉNALES" in preview["html"]


def test_navigator_relationship_queries_show_real_content(db):
    appendix = next(block for block in db_module.get_all_editor_blocks() if block["key"] == "appendice")
    appendicitis_type = next(field for field in db_module.get_all_fields() if field["key"] == "appendicite_type")

    block_usage = db_module.get_block_usage(appendix["id"])
    field_usage = db_module.get_field_usage(appendicitis_type["id"])
    snippet_usage = db_module.get_snippet_usage("absence_malignite")

    assert [preset["short_code"] for preset in block_usage["presets"]] == ["dai"]
    assert "appendicite_type" in [field["key"] for field in block_usage["fields"]]
    assert [block["key"] for block in field_usage["blocks"]] == ["appendice"]
    assert {block["key"] for block in snippet_usage["blocks"]} == {"appendice", "vesicule_biliaire"}


def test_navigator_impact_uses_saved_case_composition(mutable_db):
    gastric = _preset("gt")
    appendix = next(block for block in db_module.get_all_editor_blocks() if block["key"] == "appendice")
    gallbladder = next(block for block in db_module.get_all_editor_blocks() if block["key"] == "vesicule_biliaire")
    assert db_module.save_case(
        "EDITOR-IMPACT-1", gastric["id"], "",
        {"block_instances": [{"block_id": appendix["id"], "instance_no": 1000}]},
        "", status="pending",
    )

    assert db_module.get_block_usage(appendix["id"])["pending_case_count"] == 1
    assert db_module.get_block_usage(gallbladder["id"])["pending_case_count"] == 0
    assert db_module.get_snippet_usage("absence_malignite")["pending_case_count"] == 1


def test_editor_keeps_content_writes_locked_until_initial_snapshot(db):
    app = AppTest.from_file("pages/editor.py")
    app.run()

    assert not app.exception
    assert {widget.key for widget in app.selectbox} == {"editor_preset_select"}
    gate = app.button(key="editor_enable_direct_editing")
    assert gate.disabled
    assert {widget.key for widget in app.button} == {"editor_enable_direct_editing"}
    for section, selector in (
        ("Blocks", "editor_block_select"),
        ("Fields", "editor_field_select"),
        ("Snippets", "editor_snippet_select"),
    ):
        _go_to(app, section)
        assert {widget.key for widget in app.selectbox} == {selector}


def test_editor_unlocks_limited_forms_after_initial_snapshot(mutable_db):
    app = AppTest.from_file("pages/editor.py")
    app.run()
    app.checkbox(key="editor_initial_snapshot_ack").set_value(True).run()
    app.button(key="editor_enable_direct_editing").click().run()

    assert not app.exception
    expected = {
        "Presets": {"Save Preset wording", "Preview Preset changes"},
        "Blocks": {"Save Block wording", "Preview Block changes"},
        "Fields": {"Save Field wording", "Preview Field changes"},
        "Snippets": {"Save Snippet", "Preview Snippet changes", "Create Snippet"},
    }
    for section, button_labels in expected.items():
        _go_to(app, section)
        assert button_labels <= {widget.label for widget in app.button}


def test_new_snippet_form_resets_after_successful_creation(mutable_db):
    app = AppTest.from_file("pages/editor.py")
    app.run()
    app.checkbox(key="editor_initial_snapshot_ack").set_value(True).run()
    app.button(key="editor_enable_direct_editing").click().run()
    _go_to(app, "Snippets")

    app.text_input(key="editor_new_snippet_shortcut_0").set_value("ui_reset_probe")
    app.text_area(key="editor_new_snippet_expansion_0").set_value("Created from AppTest")
    app.text_input(key="editor_new_snippet_category_0").set_value("Test")
    next(button for button in app.button if button.label == "Create Snippet").click().run()

    assert not app.exception
    assert app.radio(key="editor_section").value == "Snippets"
    assert app.text_input(key="editor_new_snippet_shortcut_1").value == ""
    assert app.text_area(key="editor_new_snippet_expansion_1").value == ""
    assert app.text_input(key="editor_new_snippet_category_1").value == ""


def test_block_candidate_preview_renders_without_saving(mutable_db):
    appendix = next(block for block in db_module.get_all_editor_blocks() if block["key"] == "appendice")
    original = appendix["micro_template"]
    app = AppTest.from_file("pages/editor.py")
    app.run()
    app.checkbox(key="editor_initial_snapshot_ack").set_value(True).run()
    app.button(key="editor_enable_direct_editing").click().run()
    _go_to(app, "Blocks")
    app.selectbox(key="editor_block_select").set_value(appendix["id"]).run()

    microscopy = next(area for area in app.text_area if area.label == "Microscopy template")
    microscopy.set_value(original + " APPTEST-PREVIEW")
    next(button for button in app.button if button.label == "Preview Block changes").click().run()

    assert not app.exception
    assert any(header.value == "Affected default report previews" for header in app.subheader)
    stored = next(block for block in db_module.get_all_editor_blocks() if block["key"] == "appendice")
    assert stored["micro_template"] == original


def test_editor_section_and_block_selection_persist_across_reruns(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    gallbladder = next(block for block in db_module.get_all_editor_blocks() if block["key"] == "vesicule_biliaire")
    app = AppTest.from_file("pages/editor.py")
    app.run()
    _go_to(app, "Blocks")
    app.selectbox(key="editor_block_select").set_value(gallbladder["id"]).run()

    assert not app.exception
    assert app.radio(key="editor_section").value == "Blocks"
    assert app.selectbox(key="editor_block_select").value == gallbladder["id"]


def test_stale_form_save_stays_on_blocks_and_shows_reloaded_values(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    appendix = content_editing.get_editable_entity("Blocks", "appendice")
    app = AppTest.from_file("pages/editor.py")
    app.run()
    _go_to(app, "Blocks")
    appendix_id = next(block["id"] for block in db_module.get_all_editor_blocks() if block["key"] == "appendice")
    app.selectbox(key="editor_block_select").set_value(appendix_id).run()
    macro = next(area for area in app.text_area if area.label == "Macro template")
    macro.set_value(appendix["macro_template"] + " STALE-FORM")

    external = appendix["macro_template"] + " EXTERNAL-SAVE"
    content_editing.save_edit(
        "Blocks", "appendice", {"macro_template": external}, appendix["row_hash"],
    )
    next(button for button in app.button if button.label == "Save Block wording").click().run()

    assert not app.exception
    assert app.radio(key="editor_section").value == "Blocks"
    assert any("changed in another tab" in error.value for error in app.error)
    assert next(area for area in app.text_area if area.label == "Macro template").value == external
    assert content_editing.get_editable_entity("Blocks", "appendice")["macro_template"] == external


def test_revert_confirmation_and_action_stay_on_revision_section(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    preset = content_editing.get_editable_entity("Presets", "dai")
    revision_id = content_editing.save_edit(
        "Presets", "dai", {"category": "browser-review"}, preset["row_hash"],
    )["revision_id"]
    app = AppTest.from_file("pages/editor.py")
    app.run()
    _go_to(app, "Recent revisions")
    confirmation = app.checkbox(key=f"editor_revert_confirm_{revision_id}")
    confirmation.set_value(True).run()

    assert app.radio(key="editor_section").value == "Recent revisions"
    assert app.checkbox(key=f"editor_revert_confirm_{revision_id}").value is True
    revert = app.button(key=f"editor_revert_{revision_id}")
    assert not revert.disabled
    revert.click().run()
    assert app.radio(key="editor_section").value == "Recent revisions"
    assert content_editing.get_editable_entity("Presets", "dai")["category"] == preset["category"]
