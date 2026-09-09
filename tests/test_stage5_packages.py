"""No-write Stage 5 contract/review tests, using synthetic temporary databases only."""

import copy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import random
import sqlite3

import pytest

import change_packages as packages
import content_changes as changes
import content_editing
import content_snapshot
import database
import editor_preview


def envelope(db_name, operations=None):
    return {
        "format": packages.FORMAT,
        "base_snapshot_sha256": content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(db_name)),
        "summary": "Synthetic candidate",
        "operations": operations or [{"op": "create", "table": "Snippets", "key": "synthetic_phrase",
                                     "values": {"expansion": "Phrase synthétique."}}],
    }


def raw(payload):
    return json.dumps(payload, ensure_ascii=False).encode()


def run(db_name, operations=None):
    return packages.dry_run(raw(envelope(db_name, operations)), db_name)


def graph():
    return [
        {"op": "create", "table": "Fields", "key": "synthetic_size",
         "values": {"label": "Taille", "type": "decimal", "default_value": None}},
        {"op": "create", "table": "Fields", "key": "synthetic_result",
         "values": {"label": "Aspect", "type": "select", "options": ["normal", "autre"], "default_value": "normal",
                    "conclusion_addendum_template": "Aspect {{ value }}."}},
        {"op": "create", "table": "Snippets", "key": "synthetic_phrase", "values": {"expansion": "Phrase."}},
        {"op": "create", "table": "Blocks", "key": "synthetic_block",
         "values": {"name": "Spécimen", "macro_template": "Taille {{ synthetic_size_display or 'inconnue' }}.",
                    "micro_template": "{{ snippet('synthetic_phrase') }} {{ synthetic_result }}.",
                    "conclusion_template": "Conclusion.", "context_template": "Taille {{ synthetic_size_display or '?' }}",
                    "title_fragment_template": "{{ synthetic_size_display or '?' }}"}},
        {"op": "create", "table": "Presets", "key": "synthetic_preset", "values": {"name": "Examen"}},
        {"op": "link", "table": "Block_Fields", "key": {"block_key": "synthetic_block", "field_key": "synthetic_size"},
         "values": {"sort_order": 0, "context_section": True}},
        {"op": "link", "table": "Block_Fields", "key": {"block_key": "synthetic_block", "field_key": "synthetic_result"},
         "values": {"sort_order": 1}},
        {"op": "link", "table": "Preset_Blocks",
         "key": {"preset_code": "synthetic_preset", "block_key": "synthetic_block", "sort_order": 999},
         "values": {"field_overrides": {"synthetic_size": 12.5}}},
    ]


def connection(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def save_synthetic_case(path, code="dai", structured=None, number="SYNTHETIC-PENDING", status="pending"):
    conn = connection(path)
    try:
        preset = conn.execute("SELECT * FROM Presets WHERE short_code=?", (code,)).fetchone()
        data = structured if structured is not None else {}
        fingerprint = database.compute_case_content_fingerprint(preset["id"], data, conn)
        conn.execute(
            """INSERT INTO Cases (case_number,preset_id,clinical_info,structured_input,rendered_html,status,content_fingerprint)
               VALUES (?,?,?,?,?,?,?)""",
            (number, preset["id"], "Contexte sauvegardé", json.dumps(data), "<p>SAVED-CANARY</p>", status, fingerprint),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM Cases WHERE case_number=?", (number,)).fetchone())
    finally:
        conn.close()


def test_context_privacy_allowlisted_queries_and_contract_example(mutable_db, monkeypatch):
    save_synthetic_case(mutable_db, number="PATIENT-ID-CANARY")
    conn = connection(mutable_db)
    conn.execute("INSERT INTO Content_Revisions(origin,summary) VALUES ('manual_edit','AUDIT-CANARY')")
    conn.commit()
    queried = set()
    forbidden = {"Cases", "Case_Validation_History", "Case_Status_History",
                 "Content_Revisions", "Content_Changes", "Editor_Safety_State"}
    def authorize(action, table, column, *args):
        if action == sqlite3.SQLITE_READ:
            queried.add(table)
            assert table not in forbidden
            assert column != "id" or table in content_snapshot.BASE_TABLES  # joins only
        return sqlite3.SQLITE_OK
    conn.set_authorizer(authorize)
    monkeypatch.setattr(database, "get_db_connection", lambda: conn)
    exported = packages.export_ai_context()
    assert b"PATIENT-ID-CANARY" not in exported and b"SAVED-CANARY" not in exported and b"AUDIT-CANARY" not in exported
    assert queried == set(content_snapshot.BASE_TABLES) | set(content_snapshot.RELATION_TABLES)
    payload = json.loads(exported)
    assert set(payload) == {"format", "snapshot_sha256", "instructions", "snapshot"}
    assert payload["snapshot_sha256"] == content_snapshot.content_snapshot_hash(payload["snapshot"])
    assert exported.endswith(b"\n")
    contract = payload["instructions"]["contract"]
    response = payload["instructions"]["response"]
    assert "Only when the request is sufficiently grounded" in response
    assert "required clinical content is missing, ask the user for it" in response
    assert "do not invent it, use placeholders, or return an empty package" in response
    assert "cannot be expressed by writable v1 operations" in response
    assert "do not return a package" in response
    assert "separate from clinical grounding" in contract["incomplete_source"]
    example = contract["example"]
    example["base_snapshot_sha256"] = payload["snapshot_sha256"]
    assert packages.dry_run(raw(example), mutable_db).candidate_snapshot_hash
    # Named fixture measurement is documented; this fixture's content is unchanged by Cases/audit.
    assert len(exported) == 28806  # seed_data.seed_all content + generated v1 contract
    assert len(exported) == len(packages.export_ai_context(mutable_db))


def test_generated_contract_maps_operations_tables_identities_and_limits():
    contract = packages.authoring_contract()
    update = {table: sorted(spec[1]) for table, spec in content_editing.EDITABLE.items()}
    assert contract["syntax"] == {
        "create": {"members": ["op", "table", "key", "values"], "tables": packages.CREATE},
        "update": {"members": ["op", "table", "key", "set"], "tables": update},
        "link": {"members": ["op", "table", "key", "values"], "tables": packages.LINK},
    }
    assert contract["create"] == packages.CREATE
    assert contract["update"] == update
    assert contract["link"] == packages.LINK
    assert contract["limits"] == {
        "raw_utf8_bytes": {"max": packages.MAX_BYTES},
        "json_nesting": {"max": packages.MAX_DEPTH},
        "operations": {"min": 1, "max": packages.MAX_OPERATIONS},
        "summary_characters": {"min": 1, "max": 500},
        "new_key_characters": {"min": 1, "max": 80},
        "sort_order": {"min": 0, "max": 999},
        "number": {"min": 0, "max": packages.MAX_INTEGER},
        "decimal": {"min": 0, "finite": True},
    }
    assert all(contract["identity"][table] == [key]
               for table, (key, _columns) in content_snapshot.BASE_TABLES.items())
    assert contract["identity"] == {
        **{table: [key] for table, (key, _columns) in content_snapshot.BASE_TABLES.items()},
        **{table: list(key_columns) for table, (_columns, key_columns)
           in content_snapshot.RELATION_TABLES.items()},
    }
    assert set(contract["tables"]) == set(content_snapshot.BASE_TABLES) | set(content_snapshot.RELATION_TABLES)
    assert {table for table, metadata in contract["tables"].items()
            if metadata["access"] == "writable"} == set(packages.CREATE) | set(packages.LINK)
    assert {table for table, metadata in contract["tables"].items()
            if metadata["access"] == "read_only"} == set(content_snapshot.RELATION_TABLES) - set(packages.LINK)
    assert all(metadata["access"] in {"writable", "read_only"}
               for metadata in contract["tables"].values())


def test_complete_contract_example_uses_both_links_and_native_decimal_values(mutable_db):
    context = json.loads(packages.export_ai_context(mutable_db))
    example = copy.deepcopy(context["instructions"]["contract"]["example"])
    example["base_snapshot_sha256"] = context["snapshot_sha256"]
    assert len(example["operations"]) == 5
    assert {operation["table"] for operation in example["operations"] if operation["op"] == "link"} == {
        "Block_Fields", "Preset_Blocks"
    }
    field = next(operation for operation in example["operations"] if operation["table"] == "Fields")
    preset_link = next(operation for operation in example["operations"] if operation["table"] == "Preset_Blocks")
    assert type(field["values"]["default_value"]) is float
    assert type(preset_link["values"]["field_overrides"]["example_size_mm"]) is float
    assert packages.dry_run(raw(example), mutable_db).candidate_snapshot_hash


def test_former_single_create_example_still_parses_and_dry_runs(mutable_db):
    package = {
        "format": packages.FORMAT,
        "base_snapshot_sha256": content_snapshot.content_snapshot_hash(
            content_snapshot.export_content_snapshot(mutable_db)
        ),
        "summary": "Add an optional specimen size field",
        "operations": [{"op": "create", "table": "Fields", "key": "specimen_size_mm",
                        "values": {"label": "Taille (mm)", "type": "decimal", "default_value": None}}],
    }
    assert packages.parse_package(raw(package))["operations"]
    assert packages.dry_run(raw(package), mutable_db).candidate_snapshot_hash


def test_snapshot_hash_is_unchanged_when_only_instructions_change(mutable_db, monkeypatch):
    original = json.loads(packages.export_ai_context(mutable_db))
    monkeypatch.setattr(packages, "authoring_contract", lambda: {"changed": "instructions only"})
    revised = json.loads(packages.export_ai_context(mutable_db))
    assert original["snapshot"] == revised["snapshot"]
    assert original["snapshot_sha256"] == revised["snapshot_sha256"]
    assert original["snapshot_sha256"] == content_snapshot.content_snapshot_hash(original["snapshot"])


def test_graph_shuffle_complete_reports_and_no_source_writes(mutable_db, monkeypatch):
    before_bytes = Path(mutable_db).read_bytes()
    operations = graph()
    report = run(mutable_db, operations)
    shuffled = copy.deepcopy(operations)
    random.Random(31).shuffle(shuffled)
    other = run(mutable_db, shuffled)
    assert other.package_hash == report.package_hash
    assert other.candidate_snapshot_hash == report.candidate_snapshot_hash
    assert other.changes == report.changes
    assert Path(mutable_db).read_bytes() == before_bytes
    preset = next(p for p in report.presets if p["code"] == "synthetic_preset")
    assert preset["added"] and preset["affected"]
    output = preset["after"]["report"]
    assert output["clinical_info"] == "Taille 12.5" and output["title"] == "Examen 12.5"
    assert "Examen macroscopique" in output["micro_plain"]
    assert "Phrase. normal." in output["micro_plain"] and "Aspect normal." in output["conclusion_plain"]
    assert "<b>" in output["html"]
    assert not Path(mutable_db + "-journal").exists()


def test_review_is_deeply_immutable_and_does_not_expose_reports_in_repr(mutable_db):
    review = run(mutable_db)
    with pytest.raises(FrozenInstanceError):
        review.local_guard = "forged"
    review.operations.clear()
    review.changes[0]["after"]["expansion"] = "mutated"
    assert review.operations and review.changes[0]["after"]["expansion"] == "Phrase synthétique."
    assert "Phrase synthétique" not in repr(review)


@pytest.mark.parametrize("bad", [
    b"\xff", b"{}", b"{} {}", b'{"x":1,"x":2}', b"NaN", b"Infinity", b"1e999",
    b'{"x":{"a":1,"a":2}}', b"["*22+b"0"+b"]"*22, b" "* (packages.MAX_BYTES+1),
    b'{"x":"\\ud800"}',
])
def test_raw_parser_rejections(bad):
    with pytest.raises(packages.PackageError):
        packages.parse_package(bad)


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(format="pathopilot-content-snapshot-v1"),
    lambda p: p.update(summary=" "),
    lambda p: p.update(summary="x"*501),
    lambda p: p.update(base_snapshot_sha256="A"*64),
    lambda p: p.update(extra="private"),
    lambda p: p.update(operations=[]),
    lambda p: p.update(operations=p["operations"]*201),
    lambda p: p["operations"][0].update(op="delete"),
    lambda p: p["operations"][0].update(table="Cases"),
    lambda p: p["operations"][0].update(key="bad.key"),
    lambda p: p["operations"][0].update(key="x"*81),
    lambda p: p["operations"][0]["values"].update(id=1),
    lambda p: p["operations"][0]["values"].update(expansion=None),
    lambda p: p["operations"][0]["values"].update(expansion=" "),
    lambda p: p["operations"].append(copy.deepcopy(p["operations"][0])),
])
def test_exact_contract_rejections(db, mutate):
    payload = envelope(db)
    mutate(payload)
    with pytest.raises(packages.PackageError):
        packages.parse_package(raw(payload))


@pytest.mark.parametrize("kind,value,options", [
    ("number", True, None), ("number", 1.0, None), ("number", "1", None),
    ("number", -1, None), ("number", 2**53, None), ("decimal", "1.2", None),
    ("decimal", False, None), ("decimal", -0.1, None), ("checkbox", 1, None),
    ("checkbox", "true", None), ("text", 3, None), ("select", "bad", ["ok"]),
    ("select", "ok", '["ok"]'), ("select", "ok", []), ("select", "ok", ["ok", "ok"]),
    ("text", "", ["bad"]), ("invalid", None, None),
])
def test_field_native_types(db, kind, value, options):
    op = {"op": "create", "table": "Fields", "key": "synthetic_field",
          "values": {"label": "Valeur", "type": kind, "default_value": value, "options": options}}
    with pytest.raises(packages.PackageError):
        run(db, [op])


@pytest.mark.parametrize("kind,value", [
    ("text", ""), ("text", None), ("number", 2**53-1),
    ("decimal", None), ("decimal", 1e30), ("checkbox", False),
])
def test_valid_standalone_defaults(db, kind, value):
    result = run(db, [{"op": "create", "table": "Fields", "key": "synthetic_field",
                       "values": {"label": "Valeur", "type": kind, "default_value": value}}])
    assert result.data["standalone"][0]["label"] == "No Preset uses this yet"


@pytest.mark.parametrize("mutate", [
    lambda ops: ops[7]["key"].update(sort_order=1000),
    lambda ops: ops[7]["key"].update(sort_order=True),
    lambda ops: ops[5]["values"].update(sort_order=-1),
    lambda ops: ops[6]["values"].update(sort_order=0),
    lambda ops: ops[5]["values"].update(context_section=1),
    lambda ops: ops[5]["values"].update(context_section=False),
    lambda ops: ops[7]["key"].update(preset_code="dai"),
    lambda ops: ops[5]["key"].update(block_key="appendice"),
    lambda ops: ops[5]["key"].update(field_key="missing"),
    lambda ops: ops[7]["values"].update(field_overrides={"unknown": 1}),
    lambda ops: ops[7]["values"].update(field_overrides={"synthetic_result": None}),
    lambda ops: ops[7]["values"].update(field_overrides={"synthetic_size": "4.5"}),
    lambda ops: ops[7]["values"].update(field_overrides='{}'),
    lambda ops: ops.pop(7),
    lambda ops: ops[3]["values"].update(is_table=0),
])
def test_graph_rejections(db, mutate):
    ops = graph()
    mutate(ops)
    with pytest.raises(packages.PackageError):
        run(db, ops)


@pytest.mark.parametrize("key", ["snippet", "value", "site_label", "fragment_text", "true", "False",
                               "synthetic_size_display"])
def test_field_reserved_collisions(db, key):
    ops = graph()
    ops.append({"op": "create", "table": "Fields", "key": key,
                "values": {"label": "Reserved", "type": "text", "default_value": ""}})
    with pytest.raises(packages.PackageError):
        run(db, ops)


def test_decimal_alias_collision_with_existing_field(db):
    ops = [
        {"op": "create", "table": "Fields", "key": "synthetic_x_display",
         "values": {"label": "Texte", "type": "text", "default_value": ""}},
        {"op": "create", "table": "Fields", "key": "synthetic_x",
         "values": {"label": "Decimal", "type": "decimal", "default_value": None}},
    ]
    with pytest.raises(packages.PackageError):
        run(db, ops)


def test_duplicates_static_block_null_inheritance_and_prefix_warning(db):
    ops = graph()
    ops[4]["key"] = "dai_synthetic"
    ops[7]["key"]["preset_code"] = "dai_synthetic"
    ops[7]["values"]["field_overrides"]["synthetic_size"] = None
    ops.append(copy.deepcopy(ops[7]))
    ops[-1]["key"]["sort_order"] = 1
    result = run(db, ops)
    report = next(p for p in result.presets if p["code"] == "dai_synthetic")["after"]["report"]
    assert "Examen macroscopique" not in report["micro_plain"] and "2." in report["micro_plain"]
    assert result.data["warnings"]
    static = [{"op": "create", "table": "Blocks", "key": "static",
               "values": {"name": "Static", "macro_template": "Macro", "micro_template": "Micro",
                          "conclusion_template": "Conclusion"}}]
    assert run(db, static).data["standalone"][0]["report"]["micro_plain"]


def test_null_global_discrete_defaults_need_block_override(db):
    ops = graph()
    ops[1]["values"]["default_value"] = None
    with pytest.raises(packages.PackageError):
        run(db, ops)
    ops[6]["values"]["default_override"] = "normal"
    assert run(db, ops).candidate_snapshot_hash
    with pytest.raises(packages.PackageError):
        run(db, [ops[1]])


def test_updates_storage_noops_and_original_paths(db):
    op = {"op": "update", "table": "Fields", "key": "fragments", "set": {"default_value": 9}}
    result = run(db, [op])
    assert result.changes[0]["after"]["default_value"] == "9"
    original = result.changes[0]["before"]["default_value"]
    op["set"]["default_value"] = int(original)
    with pytest.raises(packages.PackageError) as error:
        run(db, [graph()[2], op])
    assert error.value.ai_feedback()["errors"][0]["path"].startswith("operations[1]")
    op["set"] = {"default_value": None}
    with pytest.raises(packages.PackageError):
        run(db, [op])


def test_pending_locks_stale_impact_and_all_pending_validation(mutable_db):
    case = save_synthetic_case(mutable_db, structured={
        "master_lock": True, "context_title_lock": True, "final_title_edit": "",
        "final_micro_edit": "", "final_conc_edit": "",
        "wildcard_notes": [{"target_idx": 0, "text": "LOCAL-NOTE", "target_name": "Spécimen", "note_type": "Autre"}],
    })
    save_synthetic_case(mutable_db, code="vb", number="UNAFFECTED")
    save_synthetic_case(mutable_db, number="VALIDATED", status="validated")
    conn = connection(mutable_db)
    conn.execute("UPDATE Cases SET content_fingerprint='older' WHERE id=?", (case["id"],))
    conn.commit()
    conn.close()
    result = run(mutable_db, [{"op": "update", "table": "Blocks", "key": "appendice",
                              "set": {"micro_template": "New micro"}}])
    assert len(result.pending_cases) == 1
    impact = result.pending_cases[0]
    assert impact["already_stale"] and impact["saved_html"] == "<p>SAVED-CANARY</p>"
    assert impact["before"]["report"] == impact["after"]["report"]
    assert impact["after"]["report"]["title"] == ""
    assert impact["after"]["report"]["micro_plain"] == ""
    assert impact["after"]["report"]["clinical_info"] == "Contexte sauvegardé"
    assert result.data["validated_pending_count"] == 2
    conn = connection(mutable_db)
    conn.execute("UPDATE Cases SET structured_input='[]' WHERE case_number='UNAFFECTED'")
    conn.commit()
    conn.close()
    with pytest.raises(packages.PackageError) as error:
        run(mutable_db)
    assert error.value.ai_feedback()["errors"][0]["code"] == "pending"


def test_invalid_base_repair_and_manual_lock_cannot_hide_failure(mutable_db):
    save_synthetic_case(mutable_db, structured={"master_lock": True, "final_micro_edit": "Locked"})
    conn = connection(mutable_db)
    conn.execute("UPDATE Blocks SET micro_template='{{ missing }}' WHERE key='appendice'")
    conn.commit()
    conn.close()
    result = run(mutable_db, [{"op": "update", "table": "Blocks", "key": "appendice",
                              "set": {"micro_template": "Repaired"}}])
    assert "error" in result.pending_cases[0]["before"]
    assert result.pending_cases[0]["after"]["report"]["micro_plain"] == "Locked"
    with pytest.raises(packages.PackageError):
        run(mutable_db, [{"op": "update", "table": "Blocks", "key": "appendice",
                         "set": {"micro_template": "{{ 1 / 0 }}"}}])


def test_orphan_addendum_branches_and_sandbox(db):
    for template in ["{% if value %}{{ 1 / 0 }}{% endif %}", "{{ snippet('missing') }}",
                     "{{ value.__class__.__mro__ }}", "{{ snippet('x', ignored=1) }}"]:
        with pytest.raises(packages.PackageError):
            run(db, [{"op": "create", "table": "Fields", "key": "orphan",
                      "values": {"label": "Orphan", "type": "checkbox", "default_value": False,
                                 "conclusion_addendum_template": template}}])


def test_discrete_branch_under_preset_override(db):
    ops = graph()
    ops[3]["values"]["micro_template"] = "{% if synthetic_size == 12.5 and synthetic_result == 'autre' %}{{ 1 / 0 }}{% endif %} OK"
    with pytest.raises(packages.PackageError):
        run(db, ops)


def test_pending_decimal_composition_notes_and_empty_vs_missing(mutable_db):
    conn = connection(mutable_db)
    preset = conn.execute("SELECT id FROM Presets WHERE short_code='etc_bi'").fetchone()[0]
    blocks = database.get_preset_blocks_on_connection(conn, preset)
    instances = [{"block_id": b["block_id"], "instance_no": b["sort_order"]} for b in reversed(blocks)]
    instances.append({"block_id": blocks[0]["block_id"], "instance_no": 1000})
    values = {f"{blocks[0]['key']}#{i['instance_no']}": {"nodule_size_mm": size}
              for i, size in zip(instances, [" 12,5 ", "", "30"])}
    case = {"preset_id": preset, "clinical_info": "Free context",
            "structured_input": {"block_instances": instances, "blocks": values,
                                 "wildcard_notes": [{"target_idx": 1, "text": "POSITION-NOTE", "target_name": "Spécimen", "note_type": "Autre"}]}}
    output = editor_preview.render_saved_case(conn, case)
    assert output["instances"] == instances
    assert "12.5" in output["micro_plain"] and "POSITION-NOTE" in output["micro_plain"]
    assert output["clinical_info"] == "Free context"
    empty = editor_preview.render_saved_case(conn, {**case, "structured_input": {"block_instances": []}})
    fallback = editor_preview.render_saved_case(conn, {**case, "structured_input": {}})
    assert empty["micro_plain"] == "" and fallback["micro_plain"]
    with pytest.raises(ValueError):
        editor_preview.render_saved_case(conn, {**case, "structured_input": {"block_instances": None}})
    conn.close()


def test_connection_tripwire_and_candidate_consistency_rules(mutable_db, monkeypatch):
    conn = connection(mutable_db)
    conn.execute("UPDATE Field_Consistency_Rules SET message='CANDIDATE-RULE'")
    conn.commit()
    case = save_synthetic_case(mutable_db)
    monkeypatch.setattr(database, "get_db_connection", lambda: pytest.fail("default connection"))
    # Use a rule's actual forbidden combination rather than assuming seed vocabulary.
    block = database.get_preset_blocks_on_connection(conn, case["preset_id"])[0]
    rule = database.get_consistency_rules_on_connection(conn, block["block_id"])[0]
    case["structured_input"] = {"blocks": {f"{block['key']}#{block['sort_order']}": {
        rule["field_a_key"]: rule["field_a_values"][0], rule["field_b_key"]: rule["field_b_values"][0]}}}
    report = editor_preview.render_saved_case(conn, case)
    assert report["warnings"] == ["CANDIDATE-RULE"]
    assert run(mutable_db).candidate_snapshot_hash
    conn.close()


def test_local_guard_tracks_pending_revision_and_identity_but_content_hash_does_not(mutable_db):
    conn = connection(mutable_db)
    before = changes.local_review_guard(conn)
    snapshot_hash = content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(conn))
    conn.execute("INSERT INTO Content_Revisions(origin,summary) VALUES ('manual_edit','ABA')")
    assert changes.local_review_guard(conn) != before
    conn.rollback()
    conn.execute("UPDATE Snippets SET id=id+1000")
    assert changes.local_review_guard(conn) != before
    assert content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(conn)) == snapshot_hash
    conn.rollback()
    conn.close()
    case = save_synthetic_case(mutable_db)
    conn = connection(mutable_db)
    assert changes.local_review_guard(conn) != before
    for column, value in [("clinical_info", "changed"), ("structured_input", '{"blocks":{}}'),
                          ("rendered_html", "new"), ("content_fingerprint", "new"), ("status", "validated"),
                          ("case_number", "renamed")]:
        initial = changes.local_review_guard(conn)
        conn.execute(f"UPDATE Cases SET {column}=? WHERE id=?", (value, case["id"]))
        assert changes.local_review_guard(conn) != initial
        conn.rollback()
    initial = changes.local_review_guard(conn)
    conn.execute("DELETE FROM Cases WHERE id=?", (case["id"],))
    assert changes.local_review_guard(conn) != initial
    conn.rollback()
    conn.close()


def test_content_stale_vs_pending_only_retry(mutable_db):
    payload = envelope(mutable_db)
    first = packages.dry_run(raw(payload), mutable_db)
    save_synthetic_case(mutable_db)
    second = packages.dry_run(raw(payload), mutable_db)
    assert first.local_guard != second.local_guard
    assert first.candidate_snapshot_hash == second.candidate_snapshot_hash
    conn = connection(mutable_db)
    conn.execute("UPDATE Snippets SET expansion=expansion || ' new'")
    conn.commit()
    conn.close()
    with pytest.raises(packages.PackageError) as error:
        packages.dry_run(raw(payload), mutable_db)
    assert error.value.ai_feedback()["errors"][0]["code"] == "stale"


def test_read_transaction_snapshot_consistency_under_concurrent_writer(mutable_db):
    conn = connection(mutable_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    reader, writer = connection(mutable_db), connection(mutable_db)
    before = content_snapshot.snapshot_from_connection(reader)
    fired = False
    def trace(sql):
        nonlocal fired
        if not fired and sql.startswith("SELECT key, name"):
            fired = True
            writer.execute("UPDATE Fields SET label=label || ' NEW'")
            writer.execute("UPDATE Blocks SET name=name || ' NEW'")
            writer.commit()
    reader.set_trace_callback(trace)
    captured = content_snapshot.snapshot_from_connection(reader)
    assert fired and captured == before and not reader.in_transaction
    reader.set_trace_callback(None)
    assert content_snapshot.snapshot_from_connection(reader) != before
    reader.execute("BEGIN")
    content_snapshot.snapshot_from_connection(reader)
    assert reader.in_transaction
    reader.rollback()
    reader.close()
    writer.close()


def test_memory_cleanup_on_success_and_failure(mutable_db, monkeypatch):
    original = sqlite3.connect
    candidates, sources, statements = [], [], []
    class Tracked(sqlite3.Connection):
        closed = False
        backups = 0
        def close(self):
            self.closed = True
            super().close()
        def backup(self, target, **kwargs):
            self.backups += 1
            return super().backup(target, **kwargs)
    def tracked(path, *args, **kwargs):
        assert str(path) in (":memory:", mutable_db)
        conn = original(path, *args, factory=Tracked, **kwargs)
        if str(path) == ":memory:":
            candidates.append(conn)
        else:
            sources.append(conn)
            conn.set_trace_callback(statements.append)
        return conn
    payload = raw(envelope(mutable_db))
    monkeypatch.setattr(sqlite3, "connect", tracked)
    packages.dry_run(payload, mutable_db)
    with pytest.raises(packages.PackageError):
        packages.dry_run(payload.replace(b'"create"', b'"update"').replace(b'"values"', b'"set"'), mutable_db)
    assert len(candidates) == 2 and all(c.closed for c in candidates)
    assert all(c.closed and c.backups == 1 for c in sources)
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE", "BEGIN IMMEDIATE")) for sql in statements)


def test_error_feedback_never_echoes_private_input_or_exception(db):
    payload = envelope(db)
    payload["operations"] *= 30
    payload["operations"][0]["table"] = "PATIENT-CANARY"
    with pytest.raises(packages.PackageError) as error:
        packages.parse_package(raw(payload))
    feedback = error.value.ai_feedback()
    assert len(feedback["errors"]) == 20 and feedback["omitted_errors"] == 10
    assert "PATIENT-CANARY" not in json.dumps(feedback)
    exception = packages.PackageError("pending", local="Patient CANARY at /private/db SQL SELECT")
    assert "CANARY" not in json.dumps(exception.ai_feedback()) and "CANARY" not in str(exception)


def test_native_decimal_integer_overflow_is_a_safe_error(db):
    with pytest.raises(packages.PackageError):
        run(db, [{"op": "create", "table": "Fields", "key": "overflow",
                  "values": {"label": "Size", "type": "decimal", "default_value": 10**400}}])


def test_existing_nullable_thyroid_options_and_text_overrides_are_preserved(db):
    result = run(db, [
        {"op": "create", "table": "Presets", "key": "reuse_thyroid", "values": {"name": "Réutilisation"}},
        {"op": "link", "table": "Preset_Blocks",
         "key": {"preset_code": "reuse_thyroid", "block_key": "thyroid_cytology", "sort_order": 0}, "values": {}},
    ])
    assert next(p for p in result.presets if p["code"] == "reuse_thyroid")["after"]["report"]["html"]


def test_storage_normalization_and_each_update_value_must_change(db):
    ops = [{"op": "create", "table": "Snippets", "key": "trimmed", "values": {"expansion": " phrase ", "category": "  "}}]
    result = run(db, ops)
    assert result.changes[0]["after"]["expansion"] == "phrase"
    assert result.changes[0]["after"]["category"] is None
    conn = connection(db)
    block = dict(conn.execute("SELECT * FROM Blocks WHERE key='appendice'").fetchone())
    field = dict(conn.execute("SELECT * FROM Fields WHERE key='appendix_size_cm'").fetchone())
    conn.close()
    with pytest.raises(packages.PackageError):
        run(db, [{"op": "update", "table": "Blocks", "key": "appendice",
                  "set": {"micro_template": "Changed", "macro_template": block["macro_template"]}}])
    with pytest.raises(packages.PackageError):
        run(db, [{"op": "update", "table": "Fields", "key": "appendix_size_cm",
                  "set": {"default_value": float(field["default_value"])}}])


def test_update_existing_legacy_key_does_not_impose_new_grammar(mutable_db):
    conn = connection(mutable_db)
    conn.execute("INSERT INTO Snippets(shortcut,expansion) VALUES ('legacy.key','Old')")
    conn.commit()
    conn.close()
    assert run(mutable_db, [{"op": "update", "table": "Snippets", "key": "legacy.key",
                            "set": {"expansion": "New"}}]).changes[0]["after"]["expansion"] == "New"


def test_create_update_collision_and_table_block_refusal(mutable_db):
    ops = [graph()[2], {"op": "update", "table": "Snippets", "key": "synthetic_phrase", "set": {"expansion": "Updated"}}]
    with pytest.raises(packages.PackageError):
        run(mutable_db, ops)
    conn = connection(mutable_db)
    conn.execute("UPDATE Blocks SET is_table=1 WHERE key='appendice'")
    conn.commit()
    conn.close()
    with pytest.raises(packages.PackageError):
        run(mutable_db, [{"op": "update", "table": "Blocks", "key": "appendice", "set": {"micro_template": "Changed"}}])
    with pytest.raises(packages.PackageError):
        run(mutable_db, [
            {"op": "create", "table": "Presets", "key": "table_reuse", "values": {"name": "Test"}},
            {"op": "link", "table": "Preset_Blocks",
             "key": {"preset_code": "table_reuse", "block_key": "appendice", "sort_order": 0}, "values": {}},
        ])


def test_all_existing_override_configuration_is_checked(mutable_db):
    conn = connection(mutable_db)
    conn.execute("UPDATE Preset_Blocks SET field_overrides=? WHERE preset_id=(SELECT id FROM Presets WHERE short_code='dai')",
                 ('{"unknown_field": 3}',))
    conn.commit()
    conn.close()
    with pytest.raises(packages.PackageError):
        run(mutable_db)


def test_updated_field_or_snippet_validates_unreferenced_block(mutable_db):
    conn = connection(mutable_db)
    conn.execute("INSERT INTO Blocks(key,name,macro_template,micro_template,conclusion_template) VALUES ('orphan','Orphan','Macro','{{ fragments / (fragments - 9) }}','Conclusion')")
    block_id = conn.execute("SELECT id FROM Blocks WHERE key='orphan'").fetchone()[0]
    field_id = conn.execute("SELECT id FROM Fields WHERE key='fragments'").fetchone()[0]
    conn.execute("INSERT INTO Block_Fields(block_id,field_id,sort_order) VALUES (?,?,0)", (block_id, field_id))
    conn.commit()
    conn.close()
    with pytest.raises(packages.PackageError):
        run(mutable_db, [{"op": "update", "table": "Fields", "key": "fragments", "set": {"default_value": 9}}])


def test_nullable_text_defaults_validate_the_fresh_workspace_value(db):
    operations = [
        {"op": "create", "table": "Fields", "key": "nullable_text",
         "values": {"label": "Optional text", "type": "text", "default_value": None}},
        {"op": "create", "table": "Blocks", "key": "nullable_text_block",
         "values": {"name": "Nullable text", "macro_template": (
             "{% if nullable_text is none %}Not mounted{% else %}{{ 1 / 0 }}{% endif %}"
         ), "micro_template": "Micro", "conclusion_template": "Conclusion"}},
        {"op": "create", "table": "Presets", "key": "nullable_text_preset",
         "values": {"name": "Nullable text"}},
        {"op": "link", "table": "Block_Fields",
         "key": {"block_key": "nullable_text_block", "field_key": "nullable_text"},
         "values": {"sort_order": 0}},
        {"op": "link", "table": "Preset_Blocks",
         "key": {"preset_code": "nullable_text_preset", "block_key": "nullable_text_block", "sort_order": 0},
         "values": {}},
    ]
    with pytest.raises(packages.PackageError) as error:
        run(db, operations)
    assert error.value.ai_feedback()["errors"][0]["code"] == "candidate"


def test_jinja_literal_snippet_dependency_changes_pending_fingerprint(mutable_db):
    conn = connection(mutable_db)
    conn.execute(
        "UPDATE Blocks SET micro_template=? WHERE key='appendice'",
        ("{{ snippet('absence_' 'malignite') }}",),
    )
    conn.commit()
    conn.close()
    case = save_synthetic_case(mutable_db)

    result = run(mutable_db, [{
        "op": "update", "table": "Snippets", "key": "absence_malignite",
        "set": {"expansion": "Nouvelle phrase."},
    }])

    assert database._snippet_shortcuts(["{{ snippet('absence_' 'malignite') }}"]) == ["absence_malignite"]
    assert len(result.pending_cases) == 1
    assert result.pending_cases[0]["before"]["fingerprint"] == case["content_fingerprint"]
    assert result.pending_cases[0]["after"]["fingerprint"] != case["content_fingerprint"]
    assert "Nouvelle phrase." in result.pending_cases[0]["after"]["report"]["micro_plain"]


def test_affected_preset_with_unchanged_output_and_snippet_metadata(db):
    result = run(db, [{"op": "update", "table": "Fields", "key": "fragments", "set": {"label": "Fragments renommés"}}])
    impacted = [p for p in result.presets if p["affected"]]
    assert impacted and all(not p["output_changed"] for p in impacted)
    conn = connection(db)
    snippet = dict(conn.execute("SELECT * FROM Snippets WHERE shortcut='absence_malignite'").fetchone())
    conn.close()
    result = run(db, [{"op": "update", "table": "Snippets", "key": snippet["shortcut"], "set": {"category": "Synthetic metadata"}}])
    assert any(p["affected"] and not p["output_changed"] for p in result.presets)


def test_candidate_grouping_conflicts_are_warnings(mutable_db):
    case = save_synthetic_case(mutable_db, code="gt")
    conn = connection(mutable_db)
    blocks = database.get_preset_blocks_on_connection(conn, case["preset_id"])
    entries = {}
    hp_blocks = [b for b in blocks if any(f["key"] == "hp_positive" for f in b["fields"])]
    assert len(hp_blocks) == 2
    # Use actual options because the test checks agreement, not seed wording.
    opts = [False, True]
    entries[f"{hp_blocks[0]['key']}#{hp_blocks[0]['sort_order']}"] = {"hp_positive": opts[0]}
    entries[f"{hp_blocks[1]['key']}#{hp_blocks[1]['sort_order']}"] = {"hp_positive": opts[1]}
    conn.execute("UPDATE Cases SET structured_input=? WHERE id=?", (json.dumps({"blocks": entries}), case["id"]))
    conn.commit()
    conn.close()
    result = run(mutable_db, [{"op": "update", "table": "Fields", "key": "hp_positive", "set": {"label": "HP updated"}}])
    assert result.pending_cases[0]["after"]["report"]["conflicts"]


@pytest.mark.parametrize("data", [
    [], {"block_instances": None}, {"block_instances": [{"block_id": 99999, "instance_no": 0}]},
    {"block_instances": [{"block_id": True, "instance_no": 0}]},
    {"wildcard_notes": [{"target_idx": "0", "text": "invalid"}]},
    {"master_lock": "true"}, {"final_micro_edit": None}, {"blocks": []},
])
def test_malformed_saved_inputs_never_yield_review(mutable_db, data):
    # Insert directly: malformed saved inputs are precisely what save_case would not generate.
    case = save_synthetic_case(mutable_db)
    conn = connection(mutable_db)
    conn.execute("UPDATE Cases SET structured_input=? WHERE id=?", (json.dumps(data), case["id"]))
    conn.commit()
    conn.close()
    with pytest.raises(packages.PackageError) as error:
        run(mutable_db)
    assert error.value.ai_feedback()["errors"][0]["code"] == "pending"


def test_missing_base_fingerprint_is_local_and_candidate_repair_is_affected(mutable_db, monkeypatch):
    save_synthetic_case(mutable_db)
    original = database.compute_case_content_fingerprint
    def fingerprint(preset_id, structured, conn=None):
        if conn.execute("SELECT COUNT(*) FROM Snippets WHERE shortcut='synthetic_phrase'").fetchone()[0] == 0:
            raise ValueError("LOCAL-BASE-CANARY")
        return original(preset_id, structured, conn)
    monkeypatch.setattr(database, "compute_case_content_fingerprint", fingerprint)
    result = run(mutable_db)
    assert result.pending_cases[0]["before"]["error"] == "LOCAL-BASE-CANARY"
    assert result.pending_cases[0]["after"]["fingerprint"]


def test_candidate_trigger_cannot_write_cases_or_audit(mutable_db):
    case = save_synthetic_case(mutable_db)
    conn = connection(mutable_db)
    conn.execute("""CREATE TRIGGER unexpected_write AFTER INSERT ON Snippets BEGIN
                    UPDATE Cases SET clinical_info='changed'; END""")
    conn.commit()
    conn.close()
    before = Path(mutable_db).read_bytes()
    with pytest.raises(packages.PackageError):
        run(mutable_db)
    assert Path(mutable_db).read_bytes() == before


def test_backup_remains_consistent_during_concurrent_commit(mutable_db, monkeypatch):
    original_connect = sqlite3.connect
    writer = connection(mutable_db)
    writer.execute("PRAGMA journal_mode=WAL")
    initial = content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(mutable_db))
    class ConcurrentSource(sqlite3.Connection):
        def backup(self, target, **kwargs):
            fired = False
            def progress(status, remaining, total):
                nonlocal fired
                if not fired:
                    fired = True
                    writer.execute("UPDATE Fields SET label=label || ' concurrent'")
                    writer.execute("UPDATE Blocks SET name=name || ' concurrent'")
                    writer.commit()
            return super().backup(target, pages=1, progress=progress)
    def connect(path, *args, **kwargs):
        return original_connect(path, *args, factory=ConcurrentSource if str(path) == mutable_db else sqlite3.Connection, **kwargs)
    payload = raw(envelope(mutable_db))
    monkeypatch.setattr(sqlite3, "connect", connect)
    # A concurrent backup may capture the complete old or new state, but may not
    # silently accept a torn copy with the old package hash.
    try:
        result = packages.dry_run(payload, mutable_db)
        assert result.base_snapshot_hash == initial
    except packages.PackageError as error:
        assert error.ai_feedback()["errors"][0]["code"] == "stale"
    writer.close()


def test_incomplete_render_never_returns_review(mutable_db, monkeypatch):
    save_synthetic_case(mutable_db)
    monkeypatch.setattr(editor_preview, "render_saved_case", lambda *a, **kw: None)
    with pytest.raises(packages.PackageError) as error:
        run(mutable_db)
    assert error.value.ai_feedback()["errors"][0]["code"] == "pending"


def test_nondefault_consistency_warnings_are_retained(db):
    result = run(db)
    assert any(w["preset_code"] == "dai" and w["warnings"] for w in result.data["branch_warnings"])


def test_internal_operations_need_no_ai_envelope(db):
    snapshot_hash = content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot(db))
    review = changes.review_candidate(
        [{"op": "create", "table": "Snippets", "key": "internal", "values": {"expansion": "Internal"}}],
        snapshot_hash, db_name=db,
    )
    assert review.package_hash is None
    assert review.operations[0]["values"]["category"] is None


def test_saved_numeric_widget_type_is_not_silently_coerced(mutable_db):
    case = save_synthetic_case(mutable_db, code="gt")
    conn = connection(mutable_db)
    blocks = database.get_preset_blocks_on_connection(conn, case["preset_id"])
    key = f"{blocks[0]['key']}#{blocks[0]['sort_order']}"
    for value in ("2", True, 2.5):
        case["structured_input"] = {"blocks": {key: {"fragments": value}}}
        with pytest.raises(ValueError):
            editor_preview.render_saved_case(conn, case)
    conn.close()
