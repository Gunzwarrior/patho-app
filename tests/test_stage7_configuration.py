"""Stage 7 CP1 configuration-authoring safety foundation."""

import json
import sqlite3

import pytest

import content_changes
import content_editing
import content_snapshot
import content_studio
import database
import quicktype


def _hash(path):
    return content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(path))


def _unlock():
    content_editing.record_initial_snapshot("7" * 64)


def _tokens(path, code="dai"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [{"block_sort_order": row["block_sort_order"], "field_key": row["field_key"],
                 "token_kind": row["token_kind"],
                 "lookup_table": json.loads(row["lookup_table"]) if row["lookup_table"] else None,
                 "digit_width": row["digit_width"]}
                for row in conn.execute("""SELECT q.* FROM Quick_Type_Tokens q JOIN Presets p ON p.id=q.preset_id
                                           WHERE p.short_code=? ORDER BY q.sort_order""", (code,))]
    finally:
        conn.close()


def _physical_tokens(path, code="dai"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute("""SELECT q.* FROM Quick_Type_Tokens q JOIN Presets p ON p.id=q.preset_id
                                                    WHERE p.short_code=? ORDER BY q.sort_order""", (code,))]
    finally:
        conn.close()


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


def test_token_position_migration_refuses_duplicate_without_partial_changes(mutable_db):
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute("DROP INDEX Quick_Type_Tokens_preset_sort_order_uq")
        conn.execute("DELETE FROM Schema_Migrations WHERE name='stage7_quick_type_token_position_unique_v1'")
        row = conn.execute("SELECT * FROM Quick_Type_Tokens LIMIT 1").fetchone()
        columns = [name for name in ("preset_id", "sort_order", "block_sort_order", "field_key", "token_kind", "lookup_table", "digit_width")]
        conn.execute("INSERT INTO Quick_Type_Tokens (%s) VALUES (%s)" % (", ".join(columns), ", ".join("?" * len(columns))),
                     tuple(row[columns.index(name) + 1] for name in columns))
        before = conn.execute("SELECT COUNT(*) FROM Quick_Type_Tokens").fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ValueError, match="duplicate token position"):
        database.migrate_schema(mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM Quick_Type_Tokens").fetchone()[0] == before
        assert conn.execute("SELECT 1 FROM Schema_Migrations WHERE name='stage7_quick_type_token_position_unique_v1'").fetchone() is None
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='Quick_Type_Tokens_preset_sort_order_uq'").fetchone() is None
    finally:
        conn.close()


def test_token_position_migration_is_named_and_idempotent(mutable_db):
    database.migrate_schema(mutable_db)
    database.migrate_schema(mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM Schema_Migrations WHERE name='stage7_quick_type_token_position_unique_v1'").fetchone()[0] == 1
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='Quick_Type_Tokens_preset_sort_order_uq'").fetchone() is not None
    finally:
        conn.close()


def test_candidate_parser_uses_only_supplied_connection(mutable_db):
    candidate = sqlite3.connect(mutable_db)
    candidate.row_factory = sqlite3.Row
    try:
        candidate.execute("""UPDATE Quick_Type_Tokens SET lookup_table='{"3":"phlegmoneuse"}'
                             WHERE preset_id=(SELECT id FROM Presets WHERE short_code='dai') AND sort_order=0""")
        preset, values, error = quicktype.parse_quick_type("dai3", conn=candidate)
        assert error is None and preset["short_code"] == "dai"
        assert values == {0: {"appendicite_type": "phlegmoneuse"}}
    finally:
        candidate.close()
    # The unmodified operational copy remains different, proving no fallback.
    assert quicktype.parse_quick_type("dai3")[1] == {0: {"appendicite_type": "periappendicite"}}


def test_complete_token_replace_reorder_apply_inverse_and_reinverse(mutable_db):
    _unlock()
    before_ids = {row["sort_order"]: row["id"] for row in _physical_tokens(mutable_db)}
    desired = _tokens(mutable_db)
    # Measurement first is valid only when its following lookup does not use a
    # digit; changing the mapping also proves full replacement rather than a
    # presentation-only reorder.
    desired[0]["lookup_table"] = {"x": "periappendicite"}
    desired = [desired[1], desired[0]]
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai", db_name=mutable_db)
    operations = content_studio.quick_type_draft_operations("dai", desired, baseline=baseline, db_name=mutable_db)
    assert [operation["op"] for operation in operations[1:]] == ["update", "update"]
    review = content_studio.review(operations, _hash(mutable_db), db_name=mutable_db)
    assert review.data["changes"] and all(change["table"] == "Quick_Type_Tokens" for change in review.data["changes"])
    first = content_changes.apply_review(review, db_name=mutable_db)
    assert {row["sort_order"]: row["id"] for row in _physical_tokens(mutable_db)} == before_ids
    assert quicktype.parse_quick_type("dai7x")[2] is None
    undo = content_changes.apply_review(content_changes.review_inverse(first, db_name=mutable_db), db_name=mutable_db)
    assert {row["sort_order"]: row["id"] for row in _physical_tokens(mutable_db)} == before_ids
    assert quicktype.parse_quick_type("dai37")[2] is None
    redo = content_changes.apply_review(content_changes.review_inverse(undo, db_name=mutable_db), db_name=mutable_db)
    assert redo > undo > first
    assert {row["sort_order"]: row["id"] for row in _physical_tokens(mutable_db)} == before_ids
    assert quicktype.parse_quick_type("dai7x")[2] is None


def test_token_attribute_edit_is_one_update_and_retains_its_physical_id(mutable_db):
    _unlock()
    before = _physical_tokens(mutable_db)
    desired = _tokens(mutable_db)
    desired[0]["lookup_table"] = {"x": "phlegmoneuse"}
    operations = content_studio.quick_type_draft_operations("dai", desired, db_name=mutable_db)
    assert [operation["op"] for operation in operations[1:]] == ["update"]
    assert operations[1]["key"]["sort_order"] == before[0]["sort_order"]
    assert set(operations[1]["values"]) == {"lookup_table"}
    revision = content_changes.apply_review(
        content_studio.review(operations, _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    assert _physical_tokens(mutable_db)[0]["id"] == before[0]["id"]
    undo = content_changes.apply_review(content_changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
    assert _physical_tokens(mutable_db)[0]["id"] == before[0]["id"]
    content_changes.apply_review(content_changes.review_inverse(undo, db_name=mutable_db), db_name=mutable_db)
    assert _physical_tokens(mutable_db)[0]["id"] == before[0]["id"]


def test_complete_rule_set_is_canonical_and_rejects_reversed_duplicate(mutable_db):
    current = _rules(mutable_db)
    extra = {"field_a_key": "appendicite_type", "field_a_values": ["phlegmoneuse"],
             "field_b_key": "false_membranes", "field_b_values": [False], "message": "Synthetic warning."}
    operations = content_studio.consistency_rule_draft_operations("appendice", current + [extra], db_name=mutable_db)
    assert operations[0]["op"] == "assert_configuration_draft"
    reversed_extra = {"field_a_key": "false_membranes", "field_a_values": [False],
                      "field_b_key": "appendicite_type", "field_b_values": ["phlegmoneuse"],
                      "message": "Another message is still a distinct rule."}
    # Same predicates are duplicates regardless of A/B orientation; messages
    # do not make two clinical predicates independently meaningful.
    with pytest.raises(content_studio.StudioIntentError, match="Equivalent consistency rules"):
        content_studio.consistency_rule_draft_operations("appendice", current + [extra, reversed_extra], db_name=mutable_db)


def test_complete_rule_replace_apply_inverse_and_reinverse(mutable_db):
    _unlock()
    desired = _rules(mutable_db) + [{
        "field_a_key": "appendicite_type", "field_a_values": ["phlegmoneuse"],
        "field_b_key": "false_membranes", "field_b_values": [False], "message": "Synthetic warning.",
    }]
    review = content_studio.review(
        content_studio.consistency_rule_draft_operations("appendice", desired, db_name=mutable_db),
        _hash(mutable_db), db_name=mutable_db,
    )
    assert review.data["configuration_review"]["after"][0]["configuration"]
    first = content_changes.apply_review(review, db_name=mutable_db)
    assert len(_rules(mutable_db)) == 2
    undo = content_changes.apply_review(content_changes.review_inverse(first, db_name=mutable_db), db_name=mutable_db)
    assert len(_rules(mutable_db)) == 1
    content_changes.apply_review(content_changes.review_inverse(undo, db_name=mutable_db), db_name=mutable_db)
    assert len(_rules(mutable_db)) == 2


def test_configuration_baseline_rejects_endpoint_aba_or_change(mutable_db):
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai", db_name=mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute("UPDATE Fields SET label='Changed endpoint' WHERE key='appendicite_type'")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_studio.StaleSourceDraftError, match="endpoint Fields changed"):
        content_studio.quick_type_draft_operations("dai", _tokens(mutable_db), baseline=baseline, db_name=mutable_db)


def test_configuration_baseline_rejects_field_endpoint_and_owner_aba(mutable_db):
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai", db_name=mutable_db)
    conn = sqlite3.connect(mutable_db)
    conn.row_factory = sqlite3.Row
    try:
        field = dict(conn.execute("SELECT * FROM Fields WHERE key='appendicite_type'").fetchone())
        binding = dict(conn.execute("SELECT * FROM Block_Fields WHERE field_id=?", (field["id"],)).fetchone())
        conn.execute("DELETE FROM Block_Fields WHERE block_id=? AND field_id=?", (binding["block_id"], binding["field_id"]))
        conn.execute("DELETE FROM Fields WHERE id=?", (field["id"],))
        columns = [name for name in field if name != "id"]
        conn.execute("INSERT INTO Fields(%s) VALUES (%s)" % (", ".join(columns), ", ".join("?" * len(columns))),
                     tuple(field[name] for name in columns))
        new_id = conn.execute("SELECT id FROM Fields WHERE key='appendicite_type'").fetchone()[0]
        conn.execute("INSERT INTO Block_Fields(block_id,field_id,sort_order,label_override,default_override,context_section) VALUES (?,?,?,?,?,?)",
                     (binding["block_id"], new_id, binding["sort_order"], binding["label_override"], binding["default_override"], binding["context_section"]))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_studio.StaleSourceDraftError, match="endpoint Fields changed"):
        content_studio.quick_type_draft_operations("dai", _tokens(mutable_db), baseline=baseline, db_name=mutable_db)

    # The owner itself is likewise bound as a physical image, not merely by
    # reusable short code.
    baseline = content_studio.configuration_draft_baseline("quick_type", "dai", db_name=mutable_db)
    conn = sqlite3.connect(mutable_db)
    conn.row_factory = sqlite3.Row
    try:
        preset = dict(conn.execute("SELECT * FROM Presets WHERE short_code='dai'").fetchone())
        links = [dict(row) for row in conn.execute("SELECT * FROM Preset_Blocks WHERE preset_id=?", (preset["id"],))]
        tokens = [dict(row) for row in conn.execute("SELECT * FROM Quick_Type_Tokens WHERE preset_id=?", (preset["id"],))]
        conn.execute("DELETE FROM Quick_Type_Tokens WHERE preset_id=?", (preset["id"],))
        conn.execute("DELETE FROM Preset_Blocks WHERE preset_id=?", (preset["id"],))
        conn.execute("DELETE FROM Presets WHERE id=?", (preset["id"],))
        columns = [name for name in preset if name != "id"]
        conn.execute("INSERT INTO Presets(%s) VALUES (%s)" % (", ".join(columns), ", ".join("?" * len(columns))),
                     tuple(preset[name] for name in columns))
        new_id = conn.execute("SELECT id FROM Presets WHERE short_code='dai'").fetchone()[0]
        for row in links:
            conn.execute("INSERT INTO Preset_Blocks(preset_id,block_id,sort_order,display_order,field_overrides) VALUES (?,?,?,?,?)",
                         (new_id, row["block_id"], row["sort_order"], row["display_order"], row["field_overrides"]))
        for row in tokens:
            conn.execute("INSERT INTO Quick_Type_Tokens(preset_id,sort_order,block_sort_order,field_key,token_kind,lookup_table,digit_width) VALUES (?,?,?,?,?,?,?)",
                         (new_id, row["sort_order"], row["block_sort_order"], row["field_key"], row["token_kind"], row["lookup_table"], row["digit_width"]))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_studio.StaleSourceDraftError, match="Configuration owner"):
        content_studio.quick_type_draft_operations("dai", _tokens(mutable_db), baseline=baseline, db_name=mutable_db)


def test_minimal_token_create_delete_and_sparse_preservation(mutable_db):
    _unlock()
    before = _physical_tokens(mutable_db)
    desired = _tokens(mutable_db) + [{"block_sort_order": 0, "field_key": "false_membranes",
                                      "token_kind": "lookup", "lookup_table": {"z": False}, "digit_width": None}]
    create = content_studio.quick_type_draft_operations("dai", desired, db_name=mutable_db)
    assert [operation["op"] for operation in create[1:]] == ["link"]
    content_changes.apply_review(content_studio.review(create, _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    after_create = _physical_tokens(mutable_db)
    assert [row["id"] for row in after_create[:2]] == [row["id"] for row in before]
    delete = content_studio.quick_type_draft_operations("dai", _tokens(mutable_db)[:2], db_name=mutable_db)
    assert [operation["op"] for operation in delete[1:]] == ["unlink"]
    content_changes.apply_review(content_studio.review(delete, _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    assert [row["id"] for row in _physical_tokens(mutable_db)] == [row["id"] for row in before]
    # Existing sparse positions are immutable identities, not a cue to rewrite
    # an otherwise unchanged grammar.
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute("UPDATE Quick_Type_Tokens SET sort_order=17 WHERE preset_id=(SELECT id FROM Presets WHERE short_code='dai') AND sort_order=0")
        conn.execute("UPDATE Quick_Type_Tokens SET sort_order=29 WHERE preset_id=(SELECT id FROM Presets WHERE short_code='dai') AND sort_order=1")
        conn.commit()
    finally:
        conn.close()
    sparse = [{"sort_order": row["sort_order"], "block_sort_order": row["block_sort_order"], "field_key": row["field_key"],
               "token_kind": row["token_kind"], "lookup_table": json.loads(row["lookup_table"]) if row["lookup_table"] else None,
               "digit_width": row["digit_width"]} for row in _physical_tokens(mutable_db)]
    with pytest.raises(content_studio.StudioIntentError, match="no persisted change"):
        content_studio.quick_type_draft_operations("dai", sparse, db_name=mutable_db)
    sparse_without_positions = [{name: value for name, value in token.items() if name != "sort_order"}
                                for token in sparse]
    sparse_without_positions.append({"block_sort_order": 0, "field_key": "false_membranes",
                                     "token_kind": "lookup", "lookup_table": {"z": False}, "digit_width": None})
    operations = content_studio.quick_type_draft_operations("dai", sparse_without_positions, db_name=mutable_db)
    assert [operation["op"] for operation in operations[1:]] == ["link"]
    assert operations[-1]["key"]["sort_order"] == 30


def test_two_consecutive_rule_edits_retain_identity_and_inverse_round_trips(mutable_db):
    _unlock()
    first_extra = {"field_a_key": "false_membranes", "field_a_values": [False],
                   "field_b_key": "appendicite_type", "field_b_values": ["phlegmoneuse"], "message": "First."}
    first = content_studio.consistency_rule_draft_operations("appendice", _rules(mutable_db) + [first_extra], db_name=mutable_db)
    first_revision = content_changes.apply_review(content_studio.review(first, _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    retained = _rules(mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        retained_ids = [row[0] for row in conn.execute("SELECT id FROM Field_Consistency_Rules ORDER BY id")]
    finally:
        conn.close()
    second_extra = {"field_a_key": "false_membranes", "field_a_values": [True],
                    "field_b_key": "appendicite_type", "field_b_values": ["phlegmoneuse"], "message": "Second."}
    second = content_studio.consistency_rule_draft_operations("appendice", retained + [second_extra], db_name=mutable_db)
    assert [operation["op"] for operation in second[1:]] == ["link"]
    second_revision = content_changes.apply_review(content_studio.review(second, _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        assert [row[0] for row in conn.execute("SELECT id FROM Field_Consistency_Rules ORDER BY id")][:2] == retained_ids
    finally:
        conn.close()
    undo = content_changes.apply_review(content_changes.review_inverse(second_revision, db_name=mutable_db), db_name=mutable_db)
    redo = content_changes.apply_review(content_changes.review_inverse(undo, db_name=mutable_db), db_name=mutable_db)
    assert redo > undo > second_revision > first_revision


def test_rule_row_delete_recreate_aba_refuses_configuration_draft(mutable_db):
    _unlock()
    desired = _rules(mutable_db) + [{
        "field_a_key": "false_membranes", "field_a_values": [False],
        "field_b_key": "appendicite_type", "field_b_values": ["phlegmoneuse"], "message": "ABA.",
    }]
    baseline = content_studio.configuration_draft_baseline("consistency", "appendice", db_name=mutable_db)
    review = content_studio.review(
        content_studio.consistency_rule_draft_operations("appendice", desired, baseline=baseline, db_name=mutable_db),
        _hash(mutable_db), db_name=mutable_db,
    )
    conn = sqlite3.connect(mutable_db)
    conn.row_factory = sqlite3.Row
    try:
        row = dict(conn.execute("SELECT * FROM Field_Consistency_Rules WHERE block_id=(SELECT id FROM Blocks WHERE key='appendice')").fetchone())
        conn.execute("DELETE FROM Field_Consistency_Rules WHERE id=?", (row["id"],))
        columns = [name for name in row if name != "id"]
        conn.execute("INSERT INTO Field_Consistency_Rules(%s) VALUES (%s)" %
                     (", ".join(columns), ", ".join("?" * len(columns))), tuple(row[name] for name in columns))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_studio.StaleSourceDraftError, match="tokens/rules"):
        content_studio.consistency_rule_draft_operations("appendice", desired, baseline=baseline, db_name=mutable_db)
    with pytest.raises((content_changes.StaleReviewError, content_changes.StaleDraftReviewError)):
        content_changes.apply_review(review, db_name=mutable_db)


def test_quick_type_block_endpoint_delete_recreate_aba_refuses_configuration_draft(mutable_db):
    conn = sqlite3.connect(mutable_db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("INSERT INTO Presets(short_code,name) VALUES ('cp1qtaba','CP1 Quick Type ABA')")
        preset_id = conn.execute("SELECT id FROM Presets WHERE short_code='cp1qtaba'").fetchone()[0]
        conn.execute("""INSERT INTO Blocks(key,name,is_table,macro_template,micro_template,conclusion_template)
                        VALUES ('cp1_token_endpoint_aba','CP1 token endpoint',0,'Macro.','Micro.','Conclusion.')""")
        block = dict(conn.execute("SELECT * FROM Blocks WHERE key='cp1_token_endpoint_aba'").fetchone())
        field_id = conn.execute("SELECT id FROM Fields WHERE key='false_membranes'").fetchone()[0]
        conn.execute("INSERT INTO Block_Fields(block_id,field_id,sort_order) VALUES (?,?,0)", (block["id"], field_id))
        conn.execute("INSERT INTO Preset_Blocks(preset_id,block_id,sort_order,display_order,field_overrides) VALUES (?,?,?,?,?)",
                     (preset_id, block["id"], 0, 0, "{}"))
        conn.execute("""INSERT INTO Quick_Type_Tokens
                        (preset_id,sort_order,block_sort_order,field_key,token_kind,lookup_table,digit_width)
                        VALUES (?,?,0,'false_membranes','lookup','{\"z\":false}',NULL)""", (preset_id, 0))
        conn.commit()
        baseline = content_studio.configuration_draft_baseline("quick_type", "cp1qtaba", db_name=mutable_db)
        token = dict(conn.execute("SELECT * FROM Quick_Type_Tokens WHERE preset_id=?", (preset_id,)).fetchone())
        binding = dict(conn.execute("SELECT * FROM Block_Fields WHERE block_id=?", (block["id"],)).fetchone())
        conn.execute("DELETE FROM Quick_Type_Tokens WHERE preset_id=?", (preset_id,))
        conn.execute("DELETE FROM Preset_Blocks WHERE preset_id=?", (preset_id,))
        conn.execute("DELETE FROM Block_Fields WHERE block_id=?", (block["id"],))
        conn.execute("DELETE FROM Blocks WHERE id=?", (block["id"],))
        columns = [name for name in block if name != "id"]
        new_block_id = conn.execute("INSERT INTO Blocks(%s) VALUES (%s)" %
                                    (", ".join(columns), ", ".join("?" * len(columns))),
                                    tuple(block[name] for name in columns)).lastrowid
        conn.execute("""INSERT INTO Block_Fields(block_id,field_id,sort_order,label_override,default_override,context_section)
                        VALUES (?,?,?,?,?,?)""", (new_block_id, binding["field_id"], binding["sort_order"],
                                                     binding["label_override"], binding["default_override"], binding["context_section"]))
        conn.execute("INSERT INTO Preset_Blocks(preset_id,block_id,sort_order,display_order,field_overrides) VALUES (?,?,?,?,?)",
                     (preset_id, new_block_id, 0, 0, "{}"))
        conn.execute("""INSERT INTO Quick_Type_Tokens
                        (preset_id,sort_order,block_sort_order,field_key,token_kind,lookup_table,digit_width)
                        VALUES (?,?,?,?,?,?,?)""", tuple(token[name] for name in (
                            "preset_id", "sort_order", "block_sort_order", "field_key", "token_kind", "lookup_table", "digit_width")))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_studio.StaleSourceDraftError, match="endpoint Fields changed"):
        content_studio.quick_type_draft_operations("cp1qtaba", _tokens(mutable_db, "cp1qtaba"),
                                                   baseline=baseline, db_name=mutable_db)


def test_consistency_block_owner_delete_recreate_aba_refuses_configuration_draft(mutable_db):
    conn = sqlite3.connect(mutable_db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""INSERT INTO Blocks(key,name,is_table,macro_template,micro_template,conclusion_template)
                        VALUES ('cp1_rule_owner_aba','CP1 rule owner',0,'Macro.','Micro.','Conclusion.')""")
        block = dict(conn.execute("SELECT * FROM Blocks WHERE key='cp1_rule_owner_aba'").fetchone())
        fields = [dict(row) for row in conn.execute("SELECT * FROM Fields WHERE key IN ('false_membranes','appendicite_type') ORDER BY key")]
        for position, field in enumerate(fields):
            conn.execute("INSERT INTO Block_Fields(block_id,field_id,sort_order) VALUES (?,?,?)",
                         (block["id"], field["id"], position))
        conn.execute("""INSERT INTO Field_Consistency_Rules
                        (block_id,field_a_key,field_a_values,field_b_key,field_b_values,message)
                        VALUES (?, 'false_membranes', '[true]', 'appendicite_type', '[\"endo\"]', 'ABA.')""", (block["id"],))
        conn.commit()
        bindings = [dict(row) for row in conn.execute("SELECT * FROM Block_Fields WHERE block_id=? ORDER BY sort_order", (block["id"],))]
        rules = [dict(row) for row in conn.execute("SELECT * FROM Field_Consistency_Rules WHERE block_id=?", (block["id"],))]
        baseline = content_studio.configuration_draft_baseline("consistency", "cp1_rule_owner_aba", db_name=mutable_db)
        conn.execute("DELETE FROM Field_Consistency_Rules WHERE block_id=?", (block["id"],))
        conn.execute("DELETE FROM Block_Fields WHERE block_id=?", (block["id"],))
        conn.execute("DELETE FROM Blocks WHERE id=?", (block["id"],))
        columns = [name for name in block if name != "id"]
        new_id = conn.execute("INSERT INTO Blocks(%s) VALUES (%s)" %
                              (", ".join(columns), ", ".join("?" * len(columns))),
                              tuple(block[name] for name in columns)).lastrowid
        for binding in bindings:
            conn.execute("""INSERT INTO Block_Fields(block_id,field_id,sort_order,label_override,default_override,context_section)
                            VALUES (?,?,?,?,?,?)""", (new_id, binding["field_id"], binding["sort_order"],
                                                         binding["label_override"], binding["default_override"], binding["context_section"]))
        for rule in rules:
            conn.execute("""INSERT INTO Field_Consistency_Rules
                            (block_id,field_a_key,field_a_values,field_b_key,field_b_values,message)
                            VALUES (?,?,?,?,?,?)""", (new_id, rule["field_a_key"], rule["field_a_values"],
                                                         rule["field_b_key"], rule["field_b_values"], rule["message"]))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_studio.StaleSourceDraftError, match="Configuration owner"):
        content_studio.consistency_rule_draft_operations("cp1_rule_owner_aba", [], baseline=baseline, db_name=mutable_db)


def test_configuration_aba_review_staleness_and_audit_fault_rollback(mutable_db, monkeypatch):
    _unlock()
    desired = _tokens(mutable_db) + [{"block_sort_order": 0, "field_key": "false_membranes",
                                      "token_kind": "lookup", "lookup_table": {"z": False}, "digit_width": None}]
    operations = content_studio.quick_type_draft_operations("dai", desired, db_name=mutable_db)
    review = content_studio.review(operations, _hash(mutable_db), db_name=mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        row = conn.execute("SELECT * FROM Quick_Type_Tokens WHERE preset_id=(SELECT id FROM Presets WHERE short_code='dai') AND sort_order=0").fetchone()
        conn.execute("DELETE FROM Quick_Type_Tokens WHERE id=?", (row[0],))
        conn.execute("INSERT INTO Quick_Type_Tokens(preset_id,sort_order,block_sort_order,field_key,token_kind,lookup_table,digit_width) VALUES (?,?,?,?,?,?,?)", tuple(row[1:]))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises((content_changes.StaleReviewError, content_changes.StaleDraftReviewError)):
        content_changes.apply_review(review, db_name=mutable_db)
    operations = content_studio.quick_type_draft_operations("dai", desired, db_name=mutable_db)
    review = content_studio.review(operations, _hash(mutable_db), db_name=mutable_db)
    before = _physical_tokens(mutable_db)
    monkeypatch.setattr(content_changes, "_record_generalized_changes", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit fault")))
    with pytest.raises(content_changes.ChangeError, match="No content or audit"):
        content_changes.apply_review(review, db_name=mutable_db)
    assert _physical_tokens(mutable_db) == before


def test_configuration_materialization_fault_rolls_back_before_audit(mutable_db, monkeypatch):
    _unlock()
    desired = _tokens(mutable_db) + [{"block_sort_order": 0, "field_key": "false_membranes",
                                      "token_kind": "lookup", "lookup_table": {"z": False}, "digit_width": None}]
    review = content_studio.review(content_studio.quick_type_draft_operations("dai", desired, db_name=mutable_db),
                                   _hash(mutable_db), db_name=mutable_db)
    before = _physical_tokens(mutable_db)
    original = content_changes._general_insert_from_intent

    def fail_after_token_insert(conn, operation):
        original(conn, operation)
        if operation["table"] == "Quick_Type_Tokens":
            raise RuntimeError("materialization fault")

    monkeypatch.setattr(content_changes, "_general_insert_from_intent", fail_after_token_insert)
    with pytest.raises(content_changes.ChangeError, match="No content or audit"):
        content_changes.apply_review(review, db_name=mutable_db)
    assert _physical_tokens(mutable_db) == before


def test_direct_intents_refuse_archived_and_table_configuration_owners(mutable_db):
    token = content_studio.operation("link", "Quick_Type_Tokens", {"preset_code": "dai", "sort_order": 99}, {
        "block_sort_order": 0, "field_key": "false_membranes", "token_kind": "lookup", "lookup_table": {"z": False}, "digit_width": None,
    })
    with pytest.raises(content_changes.ChangeError, match="active Preset owner"):
        content_studio.review([content_studio.operation("archive", "Presets", "dai"), token], _hash(mutable_db), db_name=mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute("INSERT INTO Blocks(key,name,is_table,micro_template,conclusion_template) VALUES ('cp1_table','CP1 table',1,'','')")
        table_id = conn.execute("SELECT id FROM Blocks WHERE key='cp1_table'").fetchone()[0]
        field_id = conn.execute("SELECT id FROM Fields WHERE key='false_membranes'").fetchone()[0]
        conn.execute("INSERT INTO Block_Fields(block_id,field_id,sort_order) VALUES (?,?,0)", (table_id, field_id))
        conn.commit()
    finally:
        conn.close()
    rule = content_studio.operation("link", "Field_Consistency_Rules", {
        "block_key": "cp1_table", "field_a_key": "false_membranes", "field_a_values": "[false]",
        "field_b_key": "false_membranes", "field_b_values": "[true]", "message": "No.",
    }, {"field_a_key": "false_membranes", "field_a_values": [False], "field_b_key": "false_membranes", "field_b_values": [True], "message": "No."})
    with pytest.raises(content_changes.ChangeError, match="active non-table Block owner"):
        content_studio.review([rule], _hash(mutable_db), db_name=mutable_db)


def test_candidate_prefix_findings_distinguish_harmless_and_unreachable(mutable_db):
    def preset(code):
        return [
            content_studio.operation("create", "Presets", code, {
                "name": code, "category": None, "default_adicap": None, "default_title": None,
            }),
            content_studio.operation("link", "Preset_Blocks", {
                "preset_code": code, "block_key": "appendice", "sort_order": 0,
            }, {"display_order": 0, "field_overrides": {}}),
        ]
    harmless = content_studio.review(preset("daiq"), _hash(mutable_db), db_name=mutable_db)
    assert any("shadows no configured modifier" in warning for warning in harmless.data["warnings"])
    with pytest.raises(content_changes.ChangeError, match="unreachable"):
        content_studio.review(preset("dai3"), _hash(mutable_db), db_name=mutable_db)


def test_decimal_rule_persists_semantic_canonical_values_through_inverse(mutable_db):
    _unlock()
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute("INSERT INTO Fields(key,label,type,options,default_value) VALUES ('cp1_decimal','CP1 decimal','decimal',NULL,'0')")
        conn.execute("INSERT INTO Fields(key,label,type,options,default_value) VALUES ('cp1_flag','CP1 flag','checkbox',NULL,'0')")
        conn.execute("INSERT INTO Blocks(key,name,is_table,macro_template,micro_template,conclusion_template) VALUES ('cp1_decimal_block','CP1 decimal',0,'Macro.','Micro.','Conclusion.')")
        block_id = conn.execute("SELECT id FROM Blocks WHERE key='cp1_decimal_block'").fetchone()[0]
        conn.execute("INSERT INTO Block_Fields(block_id,field_id,sort_order) SELECT ?,id,0 FROM Fields WHERE key='cp1_decimal'", (block_id,))
        conn.execute("INSERT INTO Block_Fields(block_id,field_id,sort_order) SELECT ?,id,1 FROM Fields WHERE key='cp1_flag'", (block_id,))
        conn.commit()
    finally:
        conn.close()
    rule = {"field_a_key": "cp1_decimal", "field_a_values": [1.0],
            "field_b_key": "cp1_flag", "field_b_values": [True], "message": "Decimal."}
    revision = content_changes.apply_review(content_studio.review(
        content_studio.consistency_rule_draft_operations("cp1_decimal_block", [rule], db_name=mutable_db),
        _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        assert conn.execute("""SELECT r.field_a_values FROM Field_Consistency_Rules r JOIN Blocks b ON b.id=r.block_id
                               WHERE b.key='cp1_decimal_block'""").fetchone()[0] == "[1]"
    finally:
        conn.close()
    undo = content_changes.apply_review(content_changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
    redo = content_changes.apply_review(content_changes.review_inverse(undo, db_name=mutable_db), db_name=mutable_db)
    assert redo > undo > revision


def test_direct_json_rule_creation_is_canonical_without_duplicate_provenance(mutable_db):
    _unlock()
    intent = content_studio.operation("link", "Field_Consistency_Rules", {
        "block_key": "appendice", "field_a_key": "appendicite_type",
        "field_a_values": '["suppuree", "endo"]', "field_b_key": "false_membranes",
        "field_b_values": "[false]", "message": "Direct JSON.",
    }, {
        "field_a_key": "appendicite_type", "field_a_values": '["suppuree", "endo"]',
        "field_b_key": "false_membranes", "field_b_values": "[false]", "message": "Direct JSON.",
    })
    content_changes.apply_review(content_studio.review([intent], _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    conn = sqlite3.connect(mutable_db)
    try:
        row = conn.execute("""SELECT field_a_values,field_b_values FROM Field_Consistency_Rules
                              WHERE message='Direct JSON.'""").fetchone()
        assert row == ('["endo","suppuree"]', "[false]")
    finally:
        conn.close()


def test_archived_token_removal_inverse_restores_physical_configuration(mutable_db):
    _unlock()
    archive = content_changes.apply_review(content_studio.review(
        [content_studio.operation("archive", "Presets", "dai")], _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    before = _physical_tokens(mutable_db)
    removal = content_changes.apply_review(content_studio.review([
        content_studio.operation("unlink", "Quick_Type_Tokens", {
            "preset_code": "dai", "sort_order": before[0]["sort_order"],
        })], _hash(mutable_db), db_name=mutable_db), db_name=mutable_db)
    assert len(_physical_tokens(mutable_db)) == len(before) - 1
    content_changes.apply_review(content_changes.review_inverse(removal, db_name=mutable_db), db_name=mutable_db)
    assert _physical_tokens(mutable_db) == before
    assert archive < removal
