"""Deterministic, case-safe exports of PathoPilot's content tables.

Snapshots deliberately contain no Cases or audit history.  Restore is a
content-only operation: it validates on a temporary copy first and refuses
any target that would alter content a saved case still needs to reopen.
"""

import copy
import hashlib
from contextlib import contextmanager
import json
import sqlite3
import tempfile

import database


FORMAT_V1 = "pathopilot-content-snapshot-v1"
FORMAT_V2 = "pathopilot-content-snapshot-v2"


BASE_TABLES = {
    "Fields": ("key", ("key", "label", "type", "options", "default_value", "conclusion_addendum_template", "is_archived")),
    "Blocks": ("key", ("key", "name", "is_table", "site_label", "conclusion_group", "macro_template", "micro_template", "conclusion_template", "context_template", "title_fragment_template", "conclusion_label_template", "is_archived")),
    "Presets": ("short_code", ("short_code", "name", "category", "default_adicap", "default_title", "is_archived")),
    "Snippets": ("shortcut", ("shortcut", "expansion", "category", "is_archived")),
}

_V1_BASE_TABLES = {
    "Fields": ("key", ("key", "label", "type", "options", "default_value", "conclusion_addendum_template")),
    "Blocks": ("key", ("key", "name", "is_table", "site_label", "conclusion_group", "macro_template", "micro_template", "conclusion_template", "context_template", "title_fragment_template", "conclusion_label_template")),
    "Presets": ("short_code", ("short_code", "name", "category", "default_adicap", "default_title")),
    "Snippets": ("shortcut", ("shortcut", "expansion", "category")),
}

RELATION_TABLES = {
    "Block_Fields": (
        ("block_key", "field_key", "sort_order", "label_override", "default_override", "context_section"),
        ("block_key", "field_key"),
    ),
    "Preset_Blocks": (
        ("preset_code", "block_key", "sort_order", "display_order", "field_overrides"),
        ("preset_code", "block_key", "sort_order"),
    ),
    "Preset_Block_Rows": (
        ("preset_code", "block_key", "sort_order", "field_overrides"),
        ("preset_code", "block_key", "sort_order"),
    ),
    "Quick_Type_Tokens": (
        ("preset_code", "sort_order", "block_sort_order", "field_key", "token_kind", "lookup_table", "digit_width"),
        ("preset_code", "sort_order"),
    ),
    "Field_Consistency_Rules": (
        ("block_key", "field_a_key", "field_a_values", "field_b_key", "field_b_values", "message"),
        ("block_key", "field_a_key", "field_a_values", "field_b_key", "field_b_values"),
    ),
    "Conclusion_Group_Labels": (
        ("block_key_set", "combined_label"),
        ("block_key_set",),
    ),
}

_V1_RELATION_TABLES = {
    **RELATION_TABLES,
    "Preset_Blocks": (
        ("preset_code", "block_key", "sort_order", "field_overrides"),
        ("preset_code", "block_key", "sort_order"),
    ),
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params)]


def _base_rows(conn, table, key, columns):
    return _rows(conn, f"SELECT {', '.join(columns)} FROM {table} ORDER BY {key}")


def _read_snapshot_rows(conn):
    payload = {"format": FORMAT_V2, "tables": {}}
    tables = payload["tables"]
    for table, (key, columns) in BASE_TABLES.items():
        tables[table] = _base_rows(conn, table, key, columns)
    tables["Block_Fields"] = _rows(conn, """
        SELECT b.key AS block_key, f.key AS field_key, bf.sort_order, bf.label_override,
               bf.default_override, bf.context_section
        FROM Block_Fields bf JOIN Blocks b ON b.id = bf.block_id JOIN Fields f ON f.id = bf.field_id
        ORDER BY b.key, f.key""")
    tables["Preset_Blocks"] = _rows(conn, """
        SELECT p.short_code AS preset_code, b.key AS block_key, pb.sort_order,
               pb.display_order, pb.field_overrides
        FROM Preset_Blocks pb JOIN Presets p ON p.id = pb.preset_id JOIN Blocks b ON b.id = pb.block_id
        ORDER BY p.short_code, pb.display_order, pb.sort_order, b.key""")
    tables["Preset_Block_Rows"] = _rows(conn, """
        SELECT p.short_code AS preset_code, b.key AS block_key, pbr.sort_order, pbr.field_overrides
        FROM Preset_Block_Rows pbr JOIN Presets p ON p.id = pbr.preset_id JOIN Blocks b ON b.id = pbr.block_id
        ORDER BY p.short_code, b.key, pbr.sort_order""")
    tables["Quick_Type_Tokens"] = _rows(conn, """
        SELECT p.short_code AS preset_code, q.sort_order, q.block_sort_order, q.field_key,
               q.token_kind, q.lookup_table, q.digit_width
        FROM Quick_Type_Tokens q JOIN Presets p ON p.id = q.preset_id
        ORDER BY p.short_code, q.sort_order""")
    tables["Field_Consistency_Rules"] = _rows(conn, """
        SELECT b.key AS block_key, f.field_a_key, f.field_a_values, f.field_b_key,
               f.field_b_values, f.message
        FROM Field_Consistency_Rules f JOIN Blocks b ON b.id = f.block_id
        ORDER BY b.key, f.field_a_key, f.field_a_values, f.field_b_key,
                 f.field_b_values, f.message""")
    tables["Conclusion_Group_Labels"] = _rows(
        conn, "SELECT block_key_set, combined_label FROM Conclusion_Group_Labels ORDER BY block_key_set"
    )
    return payload


@contextmanager
def consistent_read(conn):
    """Own a read transaction only when the caller does not already own one."""
    owned = not conn.in_transaction
    if owned:
        conn.execute("BEGIN")
    try:
        yield conn
    finally:
        if owned:
            conn.rollback()


def snapshot_from_connection(conn):
    """Capture the allowlisted content on one consistent connection boundary."""
    with consistent_read(conn):
        return _read_snapshot_rows(conn)


# Retain the existing restore helper name for callers from earlier stages.
_snapshot_from_connection = snapshot_from_connection


def content_snapshot_hash(snapshot):
    return hashlib.sha256(content_snapshot_json(snapshot).encode("utf-8")).hexdigest()


def export_content_snapshot(db_name=None):
    """Return an exactly ordered, JSON-serialisable content snapshot."""
    conn = database.get_db_connection() if db_name is None else sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        return _snapshot_from_connection(conn)
    finally:
        conn.close()


def content_snapshot_json(snapshot):
    """Canonical bytes for a reviewable export file or snapshot hash."""
    return _json(snapshot) + "\n"


def normalize_content_snapshot(snapshot):
    """Return a detached v2 representation of a v1 or v2 snapshot.

    v1 is accepted only at this compatibility boundary.  The caller's object
    is never changed, so inspecting or hashing a legacy recovery file remains
    deterministic and read-only.
    """
    if not isinstance(snapshot, dict) or set(snapshot) != {"format", "tables"}:
        raise ValueError("Unsupported content snapshot format")
    version = snapshot.get("format")
    if version == FORMAT_V2:
        return copy.deepcopy(snapshot)
    if version != FORMAT_V1:
        raise ValueError("Unsupported content snapshot format")
    tables = snapshot.get("tables")
    expected = set(_V1_BASE_TABLES) | set(_V1_RELATION_TABLES)
    if not isinstance(tables, dict) or set(tables) != expected:
        raise ValueError("Content snapshot has missing or unexpected tables")
    normalised = {"format": FORMAT_V2, "tables": {}}
    for table, (_key, columns) in _V1_BASE_TABLES.items():
        rows = tables[table]
        if not isinstance(rows, list) or any(not isinstance(row, dict) or set(row) != set(columns) for row in rows):
            raise ValueError(f"Invalid {table} rows")
        normalised["tables"][table] = [{**copy.deepcopy(row), "is_archived": 0} for row in rows]
    for table, (columns, _key_columns) in _V1_RELATION_TABLES.items():
        rows = tables[table]
        if not isinstance(rows, list) or any(not isinstance(row, dict) or set(row) != set(columns) for row in rows):
            raise ValueError(f"Invalid {table} rows")
        if table == "Preset_Blocks":
            normalised["tables"][table] = [
                {**copy.deepcopy(row), "display_order": row["sort_order"]} for row in rows
            ]
        else:
            normalised["tables"][table] = copy.deepcopy(rows)
    return normalised


def validate_content_snapshot(snapshot):
    """Reject an unsupported or structurally unsafe content snapshot.

    This public, non-mutating entry point lets consumers validate an export
    before choosing a target database.  Restore retains its own validation so
    its safety behaviour is unchanged.
    """
    _validate_shape(normalize_content_snapshot(snapshot))


def _validate_shape(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != {"format", "tables"} or snapshot.get("format") != FORMAT_V2:
        raise ValueError("Unsupported content snapshot format")
    tables = snapshot.get("tables")
    expected = set(BASE_TABLES) | set(RELATION_TABLES)
    if not isinstance(tables, dict) or set(tables) != expected or any(not isinstance(tables[name], list) for name in expected):
        raise ValueError("Content snapshot has missing or unexpected tables")
    for table, (key, columns) in BASE_TABLES.items():
        seen = set()
        for row in tables[table]:
            if set(row) != set(columns) or not row.get(key) or row[key] in seen:
                raise ValueError(f"Invalid {table} rows")
            if type(row["is_archived"]) is not int or row["is_archived"] not in (0, 1):
                raise ValueError(f"Invalid {table} archive state")
            seen.add(row[key])
    base_keys = {
        table: {row[key] for row in tables[table]}
        for table, (key, _columns) in BASE_TABLES.items()
    }
    for table, (columns, key_columns) in RELATION_TABLES.items():
        seen = set()
        for row in tables[table]:
            if not isinstance(row, dict) or set(row) != set(columns):
                raise ValueError(f"Invalid {table} rows")
            identity = tuple(row[column] for column in key_columns)
            try:
                invalid_identity = any(value is None or value == "" for value in identity) or identity in seen
            except TypeError as error:
                raise ValueError(f"Invalid {table} stable key") from error
            if invalid_identity:
                raise ValueError(f"Invalid or duplicate {table} stable key")
            if table == "Preset_Blocks" and type(row["display_order"]) is not int:
                raise ValueError("Invalid Preset_Blocks display order")
            seen.add(identity)

    for row in tables["Block_Fields"]:
        if row["block_key"] not in base_keys["Blocks"] or row["field_key"] not in base_keys["Fields"]:
            raise ValueError("Block_Fields references unavailable content")
    for table in ("Preset_Blocks", "Preset_Block_Rows"):
        for row in tables[table]:
            if row["preset_code"] not in base_keys["Presets"] or row["block_key"] not in base_keys["Blocks"]:
                raise ValueError(f"{table} references unavailable content")
    for row in tables["Quick_Type_Tokens"]:
        if row["preset_code"] not in base_keys["Presets"] or row["field_key"] not in base_keys["Fields"]:
            raise ValueError("Quick_Type_Tokens references unavailable content")
    for row in tables["Field_Consistency_Rules"]:
        if (row["block_key"] not in base_keys["Blocks"] or
                row["field_a_key"] not in base_keys["Fields"] or
                row["field_b_key"] not in base_keys["Fields"]):
            raise ValueError("Field_Consistency_Rules references unavailable content")
    for row in tables["Conclusion_Group_Labels"]:
        label_keys = row["block_key_set"].split(",")
        if (not row["combined_label"] or label_keys != sorted(label_keys) or
                any(key not in base_keys["Blocks"] for key in label_keys)):
            raise ValueError("Conclusion_Group_Labels references unavailable content")


def _case_guard_rows(conn):
    return _rows(
        conn,
        """SELECT id, case_number, status, preset_id, structured_input
           FROM Cases ORDER BY id""",
    )


def _assert_saved_cases_unchanged(current_conn, candidate_conn):
    """Compare exact relevant-content fingerprints for every saved Case."""
    current_cases = _case_guard_rows(current_conn)
    if current_cases != _case_guard_rows(candidate_conn):
        raise ValueError("Candidate validation unexpectedly altered saved Cases")
    for case in current_cases:
        # A validated Case is a frozen artifact.  It may intentionally have a
        # detached Preset after reviewed permanent deletion, so recovery must
        # preserve it exactly without attempting live reconstruction.
        if case["status"] != "pending":
            continue
        structured_input = json.loads(case["structured_input"] or "{}")
        try:
            before = database.compute_case_content_fingerprint(
                case["preset_id"], structured_input, current_conn
            )
            after = database.compute_case_content_fingerprint(
                case["preset_id"], structured_input, candidate_conn
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Saved Case '{case['case_number']}' cannot be safely reconstructed"
            ) from error
        if before != after:
            raise ValueError(
                f"Restore would alter content needed by saved Case '{case['case_number']}'"
            )


def _replace_relationships(conn, tables, ids):
    # Synchronise by the stable natural key for each relationship.  In
    # particular, rows with surrogate IDs (Preset_Block_Rows, Quick Type,
    # consistency rules) are updated in place whenever their identity holds.
    desired_bf = {(ids["Blocks"][r["block_key"]], ids["Fields"][r["field_key"]]): r for r in tables["Block_Fields"]}
    existing_bf = {(r["block_id"], r["field_id"]): r for r in _rows(conn, "SELECT * FROM Block_Fields")}
    for key in set(existing_bf) - set(desired_bf): conn.execute("DELETE FROM Block_Fields WHERE block_id = ? AND field_id = ?", key)
    for (block_id, field_id), row in desired_bf.items():
        if (block_id, field_id) in existing_bf:
            conn.execute("UPDATE Block_Fields SET sort_order=?, label_override=?, default_override=?, context_section=? WHERE block_id=? AND field_id=?", (row["sort_order"], row["label_override"], row["default_override"], row["context_section"], block_id, field_id))
        else: conn.execute("INSERT INTO Block_Fields VALUES (?, ?, ?, ?, ?, ?)", (block_id, field_id, row["sort_order"], row["label_override"], row["default_override"], row["context_section"]))

    desired_pb = {(ids["Presets"][r["preset_code"]], ids["Blocks"][r["block_key"]], r["sort_order"]): r for r in tables["Preset_Blocks"]}
    existing_pb = {(r["preset_id"], r["block_id"], r["sort_order"]): r for r in _rows(conn, "SELECT * FROM Preset_Blocks")}
    for key in set(existing_pb) - set(desired_pb): conn.execute("DELETE FROM Preset_Blocks WHERE preset_id=? AND block_id=? AND sort_order=?", key)
    for key, row in desired_pb.items():
        if key in existing_pb:
            conn.execute(
                "UPDATE Preset_Blocks SET display_order=?, field_overrides=? WHERE preset_id=? AND block_id=? AND sort_order=?",
                (row["display_order"], row["field_overrides"], *key),
            )
        else:
            conn.execute(
                "INSERT INTO Preset_Blocks (preset_id, block_id, sort_order, display_order, field_overrides) VALUES (?, ?, ?, ?, ?)",
                (*key, row["display_order"], row["field_overrides"]),
            )

    def sync_surrogate(table, columns, key_columns, values):
        desired = {tuple(row[c] for c in key_columns): row for row in values}
        existing = {tuple(row[c] for c in key_columns): row for row in _rows(conn, f"SELECT * FROM {table}")}
        for key, row in existing.items():
            if key not in desired: conn.execute(f"DELETE FROM {table} WHERE id = ?", (row["id"],))
        for key, row in desired.items():
            args = [row[c] for c in columns]
            if key in existing:
                conn.execute(f"UPDATE {table} SET {', '.join(f'{c}=?' for c in columns)} WHERE id=?", (*args, existing[key]["id"]))
            else: conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", args)

    sync_surrogate("Preset_Block_Rows", ("preset_id", "block_id", "sort_order", "field_overrides"), ("preset_id", "block_id", "sort_order"), [
        {"preset_id": ids["Presets"][r["preset_code"]], "block_id": ids["Blocks"][r["block_key"]], "sort_order": r["sort_order"], "field_overrides": r["field_overrides"]} for r in tables["Preset_Block_Rows"]])
    sync_surrogate("Quick_Type_Tokens", ("preset_id", "sort_order", "block_sort_order", "field_key", "token_kind", "lookup_table", "digit_width"), ("preset_id", "sort_order"), [
        {"preset_id": ids["Presets"][r["preset_code"]], "sort_order": r["sort_order"], "block_sort_order": r["block_sort_order"], "field_key": r["field_key"], "token_kind": r["token_kind"], "lookup_table": r["lookup_table"], "digit_width": r["digit_width"]} for r in tables["Quick_Type_Tokens"]])
    sync_surrogate("Field_Consistency_Rules", ("block_id", "field_a_key", "field_a_values", "field_b_key", "field_b_values", "message"), ("block_id", "field_a_key", "field_a_values", "field_b_key", "field_b_values"), [
        {"block_id": ids["Blocks"][r["block_key"]], "field_a_key": r["field_a_key"], "field_a_values": r["field_a_values"], "field_b_key": r["field_b_key"], "field_b_values": r["field_b_values"], "message": r["message"]} for r in tables["Field_Consistency_Rules"]])
    desired_labels = {r["block_key_set"]: r for r in tables["Conclusion_Group_Labels"]}
    existing_labels = {r["block_key_set"]: r for r in _rows(conn, "SELECT * FROM Conclusion_Group_Labels")}
    for key in set(existing_labels) - set(desired_labels): conn.execute("DELETE FROM Conclusion_Group_Labels WHERE id=?", (existing_labels[key]["id"],))
    for key, row in desired_labels.items():
        if key in existing_labels: conn.execute("UPDATE Conclusion_Group_Labels SET combined_label=? WHERE id=?", (row["combined_label"], existing_labels[key]["id"]))
        else: conn.execute("INSERT INTO Conclusion_Group_Labels (block_key_set, combined_label) VALUES (?, ?)", (key, row["combined_label"]))


def _apply(conn, snapshot):
    tables = snapshot["tables"]
    ids = {}
    for table, (key, columns) in BASE_TABLES.items():
        existing = {row[key]: dict(row) for row in _rows(conn, f"SELECT id, {', '.join(columns)} FROM {table}")}
        wanted = {row[key]: row for row in tables[table]}
        for stable_key, row in wanted.items():
            values = [row[column] for column in columns if column != key]
            if stable_key in existing:
                conn.execute(f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in columns if c != key)} WHERE {key} = ?", (*values, stable_key))
            else:
                conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", [row[c] for c in columns])
        ids[table] = {row[key]: row["id"] for row in _rows(conn, f"SELECT id, {key} FROM {table}")}
    _replace_relationships(conn, tables, ids)
    for table, (key, _columns) in BASE_TABLES.items():
        wanted_keys = {row[key] for row in tables[table]}
        for row in _rows(conn, f"SELECT {key} FROM {table}"):
            if row[key] not in wanted_keys:
                conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (row[key],))


def restore_content_snapshot(snapshot, db_name=None, summary="Restored content snapshot"):
    """Validate and apply a snapshot; return ``(success, message)``.

    The candidate first runs against a temporary SQLite backup.  The real DB
    receives one content-only transaction and an audit revision only if that
    validation succeeds.
    """
    target = None
    try:
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        snapshot = normalize_content_snapshot(snapshot)
        _validate_shape(snapshot)
        target = database.get_db_connection() if db_name is None else sqlite3.connect(db_name)
        target.row_factory = sqlite3.Row
        target.execute("PRAGMA foreign_keys = ON")
        initial_guard = {
            "content": _snapshot_from_connection(target),
            "cases": _case_guard_rows(target),
        }
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            candidate = sqlite3.connect(tmp.name)
            target.backup(candidate)
            candidate.row_factory = sqlite3.Row
            candidate.execute("PRAGMA foreign_keys = ON")
            try:
                _apply(candidate, copy.deepcopy(snapshot))
                if _snapshot_from_connection(candidate) != snapshot:
                    raise ValueError("Candidate content does not exactly match the requested snapshot")
                _assert_saved_cases_unchanged(target, candidate)
                candidate.commit()
            finally:
                candidate.close()
        target.execute("BEGIN IMMEDIATE")
        locked_guard = {
            "content": _snapshot_from_connection(target),
            "cases": _case_guard_rows(target),
        }
        if locked_guard != initial_guard:
            raise ValueError("Database changed during snapshot validation; retry the restore")
        _apply(target, snapshot)
        if _snapshot_from_connection(target) != snapshot:
            raise ValueError("Applied content does not exactly match the requested snapshot")
        target.execute("INSERT INTO Content_Revisions (origin, summary) VALUES (?, ?)", ("snapshot_restore", summary))
        target.commit()
        return True, None
    except (sqlite3.Error, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        if target is not None:
            target.rollback()
        return False, str(error)
    finally:
        if target is not None:
            target.close()
