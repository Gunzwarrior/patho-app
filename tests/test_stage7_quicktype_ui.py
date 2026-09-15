"""CP2 focused checks: guided evidence is candidate-only and frozen."""

import sqlite3

import pytest

import content_changes
import content_snapshot
import content_studio
import database
import content_editing
import quicktype
from streamlit.testing.v1 import AppTest


def _qt_scope(code="dai", generation=0):
    return f"editor_qt_{code}_{generation}"


def _qt_token(uid, code="dai", generation=0):
    return f"{_qt_scope(code, generation)}_token_{uid}"


def _mapping_values(row):
    return [(mapping["key"], mapping["value"]) for mapping in row["mappings"]]


def _tokens(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        preset = conn.execute("SELECT id FROM Presets WHERE short_code='dai'").fetchone()
        return database.get_quick_type_tokens_on_connection(conn, preset["id"])
    finally:
        conn.close()


def test_quick_type_evidence_is_frozen_candidate_rendering_without_a_live_write(mutable_db):
    before = _tokens(mutable_db)
    desired = [dict(row) for row in before]
    desired[0]["lookup_table"] = {"x": "periappendicite"}
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai", db_name=mutable_db)
    intents = content_studio.quick_type_draft_operations("dai", desired, baseline=baseline, db_name=mutable_db)
    snapshot = content_snapshot.export_content_snapshot(db_name=mutable_db)
    examples = content_studio.quick_type_generated_examples(
        "dai", desired, database.get_all_presets(),
    )
    review = content_studio.review(intents, content_snapshot.content_snapshot_hash(snapshot), db_name=mutable_db,
                                   evidence_factory=content_studio.quick_type_review_evidence("dai", examples))
    assert _tokens(mutable_db) == before  # Prepare/evidence has no writer.
    evidence = review.data["guided_evidence"]
    assert evidence["kind"] == "quick_type"
    assert any(row["code"] == "daix" and row["error"] is None for row in evidence["examples"])
    decoded = next(row["decoded"] for row in evidence["examples"] if row["code"] == "daix")
    assert decoded[0]["instance_no"] == 0
    assert decoded[0]["field_key"] == "appendicite_type"


def test_generated_examples_cover_required_boundary_classes():
    tokens = [
        {"block_sort_order": 5, "field_key": "kind", "token_kind": "lookup", "lookup_table": {"a": "A"}, "digit_width": None},
        {"block_sort_order": 9, "field_key": "size", "token_kind": "measurement", "lookup_table": None, "digit_width": 2},
    ]
    rows = content_studio.quick_type_generated_examples("p", tokens, [{"id": 1, "short_code": "p"}, {"id": 2, "short_code": "pa"}])
    labels = {row["label"] for row in rows}
    assert {"Bare code", "Block rollover", "Skip control !", "Reserved !", "Leftover", "Prefix routing"} <= labels
    assert any("boundary" in label for label in labels)
    assert any("excess width" in label for label in labels)
    assert next(row for row in rows if row["label"] == "Prefix routing")["expected_preset"] == "pa"


def test_generated_skip_uses_token_block_sequence_not_sorted_instance_identity():
    tokens = [
        {"block_sort_order": 9, "field_key": "first", "token_kind": "lookup",
         "lookup_table": {"x": "X"}, "digit_width": None},
        {"block_sort_order": 5, "field_key": "second", "token_kind": "lookup",
         "lookup_table": {"y": "Y"}, "digit_width": None},
    ]
    rows = content_studio.quick_type_generated_examples(
        "p", tokens, [{"id": 1, "short_code": "p"}],
    )
    skip = next(row for row in rows if row["label"] == "Skip control !")
    values, error = quicktype.parse_tokens(skip["code"][1:], tokens)
    assert error is None
    assert values == {5: {"second": "Y"}}
    terminal = next(row for row in rows if row["label"] == "Reserved !")
    _, error = quicktype.parse_tokens(terminal["code"][1:], tokens)
    assert error is not None


def test_candidate_evidence_refuses_an_example_expectation_mismatch(mutable_db):
    desired = _tokens(mutable_db)
    desired[0] = {**desired[0], "lookup_table": {"x": "periappendicite"}}
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai", db_name=mutable_db)
    intents = content_studio.quick_type_draft_operations(
        "dai", desired, baseline=baseline, db_name=mutable_db,
    )
    snapshot = content_snapshot.export_content_snapshot(db_name=mutable_db)
    evidence = content_studio.quick_type_review_evidence("dai", [{
        "label": "Contradictory generated probe", "code": "dai?", "positive": True,
        "expected_preset": "dai",
    }])
    with pytest.raises(content_changes.ChangeError, match="expected successful parsing"):
        content_studio.review(
            intents, content_snapshot.content_snapshot_hash(snapshot), db_name=mutable_db,
            evidence_factory=evidence,
        )


def test_generated_examples_tolerate_an_empty_lookup_draft_row():
    rows = content_studio.quick_type_generated_examples("dai", [
        {"sort_order": 0, "block_sort_order": 0, "field_key": "size", "token_kind": "lookup",
         "lookup_table": {}, "digit_width": None},
    ], [{"id": 1, "short_code": "dai"}])
    assert rows[0]["label"] == "Bare code"
    assert any(row["label"] == "Leftover" for row in rows)


def test_quick_type_studio_is_draft_only_until_prepare_and_apply(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    before = _tokens(mutable_db)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    assert not app.exception
    assert "Add Quick Type token" in {button.label for button in app.button}
    next(button for button in app.button if button.label == "Add Quick Type token").click().run()
    assert not app.exception
    assert _tokens(mutable_db) == before
    # The sole persistence action remains the shared frozen-review Apply;
    # adding/reordering/deleting has no direct UI writer.
    source = open("pages/editor.py", encoding="utf-8").read()
    assert "quick_type_draft_operations" in source
    assert "INSERT INTO Quick_Type_Tokens" not in source


def test_quick_type_ui_normalizes_positions_and_shows_complete_frozen_grammar(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    before = _tokens(mutable_db)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()

    # Switching the existing decimal token to lookup is an intentionally
    # incomplete intermediate state, not a page error or a persistence event.
    app.radio(key=f"{_qt_token('saved-1')}_kind").set_value("lookup").run()
    assert not app.exception
    next(button for button in app.button if button.label == "Prepare Quick Type review").click().run()
    assert any("empty lookup_table" in element.value for element in app.error)

    # Return to the valid grammar, append, target a distinct Field, and prove
    # the UI's complete ordered representation reaches frozen review.
    app.radio(key=f"{_qt_token('saved-1')}_kind").set_value("measurement").run()
    next(button for button in app.button if button.label == "Add Quick Type token").click().run()
    app.selectbox(key=f"{_qt_token('new-2')}_field").set_value("false_membranes").run()
    next(button for button in app.button if button.label == "Prepare Quick Type review").click().run()
    assert not app.exception
    assert "_editor_studio_review" in app.session_state.filtered_state
    assert _tokens(mutable_db) == before  # review remains no-write
    assert any(item.value == "Complete configuration" for item in app.subheader)
    complete_images = [item.value for item in app.code if "appendicite_type" in item.value]
    assert complete_images
    assert any("appendix_size_cm" in value and '"digit_width"' in value
               for value in complete_images)
    assert not any("assert_configuration_draft" in (item.label or "")
                   for item in app.expander)


def test_quick_type_ui_repeated_add_remove_reorder_keeps_a_draft_only_list(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    before = _tokens(mutable_db)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    app.button(key=f"{_qt_scope()}_add").click().run()
    app.button(key=f"{_qt_scope()}_add").click().run()
    app.button(key=f"{_qt_token('saved-0')}_down").click().run()
    app.button(key=f"{_qt_token('new-3')}_delete").click().run()
    app.button(key=f"{_qt_scope()}_add").click().run()
    assert not app.exception
    assert _tokens(mutable_db) == before


def test_remove_mapping_mutates_the_draft_on_one_click(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    before = list(_tokens(mutable_db)[0]["lookup_table"].items())
    app.button(key=f"{_qt_token('saved-0')}_map_saved-0_remove").click().run()
    assert not app.exception
    draft = app.session_state.filtered_state["_editor_qt_draft_dai_0"]
    assert _mapping_values(draft[0]) == before[1:]


def test_remove_middle_mapping_preserves_exact_neighbors_and_values(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    before = list(_tokens(mutable_db)[0]["lookup_table"].items())
    app.button(key=f"{_qt_token('saved-0')}_map_saved-2_remove").click().run()
    assert not app.exception
    draft = app.session_state.filtered_state["_editor_qt_draft_dai_0"]
    assert _mapping_values(draft[0]) == before[:2] + before[3:]


def test_duplicate_nonblank_mapping_keys_are_rejected_before_row_collapse(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    before = _tokens(mutable_db)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    first = f"{_qt_token('saved-0')}_map_saved-0"
    second = f"{_qt_token('saved-0')}_map_saved-1"
    first_key = app.text_input(key=f"{first}_key").value
    first_value = app.selectbox(key=f"{first}_value_select").value
    second_value = app.selectbox(key=f"{second}_value_select").value

    app.text_input(key=f"{second}_key").set_value(first_key).run()
    app.button(key=f"{_qt_scope()}_prepare").click().run()

    assert not app.exception
    assert "_editor_studio_review" not in app.session_state.filtered_state
    assert any("duplicate lookup key" in item.value.lower() for item in app.error)
    draft = app.session_state.filtered_state["_editor_qt_draft_dai_0"]
    assert _mapping_values(draft[0])[:2] == [
        (first_key, first_value), (first_key, second_value),
    ]
    assert _tokens(mutable_db) == before


def test_independent_test_panel_interprets_an_unchanged_draft_without_prepare(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    before = _tokens(mutable_db)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()

    app.text_input(key=f"{_qt_scope()}_test_code").set_value("dai3").run()
    app.button(key=f"{_qt_scope()}_test").click().run()

    assert not app.exception
    assert "_editor_studio_review" not in app.session_state.filtered_state
    assert any(item.value == "Draft test result" for item in app.subheader)
    assert app.dataframe
    decoded = app.dataframe[0].value
    assert decoded.iloc[0]["field_key"] == "appendicite_type"
    assert decoded.iloc[0]["value"] == "periappendicite"
    assert any("Production-path report" in item.value for item in app.markdown)

    # The same independent path consumes an unsaved semantic edit rather than
    # silently falling back to the authoritative grammar.
    third = f"{_qt_token('saved-0')}_map_saved-2_value_select"
    app.selectbox(key=third).set_value("phlegmoneuse").run()
    assert not any(item.value == "Draft test result" for item in app.subheader)
    app.button(key=f"{_qt_scope()}_test").click().run()
    assert app.dataframe[0].value.iloc[0]["value"] == "phlegmoneuse"
    assert _tokens(mutable_db) == before


def test_preset_scoped_widgets_cannot_rebind_another_preset_draft(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    vb = next(preset for preset in database.get_all_presets() if preset["short_code"] == "vb")
    block = database.get_preset_blocks(vb["id"])[0]
    desired = [{
        "sort_order": 0, "block_sort_order": block["sort_order"],
        "field_key": "specimen_size_cm", "token_kind": "lookup",
        "lookup_table": {"z": 1.5}, "digit_width": None,
    }]
    baseline = content_studio.configuration_draft_baseline("quick_type", "vb")
    intents = content_studio.quick_type_draft_operations("vb", desired, baseline=baseline)
    snapshot = content_snapshot.export_content_snapshot()
    content_changes.apply_review(content_studio.review(
        intents, content_snapshot.content_snapshot_hash(snapshot), summary="Configure vb probe",
    ))

    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    dai_key = f"{_qt_token('saved-0')}_map_saved-0_key"
    app.text_input(key=dai_key).set_value("q").run()
    app.selectbox(key="editor_quick_type_preset").set_value("vb").run()
    vb_draft = app.session_state.filtered_state["_editor_qt_draft_vb_0"]
    assert _mapping_values(vb_draft[0]) == [("z", 1.5)]
    app.selectbox(key="editor_quick_type_preset").set_value("dai").run()
    assert _mapping_values(app.session_state.filtered_state["_editor_qt_draft_dai_0"][0])[0][0] == "q"


def test_source_change_while_rendered_retires_all_old_quick_type_widgets(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    app.text_input(key=f"{_qt_token('saved-0')}_map_saved-0_key").set_value("q").run()

    desired = _tokens(mutable_db)
    desired[0]["lookup_table"] = {"x": "periappendicite"}
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai")
    intents = content_studio.quick_type_draft_operations("dai", desired, baseline=baseline)
    snapshot = content_snapshot.export_content_snapshot()
    content_changes.apply_review(content_studio.review(
        intents, content_snapshot.content_snapshot_hash(snapshot), summary="External Quick Type edit",
    ))

    app.run()
    assert not app.exception
    assert app.session_state["_editor_studio_form_generation"] == 1
    reloaded = app.session_state.filtered_state["_editor_qt_draft_dai_1"]
    assert _mapping_values(reloaded[0]) == [("x", "periappendicite")]
    old_prefix = _qt_scope(generation=0)
    assert not any(widget.key and widget.key.startswith(old_prefix)
                   for widget in [*app.text_input, *app.selectbox, *app.radio])


def test_quick_type_draft_survives_navigation_but_reloads_after_source_change(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    app.button(key=f"{_qt_token('saved-0')}_map_saved-0_remove").click().run()
    app.radio(key="editor_studio_kind").set_value("Presets").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    assert len(app.session_state.filtered_state["_editor_qt_draft_dai_0"][0]["mappings"]) == 5

    desired = _tokens(mutable_db)
    desired[0]["lookup_table"] = {"x": "periappendicite"}
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai")
    intents = content_studio.quick_type_draft_operations("dai", desired, baseline=baseline)
    snapshot = content_snapshot.export_content_snapshot()
    review = content_studio.review(intents, content_snapshot.content_snapshot_hash(snapshot), summary="external Quick Type edit")
    content_changes.apply_review(review)

    app.radio(key="editor_section").set_value("Recent revisions").run()
    app.radio(key="editor_section").set_value("Content Studio").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    assert not app.exception
    assert app.session_state["_editor_studio_form_generation"] == 1
    reloaded = app.session_state.filtered_state["_editor_qt_draft_dai_1"]
    assert _mapping_values(reloaded[0]) == [("x", "periappendicite")]
    # The reloaded draft is current and can enter the normal Prepare path.
    app.button(key=f"{_qt_scope(generation=1)}_add").click().run()
    assert not app.exception


def test_frozen_interpretations_render_before_confirmation_and_apply(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Quick Type").run()
    app.text_input(key=f"{_qt_token('saved-0')}_map_saved-0_key").set_value("x").run()
    app.button(key=f"{_qt_scope()}_prepare").click().run()
    assert not app.exception

    children = list(app._tree.main.children.values())
    evidence_at = next(index for index, item in enumerate(children)
                       if getattr(item, "value", None) == "Frozen Quick Type interpretations")
    confirm_at = next(index for index, item in enumerate(children)
                      if getattr(item, "label", "").startswith("I confirm this exact reviewed candidate"))
    apply_at = next(index for index, item in enumerate(children)
                    if getattr(item, "label", "") == "Apply reviewed Content Studio change")
    assert evidence_at < confirm_at < apply_at


def test_inverse_and_reinverse_reviews_freeze_complete_quick_type_evidence(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    desired = _tokens(mutable_db)
    desired[0]["lookup_table"] = {"x": "periappendicite"}
    operations = content_studio.quick_type_draft_operations(
        "dai", desired,
        baseline=content_studio.configuration_draft_baseline(
            "quick_type", "dai", db_name=mutable_db,
        ),
        db_name=mutable_db,
    )
    revision = content_changes.apply_review(content_studio.review(
        operations,
        content_snapshot.content_snapshot_hash(
            content_snapshot.export_content_snapshot(mutable_db)
        ),
        db_name=mutable_db,
    ), db_name=mutable_db)

    inverse = content_changes.review_inverse(revision, db_name=mutable_db)
    inverse_configuration = inverse.data["configuration_review"]
    assert inverse_configuration["before"][0]["owner_key"] == "dai"
    assert inverse_configuration["before"][0]["configuration"][0]["lookup_table"] == {
        "x": "periappendicite",
    }
    assert "3" in inverse_configuration["after"][0]["configuration"][0]["lookup_table"]
    inverse_examples = inverse.data["guided_evidence"]["examples"]
    assert inverse_examples
    assert all("error" in example for example in inverse_examples)
    assert any(example["error"] is None and example.get("report")
               for example in inverse_examples)

    inverse_revision = content_changes.apply_review(inverse, db_name=mutable_db)
    reinverse = content_changes.review_inverse(inverse_revision, db_name=mutable_db)
    reinverse_configuration = reinverse.data["configuration_review"]
    assert "3" in reinverse_configuration["before"][0]["configuration"][0]["lookup_table"]
    assert reinverse_configuration["after"][0]["configuration"][0]["lookup_table"] == {
        "x": "periappendicite",
    }
    assert any(example["error"] is None and example.get("report")
               for example in reinverse.data["guided_evidence"]["examples"])
