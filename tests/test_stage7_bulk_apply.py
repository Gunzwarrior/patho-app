"""Stage 7 CP5: atomic creation of reviewed pending Cases."""

from dataclasses import replace
import json
import shutil
import sqlite3
import threading

import bulk_intake
import change_packages
import content_snapshot
import database
import editor_preview
import operational_review
import pytest
from streamlit.testing.v1 import AppTest


def _counts(conn):
    return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
        "Cases", "Case_Batch_Imports", "Case_Validation_History", "Case_Status_History",
    ))


def test_apply_creates_one_linked_audit_and_normal_pending_cases(mutable_db):
    source = "CP5-ONE,dai37\nCP5-TWO,etc2"
    review = bulk_intake.prepare_bulk_review(source, ",", False)
    assert review.applicable, review.errors

    result = bulk_intake.apply_bulk_review(review, source, ",", False, confirmed=True)
    assert result
    assert result.row_count == 2

    conn = database.get_db_connection()
    try:
        audit = conn.execute("SELECT * FROM Case_Batch_Imports").fetchone()
        assert audit["id"] == result.batch_import_id
        assert dict(audit)["row_count"] == 2
        assert audit["normalized_input_sha256"] == review.normalized_source_sha256
        assert audit["content_snapshot_sha256"] == review.content_snapshot_sha256
        assert audit["content_revision_id"] == review.content_revision_id
        saved = conn.execute(
            "SELECT * FROM Cases WHERE batch_import_id=? ORDER BY case_number", (audit["id"],)
        ).fetchall()
        assert len(saved) == 2
        by_number = {row["case_number"]: row for row in saved}
        for prepared in review.rows:
            case = by_number[prepared.case_number]
            assert case["status"] == "pending"
            assert case["pending_reason"] is None
            assert case["structured_input"] == prepared.structured_input_json
            assert case["rendered_html"] == prepared.rendered_html
            assert case["content_revision_id"] == review.content_revision_id
            assert case["preset_short_code_snapshot"] == prepared.preset_code
            assert case["preset_name_snapshot"] == prepared.preset_name
            assert case["content_fingerprint"] == database.compute_case_content_fingerprint(
                prepared.preset_id, prepared.structured_input, conn
            )
            reconstructed = editor_preview.render_saved_case(conn, {
                "status": "pending", "preset_id": case["preset_id"],
                "clinical_info": case["clinical_info"], "structured_input": prepared.structured_input,
            }, strict=True)
            assert reconstructed["html"] == prepared.rendered_html
        assert conn.execute("SELECT COUNT(*) FROM Case_Validation_History").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM Case_Status_History").fetchone()[0] == 0
    finally:
        conn.close()

    # A batch-created pending Case remains an ordinary Workspace Case: an
    # individual pending edit/validation has the established save behavior.
    first = review.rows[0]
    assert database.save_case(first.case_number, first.preset_id, first.clinical_info,
                              first.structured_input, first.rendered_html, status="validated")
    reopened = database.get_case_by_number(first.case_number)
    assert reopened["status"] == "validated"
    conn = database.get_db_connection()
    try:
        assert conn.execute(
            "SELECT batch_import_id FROM Cases WHERE case_number=?", (first.case_number,)
        ).fetchone()[0] == result.batch_import_id
    finally:
        conn.close()


def test_bulk_created_decimal_case_reopens_through_workspace_widget_hydration(mutable_db):
    """Bulk input must use the same decimal text wire type as Workspace saves."""
    source = "CP5-WORKSPACE-DECIMAL,dai37"
    review = bulk_intake.prepare_bulk_review(source, ",", False)
    assert review.applicable, review.errors
    assert bulk_intake.apply_bulk_review(review, source, ",", False, confirmed=True)

    saved = database.get_case_by_number("CP5-WORKSPACE-DECIMAL")
    appendix = database.get_preset_blocks(saved["preset_id"])[0]
    saved_value = saved["structured_input"]["blocks"]["appendice#0"]["appendix_size_cm"]
    # Workspace's decimal control is text_input; a semantic float is valid
    # for rendering but invalid when restored into Streamlit widget state.
    assert saved_value == "7"
    assert isinstance(saved_value, str)

    conn = database.get_db_connection()
    try:
        reconstructed = editor_preview.render_saved_case(conn, saved, strict=True)
        assert reconstructed["html"] == saved["rendered_html"]
    finally:
        conn.close()

    workspace = AppTest.from_file("pages/workspace.py").run()
    workspace.session_state["_reopen_case_number"] = saved["case_number"]
    workspace.session_state["_do_case_reopen"] = True
    workspace.run()
    assert not workspace.exception
    generation = workspace.session_state["_form_generation"]
    widget_key = f"field_{appendix['block_id']}_0_appendix_size_cm_{generation}"
    assert workspace.text_input(key=widget_key).value == "7"


def test_bulk_cases_persist_workspace_wire_values_for_every_supported_field_type(mutable_db):
    """One representative Case covers Workspace's five widget wire shapes."""
    conn = database.get_db_connection()
    try:
        appendix_id = conn.execute("SELECT id FROM Blocks WHERE key='appendice'").fetchone()[0]
        fragments_id = conn.execute("SELECT id FROM Fields WHERE key='fragments'").fetchone()[0]
        conn.execute(
            "INSERT INTO Block_Fields(block_id,field_id,sort_order) VALUES (?,?,?)",
            (appendix_id, fragments_id, 90),
        )
        conn.execute(
            """INSERT INTO Fields(key,label,type,default_value)
               VALUES ('cp5_widget_text','CP5 widget text','text','bulk note')"""
        )
        text_id = conn.execute("SELECT id FROM Fields WHERE key='cp5_widget_text'").fetchone()[0]
        conn.execute(
            "INSERT INTO Block_Fields(block_id,field_id,sort_order) VALUES (?,?,?)",
            (appendix_id, text_id, 91),
        )
        conn.commit()
    finally:
        conn.close()

    review = bulk_intake.prepare_bulk_review("CP5-WIDGET-TYPES,dai37", ",", False)
    assert review.applicable, review.errors
    assert bulk_intake.apply_bulk_review(review, "CP5-WIDGET-TYPES,dai37", ",", False, confirmed=True)
    saved = database.get_case_by_number("CP5-WIDGET-TYPES")
    values = saved["structured_input"]["blocks"]["appendice#0"]
    assert values["appendicite_type"] == "periappendicite"  # select
    assert values["appendix_size_cm"] == "7"                # decimal text_input
    assert values["false_membranes"] is False                # checkbox
    assert values["fragments"] == 1                          # number_input
    assert values["cp5_widget_text"] == "bulk note"         # text_input

    workspace = AppTest.from_file("pages/workspace.py").run()
    workspace.session_state["_reopen_case_number"] = saved["case_number"]
    workspace.session_state["_do_case_reopen"] = True
    workspace.run()
    assert not workspace.exception


def test_replacement_review_clears_checked_confirmation_and_warning_acknowledgement(mutable_db):
    """OLD review consent cannot authorize a NEW warning-bearing review."""
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute(
            """INSERT INTO Quick_Type_Tokens
               (preset_id,sort_order,block_sort_order,field_key,token_kind,lookup_table,digit_width)
               VALUES ((SELECT id FROM Presets WHERE short_code='dai'),2,0,
                       'false_membranes','lookup','{"f":true}',NULL)"""
        )
        conn.commit()
    finally:
        conn.close()
    app = AppTest.from_file("pages/bulk_intake.py").run()
    app.text_area(key="bulk_paste_source").set_value("CP5-OLD,dai11f").run()
    app.button(key="bulk_prepare").click().run()
    app.checkbox(key="bulk_apply_confirm").set_value(True).run()
    app.checkbox(key="bulk_warning_acknowledged").set_value(True).run()

    # The changed source discards OLD before NEW is issued. Both controls
    # must be freshly false even though their widget keys are stable.
    app.text_area(key="bulk_paste_source").set_value("CP5-NEW,dai11f").run()
    app.button(key="bulk_prepare").click().run()
    assert app.checkbox(key="bulk_apply_confirm").value is False
    assert app.checkbox(key="bulk_warning_acknowledged").value is False
    app.button(key="bulk_apply").click().run()
    assert any("Confirm this batch" in item.value for item in app.error)
    app.checkbox(key="bulk_apply_confirm").set_value(True).run()
    app.button(key="bulk_apply").click().run()
    assert any("Acknowledge the batch" in item.value for item in app.error)


def test_apply_requires_confirmation_issued_review_and_warning_acknowledgement(mutable_db):
    source = "CP5-GATE,dai37"
    review = bulk_intake.prepare_bulk_review(source, ",", False)
    assert not bulk_intake.apply_bulk_review(review, source, ",", False)
    assert not bulk_intake.apply_bulk_review(
        replace(review, issuer_signature=None), source, ",", False, confirmed=True
    )

    # Make one warning-bearing review using the normal grammar/rule path.
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute(
            """INSERT INTO Quick_Type_Tokens
               (preset_id,sort_order,block_sort_order,field_key,token_kind,lookup_table,digit_width)
               VALUES ((SELECT id FROM Presets WHERE short_code='dai'),2,0,
                       'false_membranes','lookup','{"f":true}',NULL)"""
        )
        conn.commit()
    finally:
        conn.close()
    warning_source = "CP5-WARN,dai11f"
    warning_review = bulk_intake.prepare_bulk_review(warning_source, ",", False)
    assert warning_review.applicable and warning_review.rows[0].warnings
    rejected = bulk_intake.apply_bulk_review(
        warning_review, warning_source, ",", False, confirmed=True
    )
    assert not rejected and "Acknowledge" in rejected.error
    acknowledgement = bulk_intake.acknowledge_batch_warnings(warning_review)
    assert acknowledgement is not None
    assert bulk_intake.apply_bulk_review(
        warning_review, warning_source, ",", False, confirmed=True,
        warning_acknowledgement=acknowledgement,
    )


def test_apply_rechecks_source_content_and_target_absence_without_partial_rows(mutable_db):
    source = "CP5-RACE-ONE,dai37\nCP5-RACE-TWO,etc2"
    review = bulk_intake.prepare_bulk_review(source, ",", False)
    assert review.applicable
    conn = database.get_db_connection()
    try:
        before = _counts(conn)
        source_changed = bulk_intake.apply_bulk_review(
            review, "CP5-RACE-ONE,dai\nCP5-RACE-TWO,etc2", ",", False, confirmed=True, conn=conn
        )
        assert not source_changed and "input changed" in source_changed.error
        assert _counts(conn) == before
    finally:
        conn.close()

    content_review = bulk_intake.prepare_bulk_review("CP5-CONTENT,dai37", ",", False)
    conn = database.get_db_connection()
    try:
        shortcut = conn.execute("SELECT shortcut FROM Snippets ORDER BY shortcut LIMIT 1").fetchone()[0]
        conn.execute("UPDATE Snippets SET expansion=expansion || ' ' WHERE shortcut=?", (shortcut,))
        conn.commit()
    finally:
        conn.close()
    content_changed = bulk_intake.apply_bulk_review(
        content_review, "CP5-CONTENT,dai37", ",", False, confirmed=True
    )
    assert not content_changed and "Content changed" in content_changed.error

    # Replacing materialized review data without an issuer proof for its new
    # interpretation is rejected before it can reach the write transaction.
    altered_row = replace(content_review.rows[0], rendered_html="<p>altered</p>")
    altered = replace(content_review, rows=(altered_row,))
    altered_result = bulk_intake.apply_bulk_review(
        altered, "CP5-CONTENT,dai37", ",", False, confirmed=True
    )
    assert not altered_result and "altered" in altered_result.error

    # Exact target occupancy is checked after the write lock, not merely by
    # the preview-side stale indicator.
    target_review = bulk_intake.prepare_bulk_review("CP5-TARGET,dai37", ",", False)
    row = target_review.rows[0]
    assert database.save_case(row.case_number, row.preset_id, row.clinical_info,
                              row.structured_input, row.rendered_html)
    target_taken = bulk_intake.apply_bulk_review(
        target_review, "CP5-TARGET,dai37", ",", False, confirmed=True
    )
    assert not target_taken and "occupied" in target_taken.error
    conn = database.get_db_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM Case_Batch_Imports").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM Cases WHERE case_number='CP5-TARGET'").fetchone()[0] == 1
    finally:
        conn.close()


def test_apply_rolls_back_audit_and_every_case_when_inner_write_fails(mutable_db, monkeypatch):
    source = "CP5-ROLLBACK-ONE,dai37\nCP5-ROLLBACK-TWO,etc2"
    review = bulk_intake.prepare_bulk_review(source, ",", False)
    original = database.persist_case_on_connection
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.IntegrityError("fault injection")
        return original(*args, **kwargs)

    monkeypatch.setattr(database, "persist_case_on_connection", fail_second)
    result = bulk_intake.apply_bulk_review(review, source, ",", False, confirmed=True)
    assert not result
    conn = database.get_db_connection()
    try:
        assert _counts(conn) == (0, 0, 0, 0)
    finally:
        conn.close()


def test_apply_rolls_back_when_batch_audit_insert_fails(mutable_db):
    source = "CP5-AUDIT-FAIL,dai37"
    review = bulk_intake.prepare_bulk_review(source, ",", False)
    conn = database.get_db_connection()
    try:
        conn.execute(
            """CREATE TRIGGER reject_cp5_audit BEFORE INSERT ON Case_Batch_Imports
               BEGIN SELECT RAISE(ABORT, 'fault injection'); END"""
        )
        conn.commit()
    finally:
        conn.close()
    result = bulk_intake.apply_bulk_review(review, source, ",", False, confirmed=True)
    assert not result
    conn = database.get_db_connection()
    try:
        assert _counts(conn) == (0, 0, 0, 0)
    finally:
        conn.close()


def test_migration_is_repeatable_and_ordinary_cases_have_no_batch_link(mutable_db):
    conn = database.get_db_connection()
    try:
        preset_id = conn.execute("SELECT id FROM Presets WHERE short_code='dai'").fetchone()[0]
    finally:
        conn.close()
    assert database.save_case("CP5-HISTORIC", preset_id, "", {"blocks": {}}, "<p>saved</p>")
    database.migrate_schema(mutable_db)
    database.migrate_schema(mutable_db)
    conn = database.get_db_connection()
    try:
        assert conn.execute(
            "SELECT batch_import_id FROM Cases WHERE case_number='CP5-HISTORIC'"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='Cases_batch_import_id_idx'"
        ).fetchone()
    finally:
        conn.close()


def test_migration_upgrades_a_genuine_pre_cp5_case_schema_without_backfill(mutable_db, tmp_path):
    """Exercise the additive migration from a database with no CP5 objects."""
    conn = database.get_db_connection()
    try:
        preset_id = conn.execute("SELECT id FROM Presets WHERE short_code='dai'").fetchone()[0]
    finally:
        conn.close()
    assert database.save_case("CP5-PRE-MIGRATION", preset_id, "", {"blocks": {}}, "<p>historic</p>")

    legacy_path = tmp_path / "pre_cp5.sqlite"
    shutil.copy(mutable_db, legacy_path)
    legacy = sqlite3.connect(legacy_path)
    try:
        # This is the actual pre-CP5 shape, derived from the otherwise-live
        # Stage 7 schema without rebuilding or changing historic Case data.
        legacy.execute("DROP INDEX Cases_batch_import_id_idx")
        legacy.execute("DROP TABLE Case_Batch_Imports")
        legacy.execute("ALTER TABLE Cases DROP COLUMN batch_import_id")
        legacy.execute(
            "DELETE FROM Schema_Migrations WHERE name='stage7_case_batch_import_provenance_v1'"
        )
        legacy.commit()
    finally:
        legacy.close()

    database.migrate_schema(str(legacy_path))
    database.migrate_schema(str(legacy_path))
    upgraded = sqlite3.connect(legacy_path)
    try:
        assert upgraded.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Case_Batch_Imports'"
        ).fetchone()
        columns = {row[1]: row for row in upgraded.execute("PRAGMA table_info(Cases)")}
        assert "batch_import_id" in columns and columns["batch_import_id"][3] == 0
        assert upgraded.execute(
            "SELECT batch_import_id FROM Cases WHERE case_number='CP5-PRE-MIGRATION'"
        ).fetchone()[0] is None
        assert upgraded.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='Cases_batch_import_id_idx'"
        ).fetchone()
    finally:
        upgraded.close()


def test_shared_case_persistence_is_transaction_neutral_and_caller_owns_rollback(mutable_db):
    review = bulk_intake.prepare_bulk_review("CP5-NEUTRAL,dai37", ",", False)
    row = review.rows[0]
    conn = database.get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        database.persist_case_on_connection(
            conn, row.case_number, row.preset_id, row.clinical_info,
            row.structured_input, row.rendered_html, status="pending", pending_reason=None,
            mode="create",
        )
        assert conn.in_transaction
        assert conn.execute("SELECT 1 FROM Cases WHERE case_number=?", (row.case_number,)).fetchone()
        conn.rollback()
        assert not conn.in_transaction
    finally:
        conn.close()
    assert database.get_case_by_number(row.case_number) is None


def test_two_connections_cannot_claim_one_reviewed_target_namespace(mutable_db):
    first = database.get_db_connection()
    second = database.get_db_connection()
    try:
        review = bulk_intake.prepare_bulk_review("CP5-TWO-CONNECTIONS,dai37", ",", False, conn=first)
        row = review.rows[0]
        second.execute("BEGIN IMMEDIATE")
        database.persist_case_on_connection(
            second, row.case_number, row.preset_id, row.clinical_info,
            row.structured_input, row.rendered_html, status="pending", pending_reason=None,
            mode="create",
        )
        second.commit()
        result = bulk_intake.apply_bulk_review(
            review, "CP5-TWO-CONNECTIONS,dai37", ",", False, confirmed=True, conn=first,
        )
        assert not result and "occupied" in result.error
    finally:
        first.close()
        second.close()
    conn = database.get_db_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM Cases WHERE case_number='CP5-TWO-CONNECTIONS'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM Case_Batch_Imports").fetchone()[0] == 0
    finally:
        conn.close()


def test_apply_write_lock_serializes_a_simultaneous_second_connection(mutable_db, monkeypatch):
    """A competitor arriving after CP5 acquires BEGIN IMMEDIATE cannot double-create."""
    source = "CP5-CONCURRENT-TARGET,dai37"
    review = bulk_intake.prepare_bulk_review(source, ",", False)
    original_absence_check = bulk_intake._existing_case_numbers
    attempts = []

    def competing_create():
        conn = sqlite3.connect(mutable_db, timeout=0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("BEGIN IMMEDIATE")
            attempts.append("unexpected-lock")
        except sqlite3.OperationalError:
            attempts.append("locked")
        finally:
            conn.close()

    def assert_lock_then_check(conn, numbers):
        thread = threading.Thread(target=competing_create)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        return original_absence_check(conn, numbers)

    monkeypatch.setattr(bulk_intake, "_existing_case_numbers", assert_lock_then_check)
    result = bulk_intake.apply_bulk_review(review, source, ",", False, confirmed=True)
    assert result
    assert attempts == ["locked"]
    conn = database.get_db_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM Cases WHERE case_number='CP5-CONCURRENT-TARGET'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM Case_Batch_Imports").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("boundary", [
    "rebuild", "render", "reconstruction", "serialization", "final_comparison", "case_insert",
])
def test_apply_rolls_back_every_phase_after_a_review_is_issued(mutable_db, monkeypatch, boundary):
    """Every CP5 post-lock phase leaves no Case or audit on failure."""
    source = f"CP5-PHASE-{boundary},dai37"
    review = bulk_intake.prepare_bulk_review(source, ",", False)

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"{boundary} fault")

    if boundary == "rebuild":
        monkeypatch.setattr(bulk_intake, "_prepare_row", fail)
    elif boundary == "render":
        monkeypatch.setattr(editor_preview, "render_report", fail)
    elif boundary == "reconstruction":
        monkeypatch.setattr(bulk_intake, "_assert_saved_round_trip", fail)
    elif boundary == "serialization":
        monkeypatch.setattr(database, "_canonical_json", fail)
    elif boundary == "final_comparison":
        monkeypatch.setattr(bulk_intake, "_same_prepared_batch", lambda *_args: False)
    else:
        monkeypatch.setattr(database, "persist_case_on_connection", fail)

    result = bulk_intake.apply_bulk_review(review, source, ",", False, confirmed=True)
    assert not result
    conn = database.get_db_connection()
    try:
        assert _counts(conn) == (0, 0, 0, 0)
    finally:
        conn.close()


def test_warning_acknowledgement_is_rejected_when_carried_to_a_different_review(mutable_db):
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute(
            """INSERT INTO Quick_Type_Tokens
               (preset_id,sort_order,block_sort_order,field_key,token_kind,lookup_table,digit_width)
               VALUES ((SELECT id FROM Presets WHERE short_code='dai'),2,0,
                       'false_membranes','lookup','{"f":true}',NULL)"""
        )
        conn.commit()
    finally:
        conn.close()
    old_review = bulk_intake.prepare_bulk_review("CP5-ACK-OLD,dai11f", ",", False)
    new_review = bulk_intake.prepare_bulk_review("CP5-ACK-NEW,dai11f", ",", False)
    acknowledgement = bulk_intake.acknowledge_batch_warnings(old_review)
    assert acknowledgement is not None
    result = bulk_intake.apply_bulk_review(
        new_review, "CP5-ACK-NEW,dai11f", ",", False, confirmed=True,
        warning_acknowledgement=acknowledgement,
    )
    assert not result and "Acknowledge" in result.error


def test_batch_provenance_never_enters_content_ai_or_operational_exports(mutable_db, tmp_path):
    source = "CP5-PRIVATE-CASE,dai37"
    before = content_snapshot.export_content_snapshot(mutable_db)
    review = bulk_intake.prepare_bulk_review(source, ",", False)
    assert bulk_intake.apply_bulk_review(review, source, ",", False, confirmed=True)
    after = content_snapshot.export_content_snapshot(mutable_db)
    assert after == before

    ai_context = change_packages.export_ai_context(mutable_db).decode("utf-8")
    snapshot_text = content_snapshot.content_snapshot_json(after)
    snapshot_path = tmp_path / "content.json"
    artifact_path = tmp_path / "operational.json"
    snapshot_path.write_text(snapshot_text, encoding="utf-8")
    operational_text = json.dumps(operational_review.generate(snapshot_path, artifact_path), ensure_ascii=False)
    for exported in (snapshot_text, ai_context, operational_text):
        assert "CP5-PRIVATE-CASE" not in exported
        assert "dai37" not in exported
        assert "Case_Batch_Imports" not in exported

    conn = database.get_db_connection()
    try:
        audit_columns = set(conn.execute("SELECT * FROM Case_Batch_Imports").fetchone().keys())
        assert audit_columns == {
            "id", "created_at", "row_count", "normalized_input_sha256",
            "content_snapshot_sha256", "content_revision_id",
        }
    finally:
        conn.close()


def test_bulk_apply_page_resets_after_success_and_worklist_shows_the_case(mutable_db):
    app = AppTest.from_file("pages/bulk_intake.py").run()
    assert not app.exception
    app.text_area(key="bulk_paste_source").set_value("CP5-UI,dai37").run()
    app.button(key="bulk_prepare").click().run()
    assert not app.exception
    app.checkbox(key="bulk_apply_confirm").set_value(True).run()
    app.button(key="bulk_apply").click().run()
    assert not app.exception
    assert any("Created 1 pending Case(s) atomically." in item.value for item in app.success)
    assert app.text_area(key="bulk_paste_source").value == ""

    # Worklist reads the ordinary Case collection; page-link routing itself
    # requires multipage app metadata and is covered at the app level.
    assert any(case["case_number"] == "CP5-UI" for case in database.get_all_cases())
