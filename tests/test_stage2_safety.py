"""Stage 2 backend safety regression tests; all writes use mutable_db."""

import copy
import json
from pathlib import Path
import sqlite3
import subprocess

import content_snapshot
import database
import init_db


def _preset(code):
    return next(row for row in database.get_all_presets() if row["short_code"] == code)


def _case_input(blocks):
    return {
        "block_instances": [{"block_id": block["block_id"], "instance_no": block["sort_order"]} for block in blocks],
        "blocks": {}, "wildcard_notes": [], "master_lock": False,
    }


def test_pytest_redirects_every_default_database_name():
    operational_db = (Path.cwd() / "pathology.db").resolve()
    assert Path(database.DB_NAME).resolve() != operational_db
    assert Path(init_db.DB_NAME).resolve() != operational_db


def test_connections_enforce_foreign_keys_and_migration_is_idempotent(mutable_db):
    conn = database.get_db_connection()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    before = conn.execute("SELECT COUNT(*) FROM Case_Validation_History").fetchone()[0]
    conn.close()

    database.migrate_schema(mutable_db)
    database.migrate_schema(mutable_db)
    conn = database.get_db_connection()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM Case_Validation_History").fetchone()[0] == before
    conn.close()


def test_migration_backfills_validated_case_history_once(mutable_db):
    preset = _preset("dai")
    blocks = database.get_preset_blocks(preset["id"])
    assert database.save_case("BACKFILL-1", preset["id"], "ctx", _case_input(blocks), "<p>frozen</p>", status="validated")
    conn = database.get_db_connection()
    conn.execute("DELETE FROM Case_Validation_History")
    conn.execute("UPDATE Cases SET content_fingerprint = NULL WHERE case_number = ?", ("BACKFILL-1",))
    conn.commit()
    conn.close()

    database.migrate_schema(mutable_db)
    history = database.get_case_validation_history("BACKFILL-1")
    assert len(history) == 1
    assert history[0]["rendered_html"] == "<p>frozen</p>"
    assert history[0]["content_fingerprint"] == database.get_case_by_number("BACKFILL-1")["content_fingerprint"]
    assert history[0]["content_fingerprint"] is not None
    database.migrate_schema(mutable_db)
    assert len(database.get_case_validation_history("BACKFILL-1")) == 1


def test_fingerprint_schema_upgrade_runs_once_without_masking_later_content_changes(mutable_db):
    preset = _preset("dai")
    data = _case_input(database.get_preset_blocks(preset["id"]))
    assert database.save_case("FP-MIGRATION-1", preset["id"], "", data, "<p>draft</p>")
    conn = database.get_db_connection()
    conn.execute("UPDATE Cases SET content_fingerprint = 'legacy-stage2-format' WHERE case_number = ?", ("FP-MIGRATION-1",))
    conn.execute("DELETE FROM Schema_Migrations WHERE name = ?", ("stage2_relevant_content_fingerprint_v2",))
    conn.commit()
    conn.close()

    database.migrate_schema(mutable_db)
    upgraded = database.get_case_by_number("FP-MIGRATION-1")["content_fingerprint"]
    assert upgraded == database.compute_case_content_fingerprint(preset["id"], data)
    assert upgraded != "legacy-stage2-format"

    conn = database.get_db_connection()
    conn.execute("UPDATE Blocks SET micro_template = micro_template || ' later edit' WHERE key = 'appendice'")
    conn.commit()
    conn.close()
    assert database.compute_case_content_fingerprint(preset["id"], data) != upgraded
    database.migrate_schema(mutable_db)
    assert database.get_case_by_number("FP-MIGRATION-1")["content_fingerprint"] == upgraded


def test_validated_case_is_immutable_until_explicit_audited_return(mutable_db):
    preset = _preset("dai")
    blocks = database.get_preset_blocks(preset["id"])
    original = _case_input(blocks)
    assert database.save_case("VALID-1", preset["id"], "ctx", original, "<p>validated</p>", status="validated")
    assert not database.save_case("VALID-1", preset["id"], "changed", original, "<p>changed</p>", status="pending")
    assert database.get_case_by_number("VALID-1")["rendered_html"] == "<p>validated</p>"
    assert not database.return_case_to_pending("VALID-1", "")
    assert database.return_case_to_pending("VALID-1", "validated by mistake")
    assert database.get_case_by_number("VALID-1")["status"] == "pending"
    assert len(database.get_case_validation_history("VALID-1")) == 1
    conn = database.get_db_connection()
    event = conn.execute("SELECT transition, reason FROM Case_Status_History ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert dict(event) == {"transition": "validated_to_pending", "reason": "validated by mistake"}


def test_validation_and_return_transitions_roll_back_with_their_audit_rows(mutable_db):
    preset = _preset("dai")
    data = _case_input(database.get_preset_blocks(preset["id"]))
    conn = database.get_db_connection()
    conn.executescript("""
        CREATE TRIGGER fail_validation_history
        BEFORE INSERT ON Case_Validation_History
        BEGIN SELECT RAISE(ABORT, 'validation history failure'); END;
    """)
    conn.close()
    assert not database.save_case("ATOMIC-VALIDATE-1", preset["id"], "", data, "<p>report</p>", status="validated")
    assert database.get_case_by_number("ATOMIC-VALIDATE-1") is None

    conn = database.get_db_connection()
    conn.execute("DROP TRIGGER fail_validation_history")
    conn.commit()
    conn.close()
    assert database.save_case("ATOMIC-RETURN-1", preset["id"], "", data, "<p>report</p>", status="validated")
    conn = database.get_db_connection()
    conn.executescript("""
        CREATE TRIGGER fail_return_history
        BEFORE INSERT ON Case_Status_History
        WHEN NEW.transition = 'validated_to_pending'
        BEGIN SELECT RAISE(ABORT, 'return history failure'); END;
    """)
    conn.close()
    assert not database.return_case_to_pending("ATOMIC-RETURN-1", "test rollback")
    assert database.get_case_by_number("ATOMIC-RETURN-1")["status"] == "validated"


def test_fingerprint_is_case_specific_and_composition_order_sensitive(mutable_db):
    gastric = _preset("gt")
    blocks = database.get_preset_blocks(gastric["id"])
    data = _case_input(blocks)
    original = database.compute_case_content_fingerprint(gastric["id"], data)
    reordered = {**data, "block_instances": list(reversed(data["block_instances"]))}
    assert database.compute_case_content_fingerprint(gastric["id"], reordered) != original

    conn = database.get_db_connection()
    conn.execute("UPDATE Snippets SET expansion = expansion || ' unrelated' WHERE shortcut = ?", ("absence_malignite",))
    conn.commit()
    conn.close()
    # This shared snippet is used by neither gastric template, so it cannot
    # invalidate a gastric draft.
    assert database.compute_case_content_fingerprint(gastric["id"], data) == original


def test_fingerprint_tracks_each_composition_shape_but_not_preset_category(mutable_db):
    gastric = _preset("gt")
    blocks = database.get_preset_blocks(gastric["id"])
    original_data = _case_input(blocks)
    original = database.compute_case_content_fingerprint(gastric["id"], original_data)

    removed = _case_input([blocks[0], blocks[2]])
    duplicated = _case_input([blocks[0], blocks[1], blocks[2], blocks[2]])
    duplicated["block_instances"][-1]["instance_no"] = 1000
    appendix = database.get_preset_blocks(_preset("dai")["id"])[0]
    added = _case_input([*blocks, appendix])
    added["block_instances"][-1]["instance_no"] = 1000
    reordered = {**original_data, "block_instances": list(reversed(original_data["block_instances"]))}
    for changed in (removed, duplicated, added, reordered):
        assert database.compute_case_content_fingerprint(gastric["id"], changed) != original

    conn = database.get_db_connection()
    conn.execute("UPDATE Presets SET category = category || ' changed', name = name || ' renamed' WHERE id = ?", (gastric["id"],))
    conn.commit()
    conn.close()
    # Neither value renders while this Preset has an explicit default_title.
    assert database.compute_case_content_fingerprint(gastric["id"], original_data) == original

    conn = database.get_db_connection()
    conn.execute("UPDATE Presets SET default_title = default_title || ' changed' WHERE id = ?", (gastric["id"],))
    conn.commit()
    conn.close()
    assert database.compute_case_content_fingerprint(gastric["id"], original_data) != original


def test_pending_save_records_fingerprint_and_content_revision(mutable_db):
    preset = _preset("dai")
    data = _case_input(database.get_preset_blocks(preset["id"]))
    assert database.save_case("PENDING-FP-1", preset["id"], "", data, "<p>draft</p>")
    saved = database.get_case_by_number("PENDING-FP-1")
    assert saved["content_fingerprint"] == database.compute_case_content_fingerprint(preset["id"], data)
    assert saved["content_revision_id"] is not None


def test_save_rejects_a_fingerprint_that_became_stale_before_write(mutable_db):
    preset = _preset("dai")
    data = _case_input(database.get_preset_blocks(preset["id"]))
    stale_fingerprint = database.compute_case_content_fingerprint(preset["id"], data)
    assert database.save_case(
        "STALE-FP-1", preset["id"], "", data, "<p>original</p>",
        content_fingerprint=stale_fingerprint,
    )
    conn = database.get_db_connection()
    conn.execute("UPDATE Blocks SET micro_template = micro_template || ' changed' WHERE key = 'appendice'")
    conn.commit()
    conn.close()

    assert not database.save_case(
        "STALE-FP-1", preset["id"], "", data, "<p>must not persist</p>",
        content_fingerprint=stale_fingerprint,
    )
    assert database.get_case_by_number("STALE-FP-1")["rendered_html"] == "<p>original</p>"


def test_snapshot_round_trip_preserves_content_ids_and_composed_case(mutable_db):
    gastric = _preset("gt")
    blocks = database.get_preset_blocks(gastric["id"])
    composed = _case_input([blocks[2], blocks[0], blocks[2]])
    composed["block_instances"][2]["instance_no"] = 1000
    assert database.save_case("COMPOSED-SNAPSHOT-1", gastric["id"], "", composed, "<p>draft</p>")
    before_ids = {row["key"]: row["id"] for row in database.get_all_editor_blocks()}
    conn = database.get_db_connection()
    before_quick_type_ids = [row["id"] for row in conn.execute("SELECT id FROM Quick_Type_Tokens ORDER BY id")]
    conn.close()
    snapshot = content_snapshot.export_content_snapshot()
    canonical = content_snapshot.content_snapshot_json(snapshot)
    assert json.loads(canonical) == snapshot
    assert content_snapshot.content_snapshot_json(content_snapshot.export_content_snapshot()) == canonical

    # New unrelated content is safe to discard on restore; the composed Case
    # remains untouched and every stable content ID stays stable.
    conn = database.get_db_connection()
    conn.execute("INSERT INTO Snippets (shortcut, expansion, category) VALUES (?, ?, ?)", ("temporary", "temp", "test"))
    conn.commit()
    conn.close()
    ok, error = content_snapshot.restore_content_snapshot(snapshot)
    assert ok, error
    assert {row["key"]: row["id"] for row in database.get_all_editor_blocks()} == before_ids
    conn = database.get_db_connection()
    assert [row["id"] for row in conn.execute("SELECT id FROM Quick_Type_Tokens ORDER BY id")] == before_quick_type_ids
    conn.close()
    assert database.get_case_by_number("COMPOSED-SNAPSHOT-1")["structured_input"] == composed
    assert database.get_snippet_by_shortcut("temporary") is None


def test_snapshot_refuses_changes_to_content_needed_by_saved_case(mutable_db):
    preset = _preset("dai")
    data = _case_input(database.get_preset_blocks(preset["id"]))
    assert database.save_case("RESTORE-REFUSE-1", preset["id"], "", data, "<p>draft</p>")
    snapshot = content_snapshot.export_content_snapshot()
    snapshot["tables"]["Blocks"] = [
        {**row, "micro_template": "different"} if row["key"] == "appendice" else row
        for row in snapshot["tables"]["Blocks"]
    ]
    ok, error = content_snapshot.restore_content_snapshot(snapshot)
    assert not ok
    assert "saved Case" in error


def test_snapshot_refuses_new_render_dependency_for_saved_case(mutable_db):
    preset = _preset("dai")
    data = _case_input(database.get_preset_blocks(preset["id"]))
    assert database.save_case("RESTORE-ADDITION-1", preset["id"], "", data, "<p>draft</p>")
    snapshot = content_snapshot.export_content_snapshot()
    changed = copy.deepcopy(snapshot)
    changed["tables"]["Fields"].append({
        "key": "restore_probe", "label": "Probe", "type": "checkbox", "options": None,
        "default_value": "1", "conclusion_addendum_template": "ADDED REPORT CONTENT",
    })
    changed["tables"]["Block_Fields"].append({
        "block_key": "appendice", "field_key": "restore_probe", "sort_order": 999,
        "label_override": None, "default_override": None, "context_section": 0,
    })
    changed["tables"]["Fields"].sort(key=lambda row: row["key"])
    changed["tables"]["Block_Fields"].sort(key=lambda row: (row["block_key"], row["field_key"]))
    ok, error = content_snapshot.restore_content_snapshot(changed)
    assert not ok
    assert "RESTORE-ADDITION-1" in error
    assert content_snapshot.export_content_snapshot() == snapshot


def test_snapshot_rejects_duplicate_relationship_identity(mutable_db):
    snapshot = content_snapshot.export_content_snapshot()
    malformed = copy.deepcopy(snapshot)
    malformed["tables"]["Block_Fields"].append(copy.deepcopy(malformed["tables"]["Block_Fields"][0]))
    ok, error = content_snapshot.restore_content_snapshot(malformed)
    assert not ok
    assert "duplicate Block_Fields stable key" in error
    assert content_snapshot.export_content_snapshot() == snapshot


def test_snapshot_preserves_consistency_rule_id_when_message_changes(mutable_db):
    snapshot = content_snapshot.export_content_snapshot()
    conn = database.get_db_connection()
    before = conn.execute("SELECT id, message FROM Field_Consistency_Rules ORDER BY id LIMIT 1").fetchone()
    conn.close()
    changed = copy.deepcopy(snapshot)
    changed["tables"]["Field_Consistency_Rules"][0]["message"] += " updated"
    ok, error = content_snapshot.restore_content_snapshot(changed)
    assert ok, error
    conn = database.get_db_connection()
    after = conn.execute("SELECT id, message FROM Field_Consistency_Rules ORDER BY id LIMIT 1").fetchone()
    conn.close()
    assert after["id"] == before["id"]
    assert after["message"] == before["message"] + " updated"


def test_snapshot_refuses_if_database_changes_during_candidate_validation(mutable_db, monkeypatch):
    snapshot = content_snapshot.export_content_snapshot()
    original_check = content_snapshot._assert_saved_cases_unchanged

    def mutate_after_check(current_conn, candidate_conn):
        original_check(current_conn, candidate_conn)
        concurrent = sqlite3.connect(mutable_db)
        concurrent.execute("UPDATE Presets SET category = category || ' concurrent' WHERE short_code = 'dai'")
        concurrent.commit()
        concurrent.close()

    monkeypatch.setattr(content_snapshot, "_assert_saved_cases_unchanged", mutate_after_check)
    ok, error = content_snapshot.restore_content_snapshot(snapshot)
    assert not ok
    assert "changed during snapshot validation" in error


def test_initializer_cli_refuses_existing_database_without_rebuild(mutable_db):
    result = subprocess.run(
        ["venv/bin/python", "init_db.py", "--db", mutable_db],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "Refusing to overwrite" in result.stderr
