"""Reviewed candidates, atomic content changes, and dependency-safe inverses."""

from contextlib import contextmanager
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


_REVIEW_ISSUER = object()
CONTENT_TABLES = {*content_snapshot.BASE_TABLES, "Block_Fields", "Preset_Blocks"}


class ChangeError(content_editing.ContentEditError):
    """Fixed public rejection; optional details remain in the local session."""

    def __init__(self, message, *, local=None):
        self.local = local
        super().__init__(message)


class StaleReviewError(ChangeError):
    pass


@dataclass(frozen=True)
class ReviewResult:
    """Immutable server-held result; JSON-backed properties return fresh copies.

    Patient-bearing reports and the local guard belong only to the current
    session. repr deliberately omits all of them.
    """
    package_hash: str | None
    base_snapshot_hash: str
    candidate_snapshot_hash: str
    local_guard: str = field(repr=False)
    _payload_json: str = field(repr=False)
    _issuer: object = field(default=None, init=False, repr=False, compare=False)

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


def _issued_review(*args):
    review = ReviewResult(*args)
    object.__setattr__(review, "_issuer", _REVIEW_ISSUER)
    return review


@contextmanager
def _access_scope(conn, writable=(), *, insert_only=False):
    """Prevent triggers/helper regressions from writing outside this phase.

    Transaction control belongs to the caller, never a nested helper. Changing
    authorizers also invalidates SQLite's statement cache between phases.
    """
    def authorize(action, table, column, db_name, trigger):
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            if trigger or db_name != "main" or table not in writable:
                return sqlite3.SQLITE_DENY
            if insert_only and action != sqlite3.SQLITE_INSERT and table != "sqlite_sequence":
                return sqlite3.SQLITE_DENY
        elif action not in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT,
                            sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_PRAGMA,
                            sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and (column is not None or table not in {"foreign_keys", "foreign_key_check"}):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    conn.set_authorizer(authorize)
    try:
        yield
    finally:
        conn.set_authorizer(None)


@contextmanager
def _candidate_copy(db_name):
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
        candidate.execute("BEGIN")
        yield candidate
    finally:
        candidate.close()


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


def _content_rows(conn):
    return {table: {
        tuple(row[c] for c in (("id",) if table in content_snapshot.BASE_TABLES else _RELATION_COLUMNS[table])): row
        for row in _rows(conn, table)
    } for table in CONTENT_TABLES}


def _assert_exact_changes(conn, before, changes):
    """Every persisted change must have exactly one matching audit image."""
    expected = {table: dict(rows) for table, rows in before.items()}
    for change in changes:
        table = change["table"]
        columns = ("id",) if table in content_snapshot.BASE_TABLES else _RELATION_COLUMNS[table]
        old, new = change["before"], change["after"]
        key = tuple((old or new)[c] for c in columns)
        if expected[table].get(key) != old:
            raise ChangeError("Candidate changes do not match their before-images.")
        if new is None:
            del expected[table][key]
        else:
            expected[table][key] = new
    if _content_rows(conn) != expected:
        raise ChangeError("Candidate writes do not match the reviewed change list.")


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
            values = [rendering.normalize_widget_value(field, field["default_value"])]
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
        if row is None:
            continue
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
            preview["default_value"] = rendering.normalize_widget_value(row, row["default_value"])
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
        if table == "Preset_Blocks":
            affected.add(key["preset_code"])
            continue
        block_ids = set()
        if table in ("Blocks", "Block_Fields"):
            block = _fetch(conn, "Blocks", key if table == "Blocks" else key["block_key"])
            if block:
                block_ids.add(block["id"])
        elif table == "Fields":
            field_row = _fetch(conn, table, key)
            if field_row is None:
                continue
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


def _review_on_connection(candidate, operations, base_hash, *, package_hash=None,
                          summary="", inverse=None):
    base = content_snapshot.snapshot_from_connection(candidate)
    if content_snapshot.content_snapshot_hash(base) != base_hash:
        raise contract.PackageError("stale")
    guard = local_review_guard(candidate)
    with _access_scope(candidate):
        before_rows = _content_rows(candidate)
        before_presets, before_pending = _capture(candidate)
        dependent = _dependent_presets(candidate, operations)
        try:
            before_standalone = _standalone_previews(candidate, operations)
        except Exception as error:
            before_standalone = [{"error": str(error)}]
    with _access_scope(candidate, CONTENT_TABLES | {"sqlite_sequence"}):
        if inverse is None:
            changes = materialize_operations(candidate, operations)
        else:
            changes = _materialize_inverse(candidate, inverse["changes"])
    try:
        with _access_scope(candidate):
            _assert_exact_changes(candidate, before_rows, changes)
            branch_warnings = validate_candidate_content(candidate, operations)
            after_presets, after_pending = _capture(candidate, candidate=True)
    except contract.PackageError:
        raise
    except Exception as error:
        raise contract.PackageError("candidate", local=str(error)) from None
    with _access_scope(candidate):
        presets = []
        dependent.update(_dependent_presets(candidate, operations))
        unaffected = 0
        for code in sorted(set(before_presets) | set(after_presets)):
            before, after = before_presets.get(code), after_presets.get(code)
            affected = before != after or code in dependent
            if not affected:
                unaffected += 1
            presets.append({"code": code, "affected": affected,
                            "added": before is None, "removed": after is None,
                            "output_changed": (before or {}).get("report") != (after or {}).get("report"),
                            "before": before, "after": after})
        pending = [
            {"id": case_id, "case_number": after["case_number"],
             "before_label": "Current before this change", "after_label": "Candidate after this change",
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
            "before_standalone": before_standalone,
            "inverse_revision_id": inverse["revision_id"] if inverse else None,
            "inverse_source_hash": inverse["source_hash"] if inverse else None,
        }
        return _issued_review(
            package_hash, base_hash,
            content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(candidate)),
            guard, contract.canonical_json(payload),
        )


def review_candidate(operations, base_hash, *, package_hash=None, summary="", db_name=None):
    """One SQLite backup, one private memory candidate, no live write transaction."""
    operations = _checked_operations(operations)
    try:
        with _candidate_copy(db_name) as candidate:
            return _review_on_connection(candidate, operations, base_hash,
                                         package_hash=package_hash, summary=summary)
    except contract.PackageError:
        raise
    except Exception as error:
        raise contract.PackageError("candidate", local=str(error)) from None


_RELATION_COLUMNS = {
    "Block_Fields": ("block_id", "field_id"),
    "Preset_Blocks": ("preset_id", "block_id", "sort_order"),
}
_RELATION_IMAGES = {
    "Block_Fields": {"block_id", "field_id", "sort_order", "label_override", "default_override", "context_section"},
    "Preset_Blocks": {"preset_id", "block_id", "sort_order", "field_overrides"},
}


def _audit_key(table, key):
    return contract.canonical_json(key).strip() if table in _RELATION_COLUMNS else key


def _physical_target(conn, table, key):
    if table in content_snapshot.BASE_TABLES:
        return (content_snapshot.BASE_TABLES[table][0],), (key,)
    if table == "Block_Fields":
        endpoints = (("Blocks", key["block_key"]), ("Fields", key["field_key"]))
    else:
        endpoints = (("Presets", key["preset_code"]), ("Blocks", key["block_key"]))
    rows = [_fetch(conn, name, stable_key) for name, stable_key in endpoints]
    if any(row is None for row in rows):
        return _RELATION_COLUMNS[table], None
    values = tuple(row["id"] for row in rows)
    if table == "Preset_Blocks":
        values += (key["sort_order"],)
    return _RELATION_COLUMNS[table], values


def _fetch_change(conn, change):
    columns, values = _physical_target(conn, change["table"], change["key"])
    if values is None:
        return None
    where = " AND ".join(f"{column}=?" for column in columns)
    row = conn.execute(f"SELECT * FROM {change['table']} WHERE {where}", values).fetchone()
    return dict(row) if row else None


def _read_audit(conn, revision_id):
    """Decode legacy and multi-row audits by exact row shape, never by origin."""
    if type(revision_id) is not int or revision_id <= 0:
        raise ChangeError("That revision has no reversible content changes.")
    revision = conn.execute("SELECT * FROM Content_Revisions WHERE id=?", (revision_id,)).fetchone()
    records = [dict(r) for r in conn.execute(
        "SELECT * FROM Content_Changes WHERE revision_id=? ORDER BY id", (revision_id,),
    )]
    if revision is None or not records:
        raise ChangeError("That revision has no reversible content changes.")
    result, targets = [], set()
    try:
        for record in records:
            table = record["table_name"]
            if table not in CONTENT_TABLES or record["operation"] not in {"create", "update", "link", "revert"}:
                raise ValueError("Unsupported audit operation.")
            key = json.loads(record["entity_key"]) if table in _RELATION_COLUMNS else record["entity_key"]
            if table in _RELATION_COLUMNS:
                if (not isinstance(key, dict) or set(key) != set(contract.LINK[table]["key"])
                        or any(not isinstance(v, str) or not v for k, v in key.items() if k != "sort_order")
                        or (table == "Preset_Blocks" and type(key["sort_order"]) is not int)):
                    raise ValueError("Invalid audit identity.")
            elif not isinstance(key, str) or not key:
                raise ValueError("Invalid audit identity.")
            target = (table, _audit_key(table, key))
            if target in targets:
                raise ValueError("Duplicate audit identity.")
            targets.add(target)
            images = []
            for side in ("before", "after"):
                raw = record[side + "_json"]
                image = json.loads(raw) if raw is not None else None
                expected_columns = (set(content_snapshot.BASE_TABLES[table][1]) | {"id"}
                                    if table in content_snapshot.BASE_TABLES else _RELATION_IMAGES[table])
                if image is not None:
                    if not isinstance(image, dict) or set(image) != expected_columns:
                        raise ValueError("Invalid audit image.")
                    ids = [c for c in image if c == "id" or c.endswith("_id")]
                    if any(type(image[c]) is not int or image[c] <= 0 for c in ids):
                        raise ValueError("Invalid audit row ID.")
                    if table in content_snapshot.BASE_TABLES and image[content_snapshot.BASE_TABLES[table][0]] != key:
                        raise ValueError("Invalid audit stable key.")
                    if table == "Preset_Blocks" and image["sort_order"] != key["sort_order"]:
                        raise ValueError("Invalid audit position.")
                    if table == "Blocks" and image["is_table"]:
                        raise ValueError("Table Blocks cannot be reverted here.")
                if record[side + "_hash"] != (content_editing.row_hash(image) if image is not None else None):
                    raise ValueError("Invalid audit hash.")
                images.append(image)
            before, after = images
            if before == after:
                raise ValueError("Empty audit change.")
            if before is not None and after is not None:
                columns = ("id",) if table in content_snapshot.BASE_TABLES else _RELATION_COLUMNS[table]
                if any(before[c] != after[c] for c in columns):
                    raise ValueError("Audit identity changed.")
            result.append({"table": table, "key": key, "operation": "revert", "before": after, "after": before})
        # Resolve endpoint keys against both current content and recorded base
        # images, so an inverse can restore its own deleted endpoints by ID.
        recorded_ids = {}
        for change in result:
            if change["table"] in content_snapshot.BASE_TABLES:
                image = change["before"] or change["after"]
                recorded_ids[(change["table"], change["key"])] = image["id"]
        for change in result:
            table, key = change["table"], change["key"]
            if table not in _RELATION_COLUMNS:
                continue
            endpoints = (("block_id", "Blocks", key["block_key"]),
                         ("field_id", "Fields", key["field_key"])) if table == "Block_Fields" else (
                             ("preset_id", "Presets", key["preset_code"]), ("block_id", "Blocks", key["block_key"]))
            for column, endpoint, stable_key in endpoints:
                current = _fetch(conn, endpoint, stable_key)
                expected_id = recorded_ids.get((endpoint, stable_key), current["id"] if current else None)
                for image in (change["before"], change["after"]):
                    if image is not None and image[column] != expected_id:
                        raise ValueError("Audit endpoint identity is unavailable.")
    except (ValueError, TypeError, KeyError) as error:
        raise ChangeError("Revert refused: invalid or unavailable audit data.", local=str(error)) from None
    return {"revision_id": revision_id, "source_hash": contract.digest({"revision": dict(revision), "changes": records}),
            "changes": result}


def _inverse_operations(changes):
    # Descriptors for shared graph/impact validation only; the public package
    # materializer never accepts remove/restore or physical audit row images.
    operations = []
    for change in changes:
        if change["after"] is None:
            operation = "remove"
        elif change["before"] is None:
            operation = "link" if change["table"] in _RELATION_COLUMNS else "create"
        else:
            operation = "update"
        operations.append({"op": operation, "table": change["table"], "key": change["key"]})
    return operations


def _check_inverse_targets(conn, changes):
    for change in changes:
        if _fetch_change(conn, change) != change["before"]:
            raise ChangeError("Revert refused: later content changes or identities conflict with this revision.")
        if change["before"] is None and change["table"] in content_snapshot.BASE_TABLES:
            if conn.execute(f"SELECT 1 FROM {change['table']} WHERE id=?", (change["after"]["id"],)).fetchone():
                raise ChangeError("Revert refused: an original row ID has been reused.")


def _check_pending_removals(conn, changes):
    removals = [c for c in changes if c["after"] is None]
    if not removals:
        return
    for case in conn.execute("SELECT id, preset_id, structured_input FROM Cases WHERE status='pending'"):
        try:
            structured = json.loads(case["structured_input"] or "{}")
            instances = structured.get("block_instances")
            if instances is None:
                instances = [dict(r) for r in conn.execute(
                    "SELECT block_id, sort_order AS instance_no FROM Preset_Blocks WHERE preset_id=?",
                    (case["preset_id"],),
                )]
            block_ids = {i["block_id"] for i in instances}
            links = [dict(r) for block_id in block_ids for r in conn.execute(
                "SELECT * FROM Block_Fields WHERE block_id=?", (block_id,),
            )]
            field_ids = {r["field_id"] for r in links}
            templates = [row[c] for block_id in block_ids for row in conn.execute(
                "SELECT * FROM Blocks WHERE id=?", (block_id,),
            ) for c in content_editing.BLOCK_TEMPLATE_COLUMNS]
            templates.extend(row[0] for field_id in field_ids for row in conn.execute(
                "SELECT conclusion_addendum_template FROM Fields WHERE id=?", (field_id,),
            ))
            snippets = database._snippet_shortcuts(templates)
            for change in removals:
                table, row = change["table"], change["before"]
                needed = (
                    (table == "Presets" and row["id"] == case["preset_id"])
                    or (table == "Blocks" and row["id"] in block_ids)
                    or (table == "Fields" and row["id"] in field_ids)
                    or (table == "Snippets" and change["key"] in snippets)
                    or (table == "Block_Fields" and row["block_id"] in block_ids)
                    or (table == "Preset_Blocks" and row["preset_id"] == case["preset_id"] and any(
                        i["block_id"] == row["block_id"] and i.get("instance_no") == row["sort_order"] for i in instances))
                )
                if needed:
                    raise ChangeError("Revert refused: a pending case depends on content being removed.",
                                      local={"case_id": case["id"], "table": table})
        except ChangeError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ChangeError("Revert refused: a pending case cannot be checked safely.", local={"case_id": case["id"]}) from None
    for change in removals:
        if change["table"] == "Presets" and conn.execute(
            "SELECT 1 FROM Cases WHERE preset_id=? LIMIT 1", (change["before"]["id"],),
        ).fetchone():
            raise ChangeError("Revert refused: a saved case still references this Preset. Historical-reference changes are deferred.")


def _materialize_inverse(conn, changes):
    _check_inverse_targets(conn, changes)
    _check_pending_removals(conn, changes)
    # Immediate foreign keys require endpoints before links on recreation and
    # links before endpoints on deletion. Templates are checked only at the end.
    def order(change):
        relation = change["table"] in _RELATION_COLUMNS
        removing = change["after"] is None
        phase = (0 if not relation else 3) if not removing else (1 if relation else 4)
        return phase, change["table"], _audit_key(change["table"], change["key"])
    for change in sorted(changes, key=order):
        table, before, after = change["table"], change["before"], change["after"]
        if before is None:
            _insert(conn, table, after)
        else:
            columns, values = _physical_target(conn, table, change["key"])
            where = " AND ".join(f"{column}=?" for column in columns)
            if after is None:
                conn.execute(f"DELETE FROM {table} WHERE {where}", values)
            else:
                editable = [c for c in after if c not in columns and c != "id"]
                conn.execute(f"UPDATE {table} SET {', '.join(c + '=?' for c in editable)} WHERE {where}",
                             (*[after[c] for c in editable], *values))
    if any(_fetch_change(conn, change) != change["after"] for change in changes):
        raise ChangeError("Revert refused: restored rows do not match their audit images.")
    return changes


def review_inverse(revision_id, *, db_name=None):
    """Prepare a no-write inverse with current before/after report impact."""
    try:
        with _candidate_copy(db_name) as candidate:
            inverse = _read_audit(candidate, revision_id)
            base_hash = content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(candidate))
            return _review_on_connection(candidate, _inverse_operations(inverse["changes"]), base_hash,
                                         summary=f"Reverted revision {revision_id}", inverse=inverse)
    except (ChangeError, contract.PackageError):
        raise
    except Exception as error:
        raise ChangeError("Revert refused: dependencies or candidate validation prevent reversal.", local=str(error)) from None


def _record_changes(conn, review, changes):
    inverse_id = review.data["inverse_revision_id"]
    origin = "revision_revert" if inverse_id is not None else "package_import" if review.package_hash else "content_edit"
    revision_id = conn.execute(
        """INSERT INTO Content_Revisions(origin,summary,package_hash,base_snapshot_hash,result_snapshot_hash)
           VALUES (?,?,?,?,?)""",
        (origin, review.data["summary"], review.package_hash, review.base_snapshot_hash, review.candidate_snapshot_hash),
    ).lastrowid
    for change in changes:
        before, after = change["before"], change["after"]
        conn.execute(
            """INSERT INTO Content_Changes
               (revision_id,table_name,entity_key,operation,before_json,after_json,before_hash,after_hash)
               VALUES (?,?,?,?,?,?,?,?)""",
            (revision_id, change["table"], _audit_key(change["table"], change["key"]), change["operation"],
             contract.canonical_json(before).strip() if before is not None else None,
             contract.canonical_json(after).strip() if after is not None else None,
             content_editing.row_hash(before) if before is not None else None,
             content_editing.row_hash(after) if after is not None else None),
        )
    return revision_id


def _checked_review_operations(review):
    operations = _checked_operations(review.operations)
    if operations != review.operations:
        raise ChangeError("This review is invalid. Run a new review.")
    if review.package_hash is not None:
        envelope = {"format": contract.FORMAT, "base_snapshot_sha256": review.base_snapshot_hash,
                    "summary": review.data["summary"], "operations": operations}
        summary = envelope["summary"]
        if not isinstance(summary, str) or not summary.strip() or summary != summary.strip() or len(summary) > 500:
            raise ChangeError("This package summary is invalid. Run a new review.")
        # The raw upload limit was checked before issuing the review. Added
        # normalized defaults may legitimately make the canonical envelope
        # larger; do not apply a second raw-upload limit to that representation.
        if contract.digest(contract.package_envelope(envelope)) != review.package_hash:
            raise ChangeError("This package no longer matches its review. Run a new review.")
    return operations


def apply_review(review, *, db_name=None):
    """Recheck an issued review under one lock, then commit content and audit once."""
    if not isinstance(review, ReviewResult) or review._issuer is not _REVIEW_ISSUER:
        raise ChangeError("A successful current review is required before Apply.")
    conn = None
    try:
        conn = database.get_db_connection() if db_name is None else sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        content_editing._require_initial_snapshot(conn)
        with _access_scope(conn):
            before_rows = _content_rows(conn)
            current_hash = content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(conn))
            if current_hash != review.base_snapshot_hash:
                raise StaleReviewError("Content changed since review. Export fresh context or prepare a new inverse review.")
            if local_review_guard(conn) != review.local_guard:
                raise StaleReviewError("Local state changed since review. Run a new dry run before Apply.")
            inverse_id = review.data["inverse_revision_id"]
            inverse = _read_audit(conn, inverse_id) if inverse_id is not None else None
            if inverse is not None:
                if inverse["source_hash"] != review.data["inverse_source_hash"] or inverse["changes"] != review.changes:
                    raise StaleReviewError("The revision audit changed since review. Prepare a new inverse review.")
                operations = _inverse_operations(inverse["changes"])
            else:
                operations = _checked_review_operations(review)
        with _access_scope(conn, CONTENT_TABLES | {"sqlite_sequence"}):
            applied = (materialize_operations(conn, operations) if inverse is None else
                       _materialize_inverse(conn, inverse["changes"]))
        with _access_scope(conn):
            _assert_exact_changes(conn, before_rows, applied)
            validate_candidate_content(conn, operations)
            _capture(conn, candidate=True)
            result_hash = content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(conn))
            if result_hash != review.candidate_snapshot_hash or applied != review.changes:
                raise ChangeError("The candidate differs from the reviewed result. Run a new review.")
        with _access_scope(conn, {"Content_Revisions", "Content_Changes", "sqlite_sequence"}, insert_only=True):
            revision_id = _record_changes(conn, review, applied)
        conn.commit()
        return revision_id
    except (ChangeError, contract.PackageError, content_editing.ContentEditError):
        if conn is not None:
            conn.rollback()
        raise
    except sqlite3.OperationalError as error:
        if conn is not None:
            conn.rollback()
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            raise ChangeError("Database is busy. Retry Apply; if state changed, run a new review.") from None
        raise ChangeError("Apply failed. No content or audit changes were saved.", local=str(error)) from None
    except Exception as error:
        if conn is not None:
            conn.rollback()
        raise ChangeError("Apply failed. No content or audit changes were saved.", local=str(error)) from None
    finally:
        if conn is not None:
            conn.close()
