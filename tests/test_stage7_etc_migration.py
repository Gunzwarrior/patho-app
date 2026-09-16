"""Stage 7 CP6: the fixed, reviewed ``etc`` thyroid Quick Type migration."""

import json
import sqlite3
from pathlib import Path

import pytest

import content_changes
import content_editing
import content_snapshot
import content_studio
import database
import editor_preview
import quicktype
from test_stage5_packages import connection, save_synthetic_case


VARIANTS = ("etc0", "etc1", "etc2", "etc3", "etc5")


def _unlock():
    content_editing.record_initial_snapshot("6" * 64)


def _hash(path):
    return content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(path))


def _consolidated_content_operations(path):
    """Test-only representation of the already-approved, applied CP6 rows.

    Production has no CP6 planner after cleanup.  This fixture uses only the
    permanent generic Content Studio operation grammar to establish the
    resulting content state for compatibility and inverse tests.
    """
    conn = connection(path)
    try:
        source = dict(conn.execute("SELECT * FROM Presets WHERE short_code='etc2'").fetchone())
        link = dict(conn.execute(
            """SELECT pb.*, b.key AS block_key FROM Preset_Blocks pb JOIN Blocks b ON b.id=pb.block_id
               WHERE pb.preset_id=? AND pb.sort_order=0""", (source["id"],)
        ).fetchone())
    finally:
        conn.close()
    return [
        content_studio.operation("create", "Presets", "etc", {
            key: source[key] for key in ("name", "category", "default_title", "default_adicap")
        }),
        content_studio.operation("link", "Preset_Blocks", {
            "preset_code": "etc", "block_key": link["block_key"], "sort_order": 0,
        }, {
            "display_order": link["display_order"],
            "field_overrides": json.loads(link["field_overrides"] or "{}"),
        }),
        content_studio.operation("link", "Quick_Type_Tokens", {
            "preset_code": "etc", "sort_order": 0,
        }, {
            "block_sort_order": 0, "field_key": "thyroid_cytology_pattern", "token_kind": "lookup",
            "lookup_table": {code[-1]: code for code in VARIANTS}, "digit_width": None,
        }),
        *[content_studio.operation("archive", "Presets", code) for code in VARIANTS],
    ]


def _review(path):
    return content_studio.review(
        _consolidated_content_operations(path), _hash(path),
        summary="CP6 applied thyroid cytology content", db_name=path,
    )


def _default_report(path, code):
    conn = connection(path)
    try:
        preset = conn.execute("SELECT id FROM Presets WHERE short_code=?", (code,)).fetchone()
        report = editor_preview.render_preset_defaults(preset["id"], conn, strict=True)
        return {key: value for key, value in report.items() if key != "preset"}
    finally:
        conn.close()


def _quick_type_report(path, code):
    conn = connection(path)
    try:
        preset, overrides, error = quicktype.parse_quick_type_on_connection(code, conn)
        assert error is None
        blocks = database.get_preset_blocks_on_connection(conn, preset["id"])
        report = editor_preview.render_report(
            conn, preset, blocks, [overrides.get(block["sort_order"], {}) for block in blocks], strict=True,
        )
        return preset, overrides, report
    finally:
        conn.close()


def _case_row(path, number):
    conn = connection(path)
    try:
        return dict(conn.execute("SELECT * FROM Cases WHERE case_number=?", (number,)).fetchone())
    finally:
        conn.close()


def test_cp6_routes_bare_and_suffixes_with_equivalent_reports_and_leaves_etc_bi(mutable_db):
    _unlock()
    old_reports = {code: _default_report(mutable_db, code) for code in VARIANTS}
    conn = connection(mutable_db)
    try:
        etc_bi_before = (
            dict(conn.execute("SELECT * FROM Presets WHERE short_code='etc_bi'").fetchone()),
            [dict(row) for row in conn.execute(
                "SELECT * FROM Preset_Blocks WHERE preset_id=(SELECT id FROM Presets WHERE short_code='etc_bi')"
            )],
        )
    finally:
        conn.close()

    review = _review(mutable_db)
    revision = content_changes.apply_review(review, db_name=mutable_db)

    expected = {"etc": ("etc2", {}), **{
        f"etc{suffix}": (legacy, {0: {"thyroid_cytology_pattern": legacy}})
        for suffix, legacy in {"0": "etc0", "1": "etc1", "2": "etc2", "3": "etc3", "5": "etc5"}.items()
    }}
    for code, (legacy, overrides) in expected.items():
        preset, parsed, report = _quick_type_report(mutable_db, code)
        assert preset["short_code"] == "etc"
        assert parsed == overrides
        assert report == old_reports[legacy]

    conn = connection(mutable_db)
    try:
        assert conn.execute("SELECT is_archived FROM Presets WHERE short_code='etc'").fetchone()[0] == 0
        assert [row[0] for row in conn.execute(
            "SELECT short_code FROM Presets WHERE short_code IN ('etc0','etc1','etc2','etc3','etc5') AND is_archived=1 ORDER BY short_code"
        )] == list(VARIANTS)
        etc_bi_after = (
            dict(conn.execute("SELECT * FROM Presets WHERE short_code='etc_bi'").fetchone()),
            [dict(row) for row in conn.execute(
                "SELECT * FROM Preset_Blocks WHERE preset_id=(SELECT id FROM Presets WHERE short_code='etc_bi')"
            )],
        )
        assert etc_bi_after == etc_bi_before
        assert conn.execute("SELECT COUNT(*) FROM Case_Content_Reference_Changes WHERE revision_id=?", (revision,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_cp6_keeps_old_pending_and_validated_cases_unchanged_and_reopenable(mutable_db):
    _unlock()
    pending = save_synthetic_case(mutable_db, "etc2", structured={}, number="26PR670002")
    validated = save_synthetic_case(mutable_db, "etc5", structured={}, number="26PR670003", status="validated")
    pending_before, validated_before = _case_row(mutable_db, pending["case_number"]), _case_row(mutable_db, validated["case_number"])
    old_pending_report = _default_report(mutable_db, "etc2")

    content_changes.apply_review(_review(mutable_db), db_name=mutable_db)

    assert _case_row(mutable_db, pending["case_number"]) == pending_before
    assert _case_row(mutable_db, validated["case_number"]) == validated_before
    conn = connection(mutable_db)
    try:
        reopened = editor_preview.render_saved_case(conn, _case_row(mutable_db, pending["case_number"]), strict=True)
        assert {key: value for key, value in reopened.items() if key != "instances"} == old_pending_report
        assert database.compute_case_content_fingerprint(
            pending_before["preset_id"], json.loads(pending_before["structured_input"]), conn,
        ) == pending_before["content_fingerprint"]
        assert conn.execute("SELECT is_archived FROM Presets WHERE id=?", (pending_before["preset_id"],)).fetchone()[0] == 1
    finally:
        conn.close()


def test_cp6_apply_rolls_back_atomically_on_audit_failure(mutable_db, monkeypatch):
    _unlock()
    review = _review(mutable_db)
    conn = connection(mutable_db)
    try:
        revisions_before = conn.execute("SELECT COUNT(*) FROM Content_Revisions").fetchone()[0]
    finally:
        conn.close()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit fault")

    monkeypatch.setattr(content_changes, "_record_generalized_changes", fail_audit)
    with pytest.raises(content_changes.ChangeError, match="Apply failed"):
        content_changes.apply_review(review, db_name=mutable_db)

    conn = connection(mutable_db)
    try:
        assert conn.execute("SELECT 1 FROM Presets WHERE short_code='etc'").fetchone() is None
        assert [row[0] for row in conn.execute(
            "SELECT short_code FROM Presets WHERE short_code IN ('etc0','etc1','etc2','etc3','etc5') AND is_archived=0 ORDER BY short_code"
        )] == list(VARIANTS)
        assert conn.execute("SELECT COUNT(*) FROM Content_Revisions").fetchone()[0] == revisions_before
    finally:
        conn.close()


def test_cp6_review_is_stale_after_content_changes(mutable_db):
    _unlock()
    review = _review(mutable_db)
    conn = connection(mutable_db)
    try:
        revisions_before = conn.execute("SELECT COUNT(*) FROM Content_Revisions").fetchone()[0]
    finally:
        conn.close()
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute("UPDATE Presets SET name='Concurrent change' WHERE short_code='etc_bi'")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(content_changes.StaleReviewError):
        content_changes.apply_review(review, db_name=mutable_db)
    conn = connection(mutable_db)
    try:
        assert conn.execute("SELECT 1 FROM Presets WHERE short_code='etc'").fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM Content_Revisions").fetchone()[0] == revisions_before
    finally:
        conn.close()


def test_cp6_leaves_no_permanent_migration_runtime_surface():
    for path in ("content_studio.py", "pages/editor.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert "etc_consolidation" not in source
        assert "Thyroid consolidation" not in source


def test_cp6_inverse_reinverse_and_pending_new_etc_blocks_inverse(mutable_db):
    _unlock()
    first = content_changes.apply_review(_review(mutable_db), db_name=mutable_db)
    undo = content_changes.apply_review(content_changes.review_inverse(first, db_name=mutable_db), db_name=mutable_db)
    conn = connection(mutable_db)
    try:
        assert conn.execute("SELECT 1 FROM Presets WHERE short_code='etc'").fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM Presets WHERE short_code IN ('etc0','etc1','etc2','etc3','etc5') AND is_archived=0").fetchone()[0] == 5
    finally:
        conn.close()
    redo = content_changes.apply_review(content_changes.review_inverse(undo, db_name=mutable_db), db_name=mutable_db)
    assert redo > undo > first

    save_synthetic_case(mutable_db, "etc", structured={}, number="26PR670001")
    with pytest.raises(content_changes.ChangeError, match="pending case depends"):
        content_changes.review_inverse(redo, db_name=mutable_db)
