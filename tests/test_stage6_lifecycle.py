"""Stage 6 checkpoint 3: lifecycle planning and archived runtime resolution."""

import json

import pytest

import content_changes
import content_editing
import content_snapshot
import content_studio
import database
import editor_preview
import quicktype
from test_stage5_packages import connection, save_synthetic_case


def _hash(path):
    return content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(path))


def _review(path, intents):
    return content_studio.review(intents, _hash(path), summary="Lifecycle test", db_name=path)


def _unlock():
    content_editing.record_initial_snapshot("a" * 64)


def _preset(code):
    return next(row for row in database.get_all_presets(include_archived=True) if row["short_code"] == code)


def _freeze_case_identity(path, case):
    conn = connection(path)
    try:
        conn.execute("""UPDATE Cases
                        SET preset_short_code_snapshot=(SELECT short_code FROM Presets WHERE id=Cases.preset_id),
                            preset_name_snapshot=(SELECT name FROM Presets WHERE id=Cases.preset_id)
                        WHERE id=?""", (case["id"],))
        conn.commit()
    finally:
        conn.close()


def test_archive_field_expands_upward_closure_and_saved_draft_still_renders(mutable_db):
    _unlock()
    preset = _preset("dai")
    before_fingerprint = database.compute_case_content_fingerprint(preset["id"], {})
    case = save_synthetic_case(mutable_db, number="ARCHIVE-FIELD")

    plan = content_studio.lifecycle_plan("archive", "Fields", "appendicite_type", db_name=mutable_db)
    closure = {(item["table"], item["key"]) for item in plan["archive_closure"]}
    assert {("Fields", "appendicite_type"), ("Blocks", "appendice"), ("Presets", "dai")} <= closure
    prepared = _review(mutable_db, [content_studio.operation("archive", "Fields", "appendicite_type")])
    assert prepared.pending_cases == []  # availability alone is not a draft-content change
    content_changes.apply_review(prepared, db_name=mutable_db)

    assert "dai" not in {row["short_code"] for row in database.get_all_presets()}
    assert quicktype.parse_quick_type("dai")[2] == "no preset short_code matches 'dai'."
    conn = connection(mutable_db)
    try:
        saved = dict(conn.execute("SELECT * FROM Cases WHERE id=?", (case["id"],)).fetchone())
        report = editor_preview.render_saved_case(conn, saved, strict=True)
        assert report["micro_plain"]
        assert database.compute_case_content_fingerprint(saved["preset_id"], json.loads(saved["structured_input"]), conn) == before_fingerprint
    finally:
        conn.close()


def test_restore_preset_restores_archived_prerequisites(mutable_db):
    _unlock()
    archive = _review(mutable_db, [content_studio.operation("archive", "Fields", "appendicite_type")])
    content_changes.apply_review(archive, db_name=mutable_db)
    plan = content_studio.lifecycle_plan("restore", "Presets", "dai", db_name=mutable_db)
    closure = {(item["table"], item["key"]) for item in plan["restore_prerequisites"]}
    assert {("Fields", "appendicite_type"), ("Blocks", "appendice"), ("Presets", "dai")} <= closure
    content_changes.apply_review(_review(mutable_db, [content_studio.operation("restore", "Presets", "dai")]), db_name=mutable_db)
    assert "dai" in {row["short_code"] for row in database.get_all_presets()}
    assert database.get_preset_blocks(_preset("dai")["id"])


def test_pending_dependency_blocks_permanent_deletion_but_not_archive(mutable_db):
    _unlock()
    case = save_synthetic_case(mutable_db, number="DELETE-BLOCKER")
    validated = save_synthetic_case(mutable_db, number="DELETE-VALIDATED", status="validated")
    assert validated["preset_id"] == case["preset_id"]
    blockers = content_studio.pending_blockers("Presets", "dai", db_name=mutable_db)
    assert blockers == [{"case_id": case["id"], "case_number": "DELETE-BLOCKER"}]
    plan = content_studio.lifecycle_plan("delete", "Presets", "dai", db_name=mutable_db)
    assert plan["pending_blockers"] == blockers
    assert plan["refusal_reasons"] == [
        "Permanent deletion is unavailable while persisted pending Cases depend on it; prepare archive instead."
    ]
    assert plan["operations"] == []
    assert plan["validated_detachments"] == []
    with pytest.raises(content_changes.ChangeError, match="pending Cases depend"):
        _review(mutable_db, [content_studio.operation("delete", "Presets", "dai")])
    assert content_studio.lifecycle_plan("archive", "Presets", "dai", db_name=mutable_db)["pending_blockers"] == blockers


def test_lifecycle_plan_summaries_report_direct_closure_cleanup_and_detachments(mutable_db):
    _unlock()
    archived = content_studio.lifecycle_plan("archive", "Fields", "appendicite_type", db_name=mutable_db)
    assert archived["direct_dependencies"] == [{"table": "Blocks", "key": "appendice"}]
    assert {(item["table"], item["key"]) for item in archived["archive_closure"]} == {
        ("Fields", "appendicite_type"), ("Blocks", "appendice"), ("Presets", "dai"),
    }
    assert archived["restore_prerequisites"] == []
    assert archived["mechanical_deletion_cleanup"] == []
    assert archived["validated_detachments"] == []
    assert archived["refusal_reasons"] == []

    case = save_synthetic_case(mutable_db, number="PLAN-VALIDATED", status="validated")
    assert content_studio.lifecycle_plan("archive", "Presets", "dai", db_name=mutable_db)["validated_detachments"] == []
    assert content_studio.lifecycle_plan("restore", "Presets", "dai", db_name=mutable_db)["validated_detachments"] == []
    deletion = content_studio.lifecycle_plan("delete", "Presets", "dai", db_name=mutable_db)
    assert deletion["pending_blockers"] == []
    assert deletion["validated_detachments"] == [{"case_id": case["id"], "case_number": "PLAN-VALIDATED"}]
    assert any(item["table"] == "Preset_Blocks" and item["action"] == "unlink"
               for item in deletion["mechanical_deletion_cleanup"])
    assert any(item["op"] == "case_preset_reference" for item in deletion["operations"])


def test_preset_unlink_changes_explicit_pending_case_fingerprint_even_without_overrides(mutable_db):
    _unlock()
    preset = _preset("gt")
    block = database.get_preset_blocks(preset["id"])[0]
    assert block["field_overrides"] is None
    explicit = save_synthetic_case(
        mutable_db, code="gt", number="EXPLICIT-UNLINK",
        structured={"block_instances": [{"block_id": block["block_id"], "instance_no": block["sort_order"]}]},
    )
    prepared = _review(mutable_db, [content_studio.operation("unlink", "Preset_Blocks", {
        "preset_code": "gt", "block_key": block["key"], "sort_order": block["sort_order"],
    })])
    assert [item["id"] for item in prepared.pending_cases] == [explicit["id"]]


def test_delete_eligibility_race_rejects_apply_when_a_pending_case_appears(mutable_db):
    _unlock()
    prepared = _review(mutable_db, [content_studio.operation("delete", "Presets", "dai")])
    pending = save_synthetic_case(mutable_db, number="DELETE-RACE")

    with pytest.raises(content_changes.StaleReviewError, match="Local state changed"):
        content_changes.apply_review(prepared, db_name=mutable_db)

    assert database.get_case_by_number("DELETE-RACE")["id"] == pending["id"]
    assert _preset("dai")["id"] == pending["preset_id"]


def test_archive_block_keeps_legacy_and_ad_hoc_pending_compositions_renderable(mutable_db):
    _unlock()
    preset = _preset("dai")
    block = database.get_preset_blocks(preset["id"])[0]
    legacy = save_synthetic_case(mutable_db, number="LEGACY-BLOCK")
    ad_hoc = save_synthetic_case(
        mutable_db, number="AD-HOC-BLOCK",
        structured={"block_instances": [{"block_id": block["block_id"], "instance_no": 700}]},
    )
    assert {item["case_id"] for item in content_studio.pending_blockers("Blocks", "appendice", db_name=mutable_db)} == {
        legacy["id"], ad_hoc["id"],
    }
    prepared = _review(mutable_db, [content_studio.operation("archive", "Blocks", "appendice")])
    assert prepared.pending_cases == []
    content_changes.apply_review(prepared, db_name=mutable_db)
    conn = connection(mutable_db)
    try:
        for case_id in (legacy["id"], ad_hoc["id"]):
            saved = dict(conn.execute("SELECT * FROM Cases WHERE id=?", (case_id,)).fetchone())
            assert editor_preview.render_saved_case(conn, saved, strict=True)["micro_plain"]
    finally:
        conn.close()


def test_validated_preset_delete_detaches_worklist_label_and_refuses_unvalidation(mutable_db):
    _unlock()
    preset = _preset("dai")
    blocks = database.get_preset_blocks(preset["id"])
    structured = {"blocks": {f"{block['key']}#{block['sort_order']}": {} for block in blocks}}
    assert database.save_case("VALIDATED-DELETE", preset["id"], "", structured, "<p>frozen</p>", status="validated")
    prepared = _review(mutable_db, [content_studio.operation("delete", "Presets", "dai")])
    assert prepared.data["case_references"]
    revision = content_changes.apply_review(prepared, db_name=mutable_db)
    detached = database.get_case_by_number("VALIDATED-DELETE")
    assert detached["preset_id"] is None
    assert next(row for row in database.get_all_cases() if row["case_number"] == "VALIDATED-DELETE")["preset_name"] == "Appendice"
    assert not database.return_case_to_pending("VALIDATED-DELETE", "needs live draft")
    assert database.get_case_by_number("VALIDATED-DELETE")["status"] == "validated"
    content_changes.apply_review(content_changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
    assert database.get_case_by_number("VALIDATED-DELETE")["preset_id"] == preset["id"]


def test_archived_snippet_is_resolved_only_for_saved_pending_case(mutable_db):
    _unlock()
    case = save_synthetic_case(mutable_db, code="vb", number="ARCHIVED-SNIPPET")
    prepared = _review(mutable_db, [content_studio.operation("archive", "Snippets", "absence_malignite")])
    content_changes.apply_review(prepared, db_name=mutable_db)
    assert database.get_snippet_by_shortcut("absence_malignite") is None
    assert database.get_snippet_by_shortcut("absence_malignite", include_archived=True)
    conn = connection(mutable_db)
    try:
        saved = dict(conn.execute("SELECT * FROM Cases WHERE id=?", (case["id"],)).fetchone())
        report = editor_preview.render_saved_case(conn, saved, strict=True)
        assert "Absence de signe de malignité." in (report["micro_plain"] + report["conclusion_plain"])
    finally:
        conn.close()


def test_display_reorder_changes_only_legacy_pending_default_order(mutable_db):
    _unlock()
    preset = _preset("gt")
    before = database.compute_case_content_fingerprint(preset["id"], {})
    legacy = save_synthetic_case(mutable_db, code="gt", number="LEGACY-ORDER")
    explicit = save_synthetic_case(
        mutable_db, code="gt", number="EXPLICIT-ORDER",
        structured={"block_instances": [
            {"block_id": block["block_id"], "instance_no": block["sort_order"]}
            for block in database.get_preset_blocks(preset["id"])
        ]},
    )
    conn = connection(mutable_db)
    try:
        links = [dict(row) for row in conn.execute(
            "SELECT b.key,pb.sort_order,pb.display_order FROM Preset_Blocks pb JOIN Blocks b ON b.id=pb.block_id WHERE pb.preset_id=? ORDER BY pb.display_order",
            (preset["id"],),
        )]
    finally:
        conn.close()
    assert len(links) > 1
    intents = [
        content_studio.operation("reorder", "Preset_Blocks", {"preset_code": "gt", "block_key": links[0]["key"], "sort_order": links[0]["sort_order"]}, {"display_order": links[1]["display_order"]}),
        content_studio.operation("reorder", "Preset_Blocks", {"preset_code": "gt", "block_key": links[1]["key"], "sort_order": links[1]["sort_order"]}, {"display_order": links[0]["display_order"]}),
    ]
    prepared = _review(mutable_db, intents)
    assert {item["id"] for item in prepared.pending_cases} == {legacy["id"]}
    content_changes.apply_review(prepared, db_name=mutable_db)
    assert database.compute_case_content_fingerprint(preset["id"], {}) != before
    conn = connection(mutable_db)
    try:
        explicit_case = dict(conn.execute("SELECT * FROM Cases WHERE id=?", (explicit["id"],)).fetchone())
        assert editor_preview.render_saved_case(conn, explicit_case, strict=True)["micro_plain"]
    finally:
        conn.close()
