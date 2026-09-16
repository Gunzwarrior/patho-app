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
        "26PR510001", gastric["id"], "",
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
    assert "editor_studio_field_select" in {widget.key for widget in app.selectbox}
    gate = app.button(key="editor_enable_direct_editing")
    assert gate.disabled
    assert all(widget.disabled for widget in app.button)
    for section, selector in (("Blocks", "editor_block_select"),):
        _go_to(app, section)
        assert {widget.key for widget in app.selectbox} == {selector}


def test_editor_unlocks_reviewed_content_studio_after_initial_snapshot(mutable_db):
    app = AppTest.from_file("pages/editor.py")
    app.run()
    app.checkbox(key="editor_initial_snapshot_ack").set_value(True).run()
    app.button(key="editor_enable_direct_editing").click().run()

    assert not app.exception
    assert "Prepare Field review" in {widget.label for widget in app.button}
    assert not {widget.label for widget in app.button} & {
        "Save Field wording", "Save Snippet", "Create Snippet",
        "Save Block wording", "Save Preset wording",
    }
    _go_to(app, "Blocks")
    assert not {widget.label for widget in app.button} & {"Save Block wording", "Preview Block changes"}


def test_new_snippet_form_is_available_only_through_content_studio(mutable_db):
    app = AppTest.from_file("pages/editor.py")
    app.run()
    app.checkbox(key="editor_initial_snapshot_ack").set_value(True).run()
    app.button(key="editor_enable_direct_editing").click().run()
    app.radio(key="editor_studio_kind").set_value("Snippets").run()
    app.checkbox(key="editor_studio_snippet_create").set_value(True).run()
    assert "Prepare Snippet review" in {widget.label for widget in app.button}
    assert "Create Snippet" not in {widget.label for widget in app.button}


def test_checkpoint_five_enables_non_table_block_studio_but_keeps_tables_and_presets_read_only(mutable_db):
    # Seed data currently has no table Block, so add one solely to preserve
    # the CP5 boundary in the browser-facing regression test.
    conn = db_module.get_db_connection()
    try:
        conn.execute("""INSERT INTO Blocks(key,name,is_table,micro_template,conclusion_template)
                        VALUES ('cp5_table_block','CP5 table Block',1,'Table micro.','Table conclusion.')""")
        table_id = conn.execute("SELECT id FROM Blocks WHERE key='cp5_table_block'").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    app = AppTest.from_file("pages/editor.py")
    app.run()
    app.checkbox(key="editor_initial_snapshot_ack").set_value(True).run()
    app.button(key="editor_enable_direct_editing").click().run()

    assert not app.exception
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    assert "Prepare Block review" in {widget.label for widget in app.button}

    # Table Blocks remain visible but have no CP5 authoring/lifecycle action.
    app.selectbox(key="editor_studio_block_select").set_value(table_id).run()
    assert any("Table Blocks are read-only" in item.value for item in app.info)
    assert "Prepare Block review" not in {widget.label for widget in app.button}

    _go_to(app, "Presets")
    assert any("Checkpoint 6" in item.value for item in app.info)
    assert not {widget.label for widget in app.button} & {"Save Preset wording", "Preview Preset changes"}


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


def test_legacy_revision_uses_reviewed_inverse_and_stays_on_revision_section(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    preset = content_editing.get_editable_entity("Presets", "dai")
    revision_id = content_editing.save_edit(
        "Presets", "dai", {"category": "browser-review"}, preset["row_hash"],
    )["revision_id"]
    app = AppTest.from_file("pages/editor.py")
    app.run()
    _go_to(app, "Recent revisions")
    assert "Revert safely" not in {button.label for button in app.button}
    prepare = next(button for button in app.button if button.label == "Prepare inverse review")
    prepare.click().run()
    confirmation = next(box for box in app.checkbox
                        if box.label == "I confirm this exact reviewed candidate and its local pending-Case impact")
    confirmation.set_value(True).run()

    assert app.radio(key="editor_section").value == "Recent revisions"
    revert = next(button for button in app.button if button.label == "Apply reviewed inverse")
    assert not revert.disabled
    revert.click().run()
    assert app.radio(key="editor_section").value == "Recent revisions"
    assert content_editing.get_editable_entity("Presets", "dai")["category"] == preset["category"]
