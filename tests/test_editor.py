"""Regression coverage for the Stage 1 read-only Editor navigator."""

from streamlit.testing.v1 import AppTest

import database as db_module
import editor_preview
from golden_helpers import render_preset_defaults


def _preset(short_code):
    return next(preset for preset in db_module.get_all_presets() if preset["short_code"] == short_code)


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


def test_editor_has_no_write_controls_and_renders(db):
    app = AppTest.from_file("pages/editor.py")
    app.run()

    assert not app.exception
    assert {widget.key for widget in app.selectbox} == {
        "editor_preset_select", "editor_block_select", "editor_field_select", "editor_snippet_select",
    }
    assert not app.button
