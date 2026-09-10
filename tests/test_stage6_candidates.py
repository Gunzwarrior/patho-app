"""Stage 6 checkpoint 2: internal generalized candidate transactions."""

import json
import sqlite3

import pytest

import content_changes
import content_editing
import content_snapshot
import content_studio
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


def review(path, intents, summary="Content Studio test"):
    return content_studio.review(intents, snapshot_hash(path), summary=summary, db_name=path)


def test_archive_restore_and_revert_preserve_physical_row(mutable_db):
    unlock()
    before = row(mutable_db, "SELECT * FROM Snippets WHERE shortcut='absence_malignite'")
    archived = review(mutable_db, [content_studio.operation("archive", "Snippets", "absence_malignite")])
    assert archived.changes[0]["before"] == before
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
    # A single duplicate position is physical configuration but still a valid
    # candidate at this backend boundary; the Preset Studio normalises a whole
    # reorder in one future form action.  Here use a no-collision move.
    intent["values"]["display_order"] = 99
    prepared = review(mutable_db, [intent])
    revision = content_changes.apply_review(prepared, db_name=mutable_db)
    changed = row(mutable_db, """SELECT pb.* FROM Preset_Blocks pb JOIN Presets p ON p.id=pb.preset_id
                                  JOIN Blocks b ON b.id=pb.block_id
                                  WHERE p.short_code=? AND b.key=? AND pb.sort_order=?""",
                  (links[0]["short_code"], links[0]["key"], links[0]["sort_order"]))
    assert changed["sort_order"] == links[0]["sort_order"] and changed["display_order"] == 99
    content_changes.apply_review(content_changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
    assert row(mutable_db, """SELECT display_order FROM Preset_Blocks pb JOIN Presets p ON p.id=pb.preset_id
                              JOIN Blocks b ON b.id=pb.block_id WHERE p.short_code=? AND b.key=? AND pb.sort_order=?""",
               (links[0]["short_code"], links[0]["key"], links[0]["sort_order"]))["display_order"] == links[0]["display_order"]


def test_validated_preset_reference_detach_is_separate_from_content_audit(mutable_db):
    unlock()
    case = save_synthetic_case(mutable_db, number="DETACH", status="validated")
    prepared = review(mutable_db, [content_studio.case_preset_reference(case["id"], case["preset_id"], None)])
    assert prepared.changes == [] and prepared.data["case_references"]
    revision = content_changes.apply_review(prepared, db_name=mutable_db)
    assert row(mutable_db, "SELECT preset_id FROM Cases WHERE id=?", (case["id"],))["preset_id"] is None
    conn = connection(mutable_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM Content_Changes WHERE revision_id=?", (revision,)).fetchone()[0] == 0
        audit = dict(conn.execute("SELECT * FROM Case_Content_Reference_Changes WHERE revision_id=?", (revision,)).fetchone())
        assert audit["before_preset_id"] == case["preset_id"] and audit["after_preset_id"] is None
    finally:
        conn.close()
    content_changes.apply_review(content_changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
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


def test_create_and_inverse_cover_every_configuration_table(mutable_db):
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
        content_studio.operation("link", "Preset_Block_Rows", {
            "preset_code": "dai", "block_key": "appendice", "sort_order": 99,
        }, {"field_overrides": {}}),
        content_studio.operation("link", "Quick_Type_Tokens", {
            "preset_code": "dai", "sort_order": 99,
        }, {"block_sort_order": 0, "field_key": "appendicite_type", "token_kind": "lookup",
            "lookup_table": {"z": "normal"}, "digit_width": None}),
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
        for table in content_snapshot.RELATION_TABLES:
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
    case = save_synthetic_case(mutable_db, number="DELETE-PRESET", status="validated")
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
    content_changes.apply_review(content_changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
    assert row(mutable_db, "SELECT * FROM Presets WHERE id=?", (preset["id"],))["short_code"] == preset["short_code"]
    assert row(mutable_db, "SELECT preset_id FROM Cases WHERE id=?", (case["id"],))["preset_id"] == preset["id"]
