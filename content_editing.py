"""Stage 3's deliberately narrow, transaction-safe content editor service."""

import hashlib
import json
import math
import re
import sqlite3

from jinja2 import meta, nodes

import database
import editor_preview
import grouping
import rendering


EDITABLE = {
    "Blocks": ("key", {"macro_template", "micro_template", "conclusion_template", "context_template", "title_fragment_template", "conclusion_label_template"}),
    "Fields": ("key", {"label", "default_value", "conclusion_addendum_template"}),
    "Snippets": ("shortcut", {"expansion", "category"}),
    "Presets": ("short_code", {"name", "category", "default_title"}),
}
BLOCK_TEMPLATE_COLUMNS = (
    "macro_template", "micro_template", "conclusion_template", "context_template",
    "title_fragment_template", "conclusion_label_template",
)


class ContentEditError(ValueError):
    """A safe rejection suitable for displaying in the Editor."""


class StaleContentError(ContentEditError):
    """The submitted row hash no longer matches persisted content."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def row_hash(row):
    """Hash complete persisted state (not merely the editable columns)."""
    values = {key: value for key, value in dict(row).items() if key != "id"}
    return hashlib.sha256(_json(values).encode("utf-8")).hexdigest()


def get_editable_entity(table_name, entity_key):
    _check_table(table_name)
    conn = database.get_db_connection()
    try:
        row = _fetch(conn, table_name, entity_key)
        if not row:
            return None
        result = dict(row)
        result["row_hash"] = row_hash(row)
        return result
    finally:
        conn.close()


def _check_table(table_name):
    if table_name not in EDITABLE:
        raise ContentEditError("That table is not editable in Stage 3.")


def _fetch(conn, table_name, entity_key):
    key_column = EDITABLE[table_name][0]
    return conn.execute(f"SELECT * FROM {table_name} WHERE {key_column} = ?", (entity_key,)).fetchone()


def initial_snapshot_status():
    conn = database.get_db_connection()
    try:
        row = conn.execute("SELECT initial_snapshot_hash, initial_snapshot_at FROM Editor_Safety_State WHERE id = 1").fetchone()
        return dict(row) if row else {"initial_snapshot_hash": None, "initial_snapshot_at": None}
    finally:
        conn.close()


def record_initial_snapshot(snapshot_hash):
    """Record explicit acknowledgement of the one required manual export."""
    if not snapshot_hash or not re.fullmatch(r"[0-9a-f]{64}", snapshot_hash):
        raise ContentEditError("The initial snapshot hash is invalid.")
    conn = database.get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO Editor_Safety_State (id, initial_snapshot_hash, initial_snapshot_at)
               VALUES (1, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO NOTHING""", (snapshot_hash,)
        )
        conn.commit()
    finally:
        conn.close()


def _require_initial_snapshot(conn):
    row = conn.execute("SELECT initial_snapshot_hash FROM Editor_Safety_State WHERE id = 1").fetchone()
    if not row or not row["initial_snapshot_hash"]:
        raise ContentEditError("Create and save the initial content snapshot before editing live content.")


def save_edit(table_name, entity_key, changes, expected_hash=None, summary=None):
    """Validate a materialised candidate state then atomically save one edit."""
    _check_table(table_name)
    _check_changes(table_name, changes, creating=False)
    conn = database.get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_initial_snapshot(conn)
        before = _fetch(conn, table_name, entity_key)
        if not before:
            raise ContentEditError("This item no longer exists. Reload it before saving.")
        _ensure_stage3_row(table_name, before)
        if not expected_hash or row_hash(before) != expected_hash:
            raise StaleContentError("This item changed in another tab. The current values were reloaded; review them before saving again.")
        normalised = _normalise_changes(table_name, changes)
        changed_columns = [
            column for column, value in normalised.items()
            if before[column] != value
        ]
        if not changed_columns:
            raise ContentEditError("No persisted values changed.")
        candidate = {**dict(before), **normalised}
        _validate_row(table_name, candidate, original=before)
        conn.execute("SAVEPOINT content_candidate")
        _update(conn, table_name, entity_key, normalised)
        _validate_candidate(conn, table_name, entity_key)
        conn.execute("ROLLBACK TO content_candidate")
        conn.execute("RELEASE content_candidate")
        _update(conn, table_name, entity_key, normalised)
        after = _fetch(conn, table_name, entity_key)
        revision_summary = summary or _revision_summary(table_name, entity_key, changed_columns)
        revision_id = _record_revision(
            conn, "manual_edit", revision_summary,
            table_name, entity_key, "update", before, after,
        )
        conn.commit()
        return {"revision_id": revision_id, "row": {**dict(after), "row_hash": row_hash(after)}}
    except StaleContentError:
        conn.rollback()
        raise
    except Exception as error:
        conn.rollback()
        raise ContentEditError(str(error)) from error
    finally:
        conn.close()


def preview_edit(table_name, entity_key, changes, expected_hash=None):
    """Validate and render a rollback-only candidate for the Editor UI."""
    _check_table(table_name)
    _check_changes(table_name, changes, creating=False)
    conn = database.get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_initial_snapshot(conn)
        before = _fetch(conn, table_name, entity_key)
        if not before:
            raise ContentEditError("This item no longer exists. Reload it before previewing.")
        _ensure_stage3_row(table_name, before)
        if not expected_hash or row_hash(before) != expected_hash:
            raise StaleContentError("This item changed in another tab. The current values were reloaded; review them before previewing again.")
        normalised = _normalise_changes(table_name, changes)
        candidate = {**dict(before), **normalised}
        _validate_row(table_name, candidate, original=before)
        before_previews = _preset_preview_map(conn)
        conn.execute("SAVEPOINT content_preview")
        _update(conn, table_name, entity_key, normalised)
        _validate_candidate(conn, table_name, entity_key)
        after_previews = _preset_preview_map(conn)
        changed = []
        for preset_id in sorted(set(before_previews) | set(after_previews)):
            before_preview = before_previews.get(preset_id)
            after_preview = after_previews.get(preset_id)
            if before_preview != after_preview:
                changed.append({
                    "preset_id": preset_id,
                    "label": (after_preview or before_preview)["label"],
                    "before": before_preview,
                    "after": after_preview,
                })
        conn.execute("ROLLBACK TO content_preview")
        conn.execute("RELEASE content_preview")
        return {"previews": changed}
    except Exception as error:
        conn.rollback()
        if isinstance(error, ContentEditError):
            raise
        raise ContentEditError(str(error)) from error
    finally:
        conn.rollback()
        conn.close()


def _preset_preview_map(conn):
    previews = {}
    for preset in conn.execute("SELECT id, name, short_code FROM Presets ORDER BY id"):
        label = f"{preset['name']} ({preset['short_code']})"
        try:
            rendered = editor_preview.render_preset_defaults(
                preset["id"], conn=conn, strict=True,
            )
            preview = {
                "label": label,
                "title": rendered["title"],
                "clinical_info": rendered["clinical_info"],
                "micro_plain": rendered["micro_plain"],
                "conclusion_plain": rendered["conclusion_plain"],
            }
        except Exception as error:
            preview = {"label": label, "error": str(error)}
        previews[preset["id"]] = preview
    return previews


def create_snippet(shortcut, expansion, category=None, summary=None):
    """The only Stage 3 creation operation; stable shortcut is mandatory."""
    _check_changes("Snippets", {"expansion": expansion, "category": category}, creating=True)
    if not shortcut or not re.fullmatch(r"[A-Za-z0-9_-]+", shortcut):
        raise ContentEditError("Snippet shortcut must contain only letters, digits, '_' or '-'.")
    candidate = {
        "shortcut": shortcut,
        "expansion": expansion.strip() if isinstance(expansion, str) else expansion,
        "category": _blank_to_none(category),
    }
    _validate_row("Snippets", candidate)
    conn = database.get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_initial_snapshot(conn)
        if _fetch(conn, "Snippets", shortcut):
            raise ContentEditError("That snippet shortcut already exists.")
        conn.execute("SAVEPOINT content_candidate")
        conn.execute("INSERT INTO Snippets (shortcut, expansion, category) VALUES (?, ?, ?)",
                     (candidate["shortcut"], candidate["expansion"], candidate["category"]))
        _validate_candidate(conn, "Snippets", shortcut)
        conn.execute("ROLLBACK TO content_candidate")
        conn.execute("RELEASE content_candidate")
        conn.execute("INSERT INTO Snippets (shortcut, expansion, category) VALUES (?, ?, ?)",
                     (candidate["shortcut"], candidate["expansion"], candidate["category"]))
        after = _fetch(conn, "Snippets", shortcut)
        revision_id = _record_revision(
            conn, "manual_edit", summary or f"Snippets.{shortcut}: created",
            "Snippets", shortcut, "create", None, after,
        )
        conn.commit()
        return {"revision_id": revision_id, "row": {**dict(after), "row_hash": row_hash(after)}}
    except Exception as error:
        conn.rollback()
        raise ContentEditError(str(error)) from error
    finally:
        conn.close()


def _check_changes(table_name, changes, creating):
    if not isinstance(changes, dict) or not changes:
        raise ContentEditError("No editable values were supplied.")
    forbidden = set(changes) - EDITABLE[table_name][1]
    if forbidden:
        raise ContentEditError(f"Stage 3 cannot edit: {', '.join(sorted(forbidden))}.")
    if creating and table_name != "Snippets":
        raise ContentEditError("Stage 3 only creates Snippets.")


def _blank_to_none(value):
    return None if value is None or (isinstance(value, str) and not value.strip()) else value


def _normalise_changes(table_name, changes):
    result = dict(changes)
    for column in set(changes) & {"context_template", "title_fragment_template", "conclusion_label_template", "conclusion_addendum_template", "category", "default_title"}:
        result[column] = _blank_to_none(result[column])
    return result


def _ensure_stage3_row(table_name, row):
    if table_name == "Blocks" and bool(row["is_table"]):
        raise ContentEditError("Table Blocks are read-only in Stage 3.")


def _revision_summary(table_name, entity_key, columns):
    return f"{table_name}.{entity_key}: {', '.join(sorted(columns))}"


def _update(conn, table_name, entity_key, changes):
    values = _normalise_changes(table_name, changes)
    fields = list(values)
    key_column = EDITABLE[table_name][0]
    conn.execute(
        f"UPDATE {table_name} SET {', '.join(f'{column} = ?' for column in fields)} WHERE {key_column} = ?",
        (*[values[column] for column in fields], entity_key),
    )


def _validate_row(table_name, row, original=None):
    if table_name == "Blocks":
        if not str(row.get("micro_template") or "").strip() or not str(row.get("conclusion_template") or "").strip():
            raise ContentEditError("Microscopy and conclusion templates cannot be blank.")
        if not row.get("is_table") and not str(row.get("macro_template") or "").strip():
            raise ContentEditError("A non-table Block cannot clear its macro template.")
    elif table_name == "Fields":
        if not str(row.get("label") or "").strip():
            raise ContentEditError("Field label cannot be blank.")
        _validate_default(row, original)
    elif table_name == "Snippets" and not str(row.get("expansion") or "").strip():
        raise ContentEditError("Snippet expansion cannot be blank.")
    elif table_name == "Presets" and not str(row.get("name") or "").strip():
        raise ContentEditError("Preset name cannot be blank.")


def _validate_default(field, original=None):
    raw = field.get("default_value")
    field_type = field["type"]
    if raw is None or raw == "":
        # Some established Fields intentionally have no global default and
        # receive one through Block/Preset overrides (thyroid nodule_site is
        # the concrete example). Preserve those rows, but do not let direct
        # editing clear a previously usable discrete/numeric default.
        if (original is not None and original["default_value"] not in {None, ""}
                and field_type in {"number", "select", "checkbox"}):
            raise ContentEditError(f"{field_type.capitalize()} default cannot be cleared.")
        return
    value = rendering.coerce_field_value(field_type, raw)
    if field_type in {"number", "decimal"} and isinstance(value, str):
        raise ContentEditError(f"Default value must be a valid {field_type}.")
    if field_type in {"number", "decimal"} and (
        not math.isfinite(value) or value < 0
    ):
        raise ContentEditError(f"Default {field_type} must be finite and nonnegative.")
    if field_type == "checkbox" and str(raw) not in {"0", "1", "true", "false", "True", "False"}:
        raise ContentEditError("Checkbox default must be true/false (or 1/0).")
    if field_type == "select":
        options = json.loads(field["options"] or "[]")
        if raw not in options:
            raise ContentEditError("Select default must be one of the existing options.")


def _record_revision(conn, origin, summary, table_name, entity_key, operation, before, after):
    cursor = conn.execute("INSERT INTO Content_Revisions (origin, summary) VALUES (?, ?)", (origin, summary))
    revision_id = cursor.lastrowid
    conn.execute(
        """INSERT INTO Content_Changes
           (revision_id, table_name, entity_key, operation, before_json, after_json, before_hash, after_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (revision_id, table_name, entity_key, operation,
         _json(dict(before)) if before else None, _json(dict(after)) if after else None,
         row_hash(before) if before else None, row_hash(after) if after else None),
    )
    return revision_id


def _template_variables(source):
    try:
        environment = rendering.template_environment(strict=True)
        parsed = environment.parse(source)
    except Exception as error:
        raise ContentEditError(f"Invalid Jinja template: {error}") from error
    return meta.find_undeclared_variables(parsed), parsed


def _snippet_calls(parsed):
    calls = []
    for call in parsed.find_all(nodes.Call):
        if not isinstance(call.node, nodes.Name) or call.node.name != "snippet":
            raise ContentEditError("Templates may only call snippet('literal_shortcut').")
        if (len(call.args) != 1 or call.kwargs or call.dyn_args or call.dyn_kwargs
                or not isinstance(call.args[0], nodes.Const) or not isinstance(call.args[0].value, str)):
            raise ContentEditError("snippet() must use exactly one literal shortcut.")
        calls.append(call.args[0].value)
    snippet_names = [name for name in parsed.find_all(nodes.Name) if name.name == "snippet"]
    if len(snippet_names) != len(calls):
        raise ContentEditError("snippet may only be used as snippet('literal_shortcut').")
    return calls


def validate_content_templates(conn):
    """Validate rows, sandbox syntax and references on the supplied connection."""
    for row in conn.execute("SELECT * FROM Fields"):
        _validate_row("Fields", dict(row))
    for row in conn.execute("SELECT * FROM Snippets"):
        _validate_row("Snippets", dict(row))
    for row in conn.execute("SELECT * FROM Presets"):
        _validate_row("Presets", dict(row))
    snippet_keys = {row["shortcut"] for row in conn.execute("SELECT shortcut FROM Snippets")}
    for block in conn.execute("SELECT * FROM Blocks"):
        block = dict(block)
        fields = conn.execute(
            """SELECT f.key, f.type FROM Block_Fields bf JOIN Fields f ON f.id = bf.field_id
               WHERE bf.block_id = ?""", (block["id"],)
        ).fetchall()
        allowed = {field["key"] for field in fields} | {"snippet"}
        if any(field["key"] == "fragments" for field in fields):
            allowed.add("fragment_text")
        allowed.update(f"{field['key']}_display" for field in fields if field["type"] == "decimal")
        if block.get("site_label") is not None:
            allowed.add("site_label")
        for column in BLOCK_TEMPLATE_COLUMNS:
            source = block.get(column)
            if source is None:
                continue
            unknown, parsed = _template_variables(source)
            disallowed = unknown - allowed
            if disallowed:
                raise ContentEditError(f"Block '{block['key']}' has unknown variable(s): {', '.join(sorted(disallowed))}.")
            unresolved = set(_snippet_calls(parsed)) - snippet_keys
            if unresolved:
                raise ContentEditError(f"Block '{block['key']}' references unresolved snippet(s): {', '.join(sorted(unresolved))}.")
        _validate_row("Blocks", block)
    for field in conn.execute("SELECT * FROM Fields"):
        source = field["conclusion_addendum_template"]
        if source:
            unknown, parsed = _template_variables(source)
            if unknown - {"value", "snippet"}:
                raise ContentEditError(f"Field '{field['key']}' addendum has unknown variable(s).")
            unresolved = set(_snippet_calls(parsed)) - snippet_keys
            if unresolved:
                raise ContentEditError(f"Field '{field['key']}' addendum references unresolved snippet(s): {', '.join(sorted(unresolved))}.")


def _validate_candidate(conn, changed_table, changed_key):
    validate_content_templates(conn)
    _validate_changed_item(conn, changed_table, changed_key)
    _validate_renders(conn)


def _validate_changed_item(conn, changed_table, changed_key):
    """Strictly render a changed Block even when no Preset reaches it."""
    if changed_table != "Blocks":
        return
    row = _fetch(conn, "Blocks", changed_key)
    if not row:
        raise ContentEditError("The changed Block is unavailable.")
    block = database.get_block_on_connection(conn, row["id"])
    resolver = lambda shortcut: editor_preview._snippet_from_connection(conn, shortcut)
    label_lookup = lambda keys: editor_preview._label_from_connection(conn, keys)
    micro, conclusion = rendering.render_block(
        block, total_specimens=1, snippet_resolver=resolver, strict=True,
    )
    rendering.render_context_fragments(
        block, snippet_resolver=resolver, strict=True,
    )
    grouping.render_conclusion_plain(
        [{"block": block, "overrides": {}, "conc_txt": conclusion}],
        resolver, label_lookup, True,
    )
    rendering.format_micro_plain([(block["name"], micro)])


def _validate_renders(conn):
    """Render default and pending contexts visible on this exact connection."""
    for preset in conn.execute("SELECT id FROM Presets"):
        editor_preview.render_preset_defaults(preset["id"], conn=conn, strict=True)
    _validate_discrete_branches(conn)
    _validate_pending_cases(conn)


def _validate_discrete_branches(conn):
    resolver = lambda shortcut: editor_preview._snippet_from_connection(conn, shortcut)
    label_lookup = lambda keys: editor_preview._label_from_connection(conn, keys)
    for field in conn.execute("SELECT * FROM Fields WHERE type IN ('checkbox', 'select')"):
        values = ([False, True] if field["type"] == "checkbox" else json.loads(field["options"] or "[]"))
        blocks = conn.execute("SELECT block_id FROM Block_Fields WHERE field_id = ?", (field["id"],)).fetchall()
        for block_ref in blocks:
            block = database.get_block_on_connection(conn, block_ref["block_id"])
            for value in values:
                overrides = {field["key"]: value}
                _micro, conclusion = rendering.render_block(
                    block, overrides, snippet_resolver=resolver, strict=True,
                )
                rendering.render_context_fragments(
                    block, overrides, snippet_resolver=resolver, strict=True,
                )
                grouping.render_conclusion_plain(
                    [{"block": block, "overrides": overrides, "conc_txt": conclusion}],
                    resolver, label_lookup, True,
                )


def _validate_pending_cases(conn):
    for case in conn.execute("SELECT case_number, preset_id, structured_input FROM Cases WHERE status = 'pending'"):
        try:
            _render_pending_case(conn, case["preset_id"], json.loads(case["structured_input"] or "{}"))
        except Exception as error:
            raise ContentEditError(f"Pending Case '{case['case_number']}' cannot render against this change: {error}") from error


def _render_pending_case(conn, preset_id, structured):
    # Compatibility for Stage 3 callers; validation now checks the entire report.
    return editor_preview.render_saved_case(conn, {
        "preset_id": preset_id, "structured_input": structured,
    })["micro_plain"]


def recent_revisions(limit=12):
    conn = database.get_db_connection()
    try:
        rows = conn.execute(
            """SELECT r.id, r.created_at, r.origin, r.summary, COUNT(c.id) AS changes
               FROM Content_Revisions r LEFT JOIN Content_Changes c ON c.revision_id = r.id
               GROUP BY r.id ORDER BY r.id DESC LIMIT ?""", (limit,)
        ).fetchall()
        revisions = []
        for row in rows:
            revision = dict(row)
            changes = conn.execute(
                "SELECT * FROM Content_Changes WHERE revision_id = ? ORDER BY id",
                (revision["id"],),
            ).fetchall()
            revision["details"] = "; ".join(_change_detail(change) for change in changes) or "—"
            revisions.append(revision)
        return revisions
    finally:
        conn.close()


def _change_detail(change):
    before = json.loads(change["before_json"]) if change["before_json"] else None
    after = json.loads(change["after_json"]) if change["after_json"] else None
    if before is None:
        action = "created"
    elif after is None:
        action = "deleted"
    else:
        columns = sorted(
            column for column in set(before) | set(after)
            if column != "id" and before.get(column) != after.get(column)
        )
        action = ", ".join(columns) if columns else "no value change"
    return f"{change['table_name']}.{change['entity_key']} — {action}"


def revert_revision(revision_id):
    """Apply a revision's before-state only when all after-states still match."""
    conn = database.get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_initial_snapshot(conn)
        changes = conn.execute("SELECT * FROM Content_Changes WHERE revision_id = ? ORDER BY id", (revision_id,)).fetchall()
        if not changes:
            raise ContentEditError("That revision has no reversible content changes.")
        for change in changes:
            _check_table(change["table_name"])
            current = _fetch(conn, change["table_name"], change["entity_key"])
            after = json.loads(change["after_json"]) if change["after_json"] else None
            if after is None:
                matches_after = current is None
            else:
                matches_after = current is not None and row_hash(current) == change["after_hash"]
            if not matches_after:
                raise ContentEditError("Revert refused: later content changes conflict with this revision.")
        conn.execute("SAVEPOINT content_revert")
        for change in changes:
            _apply_revert(conn, change)
        _validate_candidate(conn, "revert", str(revision_id))
        conn.execute("ROLLBACK TO content_revert")
        conn.execute("RELEASE content_revert")
        new_revision = conn.execute(
            "INSERT INTO Content_Revisions (origin, summary) VALUES (?, ?)",
            ("revision_revert", f"Reverted revision {revision_id}"),
        ).lastrowid
        for change in changes:
            current = _fetch(conn, change["table_name"], change["entity_key"])
            _apply_revert(conn, change)
            restored = _fetch(conn, change["table_name"], change["entity_key"])
            conn.execute(
                """INSERT INTO Content_Changes (revision_id, table_name, entity_key, operation, before_json, after_json, before_hash, after_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_revision, change["table_name"], change["entity_key"], "revert",
                 _json(dict(current)) if current else None, _json(dict(restored)) if restored else None,
                 row_hash(current) if current else None, row_hash(restored) if restored else None),
            )
        conn.commit()
        return new_revision
    except Exception as error:
        conn.rollback()
        raise ContentEditError(str(error)) from error
    finally:
        conn.close()


def _apply_revert(conn, change):
    table_name, entity_key = change["table_name"], change["entity_key"]
    _check_table(table_name)
    before = json.loads(change["before_json"]) if change["before_json"] else None
    if before is None:
        # Only a newly-created Snippet can be removed by its own safe revert.
        conn.execute("DELETE FROM Snippets WHERE shortcut = ?", (entity_key,))
        return
    key_column = EDITABLE[table_name][0]
    current = _fetch(conn, table_name, entity_key)
    if current is None:
        columns = list(before)
        conn.execute(
            f"INSERT INTO {table_name} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(before[column] for column in columns),
        )
        return
    columns = [column for column in before if column not in {"id", key_column}]
    conn.execute(
        f"UPDATE {table_name} SET {', '.join(f'{column} = ?' for column in columns)} WHERE {key_column} = ?",
        (*[before[column] for column in columns], entity_key),
    )


def validate_standalone_content(conn):
    """Strictly render all standalone Blocks and global Field addenda."""
    resolver = lambda shortcut: editor_preview._snippet_from_connection(conn, shortcut)
    label_lookup = lambda keys: editor_preview._label_from_connection(conn, keys)

    # Orphan Blocks and Field addenda must not escape validation merely
    # because no current Preset happens to reach them.
    for row in conn.execute("SELECT id FROM Blocks ORDER BY key"):
        block = database.get_block_on_connection(conn, row["id"])
        micro, conclusion = rendering.render_block(
            block, total_specimens=1, snippet_resolver=resolver, strict=True,
        )
        rendering.render_context_fragments(block, snippet_resolver=resolver, strict=True)
        grouping.render_conclusion_plain(
            [{"block": block, "overrides": {}, "conc_txt": conclusion}],
            resolver, label_lookup, True,
        )
        rendering.format_micro_plain([(block["name"], micro)])
    for field in conn.execute("SELECT key, type, default_value, conclusion_addendum_template FROM Fields ORDER BY key"):
        if field["conclusion_addendum_template"]:
            value = rendering.coerce_field_value(field["type"], field["default_value"])
            rendering.render_template(field["conclusion_addendum_template"], {"value": value}, resolver, True)
