"""Stage 6 checkpoint 2: internal generalized candidate transactions."""

import json
import sqlite3

import pytest

import content_changes
import content_editing
import content_snapshot
import content_studio
import database
from test_stage5_packages import connection, save_synthetic_case


def snapshot_hash(path):
    return content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(path))


def unlock():
    content_editing.record_initial_snapshot("a" * 64)


def row(path, sql, values=()):
    conn = connection(path)
    try:
        found = conn.execute(sql, values).fetchone()
        return dict(found) if found else None
    finally:
        conn.close()


def relevant_state(path):
    """The content/configuration/Case-reference state an inverse may touch."""
    tables = (list(content_snapshot.BASE_TABLES) + list(content_snapshot.RELATION_TABLES)
              + ["Cases", "Content_Revisions", "Content_Changes",
                 "Case_Content_Reference_Changes", "sqlite_sequence"])
    conn = connection(path)
    try:
        return {table: [dict(found) for found in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                for table in tables}
    finally:
        conn.close()


def freeze_case_preset_identity(path, case):
    """Synthetic SQL inserts bypass the production Case snapshot writer."""
    conn = connection(path)
    try:
        conn.execute("""UPDATE Cases
                        SET preset_short_code_snapshot=(SELECT short_code FROM Presets WHERE id=Cases.preset_id),
                            preset_name_snapshot=(SELECT name FROM Presets WHERE id=Cases.preset_id)
                        WHERE id=?""", (case["id"],))
        conn.commit()
        return dict(conn.execute("SELECT * FROM Cases WHERE id=?", (case["id"],)).fetchone())
    finally:
        conn.close()


def review(path, intents, summary="Content Studio test"):
    return content_studio.review(intents, snapshot_hash(path), summary=summary, db_name=path)


def preset_deletion_intents(path, case, *, include_case=True):
    conn = connection(path)
    try:
        preset = dict(conn.execute("SELECT * FROM Presets WHERE id=?", (case["preset_id"],)).fetchone())
        links = [dict(link) for link in conn.execute("""SELECT b.key,pb.sort_order FROM Preset_Blocks pb
                                                        JOIN Blocks b ON b.id=pb.block_id WHERE pb.preset_id=?""",
                                                     (preset["id"],))]
        tokens = [dict(token) for token in conn.execute("SELECT sort_order FROM Quick_Type_Tokens WHERE preset_id=?", (preset["id"],))]
    finally:
        conn.close()
    intents = ([content_studio.case_preset_reference(case["id"], preset["id"], None)] if include_case else [])
    intents.extend(content_studio.operation("unlink", "Preset_Blocks", {
        "preset_code": preset["short_code"], "block_key": link["key"], "sort_order": link["sort_order"],
    }) for link in links)
    intents.extend(content_studio.operation("unlink", "Quick_Type_Tokens", {
        "preset_code": preset["short_code"], "sort_order": token["sort_order"],
    }) for token in tokens)
    intents.append(content_studio.operation("delete", "Presets", preset["short_code"]))
    return preset, intents


def test_archive_restore_and_revert_preserve_physical_row(mutable_db):
    unlock()
    before = row(mutable_db, "SELECT * FROM Snippets WHERE shortcut='absence_malignite'")
    archived = review(mutable_db, [content_studio.operation("archive", "Snippets", "absence_malignite")])
    snippet_change = next(change for change in archived.changes
                          if change["table"] == "Snippets" and change["key"] == "absence_malignite")
    assert snippet_change["before"] == before
    revision = content_changes.apply_review(archived, db_name=mutable_db)
    assert row(mutable_db, "SELECT is_archived FROM Snippets WHERE shortcut='absence_malignite'")["is_archived"] == 1

    inverse = content_changes.review_inverse(revision, db_name=mutable_db)
    undo = content_changes.apply_review(inverse, db_name=mutable_db)
    assert row(mutable_db, "SELECT * FROM Snippets WHERE shortcut='absence_malignite'") == before
    redo = content_changes.apply_review(content_changes.review_inverse(undo, db_name=mutable_db), db_name=mutable_db)
    assert redo > undo > revision


def test_relationship_reorder_audits_display_order_and_restores_it(mutable_db):
    unlock()
    links = []
    conn = connection(mutable_db)
    try:
        for link in conn.execute("""SELECT p.short_code,b.key,pb.sort_order,pb.display_order
                                    FROM Preset_Blocks pb JOIN Presets p ON p.id=pb.preset_id
                                    JOIN Blocks b ON b.id=pb.block_id WHERE p.short_code='gt'
                                    ORDER BY pb.display_order"""):
            links.append(dict(link))
    finally:
        conn.close()
    assert len(links) >= 2
    intent = content_studio.operation("reorder", "Preset_Blocks", {
        "preset_code": links[0]["short_code"], "block_key": links[0]["key"], "sort_order": links[0]["sort_order"],
    }, {"display_order": links[1]["display_order"]})
    swapped = content_studio.operation("reorder", "Preset_Blocks", {
        "preset_code": links[1]["short_code"], "block_key": links[1]["key"], "sort_order": links[1]["sort_order"],
    }, {"display_order": links[0]["display_order"]})
    prepared = review(mutable_db, [intent, swapped])
    revision = content_changes.apply_review(prepared, db_name=mutable_db)
    changed = row(mutable_db, """SELECT pb.* FROM Preset_Blocks pb JOIN Presets p ON p.id=pb.preset_id
                                  JOIN Blocks b ON b.id=pb.block_id
                                  WHERE p.short_code=? AND b.key=? AND pb.sort_order=?""",
                  (links[0]["short_code"], links[0]["key"], links[0]["sort_order"]))
    assert changed["sort_order"] == links[0]["sort_order"] and changed["display_order"] == links[1]["display_order"]
    content_changes.apply_review(content_changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
    assert row(mutable_db, """SELECT display_order FROM Preset_Blocks pb JOIN Presets p ON p.id=pb.preset_id
                              JOIN Blocks b ON b.id=pb.block_id WHERE p.short_code=? AND b.key=? AND pb.sort_order=?""",
               (links[0]["short_code"], links[0]["key"], links[0]["sort_order"]))["display_order"] == links[0]["display_order"]


def test_generalized_validation_accepts_unique_legacy_high_instance_positions(mutable_db):
    """Stage 5 packages use sparse positions; they are not dense list indexes."""
    unlock()
    conn = connection(mutable_db)
    try:
        conn.execute("""UPDATE Preset_Blocks SET sort_order=999, display_order=999
                        WHERE preset_id=(SELECT id FROM Presets WHERE short_code='gt')
                          AND sort_order=0""")
        conn.commit()
    finally:
        conn.close()
    prepared = review(mutable_db, [content_studio.operation("archive", "Snippets", "absence_malignite")])
    assert prepared.changes[0]["after"]["is_archived"] == 1


def test_general_configuration_accepts_table_row_with_nullable_preset_overrides(mutable_db):
    """Ownership comes from Preset/Block links, not an optional JSON payload."""
    conn = connection(mutable_db)
    try:
        conn.execute("INSERT INTO Presets(short_code,name) VALUES ('nullable_table_owner','Nullable owner')")
        preset_id = conn.execute("SELECT id FROM Presets WHERE short_code='nullable_table_owner'").fetchone()[0]
        conn.execute("""INSERT INTO Blocks(key,name,is_table,micro_template,conclusion_template)
                        VALUES ('nullable_table_block','Nullable table',1,'','')""")
        block_id = conn.execute("SELECT id FROM Blocks WHERE key='nullable_table_block'").fetchone()[0]
        conn.execute("""INSERT INTO Preset_Blocks(preset_id,block_id,sort_order,display_order,field_overrides)
                        VALUES (?,?,?,?,NULL)""", (preset_id, block_id, 17, 999))
        conn.execute("""INSERT INTO Preset_Block_Rows(preset_id,block_id,sort_order,field_overrides)
                        VALUES (?,?,?,NULL)""", (preset_id, block_id, 23))
        content_changes._validate_general_configuration(conn)
    finally:
        conn.close()


def test_final_graph_rejects_active_standalone_block_with_archived_snippet(mutable_db):
    """An orphan Block is still new work and cannot resolve archived content."""
    unlock()
    content_changes.apply_review(review(mutable_db, [
        content_studio.operation("create", "Snippets", "orphan_archived_snippet", {
            "expansion": "Archived standalone phrase", "category": None,
        }),
    ]), db_name=mutable_db)
    content_changes.apply_review(review(mutable_db, [
        content_studio.operation("archive", "Snippets", "orphan_archived_snippet"),
    ]), db_name=mutable_db)

    with pytest.raises(content_changes.ChangeError, match="active Block cannot resolve archived"):
        review(mutable_db, [
            content_studio.operation("create", "Blocks", "orphan_archived_snippet_block", {
                "name": "Orphan archived snippet", "macro_template": "Macro.",
                "micro_template": "{{ snippet('orphan_archived_snippet') }}",
                "conclusion_template": "Conclusion.", "context_template": None,
                "title_fragment_template": None, "conclusion_label_template": None,
            }),
        ])


def test_final_graph_rejects_active_standalone_block_with_archived_field(mutable_db):
    """Field availability is checked even when no active Preset reaches the Block."""
    unlock()
    content_changes.apply_review(review(mutable_db, [
        content_studio.operation("create", "Fields", "orphan_archived_field", {
            "label": "Archived orphan Field", "type": "text", "options": None,
            "default_value": "", "conclusion_addendum_template": None,
        }),
    ]), db_name=mutable_db)
    content_changes.apply_review(review(mutable_db, [
        content_studio.operation("archive", "Fields", "orphan_archived_field"),
    ]), db_name=mutable_db)

    with pytest.raises(content_changes.ChangeError, match="active Block cannot resolve archived"):
        review(mutable_db, [
            content_studio.operation("create", "Blocks", "orphan_archived_field_block", {
                "name": "Orphan archived Field", "macro_template": "Macro.",
                "micro_template": "Micro.", "conclusion_template": "Conclusion.",
                "context_template": None, "title_fragment_template": None,
                "conclusion_label_template": None,
            }),
            content_studio.operation("link", "Block_Fields", {
                "block_key": "orphan_archived_field_block", "field_key": "orphan_archived_field",
            }, {"sort_order": 0, "label_override": None, "default_override": None, "context_section": False}),
        ])


def test_standalone_validated_preset_detach_is_refused(mutable_db):
    unlock()
    case = save_synthetic_case(mutable_db, number="DETACH", status="validated")
    with pytest.raises(content_changes.ChangeError, match="Preset deletion"):
        review(mutable_db, [content_studio.case_preset_reference(case["id"], case["preset_id"], None)])
    assert row(mutable_db, "SELECT preset_id FROM Cases WHERE id=?", (case["id"],))["preset_id"] == case["preset_id"]


def test_review_is_no_write_and_apply_rejects_stale_content_and_case_set(mutable_db):
    unlock()
    prepared = review(mutable_db, [content_studio.operation("archive", "Snippets", "absence_malignite")])
    before = row(mutable_db, "SELECT is_archived FROM Snippets WHERE shortcut='absence_malignite'")
    assert before["is_archived"] == 0
    conn = connection(mutable_db)
    conn.execute("INSERT INTO Content_Revisions(origin,summary) VALUES ('manual_edit','ABA')")
    conn.commit(); conn.close()
    with pytest.raises(content_changes.StaleReviewError):
        content_changes.apply_review(prepared, db_name=mutable_db)
    assert row(mutable_db, "SELECT is_archived FROM Snippets WHERE shortcut='absence_malignite'")["is_archived"] == 0


def test_generalized_audit_fault_rolls_back_content_and_reference(mutable_db, monkeypatch):
    unlock()
    prepared = review(mutable_db, [content_studio.operation("archive", "Snippets", "absence_malignite")])
    before = row(mutable_db, "SELECT * FROM Snippets WHERE shortcut='absence_malignite'")
    original = content_changes._record_generalized_changes
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("private canary")
    monkeypatch.setattr(content_changes, "_record_generalized_changes", fail)
    with pytest.raises(content_changes.ChangeError):
        content_changes.apply_review(prepared, db_name=mutable_db)
    assert row(mutable_db, "SELECT * FROM Snippets WHERE shortcut='absence_malignite'") == before


def test_create_and_inverse_cover_configuration_tables_with_ordinary_ownership(mutable_db):
    """The guided grammar covers rows Stage 7 will later author mechanically."""
    unlock()
    intents = [
        content_studio.operation("create", "Fields", "studio_note", {
            "label": "Studio note", "type": "text", "options": None,
            "default_value": "", "conclusion_addendum_template": None,
        }),
        content_studio.operation("create", "Presets", "studio_preset", {
            "name": "Studio preset", "category": None, "default_adicap": None, "default_title": None,
        }),
        content_studio.operation("create", "Blocks", "studio_block", {
            "name": "Studio block", "macro_template": "Macro.", "micro_template": "Micro.",
            "conclusion_template": "Conclusion.", "context_template": None,
            "title_fragment_template": None, "conclusion_label_template": None,
        }),
        content_studio.operation("link", "Block_Fields", {
            "block_key": "appendice", "field_key": "studio_note",
        }, {"sort_order": 99, "label_override": None, "default_override": None, "context_section": False}),
        content_studio.operation("link", "Preset_Blocks", {
            "preset_code": "studio_preset", "block_key": "appendice", "sort_order": 0,
        }, {"display_order": 0, "field_overrides": {}}),
        content_studio.operation("link", "Quick_Type_Tokens", {
            "preset_code": "dai", "sort_order": 99,
        }, {"block_sort_order": 0, "field_key": "false_membranes", "token_kind": "lookup",
            "lookup_table": {"z": False}, "digit_width": None}),
        content_studio.operation("link", "Field_Consistency_Rules", {
            "block_key": "appendice", "field_a_key": "false_membranes", "field_a_values": "[false]",
            "field_b_key": "appendicite_type", "field_b_values": '["phlegmoneuse"]',
            "message": "Synthetic consistency check.",
        }, {"field_a_key": "false_membranes", "field_a_values": [False],
            "field_b_key": "appendicite_type", "field_b_values": ["phlegmoneuse"],
            "message": "Synthetic consistency check."}),
        content_studio.operation("link", "Conclusion_Group_Labels", {"block_key_set": "appendice"},
                                 {"combined_label": "appendicielle"}),
    ]
    prepared = review(mutable_db, list(reversed(intents)))
    # Materialisation is canonical, not dependent on a form's widget order.
    assert prepared.operations == review(mutable_db, intents).operations
    revision = content_changes.apply_review(prepared, db_name=mutable_db)
    conn = connection(mutable_db)
    try:
        for table in set(content_snapshot.RELATION_TABLES) - {"Preset_Block_Rows"}:
            assert any(change["table"] == table for change in prepared.changes)
        assert conn.execute("SELECT COUNT(*) FROM Preset_Blocks WHERE preset_id=(SELECT id FROM Presets WHERE short_code='studio_preset')").fetchone()[0] == 1
    finally:
        conn.close()
    content_changes.apply_review(content_changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
    assert row(mutable_db, "SELECT * FROM Fields WHERE key='studio_note'") is None
    assert row(mutable_db, "SELECT * FROM Presets WHERE short_code='studio_preset'") is None
    assert row(mutable_db, "SELECT * FROM Blocks WHERE key='studio_block'") is None


def test_validated_preset_detachment_and_deletion_restore_ids_on_inverse(mutable_db):
    unlock()
    case = freeze_case_preset_identity(mutable_db, save_synthetic_case(mutable_db, number="DELETE-PRESET", status="validated"))
    conn = connection(mutable_db)
    try:
        preset = dict(conn.execute("SELECT * FROM Presets WHERE id=?", (case["preset_id"],)).fetchone())
        links = [dict(link) for link in conn.execute("""SELECT b.key,pb.sort_order FROM Preset_Blocks pb
                                                        JOIN Blocks b ON b.id=pb.block_id WHERE pb.preset_id=?""",
                                                     (preset["id"],))]
        tokens = [dict(token) for token in conn.execute("SELECT sort_order FROM Quick_Type_Tokens WHERE preset_id=?", (preset["id"],))]
    finally:
        conn.close()
    intents = [content_studio.case_preset_reference(case["id"], preset["id"], None)]
    intents.extend(content_studio.operation("unlink", "Preset_Blocks", {
        "preset_code": preset["short_code"], "block_key": link["key"], "sort_order": link["sort_order"],
    }) for link in links)
    intents.extend(content_studio.operation("unlink", "Quick_Type_Tokens", {
        "preset_code": preset["short_code"], "sort_order": token["sort_order"],
    }) for token in tokens)
    intents.append(content_studio.operation("delete", "Presets", preset["short_code"]))
    prepared = review(mutable_db, intents)
    revision = content_changes.apply_review(prepared, db_name=mutable_db)
    assert row(mutable_db, "SELECT * FROM Presets WHERE id=?", (preset["id"],)) is None
    assert row(mutable_db, "SELECT preset_id FROM Cases WHERE id=?", (case["id"],))["preset_id"] is None
    undo = content_changes.apply_review(content_changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
    assert row(mutable_db, "SELECT * FROM Presets WHERE id=?", (preset["id"],))["short_code"] == preset["short_code"]
    assert row(mutable_db, "SELECT preset_id FROM Cases WHERE id=?", (case["id"],))["preset_id"] == preset["id"]
    redo_review = content_changes.review_inverse(undo, db_name=mutable_db)
    assert any(ref["before_preset_id"] == preset["id"] and ref["after_preset_id"] is None
               for ref in redo_review.data["case_references"])
    assert any("unavailable for a future Return to pending" in warning for warning in redo_review.data["warnings"])
    content_changes.apply_review(redo_review, db_name=mutable_db)
    assert row(mutable_db, "SELECT * FROM Presets WHERE id=?", (preset["id"],)) is None
    assert row(mutable_db, "SELECT preset_id FROM Cases WHERE id=?", (case["id"],))["preset_id"] is None


def test_case_reference_phase_fault_rolls_back_inverse_content_and_detachment(mutable_db, monkeypatch):
    """A failure after inverse reattachment preparation is one atomic rollback."""
    unlock()
    case = freeze_case_preset_identity(
        mutable_db, save_synthetic_case(mutable_db, number="INVERSE-PHASE-ROLLBACK", status="validated")
    )
    _preset, intents = preset_deletion_intents(mutable_db, case)
    deletion = content_changes.apply_review(review(mutable_db, intents), db_name=mutable_db)
    inverse = content_changes.review_inverse(deletion, db_name=mutable_db)
    before = relevant_state(mutable_db)
    original = content_changes._general_apply_case_references

    def fail_after_reattach(conn, references, *, attaching, inverse=False):
        original(conn, references, attaching=attaching, inverse=inverse)
        if attaching and inverse:
            raise RuntimeError("inverse reattachment phase canary")

    monkeypatch.setattr(content_changes, "_general_apply_case_references", fail_after_reattach)
    with pytest.raises(content_changes.ChangeError, match="Apply failed"):
        content_changes.apply_review(inverse, db_name=mutable_db)
    assert relevant_state(mutable_db) == before


def test_block_deletion_warns_for_validated_reconstruction_loss_and_inverse_restores_it(mutable_db):
    """The warning compares complete before/candidate graphs, not entity type."""
    unlock()
    conn = connection(mutable_db)
    try:
        preset = dict(conn.execute("SELECT * FROM Presets WHERE short_code='gt'").fetchone())
        instances = [dict(row) for row in conn.execute(
            "SELECT block_id,sort_order AS instance_no FROM Preset_Blocks WHERE preset_id=? ORDER BY display_order",
            (preset["id"],),
        )]
    finally:
        conn.close()
    assert len(instances) > 1
    case = freeze_case_preset_identity(mutable_db, save_synthetic_case(
        mutable_db, code="gt", number="VALIDATED-MULTIBLOCK-BLOCK-DELETE", status="validated",
        structured={"block_instances": instances, "blocks": {}, "wildcard_notes": [], "master_lock": False},
    ))
    deletion = review(mutable_db, [content_studio.operation("delete", "Blocks", "antrum")])
    assert any("makes 1 currently reconstructable validated Case(s) unavailable for a future Return to pending"
               in warning for warning in deletion.data["warnings"])

    revision = content_changes.apply_review(deletion, db_name=mutable_db)
    assert row(mutable_db, "SELECT status,preset_id,rendered_html FROM Cases WHERE id=?", (case["id"],))["status"] == "validated"
    assert not database.return_case_to_pending("VALIDATED-MULTIBLOCK-BLOCK-DELETE", "must refuse")

    undo = content_changes.apply_review(content_changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
    conn = connection(mutable_db)
    try:
        assert content_changes._validated_reconstructability(conn)[case["id"]] is True
    finally:
        conn.close()
    redo = content_changes.review_inverse(undo, db_name=mutable_db)
    assert any("makes 1 currently reconstructable validated Case(s) unavailable for a future Return to pending"
               in warning for warning in redo.data["warnings"])


def _gt_explicit_instances(path):
    conn = connection(path)
    try:
        return [dict(found) for found in conn.execute(
            """SELECT pb.block_id,pb.sort_order AS instance_no
               FROM Preset_Blocks pb JOIN Presets p ON p.id=pb.preset_id
               WHERE p.short_code='gt' ORDER BY pb.display_order"""
        )]
    finally:
        conn.close()


def test_destructive_review_is_stale_when_affected_validated_case_arrives(mutable_db):
    """A later validated Case cannot receive a reconstruction loss omitted by review."""
    unlock()
    prepared = review(mutable_db, [content_studio.operation("delete", "Blocks", "antrum")])
    assert not any("Return to pending" in warning for warning in prepared.data["warnings"])

    case = save_synthetic_case(
        mutable_db, code="gt", number="VALIDATED-ARRIVED-AFTER-REVIEW", status="validated",
        structured={"block_instances": _gt_explicit_instances(mutable_db), "blocks": {}},
    )
    with pytest.raises(content_changes.StaleReviewError, match="Local state changed"):
        content_changes.apply_review(prepared, db_name=mutable_db)

    assert row(mutable_db, "SELECT status FROM Cases WHERE id=?", (case["id"],))["status"] == "validated"
    assert row(mutable_db, "SELECT id FROM Blocks WHERE key='antrum'") is not None


@pytest.mark.parametrize("column,value", [
    ("structured_input", '{"block_instances":[],"blocks":{}}'),
    ("clinical_info", "Changed after the signed warning"),
    ("preset_id", None),
    ("case_number", "VALIDATED-RENAMED-AFTER-REVIEW"),
    ("status", "pending"),
])
def test_destructive_review_binds_validated_case_reconstruction_state(
        mutable_db, column, value):
    unlock()
    case = save_synthetic_case(
        mutable_db, code="gt", number="VALIDATED-INPUT-RACE", status="validated",
        structured={"block_instances": _gt_explicit_instances(mutable_db), "blocks": {}},
    )
    prepared = review(mutable_db, [content_studio.operation("delete", "Blocks", "antrum")])
    assert any("Return to pending" in warning for warning in prepared.data["warnings"])

    conn = connection(mutable_db)
    conn.execute(f"UPDATE Cases SET {column}=? WHERE id=?", (value, case["id"]))
    conn.commit()
    conn.close()
    with pytest.raises(content_changes.StaleReviewError, match="Local state changed"):
        content_changes.apply_review(prepared, db_name=mutable_db)

    assert row(mutable_db, "SELECT id FROM Blocks WHERE key='antrum'") is not None


def test_reviewed_destructive_inverse_is_stale_when_validated_case_arrives(mutable_db):
    unlock()
    deletion = content_changes.apply_review(
        review(mutable_db, [content_studio.operation("delete", "Blocks", "antrum")]),
        db_name=mutable_db,
    )
    restoration = content_changes.apply_review(
        content_changes.review_inverse(deletion, db_name=mutable_db), db_name=mutable_db,
    )
    destructive_inverse = content_changes.review_inverse(restoration, db_name=mutable_db)
    assert not any("Return to pending" in warning for warning in destructive_inverse.data["warnings"])

    save_synthetic_case(
        mutable_db, code="gt", number="VALIDATED-INVERSE-RACE", status="validated",
        structured={"block_instances": _gt_explicit_instances(mutable_db), "blocks": {}},
    )
    with pytest.raises(content_changes.StaleReviewError, match="Local state changed"):
        content_changes.apply_review(destructive_inverse, db_name=mutable_db)

    assert row(mutable_db, "SELECT id FROM Blocks WHERE key='antrum'") is not None


def test_unchanged_validated_case_state_allows_destructive_apply(mutable_db):
    unlock()
    save_synthetic_case(
        mutable_db, code="gt", number="VALIDATED-UNCHANGED-REVIEW", status="validated",
        structured={"block_instances": _gt_explicit_instances(mutable_db), "blocks": {}},
    )
    prepared = review(mutable_db, [content_studio.operation("delete", "Blocks", "antrum")])
    assert any("Return to pending" in warning for warning in prepared.data["warnings"])

    content_changes.apply_review(prepared, db_name=mutable_db)
    assert row(mutable_db, "SELECT id FROM Blocks WHERE key='antrum'") is None


def test_validated_reconstruction_warning_only_reports_true_to_false():
    warning = content_changes._validated_reconstruction_loss_warning
    assert warning({1: True}, {1: False})
    assert warning({1: False}, {1: True}) == []
    assert warning({1: False}, {1: False}) == []


def test_generalized_inverse_refuses_pending_dependency_and_reports_pending_impact(mutable_db):
    unlock()
    create = [
        content_studio.operation("create", "Snippets", "inverse_pending", {"expansion": "Pending phrase", "category": None}),
        content_studio.operation("update", "Blocks", "appendice", {"micro_template": "{{ snippet('inverse_pending') }}"}),
    ]
    created = content_changes.apply_review(review(mutable_db, create), db_name=mutable_db)
    save_synthetic_case(mutable_db, number="INVERSE-BLOCKER")
    with pytest.raises(content_changes.ChangeError, match="pending case depends"):
        content_changes.review_inverse(created, db_name=mutable_db)

    # An ordinary wording inverse keeps the pending Case renderable and must
    # expose the fingerprint/report impact instead of returning an empty list.
    wording = content_changes.apply_review(review(mutable_db, [
        content_studio.operation("update", "Blocks", "appendice", {"micro_template": "Changed wording."}),
    ]), db_name=mutable_db)
    inverse = content_changes.review_inverse(wording, db_name=mutable_db)
    assert len(inverse.pending_cases) == 1
    assert inverse.pending_cases[0]["before"]["report"] != inverse.pending_cases[0]["after"]["report"]


@pytest.mark.parametrize("intents, message", [
    ([content_studio.operation("unlink", "Preset_Blocks", {
        "preset_code": "dai", "block_key": "appendice", "sort_order": 0,
    })], "Preset must retain"),
    ([content_studio.operation("link", "Quick_Type_Tokens", {
        "preset_code": "dai", "sort_order": 99,
    }, {"block_sort_order": 999, "field_key": "appendicite_type", "token_kind": "lookup",
        "lookup_table": {"z": "endo"}, "digit_width": None})], "Quick Type token targets"),
    ([content_studio.operation("link", "Preset_Block_Rows", {
        "preset_code": "dai", "block_key": "appendice", "sort_order": 99,
    }, {"field_overrides": {}})], "Table row ownership"),
    ([content_studio.operation("create", "Fields", "missing_effective_default", {
        "label": "Missing default", "type": "number", "options": None,
        "default_value": None, "conclusion_addendum_template": None,
    }), content_studio.operation("link", "Block_Fields", {
        "block_key": "appendice", "field_key": "missing_effective_default",
    }, {"sort_order": 99, "label_override": None, "default_override": None, "context_section": False})],
     "default"),
])
def test_generalized_final_graph_rejects_semantic_cleanup_breakage(mutable_db, intents, message):
    unlock()
    with pytest.raises(content_changes.ChangeError):
        review(mutable_db, intents)


def test_multi_column_update_allows_one_unchanged_value(mutable_db):
    unlock()
    before = row(mutable_db, "SELECT * FROM Snippets WHERE shortcut='absence_malignite'")
    prepared = review(mutable_db, [content_studio.operation("update", "Snippets", "absence_malignite", {
        "category": before["category"], "expansion": "Updated absence phrase.",
    })])
    content_changes.apply_review(prepared, db_name=mutable_db)
    assert row(mutable_db, "SELECT expansion FROM Snippets WHERE shortcut='absence_malignite'")["expansion"] == "Updated absence phrase."


def test_preset_deletion_expands_to_complete_matching_case_detachments(mutable_db):
    unlock()
    first = freeze_case_preset_identity(mutable_db, save_synthetic_case(mutable_db, number="DETACH-ONE", status="validated"))
    second = freeze_case_preset_identity(mutable_db, save_synthetic_case(mutable_db, number="DETACH-TWO", status="validated"))
    preset, incomplete = preset_deletion_intents(mutable_db, first)
    prepared = review(mutable_db, incomplete)
    assert {ref["case_id"] for ref in prepared.data["case_references"]} == {first["id"], second["id"]}
    other = row(mutable_db, "SELECT id FROM Presets WHERE id<>? ORDER BY id LIMIT 1", (preset["id"],))["id"]
    raw_reattach = {"op": "case_preset_reference", "case_id": second["id"],
                    "before_preset_id": preset["id"], "after_preset_id": other}
    with pytest.raises(content_changes.ChangeError, match="only detach"):
        review(mutable_db, [raw_reattach])


def test_case_reference_audit_identity_mismatch_and_inverse_fault_roll_back(mutable_db, monkeypatch):
    unlock()
    case = freeze_case_preset_identity(mutable_db, save_synthetic_case(mutable_db, number="AUDIT-IDENTITY-DAI", status="validated"))
    other_case = freeze_case_preset_identity(mutable_db, save_synthetic_case(mutable_db, code="gt", number="AUDIT-IDENTITY-GT", status="validated"))
    _preset, intents = preset_deletion_intents(mutable_db, case)
    _other_preset, other_intents = preset_deletion_intents(mutable_db, other_case)
    intents.extend(other_intents)
    revision = content_changes.apply_review(review(mutable_db, intents), db_name=mutable_db)
    inverse = content_changes.review_inverse(revision, db_name=mutable_db)
    state_before = relevant_state(mutable_db)
    original = content_changes._general_apply_images
    def fail_after_images(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("private inverse canary")
    monkeypatch.setattr(content_changes, "_general_apply_images", fail_after_images)
    with pytest.raises(content_changes.ChangeError):
        content_changes.apply_review(inverse, db_name=mutable_db)
    assert relevant_state(mutable_db) == state_before
    monkeypatch.setattr(content_changes, "_general_apply_images", original)

    conn = connection(mutable_db)
    other_ref = conn.execute("""SELECT before_preset_id,before_preset_key
                                FROM Case_Content_Reference_Changes
                                WHERE revision_id=? AND case_id=?""", (revision, other_case["id"])).fetchone()
    # This is a valid Preset image in this same revision, so mere audit-image
    # membership would reattach the DAI Case to the GT Preset.
    conn.execute("""UPDATE Case_Content_Reference_Changes
                    SET before_preset_id=?, before_preset_key=?
                    WHERE revision_id=? AND case_id=?""",
                 (other_ref["before_preset_id"], other_ref["before_preset_key"], revision, case["id"]))
    conn.commit(); conn.close()
    state_after_tamper = relevant_state(mutable_db)
    with pytest.raises(content_changes.ChangeError, match="audit data"):
        content_changes.review_inverse(revision, db_name=mutable_db)
    assert relevant_state(mutable_db) == state_after_tamper
