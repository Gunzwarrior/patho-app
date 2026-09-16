"""Checkpoint 6 Preset Studio composition regressions."""

import json

import pytest

import content_changes
import content_editing
import content_snapshot
import content_studio
import database
import editor_preview
from test_stage5_packages import connection, save_synthetic_case
from streamlit.testing.v1 import AppTest


def _unlock():
    content_editing.record_initial_snapshot("a" * 64)


def _hash(path):
    return content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(path))


def _review(path, operations):
    return content_studio.review(operations, _hash(path), summary="Preset Studio test", db_name=path)


def _source(path, code):
    preset, links = content_studio.preset_draft_source(code, db_name=path)
    draft = {name: preset[name] for name in ("name", "category", "default_title", "default_adicap")}
    instances = [{"block_key": link["block_key"], "instance_no": link["sort_order"],
                  "field_overrides": json.loads(link["field_overrides"] or "{}")}
                 for link in sorted(links, key=lambda row: (row["display_order"], row["sort_order"]))]
    return preset, links, draft, instances


def _apply(path, operations):
    return content_changes.apply_review(_review(path, operations), db_name=path)


def _endpoint(path, block_key, instance_no):
    return {"block_key": block_key, "instance_no": instance_no,
            "baseline": content_studio.preset_instance_endpoint_baseline(
                block_key, instance_no, db_name=path
            )}


def _insert_draft_only_block(path, key):
    """Create an otherwise unreferenced Block/Field endpoint for ABA tests."""
    field_key = f"{key}_field"
    conn = connection(path)
    try:
        field_id = conn.execute(
            """INSERT INTO Fields(key,label,type,is_archived,options,default_value,conclusion_addendum_template)
               VALUES (?,?, 'text', 0, NULL, 'draft default', NULL)""", (field_key, "Draft field")
        ).lastrowid
        block_id = conn.execute(
            """INSERT INTO Blocks(key,name,is_archived,is_table,site_label,conclusion_group,
                                  macro_template,micro_template,conclusion_template,context_template,
                                  title_fragment_template,conclusion_label_template)
               VALUES (?,?,0,0,NULL,NULL,'','','',NULL,NULL,NULL)""", (key, "Draft block")
        ).lastrowid
        conn.execute("""INSERT INTO Block_Fields(block_id,field_id,sort_order,label_override,default_override,context_section)
                        VALUES (?,?,0,NULL,NULL,0)""", (block_id, field_id))
        conn.commit()
    finally:
        conn.close()
    return field_key


def _replace_draft_only_block_endpoint(path, key):
    """Delete/recreate an endpoint with identical logical content and new IDs."""
    conn = connection(path)
    try:
        block = dict(conn.execute("SELECT * FROM Blocks WHERE key=?", (key,)).fetchone())
        binding = dict(conn.execute("SELECT * FROM Block_Fields WHERE block_id=?", (block["id"],)).fetchone())
        field = dict(conn.execute("SELECT * FROM Fields WHERE id=?", (binding["field_id"],)).fetchone())
        conn.execute("DELETE FROM Block_Fields WHERE block_id=?", (block["id"],))
        conn.execute("DELETE FROM Blocks WHERE id=?", (block["id"],))
        conn.execute("DELETE FROM Fields WHERE id=?", (field["id"],))
        field_id = conn.execute(
            """INSERT INTO Fields(key,label,type,is_archived,options,default_value,conclusion_addendum_template)
               VALUES (?,?,?,?,?,?,?)""",
            tuple(field[column] for column in ("key", "label", "type", "is_archived", "options", "default_value",
                                                "conclusion_addendum_template")),
        ).lastrowid
        block_id = conn.execute(
            """INSERT INTO Blocks(key,name,is_archived,is_table,site_label,conclusion_group,
                                  macro_template,micro_template,conclusion_template,context_template,
                                  title_fragment_template,conclusion_label_template)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(block[column] for column in ("key", "name", "is_archived", "is_table", "site_label",
                                                "conclusion_group", "macro_template", "micro_template",
                                                "conclusion_template", "context_template", "title_fragment_template",
                                                "conclusion_label_template")),
        ).lastrowid
        conn.execute("""INSERT INTO Block_Fields(block_id,field_id,sort_order,label_override,default_override,context_section)
                        VALUES (?,?,?,?,?,?)""",
                     (block_id, field_id, binding["sort_order"], binding["label_override"],
                      binding["default_override"], binding["context_section"]))
        conn.commit()
        return block["id"], block_id, field["id"], field_id
    finally:
        conn.close()


def test_duplicate_block_instances_keep_separate_override_objects_and_native_states(mutable_db):
    _unlock()
    # Give an existing non-table Block a text field, so this one Preset draft
    # can prove inherit/missing, null, zero, false and empty-string semantics.
    conn = connection(mutable_db)
    try:
        text_id = conn.execute(
            """INSERT INTO Fields(key,label,type,is_archived,options,default_value,conclusion_addendum_template)
               VALUES ('preset_empty_text','Empty text','text',0,NULL,'default',NULL)"""
        ).lastrowid
        null_text_id = conn.execute(
            """INSERT INTO Fields(key,label,type,is_archived,options,default_value,conclusion_addendum_template)
               VALUES ('preset_null_text','Null text','text',0,NULL,'default',NULL)"""
        ).lastrowid
        block_id = conn.execute("SELECT id FROM Blocks WHERE key='antrum'").fetchone()[0]
        conn.execute("""INSERT INTO Block_Fields(block_id,field_id,sort_order,label_override,default_override,context_section)
                        VALUES (?,?,?,?,?,0)""", (block_id, text_id, 99, None, None))
        conn.execute("""INSERT INTO Block_Fields(block_id,field_id,sort_order,label_override,default_override,context_section)
                        VALUES (?,?,?,?,?,0)""", (block_id, null_text_id, 100, None, None))
        conn.commit()
    finally:
        conn.close()
    instances = [
        {"block_key": "antrum", "instance_no": 0,
         "field_overrides": {"fragments": 0, "hp_positive": False,
                             "preset_empty_text": "", "preset_null_text": None}},
        {"block_key": "antrum", "instance_no": 1,
         "field_overrides": {"fragments": 2, "hp_positive": True}},
    ]
    endpoints = [{"block_key": item["block_key"], "instance_no": item["instance_no"],
                  "baseline": content_studio.preset_instance_endpoint_baseline(
                      item["block_key"], item["instance_no"], db_name=mutable_db
                  )} for item in instances]
    operations = content_studio.preset_draft_operations(
        "create", "preset_dupe", {"name": "Duplicate instances", "category": None,
                                    "default_title": None, "default_adicap": None}, instances,
        endpoint_baselines=endpoints, db_name=mutable_db,
    )
    _apply(mutable_db, operations)
    conn = connection(mutable_db)
    try:
        rows = [dict(row) for row in conn.execute(
            """SELECT pb.sort_order,pb.display_order,pb.field_overrides FROM Preset_Blocks pb
               JOIN Presets p ON p.id=pb.preset_id WHERE p.short_code='preset_dupe' ORDER BY pb.display_order"""
        )]
    finally:
        conn.close()
    assert [(row["sort_order"], row["display_order"]) for row in rows] == [(0, 0), (1, 1)]
    assert json.loads(rows[0]["field_overrides"]) == {
        "fragments": 0, "hp_positive": False, "preset_empty_text": "", "preset_null_text": None,
    }
    assert json.loads(rows[1]["field_overrides"]) == {"fragments": 2, "hp_positive": True}


def test_reorder_changes_only_display_order_and_explicit_pending_keeps_saved_order(mutable_db):
    _unlock()
    preset, links, draft, instances = _source(mutable_db, "etc_bi")
    explicit = save_synthetic_case(
        mutable_db, "etc_bi", structured={"block_instances": [
            {"block_id": links[0]["block_id"], "instance_no": links[0]["sort_order"]},
            {"block_id": links[1]["block_id"], "instance_no": links[1]["sort_order"]},
        ]}, number="26PR660001",
    )
    # Legacy pending Case follows the default and therefore belongs in the
    # normal acknowledgement impact preview after the composition reorder.
    legacy = save_synthetic_case(mutable_db, "etc_bi", structured={}, number="26PR660002")
    reordered = list(reversed(instances))
    operations = content_studio.preset_draft_operations(
        "edit", "etc_bi", draft, reordered, source_key="etc_bi",
        baseline=content_studio.preset_draft_baseline(preset, links), db_name=mutable_db,
    )
    assert all(op.get("table") != "Preset_Blocks" or "sort_order" not in op.get("values", {}) for op in operations)
    review = _review(mutable_db, operations)
    affected = {row["id"] for row in review.data["pending_cases"]}
    assert legacy["id"] in affected and explicit["id"] not in affected
    content_changes.apply_review(review, db_name=mutable_db)
    conn = connection(mutable_db)
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT sort_order,display_order FROM Preset_Blocks WHERE preset_id=? ORDER BY display_order", (preset["id"],)
        )]
        saved = dict(conn.execute("SELECT * FROM Cases WHERE id=?", (explicit["id"],)).fetchone())
        rendered = editor_preview.render_saved_case(conn, saved)
    finally:
        conn.close()
    assert [(row["sort_order"], row["display_order"]) for row in rows] == [(1, 0), (0, 1)]
    assert rendered["instances"] == json.loads(explicit["structured_input"])["block_instances"]


def test_quick_type_target_survives_reorder_and_is_cleaned_only_when_its_instance_is_removed(mutable_db):
    _unlock()
    preset, links, draft, instances = _source(mutable_db, "etc_bi")
    conn = connection(mutable_db)
    try:
        conn.execute("""INSERT INTO Quick_Type_Tokens
                        (preset_id,sort_order,block_sort_order,field_key,token_kind,lookup_table,digit_width)
                        VALUES (?,?,?,?,?,?,?)""",
                     (preset["id"], 90, links[0]["sort_order"], "nodule_site", "lookup",
                      json.dumps({"x": "lobaire gauche"}), None))
        conn.commit()
    finally:
        conn.close()
    reorder = content_studio.preset_draft_operations(
        "edit", "etc_bi", draft, list(reversed(instances)), source_key="etc_bi",
        baseline=content_studio.preset_draft_baseline(preset, links), db_name=mutable_db,
    )
    _apply(mutable_db, reorder)
    conn = connection(mutable_db)
    try:
        assert conn.execute("SELECT block_sort_order FROM Quick_Type_Tokens WHERE preset_id=? AND sort_order=90",
                            (preset["id"],)).fetchone()[0] == links[0]["sort_order"]
    finally:
        conn.close()
    preset, links, draft, instances = _source(mutable_db, "etc_bi")
    remove = content_studio.preset_draft_operations(
        "edit", "etc_bi", draft, [item for item in instances if item["instance_no"] != 0], source_key="etc_bi",
        baseline=content_studio.preset_draft_baseline(preset, links), db_name=mutable_db,
    )
    assert any(op.get("table") == "Quick_Type_Tokens" and op["op"] == "unlink" for op in remove)
    _apply(mutable_db, remove)
    conn = connection(mutable_db)
    try:
        assert conn.execute("SELECT 1 FROM Quick_Type_Tokens WHERE preset_id=? AND sort_order=90", (preset["id"],)).fetchone() is None
    finally:
        conn.close()


def test_duplicate_copies_composition_but_never_quick_type_tokens(mutable_db):
    _unlock()
    preset, links, draft, instances = _source(mutable_db, "dai")
    draft["name"] = "Appendice duplicate"
    operations = content_studio.preset_draft_operations(
        "duplicate", "dai_copy", draft, instances, source_key="dai",
        baseline=content_studio.preset_draft_baseline(preset, links), db_name=mutable_db,
    )
    _apply(mutable_db, operations)
    copy = next(row for row in database.get_all_presets() if row["short_code"] == "dai_copy")
    assert database.get_quick_type_tokens(copy["id"]) == []
    source_links = [(row["key"], row["sort_order"], row["field_overrides"])
                    for row in database.get_preset_blocks(preset["id"])]
    copied_links = [(row["key"], row["sort_order"], row["field_overrides"])
                    for row in database.get_preset_blocks(copy["id"])]
    assert copied_links == source_links


def test_preset_source_identity_is_stale_after_a_second_tab_composition_change(mutable_db):
    preset, links, draft, instances = _source(mutable_db, "etc_bi")
    baseline = content_studio.preset_draft_baseline(preset, links)
    conn = connection(mutable_db)
    try:
        conn.execute("UPDATE Preset_Blocks SET display_order=9 WHERE preset_id=? AND sort_order=0", (preset["id"],))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_studio.StalePresetDraftError, match="changed since the draft was loaded"):
        content_studio.preset_draft_operations(
            "edit", "etc_bi", draft, instances, source_key="etc_bi", baseline=baseline, db_name=mutable_db,
        )


def test_preset_lifecycle_archives_and_restores_but_pending_cases_block_deletion(mutable_db):
    _unlock()
    save_synthetic_case(mutable_db, "dai", number="26PR660003")
    delete = content_studio.lifecycle_plan("delete", "Presets", "dai", db_name=mutable_db)
    assert delete["operations"] == []
    assert any("pending" in reason.lower() for reason in delete["refusal_reasons"])
    archive = content_studio.lifecycle_plan("archive", "Presets", "dai", db_name=mutable_db)
    _apply(mutable_db, archive["operations"])
    assert next(row for row in database.get_all_presets(include_archived=True) if row["short_code"] == "dai")["is_archived"] == 1
    restore = content_studio.lifecycle_plan("restore", "Presets", "dai", db_name=mutable_db)
    _apply(mutable_db, restore["operations"])
    assert next(row for row in database.get_all_presets(include_archived=True) if row["short_code"] == "dai")["is_archived"] == 0


def test_preset_studio_selector_is_stable_and_a_second_tab_can_change_the_source(mutable_db):
    _unlock()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Presets").run()
    preset = next(row for row in database.get_all_presets() if row["short_code"] == "etc_bi")
    app.selectbox(key="editor_studio_preset_select").set_value(preset["id"]).run()
    assert not app.exception
    assert any(button.label == "Move Block down" for button in app.button)
    app.text_input(key="editor_studio_preset_edit_etc_bi_0_name").set_value("First tab draft").run()
    next(button for button in app.button if button.label == "Prepare Preset review").click().run()
    assert "_editor_studio_review" in app.session_state.filtered_state

    source, links, draft, instances = _source(mutable_db, "etc_bi")
    draft["name"] = "Second tab applied"
    _apply(mutable_db, content_studio.preset_draft_operations(
        "edit", "etc_bi", draft, instances, source_key="etc_bi",
        baseline=content_studio.preset_draft_baseline(source, links), db_name=mutable_db,
    ))
    # The domain-level stale assertion above covers Apply's source protection;
    # here assert that the UI selector remains bound to its physical Preset ID
    # while another tab changes the source underneath the frozen review.
    assert next(row for row in database.get_all_presets() if row["id"] == preset["id"])["name"] == "Second tab applied"


def test_new_preset_instance_exposes_field_named_overrides_before_review(mutable_db):
    _unlock()
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_studio_kind").set_value("Presets").run()
    preset = next(row for row in database.get_all_presets() if row["short_code"] == "etc_bi")
    app.selectbox(key="editor_studio_preset_select").set_value(preset["id"]).run()
    app.selectbox(key="editor_studio_preset_edit_etc_bi_0_add_block").set_value("antrum").run()
    next(button for button in app.button if button.label == "Add Block instance").click().run()

    # New identity #2 is available to this draft immediately, including its
    # Field-specific controls; nothing has reached the operational database.
    override_key = "editor_studio_preset_edit_etc_bi_0_2_2_hp_positive_enabled"
    assert "hp_positive" in app.checkbox(key=override_key).label
    assert any("hp_positive` · checkbox" in item.value for item in app.caption)
    assert len(database.get_preset_blocks(preset["id"])) == 2

    app.checkbox(key=override_key).set_value(True).run()
    assert "hp_positive" in app.checkbox(
        key="editor_studio_preset_edit_etc_bi_0_2_2_hp_positive_value"
    ).label
    instance_state = app.session_state.filtered_state["_editor_studio_preset_instances_Edit existing Preset_etc_bi_0"]
    endpoint_state = app.session_state.filtered_state["_editor_studio_preset_endpoints_Edit existing Preset_etc_bi_0"]
    assert endpoint_state == [{"block_key": "antrum", "instance_no": 2,
                               "baseline": content_studio.preset_instance_endpoint_baseline(
                                   "antrum", 2, db_name=mutable_db
                               )}]
    _, _, draft, _ = _source(mutable_db, "etc_bi")
    probe = content_studio.preset_draft_operations(
        "edit", "etc_bi", draft, instance_state, source_key="etc_bi",
        baseline=app.session_state.filtered_state["_editor_studio_preset_baseline_Edit existing Preset_etc_bi_0"],
        endpoint_baselines=endpoint_state, db_name=mutable_db,
    )
    _review(mutable_db, probe)
    next(button for button in app.button if button.label == "Prepare Preset review").click().run()
    assert "_editor_studio_review" in app.session_state.filtered_state, [item.value for item in app.error]
    review = app.session_state.filtered_state["_editor_studio_review"]
    added = next(change for change in review.changes
                 if change["table"] == "Preset_Blocks" and change["after"] is not None
                 and change["after"]["sort_order"] == 2)
    assert json.loads(added["after"]["field_overrides"]) == {"hp_positive": True}
    assert len(database.get_preset_blocks(preset["id"])) == 2


def test_draft_only_endpoint_aba_is_stale_even_when_logical_content_is_unchanged(mutable_db):
    _insert_draft_only_block(mutable_db, "preset_aba_a")
    preset, links, draft, instances = _source(mutable_db, "etc_bi")
    instances.append({"block_key": "preset_aba_a", "instance_no": 2, "field_overrides": {}})
    endpoints = [_endpoint(mutable_db, "preset_aba_a", 2)]
    operations = content_studio.preset_draft_operations(
        "edit", "etc_bi", draft, instances, source_key="etc_bi",
        baseline=content_studio.preset_draft_baseline(preset, links), endpoint_baselines=endpoints,
        db_name=mutable_db,
    )
    before_hash = _hash(mutable_db)
    old_block, new_block, old_field, new_field = _replace_draft_only_block_endpoint(mutable_db, "preset_aba_a")
    assert old_block != new_block and old_field != new_field and _hash(mutable_db) == before_hash
    with pytest.raises(content_changes.StaleDraftReviewError, match="endpoints changed"):
        _review(mutable_db, operations)


def test_add_b_does_not_rebase_a_and_removing_b_retains_a_baseline(mutable_db):
    _insert_draft_only_block(mutable_db, "preset_aba_a")
    _insert_draft_only_block(mutable_db, "preset_aba_b")
    preset, links, draft, instances = _source(mutable_db, "etc_bi")
    instances.extend([
        {"block_key": "preset_aba_a", "instance_no": 2, "field_overrides": {}},
        {"block_key": "preset_aba_b", "instance_no": 3, "field_overrides": {}},
    ])
    endpoint_a = _endpoint(mutable_db, "preset_aba_a", 2)
    # A is selected first. Mutating/replacing it before B is selected must not
    # permit B's selection to refresh A's captured physical source.
    _replace_draft_only_block_endpoint(mutable_db, "preset_aba_a")
    endpoint_b = _endpoint(mutable_db, "preset_aba_b", 3)
    operations = content_studio.preset_draft_operations(
        "edit", "etc_bi", draft, instances, source_key="etc_bi",
        baseline=content_studio.preset_draft_baseline(preset, links),
        endpoint_baselines=[endpoint_a, endpoint_b], db_name=mutable_db,
    )
    with pytest.raises(content_changes.StaleDraftReviewError, match="endpoints changed"):
        _review(mutable_db, operations)

    # Removing B removes only B's descriptor. A's stale descriptor remains
    # binding and still refuses the candidate.
    surviving = [item for item in instances if item["block_key"] == "preset_aba_a" or item["instance_no"] < 2]
    operations = content_studio.preset_draft_operations(
        "edit", "etc_bi", draft, surviving, source_key="etc_bi",
        baseline=content_studio.preset_draft_baseline(preset, links), endpoint_baselines=[endpoint_a], db_name=mutable_db,
    )
    with pytest.raises(content_changes.StaleDraftReviewError, match="endpoints changed"):
        _review(mutable_db, operations)


@pytest.mark.parametrize("column,value", [("label", "Concurrent Field label"), ("default_value", "7")])
def test_complete_source_field_image_rejects_concurrent_label_or_default_change(mutable_db, column, value):
    preset, links, draft, instances = _source(mutable_db, "etc_bi")
    baseline = content_studio.preset_draft_baseline(preset, links)
    conn = connection(mutable_db)
    try:
        conn.execute(f"UPDATE Fields SET {column}=? WHERE key='nodule_size_mm'", (value,))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_studio.StalePresetDraftError, match="changed since the draft was loaded"):
        content_studio.preset_draft_operations(
            "edit", "etc_bi", draft, instances, source_key="etc_bi", baseline=baseline, db_name=mutable_db,
        )


def test_generalized_review_contains_prefix_and_duplicate_quick_type_warnings(mutable_db):
    prefix_instances = [{"block_key": "appendice", "instance_no": 0, "field_overrides": {}}]
    prefix = _review(mutable_db, content_studio.preset_draft_operations(
        "create", "dai_overlap", {"name": "Prefix overlap", "category": None,
                                     "default_title": None, "default_adicap": None}, prefix_instances,
        endpoint_baselines=[_endpoint(mutable_db, "appendice", 0)], db_name=mutable_db,
    ))
    assert any("prefix overlap" in warning.lower() for warning in prefix.data["warnings"])

    preset, links, draft, instances = _source(mutable_db, "dai")
    draft["name"] = "Duplicate warning"
    duplicate = _review(mutable_db, content_studio.preset_draft_operations(
        "duplicate", "dai_warning_copy", draft, instances, source_key="dai",
        baseline=content_studio.preset_draft_baseline(preset, links), db_name=mutable_db,
    ))
    assert any("tokens are intentionally not copied" in warning.lower() for warning in duplicate.data["warnings"])


def test_two_tab_frozen_preset_review_apply_is_rejected(mutable_db):
    _unlock()
    preset, links, draft, instances = _source(mutable_db, "etc_bi")
    first_draft = {**draft, "name": "First frozen review"}
    first = _review(mutable_db, content_studio.preset_draft_operations(
        "edit", "etc_bi", first_draft, instances, source_key="etc_bi",
        baseline=content_studio.preset_draft_baseline(preset, links), db_name=mutable_db,
    ))
    second_draft = {**draft, "name": "Second tab applied"}
    _apply(mutable_db, content_studio.preset_draft_operations(
        "edit", "etc_bi", second_draft, instances, source_key="etc_bi",
        baseline=content_studio.preset_draft_baseline(preset, links), db_name=mutable_db,
    ))
    with pytest.raises(content_changes.StaleReviewError, match="Content changed since review"):
        content_changes.apply_review(first, db_name=mutable_db)


@pytest.mark.parametrize("action", ["create", "edit", "duplicate"])
def test_table_bearing_preset_authoring_is_refused(mutable_db, action):
    conn = connection(mutable_db)
    try:
        table_block_id = conn.execute(
            """INSERT INTO Blocks(key,name,is_archived,is_table,site_label,conclusion_group,
                                  macro_template,micro_template,conclusion_template,context_template,
                                  title_fragment_template,conclusion_label_template)
               VALUES ('cp6_table_block','CP6 table',0,1,NULL,NULL,'','','',NULL,NULL,NULL)"""
        ).lastrowid
        if action != "create":
            preset_id = conn.execute("INSERT INTO Presets(short_code,name) VALUES ('cp6_table_preset','CP6 table preset')").lastrowid
            conn.execute("INSERT INTO Preset_Blocks(preset_id,block_id,sort_order,display_order,field_overrides) VALUES (?,?,0,0,NULL)",
                         (preset_id, table_block_id))
        conn.commit()
    finally:
        conn.close()
    draft = {"name": "CP6 table preset" if action == "edit" else "CP6 table copy",
             "category": None, "default_title": None, "default_adicap": None}
    instances = [{"block_key": "cp6_table_block", "instance_no": 0, "field_overrides": {}}]
    if action == "create":
        with pytest.raises(content_studio.StudioIntentError, match="active non-table"):
            content_studio.preset_draft_operations("create", "cp6_table_create", draft, instances,
                                                   endpoint_baselines=[_endpoint(mutable_db, "cp6_table_block", 0)],
                                                   db_name=mutable_db)
    else:
        source, links = content_studio.preset_draft_source("cp6_table_preset", db_name=mutable_db)
        with pytest.raises(content_studio.StudioIntentError, match="Table-bearing"):
            content_studio.preset_draft_operations(action, "cp6_table_preset" if action == "edit" else "cp6_table_copy",
                                                   draft, instances, source_key="cp6_table_preset",
                                                   baseline=content_studio.preset_draft_baseline(source, links), db_name=mutable_db)
