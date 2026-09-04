"""Stage 3 direct-content editing safety tests; all writes use mutable_db."""

import json

import pytest

import content_editing
import database


def _row(table, key, value):
    return content_editing.get_editable_entity(table, value)


def _unlock_editor():
    content_editing.record_initial_snapshot("a" * 64)


def _preset(code):
    return next(row for row in database.get_all_presets() if row["short_code"] == code)


def _case_data(preset_id):
    blocks = database.get_preset_blocks(preset_id)
    return {
        "block_instances": [{"block_id": block["block_id"], "instance_no": block["sort_order"]} for block in blocks],
        "blocks": {}, "wildcard_notes": [], "master_lock": False,
    }


def test_initial_snapshot_is_a_persistent_prerequisite(mutable_db):
    block = _row("Blocks", "key", "appendice")
    with pytest.raises(content_editing.ContentEditError, match="initial content snapshot"):
        content_editing.save_edit("Blocks", "appendice", {"micro_template": block["micro_template"] + " x"}, block["row_hash"])

    _unlock_editor()
    assert content_editing.initial_snapshot_status()["initial_snapshot_hash"] == "a" * 64
    content_editing.save_edit("Blocks", "appendice", {"micro_template": block["micro_template"] + " x"}, block["row_hash"])


def test_allowlists_and_required_invariants_fail_closed(mutable_db):
    _unlock_editor()
    block = _row("Blocks", "key", "appendice")
    with pytest.raises(content_editing.ContentEditError, match="cannot edit"):
        content_editing.save_edit("Blocks", "appendice", {"name": "No"}, block["row_hash"])
    with pytest.raises(content_editing.ContentEditError, match="macro"):
        content_editing.save_edit("Blocks", "appendice", {"macro_template": ""}, block["row_hash"])

    field = _row("Fields", "key", "appendicite_type")
    with pytest.raises(content_editing.ContentEditError, match="one of the existing options"):
        content_editing.save_edit("Fields", "appendicite_type", {"default_value": "not-an-option"}, field["row_hash"])
    with pytest.raises(content_editing.ContentEditError, match="cannot edit"):
        content_editing.save_edit("Fields", "appendicite_type", {"type": "text"}, field["row_hash"])

    with pytest.raises(content_editing.ContentEditError, match="cannot be cleared"):
        content_editing.save_edit("Fields", "appendicite_type", {"default_value": ""}, field["row_hash"])
    number = _row("Fields", "key", "fragments")
    with pytest.raises(content_editing.ContentEditError, match="nonnegative"):
        content_editing.save_edit("Fields", "fragments", {"default_value": "-1"}, number["row_hash"])


def test_invalid_jinja_unknown_variable_and_missing_snippet_rollback(mutable_db):
    _unlock_editor()
    block = _row("Blocks", "key", "appendice")
    for template, error in [
        ("{% if", "Invalid Jinja"),
        ("{{ definitely_unknown }}", "unknown variable"),
        ("{{ snippet('not_here') }}", "unresolved snippet"),
    ]:
        with pytest.raises(content_editing.ContentEditError, match=error):
            content_editing.save_edit("Blocks", "appendice", {"micro_template": template}, block["row_hash"])
        assert _row("Blocks", "key", "appendice")["micro_template"] == block["micro_template"]


def test_editable_jinja_is_sandboxed_and_has_no_builtin_call_surface(mutable_db):
    _unlock_editor()
    block = _row("Blocks", "key", "appendice")
    for template, error in [
        ("{{ ''.__class__.__mro__ }}", "unsafe"),
        ("{{ snippet.__globals__.db.DB_NAME }}", "snippet may only"),
        ("{{ range(3)|list }}", "unknown variable"),
    ]:
        with pytest.raises(content_editing.ContentEditError, match=error):
            content_editing.save_edit(
                "Blocks", "appendice", {"micro_template": template}, block["row_hash"],
            )
        assert _row("Blocks", "key", "appendice")["micro_template"] == block["micro_template"]


def test_candidate_validation_renders_the_candidate_connection_not_stale_operational_state(mutable_db):
    _unlock_editor()
    snippet = _row("Snippets", "shortcut", "absence_malignite")
    result = content_editing.save_edit(
        "Snippets", "absence_malignite", {"expansion": "CANDIDATE STATE"}, snippet["row_hash"], "candidate probe"
    )
    assert result["revision_id"]
    assert database.get_snippet_by_shortcut("absence_malignite")["expansion"] == "CANDIDATE STATE"


def test_edit_audits_atomically_and_rejects_stale_concurrent_hash(mutable_db):
    _unlock_editor()
    preset = _row("Presets", "short_code", "dai")
    first = content_editing.save_edit("Presets", "dai", {"name": "Appendice corrigé"}, preset["row_hash"])
    conn = database.get_db_connection()
    changes = conn.execute("SELECT * FROM Content_Changes WHERE revision_id = ?", (first["revision_id"],)).fetchall()
    conn.close()
    assert len(changes) == 1 and changes[0]["before_hash"] == preset["row_hash"]
    with pytest.raises(content_editing.ContentEditError, match="another tab"):
        content_editing.save_edit("Presets", "dai", {"name": "stale"}, preset["row_hash"])

    conn = database.get_db_connection()
    conn.executescript("""
        CREATE TRIGGER fail_content_change BEFORE INSERT ON Content_Changes
        BEGIN SELECT RAISE(ABORT, 'audit failure'); END;
    """)
    conn.close()
    current = _row("Presets", "short_code", "dai")
    with pytest.raises(content_editing.ContentEditError, match="audit failure"):
        content_editing.save_edit("Presets", "dai", {"name": "must rollback"}, current["row_hash"])
    assert _row("Presets", "short_code", "dai")["name"] == "Appendice corrigé"


def test_safe_and_refused_revision_reverts(mutable_db):
    _unlock_editor()
    snippet = _row("Snippets", "shortcut", "absence_malignite")
    revision = content_editing.save_edit(
        "Snippets", "absence_malignite", {"expansion": "changed once"}, snippet["row_hash"]
    )["revision_id"]
    reverted = content_editing.revert_revision(revision)
    assert reverted > revision
    assert _row("Snippets", "shortcut", "absence_malignite")["expansion"] == snippet["expansion"]

    current = _row("Snippets", "shortcut", "absence_malignite")
    revision = content_editing.save_edit(
        "Snippets", "absence_malignite", {"expansion": "first later"}, current["row_hash"]
    )["revision_id"]
    current = _row("Snippets", "shortcut", "absence_malignite")
    content_editing.save_edit("Snippets", "absence_malignite", {"expansion": "second later"}, current["row_hash"])
    with pytest.raises(content_editing.ContentEditError, match="Revert refused"):
        content_editing.revert_revision(revision)


def test_reverting_a_reverted_snippet_creation_restores_it(mutable_db):
    _unlock_editor()
    created = content_editing.create_snippet("revert_creation_probe", "restorable")
    deletion_revision = content_editing.revert_revision(created["revision_id"])
    assert _row("Snippets", "shortcut", "revert_creation_probe") is None

    restoration_revision = content_editing.revert_revision(deletion_revision)
    assert restoration_revision > deletion_revision
    assert _row("Snippets", "shortcut", "revert_creation_probe")["expansion"] == "restorable"


def test_validation_covers_macros_grouping_addenda_pending_cases_and_discrete_branches(mutable_db):
    _unlock_editor()
    gastric = _preset("gt")
    data = _case_data(gastric["id"])
    before_fingerprint = database.compute_case_content_fingerprint(gastric["id"], data)
    assert database.save_case("EDITOR-PENDING-1", gastric["id"], "", data, "<p>draft</p>")
    field = _row("Fields", "key", "hp_positive")
    # Its addendum is shared across gastric Blocks; successful save proves
    # candidate default + saved pending contexts (including grouping/addenda)
    # render together under the transaction.
    content_editing.save_edit(
        "Fields", "hp_positive",
        {"conclusion_addendum_template": "HP {{ value }}"}, field["row_hash"],
    )
    assert database.compute_case_content_fingerprint(gastric["id"], data) != before_fingerprint
    checkbox = _row("Fields", "key", "false_membranes")
    content_editing.save_edit("Fields", "false_membranes", {"default_value": "1"}, checkbox["row_hash"])
    assert database.get_case_by_number("EDITOR-PENDING-1")["status"] == "pending"


def test_checkbox_and_select_nondefault_branches_are_rendered_before_commit(mutable_db):
    _unlock_editor()
    block = _row("Blocks", "key", "appendice")
    # Appendix defaults leave false_membranes false, so the default Preset
    # render alone would not encounter this division-by-zero branch.
    with pytest.raises(content_editing.ContentEditError, match="division"):
        content_editing.save_edit(
            "Blocks", "appendice",
            {"micro_template": "{% if false_membranes %}{{ 1 / 0 }}{% else %}safe{% endif %}"},
            block["row_hash"],
        )
    select = _row("Fields", "key", "appendicite_type")
    options = json.loads(select["options"])
    other = next(value for value in options if value != select["default_value"])
    with pytest.raises(content_editing.ContentEditError, match="division"):
        content_editing.save_edit(
            "Blocks", "appendice",
            {"micro_template": f"{{% if appendicite_type == '{other}' %}}{{{{ 1 / 0 }}}}{{% else %}}safe{{% endif %}}"},
            block["row_hash"],
        )
    assert _row("Blocks", "key", "appendice")["micro_template"] == block["micro_template"]

    addendum = _row("Fields", "key", "hp_positive")
    with pytest.raises(content_editing.ContentEditError, match="division"):
        content_editing.save_edit(
            "Fields", "hp_positive",
            {"conclusion_addendum_template": "{% if value %}safe{% else %}{{ 1 / 0 }}{% endif %}"},
            addendum["row_hash"],
        )


def test_new_snippet_and_optional_columns_are_supported_but_no_other_creation_is(mutable_db):
    _unlock_editor()
    created = content_editing.create_snippet("stage3_probe", "usable expansion", "Test")
    assert created["row"]["shortcut"] == "stage3_probe"
    with pytest.raises(content_editing.ContentEditError, match="only creates Snippets"):
        content_editing._check_changes("Blocks", {"micro_template": "x"}, creating=True)
    with pytest.raises(content_editing.ContentEditError, match="cannot be blank"):
        content_editing.create_snippet("blank_probe", "   ")


def test_field_addendum_snippets_drive_impact_and_pending_fingerprints(mutable_db):
    _unlock_editor()
    field = _row("Fields", "key", "hp_positive")
    content_editing.save_edit(
        "Fields", "hp_positive",
        {"conclusion_addendum_template": "{{ snippet('absence_malignite') }}"},
        field["row_hash"],
    )
    gastric = _preset("gt")
    data = _case_data(gastric["id"])
    assert database.save_case("ADDENDUM-SNIPPET-1", gastric["id"], "", data, "<p>draft</p>")
    before = database.compute_case_content_fingerprint(gastric["id"], data)
    usage = database.get_snippet_usage("absence_malignite")
    assert usage["pending_case_count"] == 1
    assert [field["key"] for field in usage["fields"]] == ["hp_positive"]

    snippet = _row("Snippets", "shortcut", "absence_malignite")
    content_editing.save_edit(
        "Snippets", "absence_malignite", {"expansion": "Changed addendum phrase"},
        snippet["row_hash"],
    )
    assert database.compute_case_content_fingerprint(gastric["id"], data) != before


def test_table_blocks_are_rejected_by_the_stage3_service(mutable_db):
    _unlock_editor()
    conn = database.get_db_connection()
    conn.execute("UPDATE Blocks SET is_table = 1 WHERE key = 'appendice'")
    conn.commit()
    conn.close()
    block = _row("Blocks", "key", "appendice")
    with pytest.raises(content_editing.ContentEditError, match="Table Blocks"):
        content_editing.save_edit(
            "Blocks", "appendice", {"micro_template": block["micro_template"] + " x"},
            block["row_hash"],
        )


def test_candidate_preview_is_rollback_only_and_identifies_affected_presets(mutable_db):
    _unlock_editor()
    block = _row("Blocks", "key", "appendice")
    candidate = block["micro_template"] + " PREVIEW-ONLY"
    result = content_editing.preview_edit(
        "Blocks", "appendice", {"micro_template": candidate}, block["row_hash"],
    )
    assert [preview["label"] for preview in result["previews"]] == ["Appendice (dai)"]
    assert "PREVIEW-ONLY" in result["previews"][0]["after"]["micro_plain"]
    assert "PREVIEW-ONLY" not in result["previews"][0]["before"]["micro_plain"]
    assert _row("Blocks", "key", "appendice")["micro_template"] == block["micro_template"]


def test_direct_edit_can_repair_an_invalid_current_template(mutable_db):
    _unlock_editor()
    conn = database.get_db_connection()
    conn.execute("UPDATE Blocks SET micro_template = '{{ 1 / 0 }}' WHERE key = 'appendice'")
    conn.commit()
    conn.close()
    broken = _row("Blocks", "key", "appendice")
    content_editing.save_edit(
        "Blocks", "appendice", {"micro_template": "Repaired microscopy."}, broken["row_hash"],
    )
    assert _row("Blocks", "key", "appendice")["micro_template"] == "Repaired microscopy."


def test_recent_revisions_identify_entity_and_columns(mutable_db):
    _unlock_editor()
    preset = _row("Presets", "short_code", "dai")
    result = content_editing.save_edit(
        "Presets", "dai", {"name": "Appendice identifiable"}, preset["row_hash"],
    )
    revision = next(row for row in content_editing.recent_revisions() if row["id"] == result["revision_id"])
    assert revision["summary"] == "Presets.dai: name"
    assert revision["details"] == "Presets.dai — name"
