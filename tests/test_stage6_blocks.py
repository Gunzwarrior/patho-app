"""Checkpoint 5 Block Studio regressions.

The UI is intentionally thin; these tests exercise its read-only Block-draft
planner and the shared candidate service that actually protects content.
"""

import pytest
from streamlit.testing.v1 import AppTest

import content_changes
import content_editing
import content_snapshot
import content_studio
import database
import editor_preview
from test_stage5_packages import connection, save_synthetic_case


def _unlock():
    content_editing.record_initial_snapshot("a" * 64)


def _hash(path):
    return content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(path))


def _review(path, intents):
    return content_studio.review(intents, _hash(path), summary="Block Studio test", db_name=path)


def _source(path, key):
    conn = connection(path)
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


def _insert_unbound_field(path, key):
    conn = connection(path)
    try:
        field_id = conn.execute(
            """INSERT INTO Fields
               (key,label,type,is_archived,options,default_value,conclusion_addendum_template)
               VALUES (?,?,?,0,NULL,?,NULL)""",
            (key, f"Field {key}", "text", "same"),
        ).lastrowid
        conn.commit()
        return field_id
    finally:
        conn.close()


def _replace_unbound_field(path, key):
    """Recreate one logically identical Field and return both physical IDs."""
    conn = connection(path)
    try:
        field = dict(conn.execute("SELECT * FROM Fields WHERE key=?", (key,)).fetchone())
        old_id = field["id"]
        conn.execute("DELETE FROM Fields WHERE id=?", (old_id,))
        new_id = conn.execute(
            """INSERT INTO Fields
               (key,label,type,is_archived,options,default_value,conclusion_addendum_template)
               VALUES (?,?,?,?,?,?,?)""",
            tuple(field[column] for column in (
                "key", "label", "type", "is_archived", "options", "default_value",
                "conclusion_addendum_template",
            )),
        ).lastrowid
        conn.commit()
        return old_id, new_id
    finally:
        conn.close()


def _new_block_draft(name):
    return {
        "name": name, "site_label": None, "conclusion_group": None,
        "macro_template": "Macro.", "micro_template": "Micro.",
        "conclusion_template": "Conclusion.", "context_template": None,
        "title_fragment_template": None, "conclusion_label_template": None,
    }


def _apply(path, intents):
    return content_changes.apply_review(_review(path, intents), db_name=path)


def test_block_studio_ui_prepares_a_frozen_create_review_without_writing(mutable_db):
    _unlock()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    app.radio(key="editor_studio_block_action").set_value("Create a new Block").run()
    app.text_input(key="editor_studio_block_new_0_new_key").set_value("ui_block_candidate")
    app.text_input(key="editor_studio_block_new_0_name").set_value("UI candidate")
    app.text_area(key="editor_studio_block_new_0_macro").set_value("Macro.")
    app.text_area(key="editor_studio_block_new_0_micro").set_value("Micro.")
    app.text_area(key="editor_studio_block_new_0_conclusion").set_value("Conclusion.")
    next(button for button in app.button if button.label == "Prepare Block review").click().run()
    assert not app.exception
    assert "_editor_studio_review" in app.session_state.filtered_state
    assert database.get_all_blocks(include_archived=True)
    assert not any(row["key"] == "ui_block_candidate" for row in database.get_all_editor_blocks())


def test_duplicate_picker_includes_supported_appendix_and_gallbladder_blocks(mutable_db):
    _unlock()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    app.radio(key="editor_studio_block_action").set_value("Duplicate existing Block").run()
    options = app.selectbox(key="editor_studio_block_select").options
    assert any("appendice" in option for option in options)
    assert any("vesicule_biliaire" in option for option in options)


@pytest.mark.parametrize("action, target_key", [("edit", "antrum"), ("duplicate", "antrum_stale_copy")])
def test_block_draft_baseline_refuses_concurrent_metadata_change(mutable_db, action, target_key):
    _unlock()
    source, draft, bindings = _source(mutable_db, "antrum")
    baseline = content_studio.block_draft_baseline(source, bindings)
    draft["name"] = "Antrum stale draft" if action == "edit" else "Antrum copied stale draft"
    conn = connection(mutable_db)
    try:
        conn.execute("UPDATE Blocks SET name='Antrum concurrent current' WHERE key='antrum'")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_studio.StaleBlockDraftError, match="changed since the draft was loaded"):
        content_studio.block_draft_operations(
            action, target_key, draft, bindings, source_key="antrum", baseline=baseline, db_name=mutable_db,
        )
    assert next(row for row in database.get_all_editor_blocks() if row["key"] == "antrum")["name"] == "Antrum concurrent current"
    assert not any(row["key"] == "antrum_stale_copy" for row in database.get_all_editor_blocks())


def test_review_boundary_refuses_write_after_block_planning(mutable_db):
    """Operations planned from A cannot be reviewed against later state B."""
    _unlock()
    source, draft, bindings = _source(mutable_db, "antrum")
    draft["micro_template"] = "Microscopy from the older draft."
    intents = content_studio.block_draft_operations(
        "edit", "antrum", draft, bindings, source_key="antrum",
        baseline=content_studio.block_draft_baseline(source, bindings), db_name=mutable_db,
    )
    assert intents[0]["op"] == "assert_block_draft"

    conn = connection(mutable_db)
    try:
        conn.execute("UPDATE Blocks SET micro_template='Concurrent microscopy.' WHERE key='antrum'")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(content_changes.StaleDraftReviewError, match="changed since the draft was loaded"):
        _review(mutable_db, intents)
    assert next(row for row in database.get_all_editor_blocks()
                if row["key"] == "antrum")["micro_template"] == "Concurrent microscopy."


def test_block_field_endpoint_identity_aba_is_stale_at_review(mutable_db):
    """A natural-key-identical Field replacement is still a new endpoint."""
    _unlock()
    conn = connection(mutable_db)
    try:
        old_field_id = conn.execute(
            """INSERT INTO Fields
               (key,label,type,is_archived,options,default_value,conclusion_addendum_template)
               VALUES ('block_identity_aba','Identity ABA','text',0,NULL,'same',NULL)"""
        ).lastrowid
        block_id = conn.execute("SELECT id FROM Blocks WHERE key='antrum'").fetchone()[0]
        sort_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order),-1)+1 FROM Block_Fields WHERE block_id=?", (block_id,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO Block_Fields
               (block_id,field_id,sort_order,label_override,default_override,context_section)
               VALUES (?,?,?,?,?,?)""",
            (block_id, old_field_id, sort_order, None, None, 0),
        )
        conn.commit()
    finally:
        conn.close()

    source, draft, bindings = _source(mutable_db, "antrum")
    draft["name"] = "Identity-bound draft"
    intents = content_studio.block_draft_operations(
        "edit", "antrum", draft, bindings, source_key="antrum",
        baseline=content_studio.block_draft_baseline(source, bindings), db_name=mutable_db,
    )
    logical_hash = _hash(mutable_db)

    conn = connection(mutable_db)
    try:
        field = dict(conn.execute("SELECT * FROM Fields WHERE key='block_identity_aba'").fetchone())
        binding = dict(conn.execute(
            "SELECT * FROM Block_Fields WHERE block_id=? AND field_id=?", (block_id, old_field_id)
        ).fetchone())
        conn.execute("DELETE FROM Block_Fields WHERE block_id=? AND field_id=?", (block_id, old_field_id))
        conn.execute("DELETE FROM Fields WHERE id=?", (old_field_id,))
        new_field_id = conn.execute(
            """INSERT INTO Fields
               (key,label,type,is_archived,options,default_value,conclusion_addendum_template)
               VALUES (?,?,?,?,?,?,?)""",
            tuple(field[column] for column in (
                "key", "label", "type", "is_archived", "options", "default_value",
                "conclusion_addendum_template",
            )),
        ).lastrowid
        conn.execute(
            """INSERT INTO Block_Fields
               (block_id,field_id,sort_order,label_override,default_override,context_section)
               VALUES (?,?,?,?,?,?)""",
            (binding["block_id"], new_field_id, binding["sort_order"], binding["label_override"],
             binding["default_override"], binding["context_section"]),
        )
        conn.commit()
    finally:
        conn.close()

    assert new_field_id != old_field_id
    assert _hash(mutable_db) == logical_hash
    with pytest.raises(content_changes.StaleDraftReviewError, match="Field selected by this Block draft changed"):
        _review(mutable_db, intents)
    assert next(row for row in database.get_all_editor_blocks() if row["key"] == "antrum")["name"] == "Antrum"


def test_duplicate_rule_change_after_planning_is_stale_at_review(mutable_db):
    """Duplicate cannot copy an older rule set than its review source."""
    _unlock()
    source, draft, bindings = _source(mutable_db, "appendice")
    draft["name"] = "Appendice stale-rule duplicate"
    intents = content_studio.block_draft_operations(
        "duplicate", "appendice_stale_rule_duplicate", draft, bindings,
        source_key="appendice", baseline=content_studio.block_draft_baseline(source, bindings),
        db_name=mutable_db,
    )
    assertion = intents[0]
    assert assertion["op"] == "assert_block_draft"
    assert assertion["copied_rules_baseline"] is not None

    conn = connection(mutable_db)
    try:
        rule_id = conn.execute(
            """SELECT id FROM Field_Consistency_Rules
               WHERE block_id=(SELECT id FROM Blocks WHERE key='appendice') ORDER BY id LIMIT 1"""
        ).fetchone()[0]
        conn.execute(
            "UPDATE Field_Consistency_Rules SET message='Concurrent source rule.' WHERE id=?", (rule_id,)
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(content_changes.StaleDraftReviewError, match="consistency rules changed"):
        _review(mutable_db, intents)
    assert not any(row["key"] == "appendice_stale_rule_duplicate"
                   for row in database.get_all_editor_blocks())


def test_edit_new_field_endpoint_replacement_after_planning_is_stale(mutable_db):
    _unlock()
    field_key = "edit_new_endpoint_aba"
    _insert_unbound_field(mutable_db, field_key)
    source, draft, bindings = _source(mutable_db, "antrum")
    draft["name"] = "Edit with new endpoint"
    bindings.append({"field_key": field_key, "label_override": None,
                     "default_override": None, "context_section": False})
    intents = content_studio.block_draft_operations(
        "edit", "antrum", draft, bindings, source_key="antrum",
        baseline=content_studio.block_draft_baseline(source, bindings[:-1]), db_name=mutable_db,
    )
    logical_hash = _hash(mutable_db)
    old_id, new_id = _replace_unbound_field(mutable_db, field_key)

    assert new_id != old_id
    assert _hash(mutable_db) == logical_hash
    with pytest.raises(content_changes.StaleDraftReviewError, match="Field selected by this Block draft changed"):
        _review(mutable_db, intents)


def test_duplicate_new_field_endpoint_replacement_after_planning_is_stale(mutable_db):
    _unlock()
    field_key = "duplicate_new_endpoint_aba"
    _insert_unbound_field(mutable_db, field_key)
    source, draft, bindings = _source(mutable_db, "antrum")
    draft["name"] = "Duplicate with new endpoint"
    bindings.append({"field_key": field_key, "label_override": None,
                     "default_override": "duplicate", "context_section": False})
    intents = content_studio.block_draft_operations(
        "duplicate", "antrum_new_endpoint_duplicate", draft, bindings,
        source_key="antrum", baseline=content_studio.block_draft_baseline(source, bindings[:-1]),
        db_name=mutable_db,
    )
    old_id, new_id = _replace_unbound_field(mutable_db, field_key)

    assert new_id != old_id
    with pytest.raises(content_changes.StaleDraftReviewError, match="Field selected by this Block draft changed"):
        _review(mutable_db, intents)
    assert not any(row["key"] == "antrum_new_endpoint_duplicate"
                   for row in database.get_all_editor_blocks())


def test_create_field_endpoint_replacement_after_planning_is_stale(mutable_db):
    _unlock()
    field_key = "create_endpoint_aba"
    _insert_unbound_field(mutable_db, field_key)
    bindings = [{"field_key": field_key, "label_override": None,
                 "default_override": None, "context_section": False}]
    intents = content_studio.block_draft_operations(
        "create", "create_endpoint_block", _new_block_draft("Create endpoint block"), bindings,
        db_name=mutable_db,
    )
    assert any(intent["op"] == "assert_field_endpoints" for intent in intents)
    old_id, new_id = _replace_unbound_field(mutable_db, field_key)

    assert new_id != old_id
    with pytest.raises(content_changes.StaleDraftReviewError, match="Field selected by this Block draft changed"):
        _review(mutable_db, intents)
    assert not any(row["key"] == "create_endpoint_block" for row in database.get_all_editor_blocks())


def test_field_endpoint_replacement_after_review_is_refused_at_apply(mutable_db):
    _unlock()
    field_key = "apply_endpoint_aba"
    _insert_unbound_field(mutable_db, field_key)
    bindings = [{"field_key": field_key, "label_override": None,
                 "default_override": None, "context_section": False}]
    prepared = _review(mutable_db, content_studio.block_draft_operations(
        "create", "apply_endpoint_block", _new_block_draft("Apply endpoint block"), bindings,
        db_name=mutable_db,
    ))
    reviewed_hash = _hash(mutable_db)
    old_id, new_id = _replace_unbound_field(mutable_db, field_key)

    assert new_id != old_id
    assert _hash(mutable_db) == reviewed_hash
    # Apply's broader physical-identity guard fires before its retained
    # endpoint assertion. Either way, no replacement identity reaches writes.
    with pytest.raises(content_changes.StaleReviewError, match="Local state changed since review"):
        content_changes.apply_review(prepared, db_name=mutable_db)
    assert not any(row["key"] == "apply_endpoint_block" for row in database.get_all_editor_blocks())


def test_review_boundary_stale_refusal_reloads_the_ui_draft(mutable_db, monkeypatch):
    """Exercise the planner-to-review race through the actual Studio caller."""
    _unlock()
    original_planner = content_studio.block_draft_operations
    raced = False

    def plan_then_change(*args, **kwargs):
        nonlocal raced
        intents = original_planner(*args, **kwargs)
        if not raced:
            raced = True
            conn = connection(mutable_db)
            try:
                conn.execute("UPDATE Blocks SET micro_template='Concurrent boundary microscopy.' WHERE key='antrum'")
                conn.commit()
            finally:
                conn.close()
        return intents

    monkeypatch.setattr(content_studio, "block_draft_operations", plan_then_change)
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    antrum = next(row for row in database.get_all_editor_blocks() if row["key"] == "antrum")
    app.selectbox(key="editor_studio_block_select").set_value(antrum["id"]).run()
    stale_token = "editor_studio_block_antrum_0"
    app.text_input(key=f"{stale_token}_name").set_value("Older boundary draft")
    next(button for button in app.button if button.label == "Prepare Block review").click().run()

    assert raced
    assert not app.exception
    assert "_editor_studio_review" not in app.session_state.filtered_state
    reloaded_token = "editor_studio_block_antrum_1"
    assert app.text_input(key=f"{reloaded_token}_name").value == "Antrum"
    assert app.text_area(key=f"{reloaded_token}_micro").value == "Concurrent boundary microscopy."


def test_edit_to_duplicate_transition_keeps_widgets_and_baseline_on_one_generation(mutable_db):
    """Changing action cannot bless retained Edit widgets with a newer baseline."""
    _unlock()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    antrum = next(row for row in database.get_all_editor_blocks() if row["key"] == "antrum")
    app.selectbox(key="editor_studio_block_select").set_value(antrum["id"]).run()
    stale_token = "editor_studio_block_antrum_0"
    assert app.checkbox(key=f"{stale_token}_fragments_label_mode").value is False

    conn = connection(mutable_db)
    try:
        conn.execute("""UPDATE Block_Fields SET label_override='Concurrent duplicate label'
                        WHERE block_id=(SELECT id FROM Blocks WHERE key='antrum')
                          AND field_id=(SELECT id FROM Fields WHERE key='fragments')""")
        conn.commit()
    finally:
        conn.close()

    app.radio(key="editor_studio_block_action").set_value("Duplicate existing Block").run()
    # The action changed, but these widgets intentionally remain the original
    # loaded draft until preparation detects staleness and reloads all of it.
    assert app.checkbox(key=f"{stale_token}_fragments_label_mode").value is False
    app.text_input(key=f"{stale_token}_new_key").set_value("antrum_stale_transition")
    app.text_input(key=f"{stale_token}_name").set_value("Antrum stale transition")
    next(button for button in app.button if button.label == "Prepare Block review").click().run()

    assert not app.exception
    assert "_editor_studio_review" not in app.session_state.filtered_state
    assert not any(row["key"] == "antrum_stale_transition" for row in database.get_all_editor_blocks())
    reloaded_token = "editor_studio_block_antrum_1"
    assert app.checkbox(key=f"{reloaded_token}_fragments_label_mode").value is True
    assert app.text_input(key=f"{reloaded_token}_fragments_label").value == "Concurrent duplicate label"


def test_stale_block_binding_widgets_reload_without_manufacturing_a_rollback(mutable_db):
    _unlock()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    antrum = next(row for row in database.get_all_editor_blocks() if row["key"] == "antrum")
    app.selectbox(key="editor_studio_block_select").set_value(antrum["id"]).run()
    token = "editor_studio_block_antrum_0"
    app.text_input(key=f"{token}_name").set_value("Stale local name")
    conn = connection(mutable_db)
    try:
        conn.execute("""UPDATE Block_Fields SET label_override='Concurrent label'
                        WHERE block_id=(SELECT id FROM Blocks WHERE key='antrum')
                          AND field_id=(SELECT id FROM Fields WHERE key='fragments')""")
        conn.commit()
    finally:
        conn.close()
    next(button for button in app.button if button.label == "Prepare Block review").click().run()
    assert not app.exception
    assert "_editor_studio_review" not in app.session_state.filtered_state
    conn = connection(mutable_db)
    try:
        assert conn.execute("""SELECT label_override FROM Block_Fields
                             WHERE block_id=(SELECT id FROM Blocks WHERE key='antrum')
                               AND field_id=(SELECT id FROM Fields WHERE key='fragments')""").fetchone()[0] == "Concurrent label"
    finally:
        conn.close()
    # A new generation gives the form fresh widget state rather than retaining
    # the local name or using it to clear the concurrent override.
    reloaded = "editor_studio_block_antrum_1"
    assert app.text_input(key=f"{reloaded}_name").value == "Antrum"
    assert app.checkbox(key=f"{reloaded}_fragments_label_mode").value is True
    assert app.text_input(key=f"{reloaded}_fragments_label").value == "Concurrent label"


def test_cp5_review_apply_remains_stale_after_concurrent_change(mutable_db):
    _unlock()
    source, draft, bindings = _source(mutable_db, "antrum")
    draft["name"] = "Prepared name"
    intents = content_studio.block_draft_operations(
        "edit", "antrum", draft, bindings, source_key="antrum",
        baseline=content_studio.block_draft_baseline(source, bindings), db_name=mutable_db,
    )
    prepared = _review(mutable_db, intents)
    conn = connection(mutable_db)
    try:
        conn.execute("UPDATE Blocks SET name='Concurrent after review' WHERE key='antrum'")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_changes.StaleReviewError):
        content_changes.apply_review(prepared, db_name=mutable_db)
    assert next(row for row in database.get_all_editor_blocks() if row["key"] == "antrum")["name"] == "Concurrent after review"


def test_block_names_cannot_be_blank_for_create_edit_or_duplicate(mutable_db):
    _unlock()
    source, draft, bindings = _source(mutable_db, "antrum")
    blank = dict(draft, name="   ")
    with pytest.raises(content_studio.StudioIntentError, match="name cannot be blank"):
        content_studio.block_draft_operations("create", "blank_block_create", blank, bindings, db_name=mutable_db)
    baseline = content_studio.block_draft_baseline(source, bindings)
    with pytest.raises(content_studio.StudioIntentError, match="name cannot be blank"):
        content_studio.block_draft_operations(
            "edit", "antrum", blank, bindings, source_key="antrum", baseline=baseline, db_name=mutable_db,
        )
    with pytest.raises(content_studio.StudioIntentError, match="name cannot be blank"):
        content_studio.block_draft_operations(
            "duplicate", "blank_block_duplicate", blank, bindings, source_key="antrum", baseline=baseline,
            db_name=mutable_db,
        )
    # The shared row validator closes the same hole for internal intents that
    # do not go through the guided planner.
    with pytest.raises(content_changes.ChangeError, match="candidate could not be prepared"):
        _review(mutable_db, [content_studio.operation("create", "Blocks", "internal_blank_block", {
            "name": " ", "site_label": None, "conclusion_group": None,
            "macro_template": "Macro.", "micro_template": "Micro.", "conclusion_template": "Conclusion.",
            "context_template": None, "title_fragment_template": None, "conclusion_label_template": None,
        })])


def test_blank_label_override_never_becomes_inheritance_for_new_or_existing_binding(mutable_db):
    _unlock()
    source, draft, bindings = _source(mutable_db, "antrum")
    blank_binding = dict(bindings[0], label_override="   ")
    with pytest.raises(content_studio.StudioIntentError, match="label override cannot be blank"):
        content_studio.block_draft_operations(
            "create", "blank_label_create", dict(draft, name="Blank label create"), [blank_binding], db_name=mutable_db,
        )
    updated = [blank_binding if binding["field_key"] == blank_binding["field_key"] else binding for binding in bindings]
    with pytest.raises(content_studio.StudioIntentError, match="label override cannot be blank"):
        content_studio.block_draft_operations(
            "edit", "antrum", draft, updated, source_key="antrum",
            baseline=content_studio.block_draft_baseline(source, bindings), db_name=mutable_db,
        )


def test_numeric_block_override_native_equivalence_preserves_storage_and_real_change_updates_it(mutable_db):
    _unlock()
    conn = connection(mutable_db)
    try:
        conn.execute("""UPDATE Block_Fields SET default_override='3'
                        WHERE block_id=(SELECT id FROM Blocks WHERE key='antrum')
                          AND field_id=(SELECT id FROM Fields WHERE key='fragments')""")
        conn.commit()
    finally:
        conn.close()
    source, draft, bindings = _source(mutable_db, "antrum")
    draft["site_label"] = "antrale numérique"
    intents = content_studio.block_draft_operations(
        "edit", "antrum", draft, bindings, source_key="antrum",
        baseline=content_studio.block_draft_baseline(source, bindings), db_name=mutable_db,
    )
    assert not any(intent.get("table") == "Block_Fields" for intent in intents)
    _apply(mutable_db, intents)
    conn = connection(mutable_db)
    try:
        assert conn.execute("""SELECT default_override FROM Block_Fields
                             WHERE block_id=(SELECT id FROM Blocks WHERE key='antrum')
                               AND field_id=(SELECT id FROM Fields WHERE key='fragments')""").fetchone()[0] == "3"
    finally:
        conn.close()

    source, draft, bindings = _source(mutable_db, "antrum")
    baseline = content_studio.block_draft_baseline(source, bindings)
    for binding in bindings:
        if binding["field_key"] == "fragments":
            binding["default_override"] = 4
    intents = content_studio.block_draft_operations(
        "edit", "antrum", draft, bindings, source_key="antrum",
        baseline=baseline, db_name=mutable_db,
    )
    override_update = next(intent for intent in intents if intent.get("table") == "Block_Fields")
    assert override_update["values"] == {"default_override": 4}
    _apply(mutable_db, intents)
    conn = connection(mutable_db)
    try:
        assert conn.execute("""SELECT default_override FROM Block_Fields
                             WHERE block_id=(SELECT id FROM Blocks WHERE key='antrum')
                               AND field_id=(SELECT id FROM Fields WHERE key='fragments')""").fetchone()[0] == "4"
    finally:
        conn.close()


def test_archived_block_is_visible_under_filter_but_must_restore_before_duplicate(mutable_db):
    _unlock()
    _apply(mutable_db, [content_studio.operation("archive", "Blocks", "appendice")])
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    app.radio(key="editor_studio_filter").set_value("Archived").run()
    app.radio(key="editor_studio_block_action").set_value("Duplicate existing Block").run()
    appendix_id = next(row["id"] for row in database.get_all_editor_blocks() if row["key"] == "appendice")
    assert any("appendice" in option for option in app.selectbox(key="editor_studio_block_select").options)
    app.selectbox(key="editor_studio_block_select").set_value(appendix_id).run()
    assert any("Restore it before editing or duplicating" in item.value for item in app.info)
    assert "Prepare Block review" not in {button.label for button in app.button}
    assert "Prepare Restore review" in {button.label for button in app.button}
    next(button for button in app.button if button.label == "Prepare Restore review").click().run()
    assert "_editor_studio_review" in app.session_state.filtered_state
    assert any("Frozen Content Studio review" in item.value for item in app.subheader)
    review = app.session_state["_editor_studio_review"]
    assert any(change["table"] == "Blocks" and change["key"] == "appendice"
               and change["after"]["is_archived"] == 0 for change in review.changes)

    next(box for box in app.checkbox if box.label.startswith("I confirm this exact reviewed")).set_value(True).run()
    next(button for button in app.button if button.label == "Apply reviewed Content Studio change").click().run()
    assert next(row for row in database.get_all_editor_blocks() if row["key"] == "appendice")["is_archived"] == 0

    # Apply removes the confirmation widget synchronously; use a fresh
    # browser session before exercising the recovered Active selector.
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    app.radio(key="editor_studio_filter").set_value("Active").run()
    app.radio(key="editor_studio_block_action").set_value("Edit existing Block").run()
    assert any("appendice" in option for option in app.selectbox(key="editor_studio_block_select").options)
    app.selectbox(key="editor_studio_block_select").set_value(appendix_id).run()
    assert "Prepare Block review" in {button.label for button in app.button}
    app.radio(key="editor_studio_block_action").set_value("Duplicate existing Block").run()
    assert any("appendice" in option for option in app.selectbox(key="editor_studio_block_select").options)


def test_active_block_picker_explains_when_supported_blocks_are_archived(mutable_db):
    _unlock()
    _apply(mutable_db, [content_studio.operation("archive", "Blocks", "appendice")])
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    app.radio(key="editor_studio_block_action").set_value("Duplicate existing Block").run()
    options = app.selectbox(key="editor_studio_block_select").options
    assert not any("appendice" in option for option in options)
    assert any("appendice" in item.value.lower() and "Archived or All" in item.value for item in app.caption)


def test_removing_current_reorder_field_repairs_stale_widget_state(mutable_db):
    _unlock()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    appendix = next(row for row in database.get_all_editor_blocks() if row["key"] == "appendice")
    app.selectbox(key="editor_studio_block_select").set_value(appendix["id"]).run()
    token = f"editor_studio_block_appendice_0"
    fields = list(app.multiselect(key=f"{token}_fields").value)
    removed = fields[-1]
    app.selectbox(key=f"{token}_move").set_value(removed).run()
    app.multiselect(key=f"{token}_fields").set_value(fields[:-1]).run()
    assert not app.exception
    assert app.selectbox(key=f"{token}_move").value in fields[:-1]


def test_existing_block_group_and_site_edits_are_reviewed_and_persist_with_cases(mutable_db):
    _unlock()
    save_synthetic_case(mutable_db, code="gt", number="CP5-GROUP-PENDING")
    save_synthetic_case(mutable_db, code="gt", number="CP5-GROUP-VALIDATED", status="validated")
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    antrum = next(row for row in database.get_all_editor_blocks() if row["key"] == "antrum")
    assert antrum["conclusion_group"] == "gastric"
    app.selectbox(key="editor_studio_block_select").set_value(antrum["id"]).run()
    token = "editor_studio_block_antrum_0"
    app.selectbox(key=f"{token}_group").set_value("duodenum").run()
    assert app.selectbox(key=f"{token}_group").value == "duodenum"
    next(button for button in app.button if button.label == "Prepare Block review").click().run()
    assert "_editor_studio_review" in app.session_state.filtered_state
    review = app.session_state["_editor_studio_review"]
    change = next(change for change in review.changes if change["table"] == "Blocks" and change["key"] == "antrum")
    assert change["after"]["conclusion_group"] == "duodenum"
    assert any(item["affected"] for item in review.presets)
    next(box for box in app.checkbox if box.label.startswith("I confirm this exact reviewed")).set_value(True).run()
    next(button for button in app.button if button.label == "Apply reviewed Content Studio change").click().run()
    assert next(row for row in database.get_all_editor_blocks() if row["key"] == "antrum")["conclusion_group"] == "duodenum"

    # A fresh AppTest session also avoids retaining report-selector widgets
    # that belonged solely to the now-cleared frozen review.
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Blocks").run()
    app.selectbox(key="editor_studio_block_select").set_value(antrum["id"]).run()
    token = "editor_studio_block_antrum_0"
    app.text_input(key=f"{token}_site").set_value("antrale révisée")
    next(button for button in app.button if button.label == "Prepare Block review").click().run()
    review = app.session_state["_editor_studio_review"]
    change = next(change for change in review.changes if change["table"] == "Blocks" and change["key"] == "antrum")
    assert change["after"]["site_label"] == "antrale révisée"
    next(box for box in app.checkbox if box.label.startswith("I confirm this exact reviewed")).set_value(True).run()
    next(button for button in app.button if button.label == "Apply reviewed Content Studio change").click().run()
    assert next(row for row in database.get_all_editor_blocks() if row["key"] == "antrum")["site_label"] == "antrale révisée"


def test_complete_create_and_duplicate_copy_only_block_configuration(mutable_db):
    _unlock()
    draft = {
        "name": "Studio source", "site_label": "gauche", "conclusion_group": "studio",
        "macro_template": "Macro {{ fragments }}.", "micro_template": "Micro {{ appendicite_type }}.",
        "conclusion_template": "Conclusion {{ site_label }}.",
        "context_template": "Contexte {{ fragments }}.", "title_fragment_template": "{{ fragments }}",
        "conclusion_label_template": "Échantillon {{ fragments }}",
    }
    bindings = [
        {"field_key": "fragments", "label_override": "Fragments studio", "default_override": 2,
         "context_section": True},
        {"field_key": "appendicite_type", "label_override": None, "default_override": None,
         "context_section": False},
    ]
    _apply(mutable_db, content_studio.block_draft_operations("create", "studio_source", draft, bindings,
                                                              db_name=mutable_db))
    source, copied_draft, copied_bindings = _source(mutable_db, "studio_source")
    copied_draft["name"] = "Studio duplicate"
    prepared = _review(mutable_db, content_studio.block_draft_operations(
        "duplicate", "studio_duplicate", copied_draft, copied_bindings,
        source_key="studio_source", baseline=content_studio.block_draft_baseline(source, copied_bindings), db_name=mutable_db,
    ))
    assert not [change for change in prepared.changes if change["table"] == "Preset_Blocks"]
    content_changes.apply_review(prepared, db_name=mutable_db)

    conn = connection(mutable_db)
    try:
        duplicate = dict(conn.execute("SELECT * FROM Blocks WHERE key='studio_duplicate'").fetchone())
        links = [dict(row) for row in conn.execute(
            """SELECT f.key,bf.sort_order,bf.label_override,bf.default_override,bf.context_section
               FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id WHERE bf.block_id=? ORDER BY bf.sort_order""",
            (duplicate["id"],)
        )]
        assert [(row["key"], row["sort_order"], row["label_override"], row["default_override"], row["context_section"])
                for row in links] == [("fragments", 0, "Fragments studio", "2", 1),
                                      ("appendicite_type", 1, None, None, 0)]
        assert conn.execute("SELECT COUNT(*) FROM Preset_Blocks WHERE block_id=?", (duplicate["id"],)).fetchone()[0] == 0
    finally:
        conn.close()


def test_shared_fields_reorder_and_all_inheritance_states_are_one_candidate(mutable_db):
    _unlock()
    source, draft, bindings = _source(mutable_db, "appendice")
    baseline = content_studio.block_draft_baseline(source, bindings)
    bindings = list(reversed(bindings))
    for binding in bindings:
        if binding["field_key"] == "false_membranes":
            binding["label_override"] = "Membranes revues"
            binding["default_override"] = True
            binding["context_section"] = True
    intents = content_studio.block_draft_operations("edit", "appendice", draft, bindings,
                                                     source_key="appendice",
                                                     baseline=baseline, db_name=mutable_db)
    prepared = _review(mutable_db, intents)
    assert {change["table"] for change in prepared.changes} >= {"Block_Fields"}
    content_changes.apply_review(prepared, db_name=mutable_db)

    conn = connection(mutable_db)
    try:
        shared = conn.execute("SELECT COUNT(*) FROM Block_Fields WHERE field_id=(SELECT id FROM Fields WHERE key='false_membranes')").fetchone()[0]
        # The edited link still exists and may be shared with other Blocks;
        # no Field identity was copied or renamed by a Block edit.
        assert shared >= 1
        rows = conn.execute("""SELECT f.key,bf.sort_order,bf.label_override,bf.default_override,bf.context_section
                               FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id
                               WHERE bf.block_id=(SELECT id FROM Blocks WHERE key='appendice') ORDER BY bf.sort_order""").fetchall()
        assert [row["key"] for row in rows] == [binding["field_key"] for binding in bindings]
        changed = next(row for row in rows if row["key"] == "false_membranes")
        assert (changed["label_override"], changed["default_override"], changed["context_section"]) == ("Membranes revues", "true", 1)
    finally:
        conn.close()


def test_context_section_and_site_group_changes_use_complete_report_validation(mutable_db):
    _unlock()
    source, draft, bindings = _source(mutable_db, "thyroid_cytology")
    baseline = content_studio.block_draft_baseline(source, bindings)
    context_key = next(binding["field_key"] for binding in bindings)
    for binding in bindings:
        binding["context_section"] = binding["field_key"] == context_key
    draft.update({"site_label": "lobaire", "conclusion_group": "thyroïde",
                  "context_template": "Contexte {{ " + context_key + " }}",
                  "title_fragment_template": "{{ " + context_key + " }}"})
    prepared = _review(mutable_db, content_studio.block_draft_operations(
        "edit", "thyroid_cytology", draft, bindings, source_key="thyroid_cytology",
        baseline=baseline, db_name=mutable_db,
    ))
    assert any(item["affected"] for item in prepared.presets)
    content_changes.apply_review(prepared, db_name=mutable_db)
    conn = connection(mutable_db)
    try:
        block_id = conn.execute("SELECT id FROM Blocks WHERE key='thyroid_cytology'").fetchone()[0]
        block = database.get_block_on_connection(conn, block_id)
        rendered = editor_preview.render_report(conn, {"name": block["name"]}, [block], [{}], strict=True)
        assert "Contexte" in rendered["clinical_info"]
    finally:
        conn.close()


def test_duplicate_is_safe_with_pending_and_ad_hoc_saved_instances(mutable_db):
    _unlock()
    conn = connection(mutable_db)
    try:
        block_id = conn.execute("SELECT id FROM Blocks WHERE key='appendice'").fetchone()[0]
        source_rules = [dict(row) for row in conn.execute(
            """SELECT field_a_key,field_a_values,field_b_key,field_b_values,message
               FROM Field_Consistency_Rules WHERE block_id=? ORDER BY id""", (block_id,)
        )]
    finally:
        conn.close()
    save_synthetic_case(mutable_db, number="BLOCK-DUPLICATE-PENDING")
    save_synthetic_case(mutable_db, number="BLOCK-DUPLICATE-AD-HOC",
                        structured={"block_instances": [{"block_id": block_id, "instance_no": 700}]})
    source, draft, bindings = _source(mutable_db, "appendice")
    draft["name"] = "Appendice duplicate"
    prepared = _review(mutable_db, content_studio.block_draft_operations(
        "duplicate", "appendice_duplicate", draft, bindings, source_key="appendice",
        baseline=content_studio.block_draft_baseline(source, bindings), db_name=mutable_db,
    ))
    assert prepared.pending_cases == []
    copied_rule_intents = [intent for intent in prepared.operations
                           if intent.get("table") == "Field_Consistency_Rules"]
    # Block duplication intentionally carries the persisted JSON-text rule
    # representation through generalized insertion.  It must be accepted by
    # the same semantic canonicalizer used by guided configuration authoring.
    assert [intent["values"]["field_a_values"] for intent in copied_rule_intents] == [
        rule["field_a_values"] for rule in source_rules
    ]
    assert all(intent.get("copy_source", {}).get("block_key") == "appendice"
               and type(intent.get("copy_source", {}).get("id")) is int
               for intent in copied_rule_intents)
    content_changes.apply_review(prepared, db_name=mutable_db)
    assert content_studio.pending_blockers("Blocks", "appendice", db_name=mutable_db)
    conn = connection(mutable_db)
    try:
        copied_rules = [dict(row) for row in conn.execute(
            """SELECT field_a_key,field_a_values,field_b_key,field_b_values,message
               FROM Field_Consistency_Rules WHERE block_id=(SELECT id FROM Blocks WHERE key='appendice_duplicate')
               ORDER BY id"""
        )]
        assert copied_rules == source_rules
    finally:
        conn.close()


def test_edit_refuses_surviving_template_or_preset_that_would_be_invalid(mutable_db):
    _unlock()
    source, draft, bindings = _source(mutable_db, "appendice")
    baseline = content_studio.block_draft_baseline(source, bindings)
    # ``appendicite_type`` remains in the template but is removed from its
    # binding.  The final graph, including Presets, must reject this one
    # otherwise apparently convenient relationship edit.
    bindings = [binding for binding in bindings if binding["field_key"] != "appendicite_type"]
    with pytest.raises(content_changes.ChangeError):
        _review(mutable_db, content_studio.block_draft_operations(
            "edit", "appendice", draft, bindings, source_key="appendice",
            baseline=baseline, db_name=mutable_db,
        ))


def test_block_delete_cleanup_lists_mechanical_cleanup_but_refuses_an_invalid_surviving_preset(mutable_db):
    _unlock()
    conn = connection(mutable_db)
    try:
        block = dict(conn.execute("SELECT * FROM Blocks WHERE key='appendice'").fetchone())
        field_ids = {row[0] for row in conn.execute("SELECT field_id FROM Block_Fields WHERE block_id=?", (block["id"],))}
        conn.execute("INSERT INTO Conclusion_Group_Labels(block_key_set,combined_label) VALUES ('appendice','appendicielle')")
        conn.commit()
    finally:
        conn.close()
    # No pending Case: permanent deletion is available and its existing
    # lifecycle planner must keep CP5 cleanup mechanical.
    plan = content_studio.lifecycle_plan("delete", "Blocks", "appendice", db_name=mutable_db)
    assert not plan["refusal_reasons"]
    assert {item["table"] for item in plan["mechanical_deletion_cleanup"]} >= {
        "Block_Fields", "Preset_Blocks", "Field_Consistency_Rules",
        "Conclusion_Group_Labels", "Quick_Type_Tokens",
    }
    with pytest.raises(content_changes.ChangeError, match="Preset must retain"):
        _review(mutable_db, plan["operations"])
    conn = connection(mutable_db)
    try:
        assert conn.execute("SELECT 1 FROM Blocks WHERE key='appendice'").fetchone() is not None
        assert all(conn.execute("SELECT 1 FROM Fields WHERE id=?", (field_id,)).fetchone() is not None
                   for field_id in field_ids)
    finally:
        conn.close()
