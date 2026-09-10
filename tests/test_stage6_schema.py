"""Stage 6 checkpoint 1 storage and snapshot compatibility tests."""

import copy
import json
from pathlib import Path
import sqlite3

import pytest

import change_packages as packages
import content_changes
import content_editing
import content_snapshot
import database
import init_db
import operational_review


def _preset(code):
    return next(row for row in database.get_all_presets() if row["short_code"] == code)


def _case_input(blocks):
    return {
        "block_instances": [
            {"block_id": block["block_id"], "instance_no": block["sort_order"]}
            for block in blocks
        ],
        "blocks": {}, "wildcard_notes": [], "master_lock": False,
    }


def _v1(snapshot):
    legacy = copy.deepcopy(snapshot)
    legacy["format"] = content_snapshot.FORMAT_V1
    for table in ("Fields", "Blocks", "Presets", "Snippets"):
        for row in legacy["tables"][table]:
            row.pop("is_archived")
    for row in legacy["tables"]["Preset_Blocks"]:
        row.pop("display_order")
    return legacy


def _remove_stage6_columns(db_name):
    """Make a test-only copy look exactly like the pre-checkpoint schema."""
    conn = sqlite3.connect(db_name)
    try:
        conn.execute("DROP TABLE Case_Content_Reference_Changes")
        for table in ("Fields", "Blocks", "Presets", "Snippets"):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN is_archived")
        conn.execute("ALTER TABLE Preset_Blocks DROP COLUMN display_order")
        conn.execute("ALTER TABLE Cases DROP COLUMN preset_short_code_snapshot")
        conn.execute("ALTER TABLE Cases DROP COLUMN preset_name_snapshot")
        conn.execute("ALTER TABLE Case_Validation_History DROP COLUMN preset_short_code_snapshot")
        conn.execute("ALTER TABLE Case_Validation_History DROP COLUMN preset_name_snapshot")
        conn.execute(
            "DELETE FROM Schema_Migrations WHERE name = ?",
            ("stage6_persistence_compatibility_v1",),
        )
        conn.execute(
            "DELETE FROM Schema_Migrations WHERE name = ?",
            ("stage6_validation_history_preset_identity_repair_v1",),
        )
        conn.commit()
    finally:
        conn.close()


def test_stage6_migration_is_additive_idempotent_and_preserves_cases(mutable_db):
    preset = _preset("gt")
    data = _case_input(database.get_preset_blocks(preset["id"]))
    assert database.save_case("STAGE6-PENDING", preset["id"], "pending context", data, "<p>pending report</p>")
    assert database.save_case("STAGE6-VALID", preset["id"], "validated context", data, "<p>validated report</p>", status="validated")
    conn = database.get_db_connection()
    try:
        before_cases = [dict(row) for row in conn.execute(
            "SELECT id, case_number, preset_id, structured_input, rendered_html, content_fingerprint FROM Cases ORDER BY id"
        )]
        before_history = [dict(row) for row in conn.execute(
            "SELECT id, case_id, preset_id, structured_input, rendered_html, content_fingerprint FROM Case_Validation_History ORDER BY id"
        )]
        before_ids = {
            table: [tuple(row) for row in conn.execute(f"SELECT id FROM {table} ORDER BY id")]
            for table in ("Fields", "Blocks", "Presets", "Snippets")
        }
        before_blocks = [tuple(row) for row in conn.execute(
            "SELECT preset_id, block_id, sort_order FROM Preset_Blocks ORDER BY preset_id, sort_order, block_id"
        )]
    finally:
        conn.close()

    _remove_stage6_columns(mutable_db)
    database.migrate_schema(mutable_db)
    database.migrate_schema(mutable_db)

    conn = database.get_db_connection()
    try:
        assert all(
            "is_archived" in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in ("Fields", "Blocks", "Presets", "Snippets")
        )
        assert "display_order" in {row["name"] for row in conn.execute("PRAGMA table_info(Preset_Blocks)")}
        assert {"preset_short_code_snapshot", "preset_name_snapshot"} <= {
            row["name"] for row in conn.execute("PRAGMA table_info(Cases)")
        }
        assert {"preset_short_code_snapshot", "preset_name_snapshot"} <= {
            row["name"] for row in conn.execute("PRAGMA table_info(Case_Validation_History)")
        }
        assert conn.execute(
            "SELECT COUNT(*) FROM Schema_Migrations WHERE name = ?",
            ("stage6_persistence_compatibility_v1",),
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM Case_Content_Reference_Changes").fetchone()[0] == 0
        assert all(row[0] == 0 for row in conn.execute(
            "SELECT is_archived FROM Fields UNION ALL SELECT is_archived FROM Blocks "
            "UNION ALL SELECT is_archived FROM Presets UNION ALL SELECT is_archived FROM Snippets"
        ))
        assert [tuple(row) for row in conn.execute(
            "SELECT preset_id, block_id, sort_order FROM Preset_Blocks ORDER BY preset_id, sort_order, block_id"
        )] == before_blocks
        assert all(row["sort_order"] == row["display_order"] for row in conn.execute(
            "SELECT sort_order, display_order FROM Preset_Blocks"
        ))
        after_cases = [dict(row) for row in conn.execute(
            "SELECT id, case_number, preset_id, structured_input, rendered_html, content_fingerprint FROM Cases ORDER BY id"
        )]
        after_history = [dict(row) for row in conn.execute(
            "SELECT id, case_id, preset_id, structured_input, rendered_html, content_fingerprint FROM Case_Validation_History ORDER BY id"
        )]
        after_ids = {
            table: [tuple(row) for row in conn.execute(f"SELECT id FROM {table} ORDER BY id")]
            for table in ("Fields", "Blocks", "Presets", "Snippets")
        }
        snapshots = [dict(row) for row in conn.execute(
            "SELECT preset_short_code_snapshot, preset_name_snapshot FROM Cases ORDER BY id"
        )]
        history_snapshots = [dict(row) for row in conn.execute(
            "SELECT preset_short_code_snapshot, preset_name_snapshot FROM Case_Validation_History ORDER BY id"
        )]
    finally:
        conn.close()
    assert after_cases == before_cases
    assert after_history == before_history
    assert after_ids == before_ids
    assert snapshots == [{"preset_short_code_snapshot": "gt", "preset_name_snapshot": "Gastric Trio"}] * 2
    assert history_snapshots == [{"preset_short_code_snapshot": "gt", "preset_name_snapshot": "Gastric Trio"}]


def _save_revalidated_case(case_number):
    dai = _preset("dai")
    gt = _preset("gt")
    assert database.save_case(
        case_number, dai["id"], "first validation", _case_input(database.get_preset_blocks(dai["id"])),
        "<p>first validated report</p>", status="validated",
    )
    assert database.return_case_to_pending(case_number, "new specimen")
    assert database.save_case(
        case_number, gt["id"], "second validation", _case_input(database.get_preset_blocks(gt["id"])),
        "<p>second validated report</p>", status="validated",
    )
    return dai, gt


def test_stage6_history_backfill_uses_its_own_preset_without_case_fallback(mutable_db):
    dai, gt = _save_revalidated_case("STAGE6-HISTORY-BACKFILL")
    conn = database.get_db_connection()
    try:
        case = conn.execute(
            "SELECT id, preset_id, status, rendered_html FROM Cases WHERE case_number = ?",
            ("STAGE6-HISTORY-BACKFILL",),
        ).fetchone()
        conn.execute(
            """INSERT INTO Case_Validation_History
               (case_id, rendered_html, structured_input, clinical_info, preset_id)
               VALUES (?, ?, ?, ?, ?)""",
            (case["id"], "<p>null-preset report</p>", "{}", "legacy null", None),
        )
        conn.execute(
            """INSERT INTO Case_Validation_History
               (case_id, rendered_html, structured_input, clinical_info, preset_id)
               VALUES (?, ?, ?, ?, ?)""",
            (case["id"], "<p>missing-preset report</p>", "{}", "legacy missing", 999999),
        )
        conn.commit()
    finally:
        conn.close()

    _remove_stage6_columns(mutable_db)
    database.migrate_schema(mutable_db)
    database.migrate_schema(mutable_db)

    conn = database.get_db_connection()
    try:
        case_after = dict(conn.execute(
            "SELECT id, preset_id, status, rendered_html FROM Cases WHERE case_number = ?",
            ("STAGE6-HISTORY-BACKFILL",),
        ).fetchone())
        history = [dict(row) for row in conn.execute(
            """SELECT id, preset_id, rendered_html, structured_input,
                      preset_short_code_snapshot, preset_name_snapshot
               FROM Case_Validation_History WHERE case_id = ? ORDER BY id""",
            (case_after["id"],),
        )]
    finally:
        conn.close()

    assert case_after == dict(case)
    assert [(row["preset_id"], row["preset_short_code_snapshot"], row["preset_name_snapshot"])
            for row in history] == [
        (dai["id"], "dai", "Appendice"),
        (gt["id"], "gt", "Gastric Trio"),
        (None, None, None),
        (999999, None, None),
    ]
    assert [row["rendered_html"] for row in history] == [
        "<p>first validated report</p>", "<p>second validated report</p>",
        "<p>null-preset report</p>", "<p>missing-preset report</p>",
    ]


def test_stage6_history_identity_repair_corrects_only_proven_legacy_backfill(mutable_db):
    dai, gt = _save_revalidated_case("STAGE6-HISTORY-REPAIR")
    assert database.save_case(
        "STAGE6-UNRELATED-HISTORY", dai["id"], "unrelated", _case_input(database.get_preset_blocks(dai["id"])),
        "<p>unrelated validated report</p>", status="validated",
    )
    conn = database.get_db_connection()
    try:
        case = dict(conn.execute(
            "SELECT * FROM Cases WHERE case_number = ?", ("STAGE6-HISTORY-REPAIR",)
        ).fetchone())
        history = [dict(row) for row in conn.execute(
            "SELECT * FROM Case_Validation_History WHERE case_id = ? ORDER BY id", (case["id"],)
        )]
        unrelated = dict(conn.execute(
            """SELECT h.* FROM Case_Validation_History h JOIN Cases c ON c.id = h.case_id
               WHERE c.case_number = ?""",
            ("STAGE6-UNRELATED-HISTORY",),
        ).fetchone())
        for row in history:
            conn.execute(
                """UPDATE Case_Validation_History
                   SET preset_short_code_snapshot = ?, preset_name_snapshot = ? WHERE id = ?""",
                ("gt", "Gastric Trio", row["id"]),
            )
        conn.execute(
            """INSERT INTO Case_Validation_History
               (case_id, rendered_html, structured_input, clinical_info, preset_id,
                preset_short_code_snapshot, preset_name_snapshot)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (case["id"], "<p>legacy missing-preset report</p>", "{}", "legacy missing", 999999,
             "gt", "Gastric Trio"),
        )
        conn.execute(
            """INSERT INTO Case_Validation_History
               (case_id, rendered_html, structured_input, clinical_info, preset_id,
                preset_short_code_snapshot, preset_name_snapshot)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (case["id"], "<p>deleted-preset report</p>", "{}", "deleted preset", 999998,
             "old", "Deleted historical preset"),
        )
        conn.execute(
            """INSERT INTO Case_Validation_History
               (case_id, rendered_html, structured_input, clinical_info, preset_id,
                preset_short_code_snapshot, preset_name_snapshot)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (case["id"], "<p>legacy null-preset report</p>", "{}", "legacy null", None,
             "gt", "Gastric Trio"),
        )
        conn.execute(
            "UPDATE Schema_Migrations SET applied_at = ? WHERE name = ?",
            ("2099-01-01 00:00:00", "stage6_persistence_compatibility_v1"),
        )
        conn.execute(
            "DELETE FROM Schema_Migrations WHERE name = ?",
            ("stage6_validation_history_preset_identity_repair_v1",),
        )
        conn.commit()
    finally:
        conn.close()

    database.migrate_schema(mutable_db)
    conn = database.get_db_connection()
    try:
        repaired_case = dict(conn.execute(
            "SELECT * FROM Cases WHERE id = ?", (case["id"],)
        ).fetchone())
        repaired_history = [dict(row) for row in conn.execute(
            "SELECT * FROM Case_Validation_History WHERE case_id = ? ORDER BY id", (case["id"],)
        )]
        repaired_unrelated = dict(conn.execute(
            "SELECT * FROM Case_Validation_History WHERE id = ?", (unrelated["id"],)
        ).fetchone())
        marker_count = conn.execute(
            "SELECT COUNT(*) FROM Schema_Migrations WHERE name = ?",
            ("stage6_validation_history_preset_identity_repair_v1",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert repaired_case == case
    assert repaired_unrelated == unrelated
    assert [(row["preset_id"], row["preset_short_code_snapshot"], row["preset_name_snapshot"])
            for row in repaired_history] == [
        (dai["id"], "dai", "Appendice"),
        (gt["id"], "gt", "Gastric Trio"),
        (999999, "gt", "Gastric Trio"),
        (999998, "old", "Deleted historical preset"),
        (None, "gt", "Gastric Trio"),
    ]
    assert [(row["id"], row["rendered_html"], row["structured_input"], row["clinical_info"])
            for row in repaired_history] == [
        (history[0]["id"], history[0]["rendered_html"], history[0]["structured_input"], history[0]["clinical_info"]),
        (history[1]["id"], history[1]["rendered_html"], history[1]["structured_input"], history[1]["clinical_info"]),
        (repaired_history[2]["id"], "<p>legacy missing-preset report</p>", "{}", "legacy missing"),
        (repaired_history[3]["id"], "<p>deleted-preset report</p>", "{}", "deleted preset"),
        (repaired_history[4]["id"], "<p>legacy null-preset report</p>", "{}", "legacy null"),
    ]
    assert marker_count == 1

    database.migrate_schema(mutable_db)
    conn = database.get_db_connection()
    try:
        rerun_history = [dict(row) for row in conn.execute(
            "SELECT * FROM Case_Validation_History WHERE case_id = ? ORDER BY id", (case["id"],)
        )]
        rerun_marker_count = conn.execute(
            "SELECT COUNT(*) FROM Schema_Migrations WHERE name = ?",
            ("stage6_validation_history_preset_identity_repair_v1",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert rerun_history == repaired_history
    assert rerun_marker_count == 1


def test_stage6_history_repair_preserves_post_marker_identity_after_preset_rename(mutable_db):
    conn = database.get_db_connection()
    try:
        conn.execute(
            "UPDATE Schema_Migrations SET applied_at = ? WHERE name = ?",
            ("2000-01-01 00:00:00", "stage6_persistence_compatibility_v1"),
        )
        conn.commit()
    finally:
        conn.close()
    dai, _ = _save_revalidated_case("STAGE6-POST-MARKER")
    conn = database.get_db_connection()
    try:
        history = dict(conn.execute(
            """SELECT h.* FROM Case_Validation_History h JOIN Cases c ON c.id = h.case_id
               WHERE c.case_number = ? ORDER BY h.id LIMIT 1""",
            ("STAGE6-POST-MARKER",),
        ).fetchone())
        conn.execute("UPDATE Presets SET name = ? WHERE id = ?", ("Appendice renommée", dai["id"]))
        conn.execute(
            "UPDATE Schema_Migrations SET applied_at = ? WHERE name = ?",
            (history["validated_at"], "stage6_persistence_compatibility_v1"),
        )
        conn.execute(
            "DELETE FROM Schema_Migrations WHERE name = ?",
            ("stage6_validation_history_preset_identity_repair_v1",),
        )
        conn.commit()
    finally:
        conn.close()

    database.migrate_schema(mutable_db)
    conn = database.get_db_connection()
    try:
        after = dict(conn.execute(
            "SELECT * FROM Case_Validation_History WHERE id = ?", (history["id"],)
        ).fetchone())
    finally:
        conn.close()
    assert after == history


def test_stage6_history_repair_preserves_unavailable_historical_preset_identity(mutable_db):
    preset = _preset("dai")
    assert database.save_case(
        "STAGE6-UNAVAILABLE", preset["id"], "unavailable", _case_input(database.get_preset_blocks(preset["id"])),
        "<p>unavailable report</p>", status="validated",
    )
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.row_factory = sqlite3.Row
        history = dict(conn.execute(
            """SELECT h.* FROM Case_Validation_History h JOIN Cases c ON c.id = h.case_id
               WHERE c.case_number = ?""",
            ("STAGE6-UNAVAILABLE",),
        ).fetchone())
        conn.execute("DELETE FROM Presets WHERE id = ?", (preset["id"],))
        conn.execute(
            "UPDATE Schema_Migrations SET applied_at = ? WHERE name = ?",
            ("2099-01-01 00:00:00", "stage6_persistence_compatibility_v1"),
        )
        conn.execute(
            "DELETE FROM Schema_Migrations WHERE name = ?",
            ("stage6_validation_history_preset_identity_repair_v1",),
        )
        conn.commit()
    finally:
        conn.close()

    database.migrate_schema(mutable_db)
    conn = database.get_db_connection()
    try:
        after = dict(conn.execute(
            "SELECT * FROM Case_Validation_History WHERE id = ?", (history["id"],)
        ).fetchone())
    finally:
        conn.close()
    assert after == history


def test_saving_and_validating_a_case_freezes_its_live_preset_identity(mutable_db):
    preset = _preset("dai")
    data = _case_input(database.get_preset_blocks(preset["id"]))
    assert database.save_case("STAGE6-FROZEN", preset["id"], "", data, "<p>pending</p>")
    assert database.save_case("STAGE6-FROZEN", preset["id"], "", data, "<p>validated</p>", status="validated")
    case = database.get_case_by_number("STAGE6-FROZEN")
    history = database.get_case_validation_history("STAGE6-FROZEN")
    assert (case["preset_short_code_snapshot"], case["preset_name_snapshot"]) == ("dai", "Appendice")
    assert [(row["preset_short_code_snapshot"], row["preset_name_snapshot"]) for row in history] == [
        ("dai", "Appendice")
    ]


def test_snapshot_v1_normalisation_is_read_only_and_v2_round_trips(mutable_db, tmp_path):
    v2 = content_snapshot.export_content_snapshot()
    v1 = _v1(v2)
    original_v1_bytes = content_snapshot.content_snapshot_json(v1)
    assert content_snapshot.normalize_content_snapshot(v1) == v2
    assert content_snapshot.normalize_content_snapshot(v1) == v2
    assert content_snapshot.content_snapshot_json(v1) == original_v1_bytes
    assert content_snapshot.content_snapshot_hash(v1) != content_snapshot.content_snapshot_hash(v2)
    content_snapshot.validate_content_snapshot(v1)

    restored_db = tmp_path / "stage6_restore.db"
    init_db.setup_database(db_name=str(restored_db))
    ok, error = content_snapshot.restore_content_snapshot(v1, db_name=str(restored_db))
    assert ok, error
    assert content_snapshot.export_content_snapshot(str(restored_db)) == v2
    canonical_v2 = content_snapshot.content_snapshot_json(v2)
    ok, error = content_snapshot.restore_content_snapshot(v2, db_name=str(restored_db))
    assert ok, error
    assert content_snapshot.content_snapshot_json(
        content_snapshot.export_content_snapshot(str(restored_db))
    ) == canonical_v2


def test_operational_artifacts_accept_both_snapshot_versions(tmp_path, db):
    v2 = content_snapshot.export_content_snapshot(db)
    v1 = _v1(v2)
    v1_path, v2_path = tmp_path / "legacy.json", tmp_path / "current.json"
    v1_path.write_text(content_snapshot.content_snapshot_json(v1), encoding="utf-8")
    v2_path.write_text(content_snapshot.content_snapshot_json(v2), encoding="utf-8")
    legacy = operational_review.generate(v1_path, tmp_path / "legacy-artifact.json")
    current = operational_review.generate(v2_path, tmp_path / "current-artifact.json")
    assert legacy["reports"] == current["reports"]
    assert legacy["source_snapshot_sha256"] == current["source_snapshot_sha256"]
    assert current["source_snapshot_sha256"] == content_snapshot.content_snapshot_hash(v2)


def test_ai_v1_documents_and_refuses_archived_targets(mutable_db):
    contract = packages.authoring_contract()
    assert "Archived Fields, Blocks, Presets, and Snippets" in contract["archived_content"]
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute("UPDATE Snippets SET is_archived = 1 WHERE shortcut = 'niv'")
        conn.execute("UPDATE Blocks SET is_archived = 1 WHERE key = 'appendice'")
        conn.commit()
    finally:
        conn.close()
    base_hash = content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(mutable_db))
    archived_update = {
        "format": packages.FORMAT, "base_snapshot_sha256": base_hash, "summary": "must refuse",
        "operations": [{"op": "update", "table": "Snippets", "key": "niv", "set": {"expansion": "Changed."}}],
    }
    with pytest.raises(packages.PackageError) as error:
        packages.dry_run(json.dumps(archived_update).encode(), mutable_db)
    assert error.value.ai_feedback()["errors"][0]["code"] == "target"

    archived_link = {
        "format": packages.FORMAT, "base_snapshot_sha256": base_hash, "summary": "must refuse link",
        "operations": [
            {"op": "create", "table": "Presets", "key": "archive_probe", "values": {"name": "Probe"}},
            {"op": "link", "table": "Preset_Blocks",
             "key": {"preset_code": "archive_probe", "block_key": "appendice", "sort_order": 0},
             "values": {"field_overrides": {}}},
        ],
    }
    with pytest.raises(packages.PackageError) as error:
        packages.dry_run(json.dumps(archived_link).encode(), mutable_db)
    assert error.value.ai_feedback()["errors"][0]["code"] == "target"


def test_legacy_stage5_audit_images_remain_readable_after_migration(mutable_db):
    conn = sqlite3.connect(mutable_db)
    conn.row_factory = sqlite3.Row
    try:
        after = dict(conn.execute("SELECT * FROM Snippets WHERE shortcut = 'absence_malignite'").fetchone())
        before = {**after, "expansion": "Ancienne phrase."}
        legacy_before = {key: value for key, value in before.items() if key != "is_archived"}
        legacy_after = {key: value for key, value in after.items() if key != "is_archived"}
        conn.execute("UPDATE Snippets SET expansion = ? WHERE shortcut = 'absence_malignite'", (after["expansion"],))
        conn.execute("INSERT INTO Content_Revisions(origin, summary) VALUES (?, ?)", ("ai_package", "legacy image"))
        revision_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO Content_Changes
               (revision_id, table_name, entity_key, operation, before_json, after_json, before_hash, after_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                revision_id, "Snippets", "absence_malignite", "update",
                json.dumps(legacy_before, sort_keys=True), json.dumps(legacy_after, sort_keys=True),
                content_editing.row_hash(legacy_before), content_editing.row_hash(legacy_after),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    review = content_changes.review_inverse(revision_id, db_name=mutable_db)
    assert review.changes[0]["before"]["is_archived"] == 0
    assert review.changes[0]["after"]["is_archived"] == 0
