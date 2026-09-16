"""Stage 7 CP4: decoded bulk preview is bounded, materialized and read-only."""

import json
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

import bulk_intake
import content_snapshot
import database
import editor_preview


def test_csv_tsv_bom_and_quoting_normalize_to_one_source_digest():
    csv_source = b"\xef\xbb\xbfCase ID,Quick Type\r\n  26PR40001  ,  dai37  \r\n\"26PR40002\",etc2\r\n"
    tsv_source = "header\tcode\n26PR40001\tdai37\n\"26PR40002\"\tetc2\n"
    csv_rows = bulk_intake.parse_bulk_source(csv_source, ",", True)
    tsv_rows = bulk_intake.parse_bulk_source(tsv_source, "\t", True)

    assert [(row.case_number, row.quick_type) for row in csv_rows.rows] == [
        ("26PR40001", "dai37"), ("26PR40002", "etc2"),
    ]
    assert csv_rows.normalized_source_sha256 == tsv_rows.normalized_source_sha256


@pytest.mark.parametrize("source, delimiter, header, message", [
    (b"\xff", ",", False, "UTF-8"),
    (b"A,dai\x00", ",", False, "NUL"),
    ("A,dai,extra", ",", False, "exactly two"),
    ("A,", ",", False, "blank"),
    ('A,"dai', ",", False, "Malformed"),
    ("A,dai\n" * 251, ",", False, "250"),
])
def test_source_contract_rejects_before_database_evaluation(source, delimiter, header, message):
    with pytest.raises(bulk_intake.BulkInputError, match=message):
        bulk_intake.parse_bulk_source(source, delimiter, header)


def test_preview_materializes_workspace_shape_round_trips_and_never_writes(mutable_db):
    conn = database.get_db_connection()
    try:
        before_snapshot = content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(conn))
        before_cases = conn.execute("SELECT COUNT(*) FROM Cases").fetchone()[0]
        before_changes = conn.total_changes
        review = bulk_intake.prepare_bulk_review(
            "Case ID,Quick Type\n26PR40003,dai37\n26PR40004,etc2\n", ",", True, conn=conn,
        )
        assert review.applicable, review.errors
        assert conn.total_changes == before_changes
        assert conn.execute("SELECT COUNT(*) FROM Cases").fetchone()[0] == before_cases
        assert content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(conn)) == before_snapshot
        assert review.content_snapshot_sha256 == before_snapshot
        assert review.interpretation_sha256
        assert bulk_intake.review_staleness(review, conn=conn) is None

        for row in review.rows:
            assert "dai37" not in json.dumps(row.structured_input, ensure_ascii=False)
            reconstructed = editor_preview.render_saved_case(conn, {
                "status": "pending", "preset_id": row.preset_id,
                "clinical_info": row.clinical_info, "structured_input": row.structured_input,
            })
            assert reconstructed["html"] == row.rendered_html
        # An issued review stores canonical JSON, not a mutable dict/list
        # graph. Every materialization is detached, including dict.__ior__
        # and nested references that previously altered the frozen review.
        issued_digest = review.interpretation_sha256
        detached = review.rows[0].structured_input
        dict.__ior__(detached, {"master_lock": True})
        detached["blocks"]["appendice#0"]["appendicite_type"] = "gangreneuse"
        detached["block_instances"].append({"block_id": 999, "instance_no": 999})
        with pytest.raises((AttributeError, TypeError)):
            review.rows[0].structured_input |= {"master_lock": True}
        with pytest.raises(AttributeError):
            review.rows[0].__dict__
        fresh = review.rows[0].structured_input
        assert fresh["master_lock"] is False
        assert fresh["blocks"]["appendice#0"]["appendicite_type"] == "periappendicite"
        assert fresh["block_instances"] != detached["block_instances"]
        assert review.interpretation_sha256 == issued_digest
        assert "dai37" not in repr(review)
        assert "26PR40003" not in repr(review)
    finally:
        conn.close()


def test_preview_is_read_only_under_sqlite_authorizer(mutable_db):
    conn = database.get_db_connection()
    write_actions = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
    seen_writes = []

    def authorizer(action, _one, _two, _database, _trigger):
        if action in write_actions:
            seen_writes.append(action)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        conn.set_authorizer(authorizer)
        review = bulk_intake.prepare_bulk_review("26PR40005,dai37", ",", False, conn=conn)
        assert review.applicable, review.errors
        assert seen_writes == []
    finally:
        conn.set_authorizer(None)
        conn.close()


def test_duplicate_and_existing_case_ids_make_the_whole_preview_inapplicable(mutable_db):
    conn = sqlite3.connect(mutable_db)
    try:
        preset_id = conn.execute("SELECT id FROM Presets WHERE short_code='dai'").fetchone()[0]
    finally:
        conn.close()
    assert database.save_case("26PR40006", preset_id, "", {"blocks": {}}, "<p>saved</p>")

    duplicate = bulk_intake.prepare_bulk_review("26PR40007,dai\n26PR40007,etc2", ",", False)
    existing = bulk_intake.prepare_bulk_review("26PR40006,dai", ",", False)
    assert not duplicate.applicable and "Duplicate" in duplicate.errors[0]
    assert not existing.applicable and "already exist" in existing.errors[0]


def test_review_binds_content_and_exact_target_namespace(mutable_db):
    review = bulk_intake.prepare_bulk_review("26PR40008,dai37", ",", False)
    assert review.applicable
    conn = database.get_db_connection()
    try:
        shortcut = conn.execute("SELECT shortcut FROM Snippets ORDER BY shortcut LIMIT 1").fetchone()[0]
        conn.execute("UPDATE Snippets SET expansion=expansion || ' ' WHERE shortcut=?", (shortcut,))
        conn.commit()
        assert bulk_intake.review_staleness(review, conn=conn) == "Content changed after this batch was reviewed."
    finally:
        conn.close()

    # A newly occupied exact target also invalidates a fresh review, while an
    # unrelated Case would not be part of the CP4 review binding.
    fresh = bulk_intake.prepare_bulk_review("26PR40009,dai37", ",", False)
    row = fresh.rows[0]
    assert database.save_case(row.case_number, row.preset_id, row.clinical_info,
                              row.structured_input, row.rendered_html)
    assert bulk_intake.review_staleness(fresh) == "A reviewed Case ID is now occupied."


def test_unreachable_active_quick_type_grammar_refuses_the_whole_review(mutable_db):
    conn = sqlite3.connect(mutable_db)
    try:
        # ``2`` is a valid modifier for dai, but longest-prefix routing would
        # route dai2 to this active owner instead.  CP4 must not preview a
        # batch against that damaged graph.
        conn.execute("INSERT INTO Presets(short_code,name,category) VALUES ('dai2','collision','test')")
        conn.commit()
    finally:
        conn.close()
    review = bulk_intake.prepare_bulk_review("26PR40010,dai", ",", False)
    assert not review.applicable
    assert review.errors == ("Active Quick Type configuration or endpoint graph is invalid.",)


@pytest.mark.parametrize("sql, params", [
    ("UPDATE Quick_Type_Tokens SET block_sort_order=999 WHERE preset_id=(SELECT id FROM Presets WHERE short_code='dai') AND sort_order=0", ()),
    ("UPDATE Quick_Type_Tokens SET field_key='cp4_missing_field' WHERE preset_id=(SELECT id FROM Presets WHERE short_code='dai') AND sort_order=0", ()),
    ("UPDATE Fields SET is_archived=1 WHERE key='appendicite_type'", ()),
])
def test_invalid_active_quick_type_endpoints_refuse_even_a_bare_code(mutable_db, sql, params):
    conn = sqlite3.connect(mutable_db)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
    review = bulk_intake.prepare_bulk_review("26PR40011,dai", ",", False)
    assert not review.applicable
    assert review.errors == ("Active Quick Type configuration or endpoint graph is invalid.",)


def test_warning_multiplicity_is_preserved_in_frozen_review_and_digest(mutable_db):
    shared = "CP4 duplicate warning text"
    conn = sqlite3.connect(mutable_db)
    try:
        block_id = conn.execute("SELECT id FROM Blocks WHERE key='appendice'").fetchone()[0]
        # The seed grammar stops after the measurement. Add a valid active
        # boolean lookup so this CP4 probe can fire the existing warning too.
        conn.execute(
            """INSERT INTO Quick_Type_Tokens
               (preset_id,sort_order,block_sort_order,field_key,token_kind,lookup_table,digit_width)
               VALUES ((SELECT id FROM Presets WHERE short_code='dai'),2,0,
                       'false_membranes','lookup','{"f":true}',NULL)"""
        )
        conn.commit()
    finally:
        conn.close()
    base = bulk_intake.prepare_bulk_review("26PR40012,dai11f", ",", False)
    assert base.applicable
    # Insert distinct matching predicates with the same presentation text
    # only after the baseline digest has captured the original rule set.
    conn = sqlite3.connect(mutable_db)
    try:
        conn.executemany(
            """INSERT INTO Field_Consistency_Rules
               (block_id,field_a_key,field_a_values,field_b_key,field_b_values,message)
               VALUES (?,?,?,?,?,?)""",
            [
                (block_id, "false_membranes", "[true]", "appendicite_type", '["endo"]', shared),
                (block_id, "false_membranes", "[true]", "appendix_size_cm", "[1]", shared),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    review = bulk_intake.prepare_bulk_review("26PR40012,dai11f", ",", False)
    assert review.applicable
    assert review.rows[0].warnings.count(shared) == 2
    assert review.interpretation_sha256 != base.interpretation_sha256


def test_csv_field_ceiling_and_exact_source_boundaries_match_cp4_contract():
    large_cell = "26PR40013," + ("x" * (140 * 1024))
    assert bulk_intake.parse_bulk_source(large_cell, ",", False).rows[0].case_number == "26PR40013"

    exact_limit = "1," + ("x" * (bulk_intake.MAX_SOURCE_BYTES - 2))
    assert len(exact_limit.encode("utf-8")) == bulk_intake.MAX_SOURCE_BYTES
    assert bulk_intake.parse_bulk_source(exact_limit, ",", False).rows[0].quick_type
    with pytest.raises(bulk_intake.BulkInputError, match="1 MiB"):
        bulk_intake.parse_bulk_source(exact_limit + "x", ",", False)

    exact_rows = "\n".join(f"26PR401{number:03d},dai" for number in range(250))
    assert len(bulk_intake.parse_bulk_source(exact_rows, ",", False).rows) == 250


def test_bulk_page_renders_each_safe_batch_error_once(mutable_db):
    """A BatchReview error is an iterable of messages, never a message string."""
    conn = sqlite3.connect(mutable_db)
    try:
        preset_id = conn.execute("SELECT id FROM Presets WHERE short_code='dai'").fetchone()[0]
    finally:
        conn.close()
    assert database.save_case("26PR40014", preset_id, "", {"blocks": {}}, "<p>saved</p>")

    app = AppTest.from_file("pages/bulk_intake.py").run()
    app.text_area(key="bulk_paste_source").set_value("26PR40015,not-a-code").run()
    app.button(key="bulk_prepare").click().run()
    assert [item.value for item in app.error] == ["Row 1 has an invalid Quick Type."]
    assert "not-a-code" not in app.error[0].value

    # Existing-ID refusal shares the same ``for error in review.errors``
    # presentation path and stays a single, readable safe message.
    app.text_area(key="bulk_paste_source").set_value("26PR40014,dai").run()
    app.button(key="bulk_prepare").click().run()
    assert [item.value for item in app.error] == [
        "One or more Case IDs already exist and cannot be imported."
    ]
