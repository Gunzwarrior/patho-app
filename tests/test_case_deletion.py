"""PR1 permanent pending-Case deletion; every write uses ``mutable_db``."""

import database


def _preset_and_input():
    preset = next(row for row in database.get_all_presets() if row["short_code"] == "dai")
    blocks = database.get_preset_blocks(preset["id"])
    return preset, {
        "block_instances": [
            {"block_id": block["block_id"], "instance_no": block["sort_order"]}
            for block in blocks
        ],
        "blocks": {},
        "wildcard_notes": [],
        "master_lock": False,
    }


def _save(case_number, *, status="pending"):
    preset, structured_input = _preset_and_input()
    assert database.save_case(
        case_number, preset["id"], "clinical context", structured_input, "<p>report</p>", status=status
    )


def test_ordinary_pending_case_deletion_removes_case(mutable_db):
    _save("10001")

    assert database.delete_pending_case("10001")
    assert database.get_case_by_number("10001") is None
    assert not database.delete_pending_case("10001")


def test_validated_case_cannot_be_deleted_directly(mutable_db):
    _save("10002", status="validated")

    assert not database.delete_pending_case("10002")
    assert database.get_case_by_number("10002")["status"] == "validated"


def test_returned_to_pending_case_deletion_cleans_owned_history_and_references(mutable_db):
    _save("10003", status="validated")
    assert database.return_case_to_pending("10003", "entered in error")
    case = database.get_case_by_number("10003")
    assert case["status"] == "pending"

    conn = database.get_db_connection()
    try:
        revision_id = conn.execute("SELECT id FROM Content_Revisions ORDER BY id DESC LIMIT 1").fetchone()[0]
        conn.execute(
            """INSERT INTO Case_Content_Reference_Changes
               (revision_id, case_id, reference_kind) VALUES (?, ?, ?)""",
            (revision_id, case["id"], "preset"),
        )
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM Case_Validation_History WHERE case_id = ?", (case["id"],)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM Case_Status_History WHERE case_id = ?", (case["id"],)
        ).fetchone()[0] == 2
    finally:
        conn.close()

    assert database.delete_pending_case("10003")
    conn = database.get_db_connection()
    try:
        for table in (
            "Cases", "Case_Validation_History", "Case_Status_History", "Case_Content_Reference_Changes",
        ):
            column = "id" if table == "Cases" else "case_id"
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (case["id"],)
            ).fetchone()[0] == 0
    finally:
        conn.close()


def test_deletion_rolls_back_when_case_delete_fails(mutable_db):
    _save("10004", status="validated")
    assert database.return_case_to_pending("10004", "correction")
    case = database.get_case_by_number("10004")
    conn = database.get_db_connection()
    try:
        before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE case_id = ?", (case["id"],)).fetchone()[0]
            for table in ("Case_Validation_History", "Case_Status_History")
        }
        conn.execute(
            """CREATE TRIGGER reject_pr1_case_delete BEFORE DELETE ON Cases
               WHEN OLD.id = %d BEGIN SELECT RAISE(ABORT, 'refuse deletion'); END""" % case["id"]
        )
        conn.commit()
    finally:
        conn.close()

    assert not database.delete_pending_case("10004")
    assert database.get_case_by_number("10004")["status"] == "pending"
    conn = database.get_db_connection()
    try:
        for table, count in before.items():
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE case_id = ?", (case["id"],)
            ).fetchone()[0] == count
    finally:
        conn.close()


def test_batch_provenance_is_removed_only_when_its_last_case_is_deleted(mutable_db):
    _save("10005")
    _save("10006")
    conn = database.get_db_connection()
    try:
        revision_id = conn.execute("SELECT id FROM Content_Revisions ORDER BY id DESC LIMIT 1").fetchone()[0]
        batch_id = conn.execute(
            """INSERT INTO Case_Batch_Imports
               (row_count, normalized_input_sha256, content_snapshot_sha256, content_revision_id)
               VALUES (?, ?, ?, ?)""",
            (2, "input-hash", "content-hash", revision_id),
        ).lastrowid
        conn.execute(
            "UPDATE Cases SET batch_import_id = ? WHERE case_number IN (?, ?)",
            (batch_id, database.normalize_case_number("10005"), database.normalize_case_number("10006")),
        )
        conn.commit()
    finally:
        conn.close()

    assert database.delete_pending_case("10005")
    conn = database.get_db_connection()
    try:
        assert conn.execute("SELECT 1 FROM Case_Batch_Imports WHERE id = ?", (batch_id,)).fetchone()
    finally:
        conn.close()
    assert database.delete_pending_case("10006")
    conn = database.get_db_connection()
    try:
        assert conn.execute("SELECT 1 FROM Case_Batch_Imports WHERE id = ?", (batch_id,)).fetchone() is None
    finally:
        conn.close()
