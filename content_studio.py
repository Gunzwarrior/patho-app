"""Internal, guided Content Studio intents.

This module deliberately contains no database writes.  It is the small
translation boundary between future guided forms and the source-independent
candidate service in :mod:`content_changes`.  The Stage 5 package parser does
not import this module and therefore retains its intentionally narrower v1
contract.
"""

from __future__ import annotations

import copy
import json
import sqlite3

import content_changes
import content_snapshot
import database


BASE_TABLES = frozenset(content_snapshot.BASE_TABLES)
CONFIGURATION_TABLES = frozenset(content_snapshot.RELATION_TABLES)
SUPPORTED_TABLES = BASE_TABLES | CONFIGURATION_TABLES


class StudioIntentError(ValueError):
    """A guided form supplied an incomplete or unsupported intent."""


def _copy_mapping(value, name):
    if not isinstance(value, dict):
        raise StudioIntentError(f"{name} must be an object.")
    return copy.deepcopy(value)


def operation(action, table, key, values=None):
    """Build one immutable-shape internal operation.

    ``action`` is one of create, update, archive, restore, delete, link,
    unlink, or reorder.  Keys are stable content keys (or the documented
    composite relationship keys), never SQLite row IDs.  The candidate engine
    resolves them only on its private copy and records physical row images.
    """
    if action not in {"create", "update", "archive", "restore", "delete", "link", "unlink", "reorder"}:
        raise StudioIntentError("Unsupported Content Studio action.")
    if table not in SUPPORTED_TABLES:
        raise StudioIntentError("Unsupported Content Studio table.")
    if not isinstance(key, (str, dict)):
        raise StudioIntentError("Content Studio keys must be stable keys.")
    if action in {"create", "update", "link", "reorder"}:
        values = _copy_mapping(values, "values")
    elif values is not None:
        raise StudioIntentError("This Content Studio action does not take values.")
    if table in BASE_TABLES and action in {"link", "unlink", "reorder"}:
        raise StudioIntentError("Base content cannot be linked or reordered.")
    if table in CONFIGURATION_TABLES and action in {"archive", "restore"}:
        raise StudioIntentError("Only base content has an archive state.")
    result = {"op": action, "table": table, "key": copy.deepcopy(key)}
    if values is not None:
        result["values"] = values
    return result


def case_preset_reference(case_id, before_preset_id, after_preset_id):
    """Prepare the narrowly allowed validated-Case Preset reference change.

    No case text is accepted here.  The candidate service independently checks
    the current before value and records the change in its dedicated audit
    table, rather than in content provenance.
    """
    if type(case_id) is not int or case_id <= 0:
        raise StudioIntentError("Case reference needs a valid Case ID.")
    if type(before_preset_id) is not int or before_preset_id <= 0 or after_preset_id is not None:
        raise StudioIntentError("A Case reference may only detach an existing Preset.")
    return {"op": "case_preset_reference", "case_id": case_id,
            "before_preset_id": before_preset_id, "after_preset_id": after_preset_id}


def review(intents, base_snapshot_hash, *, summary="", db_name=None):
    """Prepare the same immutable review used by package imports and inverses."""
    if not isinstance(intents, list) or not intents:
        raise StudioIntentError("Prepare at least one Content Studio change.")
    return content_changes.review_candidate(
        copy.deepcopy(intents), base_snapshot_hash, summary=summary,
        db_name=db_name, internal=True,
    )


# Stage 6 checkpoint 3 ------------------------------------------------------
#
# This remains a read-only domain layer.  It determines the full lifecycle
# intent before the generalized candidate service writes its private copy;
# neither the planner nor its summaries write the operational database.

_BASE_KEY = {"Fields": "key", "Blocks": "key", "Presets": "short_code", "Snippets": "shortcut"}


def _connection(db_name):
    conn = database.get_db_connection() if db_name is None else sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _target(conn, table, key):
    if table not in BASE_TABLES or not isinstance(key, str):
        raise StudioIntentError("Lifecycle actions require one base-content stable key.")
    row = conn.execute(f"SELECT * FROM {table} WHERE {_BASE_KEY[table]}=?", (key,)).fetchone()
    if row is None:
        raise StudioIntentError("Content Studio lifecycle target is unavailable.")
    return dict(row)


def _snippet_keys(conn, block_ids=(), field_ids=()):
    templates = []
    for block_id in block_ids:
        row = conn.execute("SELECT * FROM Blocks WHERE id=?", (block_id,)).fetchone()
        if row:
            templates.extend(row[column] for column in (
                "macro_template", "micro_template", "conclusion_template", "context_template",
                "title_fragment_template", "conclusion_label_template",
            ))
    for field_id in field_ids:
        row = conn.execute("SELECT conclusion_addendum_template FROM Fields WHERE id=?", (field_id,)).fetchone()
        if row:
            templates.append(row["conclusion_addendum_template"])
    return database._snippet_shortcuts(templates)


def _archive_closure(conn, table, row):
    """Return the upward active dependency closure as (table, physical-row)."""
    ids = {name: set() for name in BASE_TABLES}
    ids[table].add(row["id"])
    changed = True
    while changed:
        changed = False
        for field_id in tuple(ids["Fields"]):
            for found in conn.execute("SELECT block_id FROM Block_Fields WHERE field_id=?", (field_id,)):
                if found["block_id"] not in ids["Blocks"]:
                    ids["Blocks"].add(found["block_id"]); changed = True
        for shortcut_id in tuple(ids["Snippets"]):
            shortcut = conn.execute("SELECT shortcut FROM Snippets WHERE id=?", (shortcut_id,)).fetchone()
            if shortcut is None:
                continue
            needle = shortcut["shortcut"]
            for found in conn.execute("SELECT id FROM Blocks"):
                if needle in _snippet_keys(conn, block_ids=(found["id"],)) and found["id"] not in ids["Blocks"]:
                    ids["Blocks"].add(found["id"]); changed = True
            for found in conn.execute("SELECT id FROM Fields"):
                if needle in _snippet_keys(conn, field_ids=(found["id"],)) and found["id"] not in ids["Fields"]:
                    ids["Fields"].add(found["id"]); changed = True
        for block_id in tuple(ids["Blocks"]):
            for found in conn.execute("SELECT preset_id FROM Preset_Blocks WHERE block_id=?", (block_id,)):
                if found["preset_id"] not in ids["Presets"]:
                    ids["Presets"].add(found["preset_id"]); changed = True
    result = []
    for name, key_column in _BASE_KEY.items():
        for identifier in sorted(ids[name]):
            found = conn.execute(f"SELECT * FROM {name} WHERE id=? AND is_archived=0", (identifier,)).fetchone()
            if found:
                result.append((name, dict(found)))
    return result


def _direct_dependents(conn, table, row):
    """Active content that directly depends on one lifecycle target.

    These are deliberately one-hop relationships, not the archive closure.
    The distinction lets a future guided UI explain both the immediate reason
    for a lifecycle consequence and every transitive item it will affect.
    """
    result = []
    if table == "Fields":
        result.extend(("Blocks", dict(found)) for found in conn.execute(
            """SELECT b.* FROM Block_Fields bf JOIN Blocks b ON b.id=bf.block_id
               WHERE bf.field_id=? AND b.is_archived=0""", (row["id"],)
        ))
    elif table == "Blocks":
        result.extend(("Presets", dict(found)) for found in conn.execute(
            """SELECT p.* FROM Preset_Blocks pb JOIN Presets p ON p.id=pb.preset_id
               WHERE pb.block_id=? AND p.is_archived=0""", (row["id"],)
        ))
    elif table == "Snippets":
        shortcut = row["shortcut"]
        for found in conn.execute("SELECT * FROM Blocks WHERE is_archived=0"):
            if shortcut in _snippet_keys(conn, block_ids=(found["id"],)):
                result.append(("Blocks", dict(found)))
        for found in conn.execute("SELECT * FROM Fields WHERE is_archived=0"):
            if shortcut in _snippet_keys(conn, field_ids=(found["id"],)):
                result.append(("Fields", dict(found)))
    order = {name: index for index, name in enumerate(("Fields", "Blocks", "Presets", "Snippets"))}
    return sorted(result, key=lambda item: (order[item[0]], item[1][_BASE_KEY[item[0]]]))


def _validated_detachments(conn, table, row):
    if table != "Presets":
        return []
    return [
        {"case_id": found["id"], "case_number": found["case_number"]}
        for found in conn.execute(
            "SELECT id,case_number FROM Cases WHERE status='validated' AND preset_id=? ORDER BY id", (row["id"],)
        )
    ]


def _restore_dependencies(conn, table, row):
    """Return archived prerequisites needed to make the restored target usable."""
    ids = {name: {row["id"]} if name == table else set() for name in BASE_TABLES}
    if table == "Presets":
        ids["Blocks"].update(found["block_id"] for found in conn.execute(
            "SELECT block_id FROM Preset_Blocks WHERE preset_id=?", (row["id"],)
        ))
    if table in {"Presets", "Blocks"}:
        for block_id in tuple(ids["Blocks"]):
            ids["Fields"].update(found["field_id"] for found in conn.execute(
                "SELECT field_id FROM Block_Fields WHERE block_id=?", (block_id,)
            ))
    ids["Snippets"].update(found["id"] for found in conn.execute("SELECT id,shortcut FROM Snippets")
                              if found["shortcut"] in _snippet_keys(conn, ids["Blocks"], ids["Fields"]))
    result = []
    for name, key_column in _BASE_KEY.items():
        for identifier in sorted(ids[name]):
            found = conn.execute(f"SELECT * FROM {name} WHERE id=? AND is_archived=1", (identifier,)).fetchone()
            if found:
                result.append((name, dict(found)))
    return result


def pending_blockers(table, key, *, conn=None, db_name=None):
    """Persisted pending Cases that depend on one reusable base target.

    Browser-only drafts are intentionally absent: the persisted pending Case
    set is the Stage 6 authority and is also bound by the candidate stale
    guard.  No clinical text is returned.
    """
    own = conn is None
    conn = conn or _connection(db_name)
    try:
        target = _target(conn, table, key)
        blocked = []
        for case in conn.execute("SELECT id,case_number,preset_id,structured_input FROM Cases WHERE status='pending'"):
            structured = json.loads(case["structured_input"] or "{}")
            instances = structured.get("block_instances")
            if instances is None:
                instances = [dict(found) for found in conn.execute(
                    "SELECT block_id,sort_order AS instance_no FROM Preset_Blocks WHERE preset_id=?", (case["preset_id"],)
                )]
            block_ids = {item.get("block_id") for item in instances if isinstance(item, dict)}
            field_ids = {found["field_id"] for block_id in block_ids for found in conn.execute(
                "SELECT field_id FROM Block_Fields WHERE block_id=?", (block_id,)
            )}
            snippets = _snippet_keys(conn, block_ids, field_ids)
            needed = ((table == "Presets" and case["preset_id"] == target["id"])
                      or (table == "Blocks" and target["id"] in block_ids)
                      or (table == "Fields" and target["id"] in field_ids)
                      or (table == "Snippets" and target["shortcut"] in snippets))
            if needed:
                blocked.append({"case_id": case["id"], "case_number": case["case_number"]})
        return blocked
    finally:
        if own:
            conn.close()


def _delete_operations(conn, table, row):
    key = row[_BASE_KEY[table]]
    if table == "Snippets":
        callers = _snippet_keys(conn, block_ids=[r["id"] for r in conn.execute("SELECT id FROM Blocks")],
                                field_ids=[r["id"] for r in conn.execute("SELECT id FROM Fields")])
        if key in callers:
            raise StudioIntentError("Snippet deletion is unavailable while a template or addendum calls it.")
    ops = []
    if table == "Presets":
        for found in conn.execute("SELECT b.key,pb.sort_order FROM Preset_Blocks pb JOIN Blocks b ON b.id=pb.block_id WHERE pb.preset_id=?", (row["id"],)):
            ops.append(operation("unlink", "Preset_Blocks", {"preset_code": key, "block_key": found["key"], "sort_order": found["sort_order"]}))
        for found in conn.execute("SELECT b.key,pbr.sort_order FROM Preset_Block_Rows pbr JOIN Blocks b ON b.id=pbr.block_id WHERE pbr.preset_id=?", (row["id"],)):
            ops.append(operation("unlink", "Preset_Block_Rows", {"preset_code": key, "block_key": found["key"], "sort_order": found["sort_order"]}))
        for found in conn.execute("SELECT sort_order FROM Quick_Type_Tokens WHERE preset_id=?", (row["id"],)):
            ops.append(operation("unlink", "Quick_Type_Tokens", {"preset_code": key, "sort_order": found["sort_order"]}))
        for case in conn.execute("SELECT id FROM Cases WHERE status='validated' AND preset_id=?", (row["id"],)):
            ops.append(case_preset_reference(case["id"], row["id"], None))
    elif table == "Blocks":
        links = [dict(found) for found in conn.execute("SELECT p.short_code,pb.sort_order FROM Preset_Blocks pb JOIN Presets p ON p.id=pb.preset_id WHERE pb.block_id=?", (row["id"],))]
        for link in links:
            for token in conn.execute("SELECT sort_order FROM Quick_Type_Tokens WHERE preset_id=? AND block_sort_order=?", (conn.execute("SELECT id FROM Presets WHERE short_code=?", (link["short_code"],)).fetchone()[0], link["sort_order"])):
                ops.append(operation("unlink", "Quick_Type_Tokens", {"preset_code": link["short_code"], "sort_order": token["sort_order"]}))
            ops.append(operation("unlink", "Preset_Blocks", {"preset_code": link["short_code"], "block_key": key, "sort_order": link["sort_order"]}))
        for found in conn.execute("SELECT p.short_code,pbr.sort_order FROM Preset_Block_Rows pbr JOIN Presets p ON p.id=pbr.preset_id WHERE pbr.block_id=?", (row["id"],)):
            ops.append(operation("unlink", "Preset_Block_Rows", {"preset_code": found["short_code"], "block_key": key, "sort_order": found["sort_order"]}))
        for found in conn.execute("SELECT f.key FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id WHERE bf.block_id=?", (row["id"],)):
            ops.append(operation("unlink", "Block_Fields", {"block_key": key, "field_key": found["key"]}))
        for found in conn.execute("""SELECT r.*,b.key AS block_key FROM Field_Consistency_Rules r
                                    JOIN Blocks b ON b.id=r.block_id WHERE r.block_id=?""", (row["id"],)):
            ops.append(operation("unlink", "Field_Consistency_Rules", {name: json.loads(found[name]) if name.endswith("_values") else found[name]
                                                                            for name in _GENERAL_RULE_KEY}))
        for found in conn.execute("SELECT block_key_set FROM Conclusion_Group_Labels"):
            if key in found["block_key_set"].split(","):
                ops.append(operation("unlink", "Conclusion_Group_Labels", {"block_key_set": found["block_key_set"]}))
    elif table == "Fields":
        for found in conn.execute("SELECT b.key FROM Block_Fields bf JOIN Blocks b ON b.id=bf.block_id WHERE bf.field_id=?", (row["id"],)):
            ops.append(operation("unlink", "Block_Fields", {"block_key": found["key"], "field_key": key}))
        for found in conn.execute("SELECT p.short_code,qt.sort_order FROM Quick_Type_Tokens qt JOIN Presets p ON p.id=qt.preset_id WHERE qt.field_key=?", (key,)):
            ops.append(operation("unlink", "Quick_Type_Tokens", {"preset_code": found["short_code"], "sort_order": found["sort_order"]}))
        for found in conn.execute("""SELECT r.*,b.key AS block_key FROM Field_Consistency_Rules r
                                    JOIN Blocks b ON b.id=r.block_id
                                    WHERE r.field_a_key=? OR r.field_b_key=?""", (key, key)):
            ops.append(operation("unlink", "Field_Consistency_Rules", {name: json.loads(found[name]) if name.endswith("_values") else found[name]
                                                                            for name in _GENERAL_RULE_KEY}))
        for found in conn.execute("SELECT p.short_code,b.key,pb.sort_order,pb.field_overrides FROM Preset_Blocks pb JOIN Presets p ON p.id=pb.preset_id JOIN Blocks b ON b.id=pb.block_id"):
            overrides = json.loads(found["field_overrides"] or "{}")
            if key in overrides:
                overrides.pop(key)
                ops.append(operation("update", "Preset_Blocks", {"preset_code": found["short_code"], "block_key": found["key"], "sort_order": found["sort_order"]}, {"field_overrides": overrides}))
        for found in conn.execute("SELECT p.short_code,b.key,pbr.sort_order,pbr.field_overrides FROM Preset_Block_Rows pbr JOIN Presets p ON p.id=pbr.preset_id JOIN Blocks b ON b.id=pbr.block_id"):
            overrides = json.loads(found["field_overrides"] or "{}")
            if key in overrides:
                overrides.pop(key)
                ops.append(operation("update", "Preset_Block_Rows", {"preset_code": found["short_code"], "block_key": found["key"], "sort_order": found["sort_order"]}, {"field_overrides": overrides}))
    ops.append(operation("delete", table, key))
    return ops


_GENERAL_RULE_KEY = ("block_key", "field_a_key", "field_a_values", "field_b_key", "field_b_values", "message")


def lifecycle_plan(action, table, key, *, db_name=None, conn=None):
    """Describe and expand one archive/restore/delete lifecycle action.

    ``operations`` is safe to send directly to :func:`review`; it contains
    only stable keys plus explicit mechanical cleanup/reference detachments.
    """
    if action not in {"archive", "restore", "delete"}:
        raise StudioIntentError("Lifecycle action must be archive, restore, or delete.")
    own = conn is None
    conn = conn or _connection(db_name)
    try:
        row = _target(conn, table, key)
        blockers = pending_blockers(table, key, conn=conn)
        refusal_reasons = []
        if action == "archive" and row["is_archived"]:
            refusal_reasons.append("Content is already archived.")
        elif action == "restore" and not row["is_archived"]:
            refusal_reasons.append("Content is already active.")
        if action == "delete" and blockers:
            refusal_reasons.append(
                "Permanent deletion is unavailable while persisted pending Cases depend on it; prepare archive instead."
            )
        closure = _archive_closure(conn, table, row) if action == "archive" else []
        restore = _restore_dependencies(conn, table, row) if action == "restore" else []
        delete_ops = []
        if action == "delete" and not refusal_reasons:
            try:
                delete_ops = _delete_operations(conn, table, row)
            except StudioIntentError as error:
                refusal_reasons.append(str(error))
        ops = ([operation(action, name, found[_BASE_KEY[name]]) for name, found in closure]
               if action == "archive" else
               [operation(action, name, found[_BASE_KEY[name]]) for name, found in restore]
               if action == "restore" else delete_ops)
        cleanup = [
            {"action": item["op"], "table": item["table"], "key": item.get("key")}
            for item in delete_ops
            if item["op"] not in {"delete", "case_preset_reference"}
        ]
        return {
            "action": action, "target": {"table": table, "key": key},
            "direct_dependencies": [{"table": name, "key": found[_BASE_KEY[name]]}
                                    for name, found in _direct_dependents(conn, table, row)],
            "archive_closure": [{"table": name, "key": found[_BASE_KEY[name]]} for name, found in closure],
            "restore_prerequisites": [{"table": name, "key": found[_BASE_KEY[name]]} for name, found in restore],
            "pending_blockers": blockers,
            "validated_detachments": (
                _validated_detachments(conn, table, row)
                if any(item["op"] == "case_preset_reference" for item in delete_ops) else []
            ),
            "mechanical_deletion_cleanup": cleanup,
            "refusal_reasons": refusal_reasons,
            "operations": ops,
        }
    finally:
        if own:
            conn.close()


def expand_lifecycle_operations(intents, conn):
    """Replace bare lifecycle intents with their checked full plan.

    This makes the candidate service itself enforce the lifecycle rules even
    before Checkpoint 4 exposes forms that call ``lifecycle_plan`` directly.
    """
    expanded = []
    seen = set()
    for intent in intents:
        if intent.get("op") in {"archive", "restore", "delete"} and intent.get("table") in BASE_TABLES:
            plan = lifecycle_plan(intent["op"], intent["table"], intent["key"], conn=conn)
            if plan["refusal_reasons"]:
                raise StudioIntentError(plan["refusal_reasons"][0])
            for operation_item in plan["operations"]:
                marker = json.dumps(operation_item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if marker not in seen:
                    expanded.append(operation_item); seen.add(marker)
        else:
            marker = json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if marker not in seen:
                expanded.append(intent); seen.add(marker)
    return expanded
