"""Stage 7 CP3: guided consistency-rule authoring and warning-only impact."""

import copy
import json
import sqlite3

from streamlit.testing.v1 import AppTest

import content_changes
import content_editing
import content_snapshot
import content_studio
import database


def _hash(path):
    return content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(path))


def _rules(path, block="appendice"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [{"field_a_key": row["field_a_key"], "field_a_values": json.loads(row["field_a_values"]),
                 "field_b_key": row["field_b_key"], "field_b_values": json.loads(row["field_b_values"]),
                 "message": row["message"]}
                for row in conn.execute("""SELECT r.* FROM Field_Consistency_Rules r JOIN Blocks b ON b.id=r.block_id
                                           WHERE b.key=? ORDER BY r.id""", (block,))]
    finally:
        conn.close()


def _save_triggering_pending(path):
    """A normal materialized pending draft, not a special CP3 Case shape."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        preset_id = conn.execute("SELECT id FROM Presets WHERE short_code='dai'").fetchone()["id"]
    finally:
        conn.close()
    structured = {"blocks": {"appendice#0": {
        "appendicite_type": "phlegmoneuse", "false_membranes": True,
    }}}
    assert database.save_case("26PR550003", preset_id, "", structured, "<p>saved</p>")


def _block_duplicate_source(path, key="appendice"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        block = dict(conn.execute("SELECT * FROM Blocks WHERE key=?", (key,)).fetchone())
        bindings = [dict(row) for row in conn.execute(
            """SELECT f.id AS field_id,f.key AS field_key,bf.label_override,bf.default_override,bf.context_section
               FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id
               WHERE bf.block_id=? ORDER BY bf.sort_order""", (block["id"],)
        )]
    finally:
        conn.close()
    draft = {name: block[name] for name in (
        "name", "site_label", "conclusion_group", "macro_template", "micro_template",
        "conclusion_template", "context_template", "title_fragment_template", "conclusion_label_template",
    )}
    for binding in bindings:
        binding["context_section"] = bool(binding["context_section"])
    return block, draft, bindings


def test_rule_review_freezes_matching_probes_and_warning_only_pending_delta(mutable_db):
    _save_triggering_pending(mutable_db)
    before_rules = _rules(mutable_db)
    conn = sqlite3.connect(mutable_db)
    conn.row_factory = sqlite3.Row
    try:
        before_case = dict(conn.execute("SELECT * FROM Cases WHERE case_number='26PR550003'").fetchone())
    finally:
        conn.close()
    added = {"field_a_key": "false_membranes", "field_a_values": [True],
             "field_b_key": "appendicite_type", "field_b_values": ["phlegmoneuse"],
             "message": "CP3 warning: verify phlegmonous false membranes."}
    baseline = content_studio.configuration_draft_baseline("consistency", "appendice", db_name=mutable_db)
    intents = content_studio.consistency_rule_draft_operations(
        "appendice", before_rules + [added], baseline=baseline, db_name=mutable_db,
    )
    review = content_studio.review(
        intents, _hash(mutable_db), db_name=mutable_db,
        evidence_factory=content_studio.consistency_rule_review_evidence("appendice"),
    )

    assert _rules(mutable_db) == before_rules  # Prepare and candidate probes never write live content.
    evidence = review.data["guided_evidence"]
    assert evidence["kind"] == "consistency"
    added_probe = next(item for item in evidence["blocks"] if item["rule"]["message"] == added["message"])
    assert added_probe["matching"]["values"] == {
        "appendicite_type": "phlegmoneuse", "false_membranes": True,
    }
    assert added["message"] in added_probe["matching"]["warnings"]
    assert added_probe["nonmatching"]["available"]
    assert added["message"] not in added_probe["nonmatching"]["warnings"]

    impact = review.data["consistency_warning_impact"]
    pending = next(item for item in impact["pending_cases"] if item["case_number"] == "26PR550003")
    assert pending["warning_changed"] is True
    assert pending["rendering_changed"] is False
    assert pending["fingerprint_changed"] is False
    assert added["message"] in pending["after"] and added["message"] not in pending["before"]
    assert review.data["pending_cases"] == []  # Fingerprint-oriented impact remains separate.

    content_editing.record_initial_snapshot("c" * 64)
    revision = content_changes.apply_review(review, db_name=mutable_db)
    conn = sqlite3.connect(mutable_db)
    conn.row_factory = sqlite3.Row
    try:
        after_case = dict(conn.execute("SELECT * FROM Cases WHERE case_number='26PR550003'").fetchone())
    finally:
        conn.close()
    assert after_case == before_case
    assert database.compute_case_content_fingerprint(after_case["preset_id"], json.loads(after_case["structured_input"])) == after_case["content_fingerprint"]

    inverse = content_changes.review_inverse(revision, db_name=mutable_db)
    assert inverse.data["guided_evidence"]["kind"] == "consistency"
    reversed_pending = next(item for item in inverse.data["consistency_warning_impact"]["pending_cases"]
                            if item["case_number"] == "26PR550003")
    assert reversed_pending["warning_changed"] is True
    assert reversed_pending["fingerprint_changed"] is False


def test_typed_rule_crud_canonicalizes_every_field_widget_type(mutable_db):
    """CP3's planner accepts the same typed values exposed by its widgets."""
    conn = sqlite3.connect(mutable_db)
    try:
        for key, field_type, options, default in (
            ("cp3_select", "select", '["Option A","Option B","Option C"]', "Option A"),
            ("cp3_checkbox", "checkbox", None, "0"),
            ("cp3_number", "number", None, "0"),
            ("cp3_decimal", "decimal", None, "0"),
            ("cp3_text", "text", None, ""),
        ):
            conn.execute("INSERT INTO Fields(key,label,type,options,default_value) VALUES (?,?,?,?,?)",
                         (key, key, field_type, options, default))
        block_id = conn.execute(
            """INSERT INTO Blocks(key,name,is_table,macro_template,micro_template,conclusion_template)
               VALUES ('cp3_typed_rules','CP3 typed rules',0,'Macro.','Micro.','Conclusion.')
               RETURNING id"""
        ).fetchone()[0]
        for position, key in enumerate(("cp3_select", "cp3_checkbox", "cp3_number", "cp3_decimal", "cp3_text")):
            conn.execute("INSERT INTO Block_Fields(block_id,field_id,sort_order) VALUES (?,(SELECT id FROM Fields WHERE key=?),?)",
                         (block_id, key, position))
        conn.commit()
    finally:
        conn.close()


    rules = [
        {"field_a_key": "cp3_select", "field_a_values": ["Option C", "Option A"],
         "field_b_key": "cp3_checkbox", "field_b_values": [True], "message": "select"},
        {"field_a_key": "cp3_number", "field_a_values": [7, 2],
         "field_b_key": "cp3_checkbox", "field_b_values": [False], "message": "number"},
        {"field_a_key": "cp3_decimal", "field_a_values": [3.0, 1.5],
         "field_b_key": "cp3_checkbox", "field_b_values": [True], "message": "decimal"},
        {"field_a_key": "cp3_text", "field_a_values": ["second", "first"],
         "field_b_key": "cp3_checkbox", "field_b_values": [False], "message": "text"},
    ]
    create = content_studio.consistency_rule_draft_operations("cp3_typed_rules", rules, db_name=mutable_db)
    content_editing.record_initial_snapshot("c" * 64)
    first = content_changes.apply_review(
        content_studio.review(create, _hash(mutable_db), db_name=mutable_db), db_name=mutable_db,
    )
    conn = sqlite3.connect(mutable_db)
    try:
        values = [value for row in conn.execute(
            "SELECT field_a_values,field_b_values FROM Field_Consistency_Rules WHERE block_id=? ORDER BY id", (block_id,)
        ) for value in row]
    finally:
        conn.close()
    assert '[1.5,3]' in values and '[2,7]' in values and '["first","second"]' in values

    edited = [dict(rule) for rule in rules]
    edited[0]["message"] = "select edited"
    second = content_changes.apply_review(content_studio.review(
        content_studio.consistency_rule_draft_operations("cp3_typed_rules", edited, db_name=mutable_db),
        _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    third = content_changes.apply_review(content_studio.review(
        content_studio.consistency_rule_draft_operations("cp3_typed_rules", [], db_name=mutable_db),
        _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    assert third > second > first
    conn = sqlite3.connect(mutable_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM Field_Consistency_Rules WHERE block_id=?", (block_id,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_block_duplicate_with_rules_inverse_and_reinverse_remain_reviewable(mutable_db):
    """Copied rules do not make a deleted duplicate into an inverse probe owner."""
    content_editing.record_initial_snapshot("c" * 64)
    source, draft, bindings = _block_duplicate_source(mutable_db)
    original_rules = _rules(mutable_db)
    draft["name"] = "CP3 duplicate"
    forward = content_studio.review(content_studio.block_draft_operations(
        "duplicate", "cp3_appendice_duplicate", draft, bindings, source_key="appendice",
        baseline=content_studio.block_draft_baseline(source, bindings), db_name=mutable_db,
    ), _hash(mutable_db), db_name=mutable_db)
    first = content_changes.apply_review(forward, db_name=mutable_db)
    assert _rules(mutable_db, "cp3_appendice_duplicate") == original_rules

    undo_review = content_changes.review_inverse(first, db_name=mutable_db)
    assert undo_review.data["guided_evidence"]["kind"] == "consistency"
    assert undo_review.data["guided_evidence"]["blocks"] == []
    undo = content_changes.apply_review(undo_review, db_name=mutable_db)
    assert _rules(mutable_db, "cp3_appendice_duplicate") == []

    redo_review = content_changes.review_inverse(undo, db_name=mutable_db)
    assert redo_review.data["guided_evidence"]["kind"] == "consistency"
    assert redo_review.data["guided_evidence"]["blocks"]
    redo = content_changes.apply_review(redo_review, db_name=mutable_db)
    assert redo > undo > first
    assert _rules(mutable_db, "cp3_appendice_duplicate") == original_rules


def test_rule_only_default_and_pending_changes_are_not_generic_report_output(mutable_db):
    """Warnings affect review state, not clinical output/fingerprints."""
    conn = sqlite3.connect(mutable_db)
    try:
        preset_id = conn.execute("SELECT id FROM Presets WHERE short_code='dai'").fetchone()[0]
    finally:
        conn.close()
    assert database.save_case("26PR550001", preset_id, "", {}, "<p>saved</p>")
    added = {"field_a_key": "appendix_size_cm", "field_a_values": [8],
             "field_b_key": "appendicite_type", "field_b_values": ["endo"],
             "message": "CP3 default warning."}
    review = content_studio.review(content_studio.consistency_rule_draft_operations(
        "appendice", _rules(mutable_db) + [added], db_name=mutable_db,
    ), _hash(mutable_db), db_name=mutable_db)
    preset = next(item for item in review.data["presets"] if item["code"] == "dai")
    assert preset["affected"] is True and preset["output_changed"] is False
    default = next(item for item in review.data["consistency_warning_impact"]["default_presets"]
                   if item["preset_code"] == "dai")
    pending = next(item for item in review.data["consistency_warning_impact"]["pending_cases"]
                   if item["case_number"] == "26PR550001")
    assert default["warning_changed"] is True and default["rendering_changed"] is False
    assert pending["warning_changed"] is True and pending["rendering_changed"] is False
    assert pending["fingerprint_changed"] is False and review.data["pending_cases"] == []

    content_editing.record_initial_snapshot("c" * 64)
    revision = content_changes.apply_review(review, db_name=mutable_db)
    inverse = content_changes.review_inverse(revision, db_name=mutable_db)
    inverse_preset = next(item for item in inverse.data["presets"] if item["code"] == "dai")
    assert inverse_preset["affected"] is True and inverse_preset["output_changed"] is False
    inverse_pending = next(item for item in inverse.data["consistency_warning_impact"]["pending_cases"]
                           if item["case_number"] == "26PR550001")
    assert inverse_pending["warning_changed"] is True and inverse_pending["rendering_changed"] is False


def test_warning_impact_is_order_insensitive_and_multiplicity_safe():
    """Rule retrieval order is not warning semantics; duplicate messages still count."""
    def record(warnings, *, fingerprint="same"):
        return {"case_number": "26PR550002", "fingerprint": fingerprint,
                "report": {"warnings": warnings, "html": "same", "micro_plain": "same"}}

    before_presets = {"p": record(["Order A", "Order B"])}
    after_presets = {"p": record(["Order B", "Order A"])}
    before_pending = {7: record(["Order A", "Order B"])}
    after_pending = {7: record(["Order B", "Order A"])}
    impact = content_changes._consistency_warning_impact(
        before_presets, after_presets, before_pending, after_pending,
        [{"table": "Field_Consistency_Rules"}],
    )
    assert impact["default_presets"][0]["warning_changed"] is False
    assert impact["pending_cases"][0]["warning_changed"] is False
    assert impact["default_presets"][0]["before"] == ["Order A", "Order B"]
    duplicated = content_changes._consistency_warning_impact(
        {"p": record(["Order A"])}, {"p": record(["Order A", "Order A"])}, {}, {},
        [{"table": "Field_Consistency_Rules"}],
    )
    assert duplicated["default_presets"][0]["warning_changed"] is True

def test_consistency_rule_studio_uses_source_bound_rule_widget_ids_and_no_direct_writer(mutable_db):
    content_editing.record_initial_snapshot("c" * 64)
    before = _rules(mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        rule_id = conn.execute("SELECT id FROM Field_Consistency_Rules WHERE block_id=(SELECT id FROM Blocks WHERE key='appendice')").fetchone()[0]
    finally:
        conn.close()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Consistency rules").run()
    app.selectbox(key="editor_consistency_block").set_value("appendice").run()
    assert not app.exception
    scope = "editor_consistency_appendice_0"
    assert app.text_area(key=f"{scope}_rule_saved-{rule_id}_message")
    app.button(key=f"{scope}_add").click().run()
    assert not app.exception
    assert _rules(mutable_db) == before
    source = open("pages/editor.py", encoding="utf-8").read()
    assert "consistency_rule_draft_operations" in source
    assert "INSERT INTO Field_Consistency_Rules" not in source


def test_consistency_rule_studio_prepares_a_frozen_candidate_without_a_live_write(mutable_db):
    content_editing.record_initial_snapshot("c" * 64)
    before = _rules(mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        rule_id = conn.execute("SELECT id FROM Field_Consistency_Rules WHERE block_id=(SELECT id FROM Blocks WHERE key='appendice')").fetchone()[0]
    finally:
        conn.close()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Consistency rules").run()
    app.selectbox(key="editor_consistency_block").set_value("appendice").run()
    scope = "editor_consistency_appendice_0"
    app.text_area(key=f"{scope}_rule_saved-{rule_id}_message").set_value("CP3 edited warning.").run()
    app.button(key=f"{scope}_prepare").click().run()
    assert not app.exception
    assert "_editor_studio_review" in app.session_state.filtered_state
    assert _rules(mutable_db) == before
    assert any(item.value == "Frozen consistency-rule probes" for item in app.subheader)
    assert any(item.value == "Consistency warning impact" for item in app.subheader)


def test_consistency_rule_stale_source_preserves_draft_and_explains_deliberate_reload(mutable_db):
    """A second-tab Block Apply must not silently remap CP3's old endpoints."""
    content_editing.record_initial_snapshot("c" * 64)
    conn = sqlite3.connect(mutable_db)
    try:
        rule_id = conn.execute("SELECT id FROM Field_Consistency_Rules WHERE block_id=(SELECT id FROM Blocks WHERE key='appendice')").fetchone()[0]
        original_micro = conn.execute("SELECT micro_template FROM Blocks WHERE key='appendice'").fetchone()[0]
    finally:
        conn.close()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Consistency rules").run()
    app.selectbox(key="editor_consistency_block").set_value("appendice").run()
    scope, draft_key = "editor_consistency_appendice_0", "_editor_consistency_draft_appendice_0"
    message_key = f"{scope}_rule_saved-{rule_id}_message"
    app.text_area(key=message_key).set_value("Unsaved CP3 stale draft.").run()
    preserved = copy.deepcopy(app.session_state.filtered_state[draft_key])

    # Tab B's ordinary reviewed Content Studio write changes the owner image.
    review = content_studio.review(
        [content_studio.operation("update", "Blocks", "appendice", {
            "micro_template": original_micro + "\n",
        })],
        _hash(mutable_db), db_name=mutable_db,
    )
    content_changes.apply_review(review, db_name=mutable_db)

    app.button(key=f"{scope}_prepare").click().run()
    assert not app.exception
    assert "_editor_studio_review" not in app.session_state.filtered_state
    assert app.session_state.filtered_state[draft_key] == preserved
    assert any("draft is stale" in item.value.lower() and "preserved" in item.value.lower()
               for item in app.error)

    app.button(key=f"{scope}_reload_stale").click().run()
    assert not app.exception
    assert app.session_state.filtered_state["_editor_studio_form_generation"] == 1
    reloaded_key = "_editor_consistency_draft_appendice_1"
    assert app.session_state.filtered_state[reloaded_key][0]["message"] != "Unsaved CP3 stale draft."


def test_consistency_rule_review_time_stale_race_uses_preserved_draft_flow(mutable_db, monkeypatch):
    """A write after CP3's local precheck cannot trigger the generic auto-reset."""
    content_editing.record_initial_snapshot("c" * 64)
    conn = sqlite3.connect(mutable_db)
    try:
        rule_id = conn.execute("SELECT id FROM Field_Consistency_Rules WHERE block_id=(SELECT id FROM Blocks WHERE key='appendice')").fetchone()[0]
        original_micro = conn.execute("SELECT micro_template FROM Blocks WHERE key='appendice'").fetchone()[0]
    finally:
        conn.close()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Consistency rules").run()
    app.selectbox(key="editor_consistency_block").set_value("appendice").run()
    scope, draft_key = "editor_consistency_appendice_0", "_editor_consistency_draft_appendice_0"
    app.text_area(key=f"{scope}_rule_saved-{rule_id}_message").set_value("Race-preserved draft.").run()
    preserved = copy.deepcopy(app.session_state.filtered_state[draft_key])
    original_review = content_studio.review

    def race_review(*args, **kwargs):
        writer = sqlite3.connect(mutable_db)
        try:
            writer.execute("UPDATE Blocks SET micro_template=? WHERE key='appendice'", (original_micro + "\n",))
            writer.commit()
        finally:
            writer.close()
        return original_review(*args, **kwargs)

    monkeypatch.setattr(content_studio, "review", race_review)
    app.button(key=f"{scope}_prepare").click().run()
    assert not app.exception
    assert app.session_state.filtered_state.get("_editor_studio_form_generation", 0) == 0
    assert app.session_state.filtered_state[draft_key] == preserved
    assert "_editor_studio_review" not in app.session_state.filtered_state
    assert any("draft is stale" in item.value.lower() and "preserved" in item.value.lower()
               for item in app.error)
