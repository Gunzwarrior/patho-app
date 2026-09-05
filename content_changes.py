"""Reusable no-write candidate operations and immutable local review.

Only the caller-supplied candidate connection is mutated. There is deliberately
no live Apply, inverse writer, migration, or provenance persistence here.
"""

from dataclasses import dataclass, field
import json
import re
import sqlite3

import change_packages as contract
import content_editing
import content_snapshot
import database
import editor_preview
import rendering


@dataclass(frozen=True)
class ReviewResult:
    """Immutable server-held result; JSON-backed properties return fresh copies.

    Patient-bearing reports and the local guard belong only to the current
    session. repr deliberately omits all of them.
    """
    package_hash: str | None
    base_snapshot_hash: str
    candidate_snapshot_hash: str
    local_guard: str
    _payload_json: str = field(repr=False)

    @property
    def data(self):
        return json.loads(self._payload_json)

    @property
    def operations(self):
        return self.data["operations"]

    @property
    def changes(self):
        return self.data["changes"]

    @property
    def presets(self):
        return self.data["presets"]

    @property
    def pending_cases(self):
        return self.data["pending_cases"]


def local_review_guard(conn):
    """Bind content identities, audited ABA, and the complete current pending set."""
    identities = {
        table: [dict(row) for row in conn.execute(f"SELECT {key}, id FROM {table} ORDER BY {key}")]
        for table, (key, _) in content_snapshot.BASE_TABLES.items()
    }
    pending = [dict(row) for row in conn.execute(
        """SELECT id, case_number, preset_id, clinical_info, structured_input,
                  rendered_html, content_fingerprint, status
           FROM Cases WHERE status='pending' ORDER BY id"""
    )]
    return contract.digest({
        "revision_id": database.current_content_revision_id(conn),
        "identities": identities, "pending": pending,
    })


def _rows(conn, table):
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]


def _fetch(conn, table, key):
    column = content_snapshot.BASE_TABLES[table][0]
    row = conn.execute(f"SELECT * FROM {table} WHERE {column}=?", (key,)).fetchone()
    return dict(row) if row else None


def _insert(conn, table, row):
    columns = list(row)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(row[c] for c in columns),
    )


def _native_stored(field, value):
    """Decode existing text storage without changing it or forgiving bad values."""
    if value is None:
        return None
    kind = field["type"]
    if kind == "number":
        if isinstance(value, bool):
            raise ValueError("Invalid number.")
        if type(value) is not int and (
            not isinstance(value, str) or not re.fullmatch(r"[+]?[0-9]+", value.strip())
        ):
            raise ValueError("Invalid integer storage.")
        return int(value)
    if kind == "decimal":
        return float(value) if value != "" else None
    if kind == "checkbox":
        if str(value) not in ("0", "1", "true", "false", "True", "False"):
            raise ValueError("Invalid checkbox storage.")
        return str(value) in ("1", "true", "True")
    return value


def _field_storage(field, value, path, nullable_global=False):
    return contract.field_value(field, value, stored=True, path=path, nullable_global=nullable_global)


def _checked_operations(operations):
    """Normalize internal operations while retaining only valid local indices."""
    if not isinstance(operations, list) or any(not isinstance(op, dict) for op in operations):
        raise contract.PackageError("contract")
    indices = [op.get("index", i) for i, op in enumerate(operations)]
    if any(type(index) is not int or not 0 <= index < contract.MAX_OPERATIONS for index in indices):
        raise contract.PackageError("contract")
    clean = [{k: v for k, v in op.items() if k != "index"} for op in operations]
    normalized = contract.normalize_operations(clean)
    for op in normalized:
        op["index"] = indices[op["index"]]
    return normalized


def materialize_operations(conn, operations):
    """Resolve deterministic operations against an explicit FK-enabled candidate.

    Syntax is rechecked for internal callers too. All supplied operations are
    additive/change-only; only newly created owners may receive links.
    Returns exact physical before/after images for checkpoint 2's audit.
    """
    if not conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise contract.PackageError("graph")
    normalized = _checked_operations(operations)
    created = {(o["table"], o["key"]) for o in normalized if o["op"] == "create"}
    changes = []
    for op in normalized:
        path = f"operations[{op['index']}]"
        table, key, kind = op["table"], op["key"], op["op"]
        try:
            if kind in ("create", "update"):
                before = _fetch(conn, table, key)
                if (kind == "create" and before is not None) or (kind == "update" and before is None):
                    raise contract.PackageError("target", path)
                if table == "Blocks" and before and before["is_table"]:
                    raise contract.PackageError("target", path)
                values = dict(op["values" if kind == "create" else "set"])
                if table == "Fields":
                    field = {**(before or {}), **values}
                    if "default_value" in values:
                        value = values["default_value"]
                        if (value is None and before and before["default_value"] is not None
                                and field["type"] in ("number", "select", "checkbox")):
                            raise contract.PackageError("value", path + ".set.default_value")
                        values["default_value"] = _field_storage(field, value, path, nullable_global=True)
                        if before:
                            try:
                                unchanged = _native_stored(field, before["default_value"]) == value
                            except (TypeError, ValueError):
                                unchanged = False  # A valid candidate may repair invalid base content.
                            if unchanged:
                                raise contract.PackageError("noop", path + ".set.default_value")
                    if "options" in values:
                        values["options"] = contract.canonical_json(values["options"]).strip() if values["options"] is not None else None
                if kind == "create":
                    if table == "Blocks":
                        values.update(is_table=0, site_label=None, conclusion_group=None)
                    elif table == "Presets":
                        values["default_adicap"] = None
                    values[content_snapshot.BASE_TABLES[table][0]] = key
                    content_editing._validate_row(table, values)
                    _insert(conn, table, values)
                else:
                    if any(before[column] == value for column, value in values.items()):
                        raise contract.PackageError("noop", path)
                    content_editing._validate_row(table, {**before, **values}, original=before)
                    column = content_snapshot.BASE_TABLES[table][0]
                    conn.execute(
                        f"UPDATE {table} SET {', '.join(c + '=?' for c in values)} WHERE {column}=?",
                        (*values.values(), key),
                    )
                after = _fetch(conn, table, key)
            elif table == "Block_Fields":
                if ("Blocks", key["block_key"]) not in created:
                    raise contract.PackageError("target", path)
                block = _fetch(conn, "Blocks", key["block_key"])
                field = _fetch(conn, "Fields", key["field_key"])
                if block is None or field is None:
                    raise contract.PackageError("target", path)
                values = dict(op["values"])
                if values["default_override"] is not None:
                    values["default_override"] = _field_storage(field, values["default_override"], path)
                values["context_section"] = int(values["context_section"])
                after = {"block_id": block["id"], "field_id": field["id"], **values}
                before = None
                _insert(conn, table, after)
            else:
                if ("Presets", key["preset_code"]) not in created:
                    raise contract.PackageError("target", path)
                preset, block = _fetch(conn, "Presets", key["preset_code"]), _fetch(conn, "Blocks", key["block_key"])
                if preset is None or block is None or block["is_table"]:
                    raise contract.PackageError("target", path)
                fields = {r["key"]: dict(r) for r in conn.execute(
                    "SELECT f.* FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id WHERE bf.block_id=?",
                    (block["id"],),
                )}
                overrides = op["values"]["field_overrides"]
                if set(overrides) - set(fields):
                    raise contract.PackageError("value", path + ".values.field_overrides")
                for name, value in overrides.items():
                    contract.field_value(fields[name], value, path=path + ".values.field_overrides")
                after = {"preset_id": preset["id"], "block_id": block["id"], "sort_order": key["sort_order"],
                         "field_overrides": contract.canonical_json(overrides).strip()}
                before = None
                _insert(conn, table, after)
            changes.append({"table": table, "key": key, "operation": kind, "before": before, "after": after})
        except contract.PackageError:
            raise
        except Exception:
            raise contract.PackageError("graph", path) from None
    return changes


def _check_widget_defaults(block):
    for field in block["fields"]:
        kind, value = field["type"], field["value"]
        if value is None and kind in ("number", "select", "checkbox"):
            raise contract.PackageError("graph")
        contract.field_value(field, _native_stored(field, value))


def _validate_stored_field_configuration(conn):
    """Check every stored option/override against final Field definitions.

    Untouched legacy empty global defaults remain valid; they are not rewritten.
    New effective widget uses receive the stricter checks below.
    """
    fields = {row["id"]: row for row in _rows(conn, "Fields")}
    for field_row in fields.values():
        kind = field_row["type"]
        if kind not in contract.FIELD_TYPES:
            raise contract.PackageError("graph")
        options = json.loads(field_row["options"]) if field_row["options"] is not None else None
        if kind == "select":
            if (not isinstance(options, list) or not options
                    or any(not isinstance(v, str) for v in options)
                    or len(set(options)) != len(options)):
                raise contract.PackageError("graph")
        elif options is not None:
            raise contract.PackageError("graph")
        if field_row["default_value"] not in (None, ""):
            contract.field_value(field_row, _native_stored(field_row, field_row["default_value"]))
    for link in _rows(conn, "Block_Fields"):
        if link["default_override"] is not None:
            field_row = fields[link["field_id"]]
            contract.field_value(field_row, _native_stored(field_row, link["default_override"]))
    for link in _rows(conn, "Preset_Blocks"):
        overrides = json.loads(link["field_overrides"]) if link["field_overrides"] else {}
        if not isinstance(overrides, dict):
            raise contract.PackageError("graph")
        linked = {fields[r[0]]["key"]: fields[r[0]] for r in conn.execute(
            "SELECT field_id FROM Block_Fields WHERE block_id=?", (link["block_id"],),
        )}
        if set(overrides) - set(linked):
            raise contract.PackageError("graph")
        for name, value in overrides.items():
            # Historical Presets store typed values; some established seed
            # overrides use text storage too, as the original resolver accepts.
            field_row = linked[name]
            native = _native_stored(field_row, value)
            contract.field_value(field_row, native)


def validate_graph(conn, operations):
    """New graph/widget constraints; existing configuration is not migrated."""
    content_snapshot.validate_content_snapshot(content_snapshot.snapshot_from_connection(conn))
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise contract.PackageError("graph")
    _validate_stored_field_configuration(conn)
    created = {(o["table"], o["key"]) for o in operations if o["op"] == "create"}
    new_blocks = {key for table, key in created if table == "Blocks"}
    new_fields = {key for table, key in created if table == "Fields"}
    fields = {row["key"]: row for row in _rows(conn, "Fields")}
    reserved = {"snippet", "value", "site_label", "fragment_text", "true", "false", "none",
                "True", "False", "None"}
    aliases = {f"{key}_display" for key, field in fields.items() if field["type"] == "decimal"}
    for name in new_fields:
        if name in reserved or name in aliases or (fields[name]["type"] == "decimal" and f"{name}_display" in fields):
            raise contract.PackageError("graph")
    for name in new_blocks:
        block_row = _fetch(conn, "Blocks", name)
        block = database.get_block_on_connection(conn, block_row["id"])
        positions = [r[0] for r in conn.execute("SELECT sort_order FROM Block_Fields WHERE block_id=?", (block_row["id"],))]
        if len(positions) != len(set(positions)):
            raise contract.PackageError("graph")
        keys = {f["key"] for f in block["fields"]}
        if keys & (reserved | {f"{f['key']}_display" for f in block["fields"] if f["type"] == "decimal"}):
            raise contract.PackageError("graph")
        context_keys = {f["key"] for f in block["fields"] if f["context_section"]}
        context_aliases = context_keys | {f"{f['key']}_display" for f in block["fields"] if f["context_section"] and f["type"] == "decimal"}
        if "fragments" in context_keys:
            context_aliases.add("fragment_text")
        for column in ("context_template", "title_fragment_template"):
            if block.get(column):
                variables, _ = content_editing._template_variables(block[column])
                if variables - context_aliases - {"snippet"}:
                    raise contract.PackageError("graph")
        _check_widget_defaults(block)
    for table, name in created:
        if table == "Fields" and fields[name]["type"] in ("number", "select", "checkbox"):
            field = fields[name]
            uses = conn.execute("SELECT block_id FROM Block_Fields WHERE field_id=?", (field["id"],)).fetchall()
            if not uses:
                contract.field_value(field, _native_stored(field, field["default_value"]))
        if table == "Presets":
            preset = _fetch(conn, table, name)
            blocks = database.get_preset_blocks_on_connection(conn, preset["id"])
            if not blocks or len({b["sort_order"] for b in blocks}) != len(blocks):
                raise contract.PackageError("graph")
            for block in blocks:
                _check_widget_defaults(block)


def validate_candidate_content(conn, operations):
    """Validate the complete materialized candidate, reusing Stage 3/4 primitives."""
    validate_graph(conn, operations)
    content_editing.validate_content_templates(conn)
    content_editing.validate_standalone_content(conn)
    content_editing._validate_discrete_branches(conn)
    resolver = lambda key: editor_preview._snippet_from_connection(conn, key)
    branch_warnings = []
    # Include orphan addendum branches and branches under each Preset's resolved
    # defaults; Block-only defaults can miss interactions with Preset overrides.
    for field in _rows(conn, "Fields"):
        if field["conclusion_addendum_template"]:
            values = [rendering.coerce_field_value(field["type"], field["default_value"])]
            if field["type"] == "select":
                values.extend(json.loads(field["options"] or "[]"))
            elif field["type"] == "checkbox":
                values.extend([False, True])
            for value in values:
                rendering.render_template(field["conclusion_addendum_template"], {"value": value}, resolver, True)
    for preset in _rows(conn, "Presets"):
        blocks = database.get_preset_blocks_on_connection(conn, preset["id"])
        for i, block in enumerate(blocks):
            for field in block["fields"]:
                values = [False, True] if field["type"] == "checkbox" else (field["options"] or []) if field["type"] == "select" else []
                for value in values:
                    overrides = [{} for _ in blocks]
                    overrides[i] = {field["key"]: value}
                    report = editor_preview.render_report(conn, preset, blocks, overrides)
                    if report["warnings"] or report["conflicts"]:
                        branch_warnings.append({
                            "preset_code": preset["short_code"], "block_key": block["key"],
                            "position": block["sort_order"], "field_key": field["key"], "value": value,
                            "warnings": report["warnings"], "conflicts": report["conflicts"],
                        })
    return branch_warnings


def _check_complete_report(report):
    if (not isinstance(report, dict)
            or any(not isinstance(report.get(k), str) for k in
                   ("title", "clinical_info", "micro_plain", "conclusion_plain", "html"))
            or any(not isinstance(report.get(k), list) or any(not isinstance(v, str) for v in report[k])
                   for k in ("conflicts", "warnings", "locks"))):
        raise ValueError("Incomplete report preview.")
    return report


def _capture(conn, *, candidate=False):
    presets, pending = {}, {}
    pending_errors = []
    for preset in conn.execute("SELECT id, short_code FROM Presets ORDER BY short_code"):
        try:
            report = _check_complete_report(editor_preview.render_preset_defaults(preset["id"], conn, True))
            report.pop("preset")
            presets[preset["short_code"]] = {
                "report": report,
                "fingerprint": database.compute_case_content_fingerprint(preset["id"], {}, conn),
            }
        except Exception as error:
            if candidate:
                raise contract.PackageError("candidate", local=str(error)) from None
            presets[preset["short_code"]] = {"error": str(error)}
    for case_row in conn.execute("SELECT * FROM Cases WHERE status='pending' ORDER BY id"):
        case = dict(case_row)
        record = {"id": case["id"], "case_number": case["case_number"],
                  "saved_html": case["rendered_html"], "saved_fingerprint": case["content_fingerprint"]}
        try:
            structured = json.loads(case["structured_input"])
            record["fingerprint"] = database.compute_case_content_fingerprint(case["preset_id"], structured, conn)
            record["already_stale"] = record["fingerprint"] != case["content_fingerprint"]
            record["report"] = _check_complete_report(editor_preview.render_saved_case(conn, case))
        except Exception as error:
            record["error"] = str(error)
            if candidate:
                pending_errors.append({"id": case["id"], "error": str(error)})
        pending[case["id"]] = record
    if pending_errors:
        raise contract.PackageError("pending", local=pending_errors,
                                    errors=[("pending", "") for _ in pending_errors])
    return presets, pending


def _standalone_previews(conn, operations):
    """Offer useful wording/default output for content that has no Preset use."""
    result = []
    resolver = lambda key: editor_preview._snippet_from_connection(conn, key)
    used_blocks = {r[0] for r in conn.execute("SELECT block_id FROM Preset_Blocks")}
    used_fields = {r[0] for r in conn.execute(
        "SELECT bf.field_id FROM Block_Fields bf JOIN Preset_Blocks pb ON pb.block_id=bf.block_id"
    )}
    used_snippets = set()
    for block_id in used_blocks:
        block = database.get_block_on_connection(conn, block_id)
        used_snippets.update(database._snippet_shortcuts(
            [block.get(c) for c in content_editing.BLOCK_TEMPLATE_COLUMNS]
            + [f.get("conclusion_addendum_template") for f in block["fields"]]
        ))
    for op in operations:
        table, key = op["table"], op["key"]
        if table not in ("Fields", "Blocks", "Snippets"):
            continue
        row = _fetch(conn, table, key)
        used = (row["id"] in used_blocks if table == "Blocks" else
                row["id"] in used_fields if table == "Fields" else key in used_snippets)
        if used:
            continue
        preview = {"table": table, "key": key, "label": "No Preset uses this yet"}
        if table == "Blocks":
            block = database.get_block_on_connection(conn, row["id"])
            preview["report"] = editor_preview.render_report(conn, {"name": row["name"]}, [block], [{}])
        elif table == "Snippets":
            preview["expansion"] = row["expansion"]
        else:
            preview["default_value"] = rendering.coerce_field_value(row["type"], row["default_value"])
            preview["label_text"] = row["label"]
            preview["addendum"] = (
                rendering.render_template(row["conclusion_addendum_template"], {"value": preview["default_value"]}, resolver, True)
                if row["conclusion_addendum_template"] else None
            )
        result.append(preview)
    return result


def _dependent_presets(conn, operations):
    affected = set()
    for op in operations:
        table, key = op["table"], op["key"]
        if table == "Presets":
            affected.add(key)
            continue
        block_ids = set()
        if table == "Blocks":
            block_ids.add(_fetch(conn, table, key)["id"])
        elif table == "Fields":
            field_row = _fetch(conn, table, key)
            block_ids.update(r[0] for r in conn.execute(
                "SELECT block_id FROM Block_Fields WHERE field_id=?", (field_row["id"],),
            ))
        elif table == "Snippets":
            for block in _rows(conn, "Blocks"):
                templates = [block[c] for c in content_editing.BLOCK_TEMPLATE_COLUMNS]
                templates.extend(r[0] for r in conn.execute(
                    "SELECT f.conclusion_addendum_template FROM Fields f JOIN Block_Fields bf ON bf.field_id=f.id WHERE bf.block_id=?",
                    (block["id"],),
                ))
                if key in database._snippet_shortcuts(templates):
                    block_ids.add(block["id"])
        for block_id in block_ids:
            affected.update(r[0] for r in conn.execute(
                "SELECT p.short_code FROM Presets p JOIN Preset_Blocks pb ON pb.preset_id=p.id WHERE pb.block_id=?",
                (block_id,),
            ))
    return affected


def _prefix_warnings(conn, operations):
    codes = [row[0] for row in conn.execute("SELECT short_code FROM Presets ORDER BY short_code")]
    new = {o["key"] for o in operations if o["op"] == "create" and o["table"] == "Presets"}
    return [f"Quick Type prefix overlap: {a} / {b}. Longest-prefix matching is unchanged."
            for i, a in enumerate(codes) for b in codes[i+1:]
            if (a in new or b in new) and (a.startswith(b) or b.startswith(a))]


def review_candidate(operations, base_hash, *, package_hash=None, summary="", db_name=None):
    """One SQLite backup, one private memory candidate, no live write transaction."""
    operations = _checked_operations(operations)
    candidate = sqlite3.connect(":memory:")
    try:
        source = database.get_db_connection() if db_name is None else sqlite3.connect(db_name)
        try:
            source.backup(candidate)
        finally:
            source.close()
        candidate.row_factory = sqlite3.Row
        candidate.execute("PRAGMA foreign_keys=ON")
        candidate.execute("PRAGMA temp_store=MEMORY")
        base = content_snapshot.snapshot_from_connection(candidate)
        if content_snapshot.content_snapshot_hash(base) != base_hash:
            raise contract.PackageError("stale")
        guard = local_review_guard(candidate)
        before_presets, before_pending = _capture(candidate)
        def content_only(action, table, column, db_name, trigger):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                if trigger or table not in {*content_snapshot.BASE_TABLES, "Block_Fields", "Preset_Blocks", "sqlite_sequence"}:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        candidate.set_authorizer(content_only)
        try:
            changes = materialize_operations(candidate, operations)
        finally:
            candidate.set_authorizer(None)
        try:
            branch_warnings = validate_candidate_content(candidate, operations)
        except contract.PackageError:
            raise
        except Exception as error:
            raise contract.PackageError("candidate", local=str(error)) from None
        after_presets, after_pending = _capture(candidate, candidate=True)
        presets = []
        dependent = _dependent_presets(candidate, operations)
        unaffected = 0
        for code in sorted(set(before_presets) | set(after_presets)):
            before, after = before_presets.get(code), after_presets.get(code)
            affected = before != after or code in dependent
            if not affected:
                unaffected += 1
            presets.append({"code": code, "affected": affected,
                            "added": before is None,
                            "output_changed": (before or {}).get("report") != (after or {}).get("report"),
                            "before": before, "after": after})
        pending = [
            {"id": case_id, "case_number": after["case_number"],
             "before_label": "Current before this package", "after_label": "Candidate after this package",
             "saved_label": "Last saved report", "saved_html": after["saved_html"],
             "already_stale": before_pending[case_id].get("already_stale"),
             "before": before_pending[case_id], "after": after}
            for case_id, after in after_pending.items()
            if before_pending[case_id].get("fingerprint") != after["fingerprint"]
            or "error" in before_pending[case_id]
        ]
        payload = {
            "summary": summary, "operations": operations, "changes": changes,
            "presets": presets, "unaffected_presets": unaffected, "pending_cases": pending,
            "validated_pending_count": len(after_pending),
            "warnings": _prefix_warnings(candidate, operations), "branch_warnings": branch_warnings,
            "standalone": _standalone_previews(candidate, operations),
        }
        return ReviewResult(
            package_hash, base_hash,
            content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(candidate)),
            guard, contract.canonical_json(payload),
        )
    except contract.PackageError:
        raise
    except Exception as error:
        raise contract.PackageError("candidate", local=str(error)) from None
    finally:
        candidate.close()
