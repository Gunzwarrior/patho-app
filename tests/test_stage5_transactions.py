"""Reviewed Apply/inverse transactions. Every connection targets a synthetic DB."""

from dataclasses import replace
import json
import sqlite3

import pytest

import change_packages as packages
import content_changes as changes
import content_editing
import content_snapshot
import database
from test_stage5_packages import connection, envelope, graph, raw, run, save_synthetic_case


def unlock():
    content_editing.record_initial_snapshot("a" * 64)


def state(path):
    conn = connection(path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: [dict(r) for r in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')] for table in tables}
    finally:
        conn.close()


def snapshot_hash(path):
    return content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(path))


def apply_graph(path):
    unlock()
    review = run(path, graph())
    return review, changes.apply_review(review, db_name=path)


def test_provenance_migration_is_additive_and_idempotent(mutable_db):
    save_synthetic_case(mutable_db, status="validated")
    database.migrate_schema(mutable_db)  # finish the legacy Case backfill first
    conn = connection(mutable_db)
    for column in ("package_hash", "base_snapshot_hash", "result_snapshot_hash"):
        conn.execute(f"ALTER TABLE Content_Revisions DROP COLUMN {column}")
    conn.commit()
    conn.close()
    before = state(mutable_db)
    database.migrate_schema(mutable_db)
    after = state(mutable_db)
    for table in before:
        if table != "Content_Revisions":
            assert after[table] == before[table]
    for old, new in zip(before["Content_Revisions"], after["Content_Revisions"]):
        assert {k: new[k] for k in old} == old
        assert all(new[k] is None for k in set(new) - set(old))
    database.migrate_schema(mutable_db)
    assert state(mutable_db) == after


def test_apply_requires_snapshot_then_commits_exact_content_and_audit(mutable_db):
    save_synthetic_case(mutable_db)
    save_synthetic_case(mutable_db, number="FROZEN", status="validated")
    ops = graph() + [{"op": "update", "table": "Blocks", "key": "appendice", "set": {"micro_template": "Correction."}}]
    review = run(mutable_db, ops)
    before = state(mutable_db)
    with pytest.raises(content_editing.ContentEditError, match="initial content snapshot"):
        changes.apply_review(review, db_name=mutable_db)
    assert state(mutable_db) == before
    unlock()
    before = state(mutable_db)
    revision_id = changes.apply_review(review, db_name=mutable_db)
    after = state(mutable_db)
    assert snapshot_hash(mutable_db) == review.candidate_snapshot_hash
    for table in ("Cases", "Case_Validation_History", "Case_Status_History", "Editor_Safety_State"):
        assert after[table] == before[table]
    for table, (key, _) in content_snapshot.BASE_TABLES.items():
        identities = {r[key]: r["id"] for r in after[table]}
        assert all(identities[r[key]] == r["id"] for r in before[table])
    assert len(after["Content_Revisions"]) == len(before["Content_Revisions"]) + 1
    revision = after["Content_Revisions"][-1]
    assert revision["id"] == revision_id and revision["origin"] == "package_import"
    assert revision["package_hash"] == review.package_hash
    assert revision["base_snapshot_hash"] == review.base_snapshot_hash
    assert revision["result_snapshot_hash"] == review.candidate_snapshot_hash
    audited = [r for r in after["Content_Changes"] if r["revision_id"] == revision_id]
    assert len(audited) == len(review.changes)
    for entry, proposed in zip(audited, review.changes):
        assert entry["entity_key"] == changes._audit_key(proposed["table"], proposed["key"])
        for side in ("before", "after"):
            image = json.loads(entry[side + "_json"]) if entry[side + "_json"] is not None else None
            assert image == proposed[side]
            assert entry[side + "_hash"] == (content_editing.row_hash(image) if image else None)
    with pytest.raises(changes.StaleReviewError):
        changes.apply_review(review, db_name=mutable_db)
    assert state(mutable_db) == after
    with pytest.raises(content_editing.ContentEditError, match="inverse review"):
        content_editing.revert_revision(revision_id)


def test_internal_review_apply_and_legacy_audit_inverse(mutable_db):
    unlock()
    review = changes.review_candidate(envelope(mutable_db)["operations"], snapshot_hash(mutable_db),
                                      summary="Guided content", db_name=mutable_db)
    revision = changes.apply_review(review, db_name=mutable_db)
    assert state(mutable_db)["Content_Revisions"][-1]["origin"] == "content_edit"
    inverse = changes.review_inverse(revision, db_name=mutable_db)
    assert inverse.package_hash is None
    changes.apply_review(inverse, db_name=mutable_db)
    legacy = content_editing.create_snippet("legacy_inverse", "Legacy phrase")["revision_id"]
    undo = changes.apply_review(changes.review_inverse(legacy, db_name=mutable_db), db_name=mutable_db)
    redo = changes.apply_review(changes.review_inverse(undo, db_name=mutable_db), db_name=mutable_db)
    assert redo > undo > legacy
    assert content_editing.get_editable_entity("Snippets", "legacy_inverse")["expansion"] == "Legacy phrase"


def test_inverse_restores_hash_and_original_ids_and_is_itself_reversible(mutable_db):
    before_hash = snapshot_hash(mutable_db)
    review, revision = apply_graph(mutable_db)
    applied = state(mutable_db)
    inverse = changes.review_inverse(revision, db_name=mutable_db)
    assert state(mutable_db) == applied  # inverse preparation is no-write too
    assert inverse.candidate_snapshot_hash == before_hash
    assert next(p for p in inverse.presets if p["code"] == "synthetic_preset")["removed"]
    undo = changes.apply_review(inverse, db_name=mutable_db)
    assert snapshot_hash(mutable_db) == before_hash
    redo_review = changes.review_inverse(undo, db_name=mutable_db)
    redo = changes.apply_review(redo_review, db_name=mutable_db)
    assert snapshot_hash(mutable_db) == review.candidate_snapshot_hash
    restored = state(mutable_db)
    for table in changes.CONTENT_TABLES:
        # Relationship tables have no persisted surrogate ID; insertion rowid
        # order is not report order. Compare every stored column, including all
        # base and endpoint IDs and the explicit relationship sort_order.
        assert sorted(restored[table], key=packages.canonical_json) == sorted(applied[table], key=packages.canonical_json)
    assert redo > undo > revision
    assert restored["Content_Revisions"][-1]["package_hash"] is None


@pytest.mark.parametrize("mutation", ["content", "audit_aba", "identity", "arrival", "edit", "clinical", "saved_html", "delete", "validate", "unvalidate"])
def test_apply_rejects_all_stale_states(mutable_db, mutation):
    unlock()
    case = save_synthetic_case(mutable_db)
    save_synthetic_case(mutable_db, number="FROZEN", status="validated")
    review = run(mutable_db)
    conn = connection(mutable_db)
    if mutation == "content":
        conn.execute("UPDATE Presets SET category='later' WHERE short_code='dai'")
    elif mutation == "audit_aba":
        conn.execute("INSERT INTO Content_Revisions(origin,summary) VALUES ('manual_edit','intervening audit')")
    elif mutation == "identity":
        conn.execute("UPDATE Snippets SET id=id+1000 WHERE shortcut='absence_malignite'")
    elif mutation == "arrival":
        conn.close()
        save_synthetic_case(mutable_db, number="ARRIVED")
        conn = connection(mutable_db)
    elif mutation == "edit":
        conn.execute("UPDATE Cases SET structured_input='{} ' WHERE id=?", (case["id"],))
    elif mutation == "clinical":
        conn.execute("UPDATE Cases SET clinical_info='changed' WHERE id=?", (case["id"],))
    elif mutation == "saved_html":
        conn.execute("UPDATE Cases SET rendered_html='changed' WHERE id=?", (case["id"],))
    elif mutation == "delete":
        conn.execute("DELETE FROM Cases WHERE id=?", (case["id"],))
    elif mutation == "validate":
        conn.execute("UPDATE Cases SET status='validated' WHERE id=?", (case["id"],))
    else:
        conn.execute("UPDATE Cases SET status='pending' WHERE case_number='FROZEN'")
    conn.commit()
    conn.close()
    before = state(mutable_db)
    with pytest.raises(changes.StaleReviewError):
        changes.apply_review(review, db_name=mutable_db)
    assert state(mutable_db) == before
    if mutation != "content":
        refreshed = packages.dry_run(raw(envelope(mutable_db)), mutable_db)
        assert changes.apply_review(refreshed, db_name=mutable_db)


def test_forged_or_replaced_review_is_not_an_issued_result(mutable_db):
    unlock()
    review = run(mutable_db)
    before = state(mutable_db)
    for forged in (None, review.data, replace(review), replace(review, candidate_snapshot_hash="0" * 64),
                   replace(review, _payload_json=packages.canonical_json({**review.data, "summary": "changed"}))):
        with pytest.raises(changes.ChangeError, match="successful current review"):
            changes.apply_review(forged, db_name=mutable_db)
    assert state(mutable_db) == before


@pytest.mark.parametrize("fault", ["Fields", "Snippets", "Blocks", "Presets", "Block_Fields", "Preset_Blocks", "update", "revision", "change", "commit"])
def test_each_write_phase_and_commit_failure_roll_back_everything(mutable_db, monkeypatch, fault):
    unlock()
    ops = graph() + [{"op": "update", "table": "Snippets", "key": "absence_malignite", "set": {"expansion": "Updated"}}]
    review = run(mutable_db, ops)
    before = state(mutable_db)
    fired = []
    class Failing(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            pattern = ("UPDATE Snippets" if fault == "update" else "INSERT INTO Content_Revisions" if fault == "revision"
                       else "INSERT INTO Content_Changes" if fault == "change" else "INSERT INTO " + fault)
            result = super().execute(sql, parameters)
            if sql.lstrip().startswith(pattern):
                fired.append(fault)
                raise RuntimeError("PRIVATE-FAILURE-CANARY")
            return result
        def commit(self):
            if fault == "commit":
                fired.append(fault)
                raise sqlite3.OperationalError("PRIVATE-COMMIT-CANARY")
            return super().commit()
    monkeypatch.setattr(database, "get_db_connection", lambda: sqlite3.connect(mutable_db, factory=Failing))
    with pytest.raises((changes.ChangeError, packages.PackageError)) as error:
        changes.apply_review(review)
    assert fired and "PRIVATE" not in str(error.value)
    assert state(mutable_db) == before


@pytest.mark.parametrize("fault", ["validation", "result_hash", "change_images", "validator_case_write", "audit_case_write", "nested_commit"])
def test_candidate_and_helper_failures_roll_back(mutable_db, monkeypatch, fault):
    unlock()
    save_synthetic_case(mutable_db)
    review = run(mutable_db)
    before = state(mutable_db)
    if fault in ("validation", "validator_case_write", "nested_commit"):
        def validate(conn, operations):
            if fault == "validator_case_write":
                conn.execute("UPDATE Cases SET clinical_info='forbidden'")
            elif fault == "nested_commit":
                conn.commit()
            else:
                raise RuntimeError("PRIVATE-VALIDATION-CANARY")
        monkeypatch.setattr(changes, "validate_candidate_content", validate)
    elif fault == "result_hash":
        original = content_snapshot.content_snapshot_hash
        calls = []
        def bad_hash(snapshot):
            calls.append(1)
            return "0" * 64 if len(calls) == 2 else original(snapshot)
        monkeypatch.setattr(content_snapshot, "content_snapshot_hash", bad_hash)
    elif fault == "change_images":
        original = changes.materialize_operations
        def bad_images(conn, operations):
            result = original(conn, operations)
            result[0]["after"]["expansion"] = "incorrect audit"
            return result
        monkeypatch.setattr(changes, "materialize_operations", bad_images)
    else:
        original = changes._record_changes
        def bad_audit(conn, review, images):
            original(conn, review, images)
            conn.execute("UPDATE Cases SET clinical_info='forbidden'")
        monkeypatch.setattr(changes, "_record_changes", bad_audit)
    with pytest.raises((changes.ChangeError, packages.PackageError)):
        changes.apply_review(review, db_name=mutable_db)
    assert state(mutable_db) == before


@pytest.mark.parametrize("dependency", ["later_edit", "later_relation", "later_template", "pending_preset", "pending_ad_hoc", "validated_preset"])
def test_inverse_refuses_newer_conflicts_and_dependencies(mutable_db, dependency):
    _, revision = apply_graph(mutable_db)
    conn = connection(mutable_db)
    block = conn.execute("SELECT id FROM Blocks WHERE key='synthetic_block'").fetchone()[0]
    if dependency == "later_edit":
        conn.execute("UPDATE Snippets SET expansion='later' WHERE shortcut='synthetic_phrase'")
    elif dependency == "later_relation":
        preset = conn.execute("SELECT id FROM Presets WHERE short_code='dai'").fetchone()[0]
        conn.execute("INSERT INTO Preset_Blocks(preset_id,block_id,sort_order) VALUES (?,?,99)", (preset, block))
    elif dependency == "later_template":
        conn.execute("UPDATE Blocks SET micro_template=? WHERE key='appendice'", ("{{ snippet('synthetic_phrase') }}",))
    conn.commit()
    conn.close()
    if dependency == "pending_preset":
        save_synthetic_case(mutable_db, code="synthetic_preset")
    elif dependency == "pending_ad_hoc":
        save_synthetic_case(mutable_db, structured={"block_instances": [{"block_id": block, "instance_no": 1000}]})
    elif dependency == "validated_preset":
        save_synthetic_case(mutable_db, code="synthetic_preset", status="validated")
    before = state(mutable_db)
    with pytest.raises((changes.ChangeError, packages.PackageError)):
        changes.review_inverse(revision, db_name=mutable_db)
    assert state(mutable_db) == before


def test_inverse_keeps_unrelated_later_changes_and_rejects_id_reuse(mutable_db):
    _, revision = apply_graph(mutable_db)
    conn = connection(mutable_db)
    conn.execute("UPDATE Presets SET category='unrelated' WHERE short_code='dai'")
    conn.commit()
    conn.close()
    undo = changes.apply_review(changes.review_inverse(revision, db_name=mutable_db), db_name=mutable_db)
    assert next(p for p in state(mutable_db)["Presets"] if p["short_code"] == "dai")["category"] == "unrelated"
    conn = connection(mutable_db)
    deleted = json.loads(conn.execute(
        "SELECT before_json FROM Content_Changes WHERE revision_id=? AND table_name='Snippets'", (undo,),
    ).fetchone()[0])
    conn.execute("INSERT INTO Snippets(id,shortcut,expansion) VALUES (?,?,?)", (deleted["id"], "different_key", "Occupied"))
    conn.commit()
    conn.close()
    before = state(mutable_db)
    with pytest.raises(changes.ChangeError, match="ID has been reused"):
        changes.review_inverse(undo, db_name=mutable_db)
    assert state(mutable_db) == before


def test_inverse_audit_tampering_and_stale_confirmation_refuse(mutable_db):
    _, revision = apply_graph(mutable_db)
    review = changes.review_inverse(revision, db_name=mutable_db)
    conn = connection(mutable_db)
    conn.execute("UPDATE Content_Revisions SET summary='audit changed' WHERE id=?", (revision,))
    conn.commit()
    conn.close()
    before = state(mutable_db)
    with pytest.raises(changes.StaleReviewError, match="audit changed"):
        changes.apply_review(review, db_name=mutable_db)
    assert state(mutable_db) == before
    conn = connection(mutable_db)
    conn.execute("UPDATE Content_Changes SET after_hash='corrupt' WHERE revision_id=?", (revision,))
    conn.commit()
    conn.close()
    with pytest.raises(changes.ChangeError, match="audit data"):
        changes.review_inverse(revision, db_name=mutable_db)


def test_inverse_validation_failure_rolls_back_removals_and_audit(mutable_db, monkeypatch):
    _, revision = apply_graph(mutable_db)
    review = changes.review_inverse(revision, db_name=mutable_db)
    before = state(mutable_db)
    def fail(conn, operations):
        raise RuntimeError("Invalid final graph")
    monkeypatch.setattr(changes, "validate_candidate_content", fail)
    with pytest.raises(changes.ChangeError):
        changes.apply_review(review, db_name=mutable_db)
    assert state(mutable_db) == before


def test_real_connection_lock_contention_and_locked_apply(mutable_db, monkeypatch):
    unlock()
    review = run(mutable_db)
    locker = connection(mutable_db)
    locker.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr(database, "get_db_connection", lambda: sqlite3.connect(mutable_db, timeout=0.01))
    with pytest.raises(changes.ChangeError, match="busy"):
        changes.apply_review(review)
    locker.rollback()
    original = changes.materialize_operations
    races = []
    def concurrent_write(conn, operations):
        other = sqlite3.connect(mutable_db, timeout=0.01)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("UPDATE Presets SET category='racing' WHERE short_code='dai'")
            races.append(True)
        finally:
            other.close()
        return original(conn, operations)
    monkeypatch.setattr(changes, "materialize_operations", concurrent_write)
    assert changes.apply_review(review)
    assert races == [True]
    locker.close()


@pytest.mark.parametrize("phase", ["review", "apply"])
def test_unreported_content_writes_cannot_hide_behind_matching_snapshot(mutable_db, monkeypatch, phase):
    unlock()
    review = run(mutable_db) if phase == "apply" else None
    before = state(mutable_db)
    original = changes.materialize_operations
    def extra_write(conn, operations):
        result = original(conn, operations)
        conn.execute("UPDATE Presets SET category='not audited' WHERE short_code='dai'")
        return result
    monkeypatch.setattr(changes, "materialize_operations", extra_write)
    with pytest.raises((changes.ChangeError, packages.PackageError)):
        changes.apply_review(review, db_name=mutable_db) if review else run(mutable_db)
    assert state(mutable_db) == before


def test_normalization_does_not_reapply_raw_upload_size_limit(mutable_db):
    unlock()
    payload = envelope(mutable_db)
    encoded = packages.canonical_json(payload).encode()
    payload["operations"][0]["values"]["expansion"] += "x" * (packages.MAX_BYTES - len(encoded))
    encoded = packages.canonical_json(payload).encode()
    assert len(encoded) == packages.MAX_BYTES
    parsed = packages.parse_package(encoded)
    assert len(packages.canonical_json(packages.package_envelope(parsed)).encode()) > packages.MAX_BYTES
    review = packages.dry_run(encoded, mutable_db)
    assert changes.apply_review(review, db_name=mutable_db)


def test_update_inverse_reviews_pending_impact_without_acknowledging_cases(mutable_db):
    unlock()
    case = save_synthetic_case(mutable_db)
    before = state(mutable_db)["Cases"]
    review = run(mutable_db, [{"op": "update", "table": "Blocks", "key": "appendice", "set": {"micro_template": "New wording"}}])
    revision = changes.apply_review(review, db_name=mutable_db)
    assert not database.save_case(case["case_number"], case["preset_id"], case["clinical_info"], {}, case["rendered_html"],
                                  content_fingerprint=case["content_fingerprint"])
    assert state(mutable_db)["Cases"] == before
    inverse = changes.review_inverse(revision, db_name=mutable_db)
    assert len(inverse.pending_cases) == 1
    assert inverse.pending_cases[0]["before"]["report"] != inverse.pending_cases[0]["after"]["report"]
    changes.apply_review(inverse, db_name=mutable_db)
    assert state(mutable_db)["Cases"] == before


@pytest.mark.parametrize("table", ["Block_Fields", "Preset_Blocks"])
def test_pending_relationship_dependency_refuses_even_with_valid_fallback(mutable_db, table):
    unlock()
    conn = connection(mutable_db)
    block = conn.execute("SELECT id FROM Blocks WHERE key='appendice'").fetchone()[0]
    if table == "Block_Fields":
        field = conn.execute("INSERT INTO Fields(key,label,type,default_value) VALUES ('unused_binding','Unused','number','0')").lastrowid
        conn.execute("INSERT INTO Block_Fields(block_id,field_id,sort_order) VALUES (?,?,99)", (block, field))
        row = dict(conn.execute("SELECT * FROM Block_Fields WHERE block_id=? AND field_id=?", (block, field)).fetchone())
        key = {"block_key": "appendice", "field_key": "unused_binding"}
        case_code = "dai"
    else:
        preset = conn.execute("INSERT INTO Presets(short_code,name) VALUES ('binding_only','Binding only')").lastrowid
        conn.execute("INSERT INTO Preset_Blocks(preset_id,block_id,sort_order) VALUES (?,?,0)", (preset, block))
        row = dict(conn.execute("SELECT * FROM Preset_Blocks WHERE preset_id=?", (preset,)).fetchone())
        key = {"preset_code": "binding_only", "block_key": "appendice", "sort_order": 0}
        case_code = "binding_only"
    revision = conn.execute("INSERT INTO Content_Revisions(origin,summary) VALUES ('content_edit','Recorded binding')").lastrowid
    conn.execute("""INSERT INTO Content_Changes(revision_id,table_name,entity_key,operation,after_json,after_hash)
                    VALUES (?,?,?,?,?,?)""",
                 (revision, table, packages.canonical_json(key).strip(), "link", packages.canonical_json(row), content_editing.row_hash(row)))
    conn.commit()
    conn.close()
    save_synthetic_case(mutable_db, code=case_code, structured={"block_instances": [{"block_id": block, "instance_no": 0}]})
    before = state(mutable_db)
    with pytest.raises(changes.ChangeError, match="pending case depends"):
        changes.review_inverse(revision, db_name=mutable_db)
    assert state(mutable_db) == before


@pytest.mark.parametrize("template", [
    "{{ snippet('synthetic_phrase') }}",
    "{{ snippet('synthetic_' 'phrase') }}",
])
def test_pending_snippet_dependency_refuses_inverse(mutable_db, template):
    unlock()
    save_synthetic_case(mutable_db)
    ops = [graph()[2], {"op": "update", "table": "Blocks", "key": "appendice",
                       "set": {"micro_template": template}}]
    revision = changes.apply_review(run(mutable_db, ops), db_name=mutable_db)
    before = state(mutable_db)
    with pytest.raises(changes.ChangeError, match="pending case depends"):
        changes.review_inverse(revision, db_name=mutable_db)
    assert state(mutable_db) == before


@pytest.mark.parametrize("corruption", ["extra_column", "identity", "duplicate_target", "unknown_table"])
def test_invalid_audit_images_are_refused_without_writes(mutable_db, corruption):
    _, revision = apply_graph(mutable_db)
    conn = connection(mutable_db)
    row = dict(conn.execute("SELECT * FROM Content_Changes WHERE revision_id=? ORDER BY id LIMIT 1", (revision,)).fetchone())
    if corruption in ("extra_column", "identity"):
        image = json.loads(row["after_json"])
        image["unexpected" if corruption == "extra_column" else "key"] = "invalid"
        conn.execute("UPDATE Content_Changes SET after_json=?,after_hash=? WHERE id=?",
                     (packages.canonical_json(image), content_editing.row_hash(image), row["id"]))
    elif corruption == "unknown_table":
        conn.execute("UPDATE Content_Changes SET table_name='Cases' WHERE id=?", (row["id"],))
    else:
        columns = [k for k in row if k != "id"]
        conn.execute(f"INSERT INTO Content_Changes ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                     [row[k] for k in columns])
    conn.commit()
    conn.close()
    before = state(mutable_db)
    with pytest.raises(changes.ChangeError, match="audit data"):
        changes.review_inverse(revision, db_name=mutable_db)
    assert state(mutable_db) == before
