"""Reviewed candidates, atomic content changes, and dependency-safe inverses."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import copy
import json
import re
import sqlite3

import change_packages as contract
import content_editing
import content_snapshot
import database
import editor_preview
import rendering
import quicktype
import consistency


_REVIEW_ISSUER = object()
CONTENT_TABLES = {*content_snapshot.BASE_TABLES, "Block_Fields", "Preset_Blocks"}


class ChangeError(content_editing.ContentEditError):
    """Fixed public rejection; optional details remain in the local session."""

    def __init__(self, message, *, local=None):
        self.local = local
        super().__init__(message)


class StaleReviewError(ChangeError):
    pass


class StaleDraftReviewError(ChangeError):
    """A guided draft assertion does not match the review's source snapshot."""


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


def _validated_case_rows(conn):
    """Return the complete, ordered input set for reconstruction analysis."""
    return [dict(row) for row in conn.execute(
        "SELECT * FROM Cases WHERE status='validated' ORDER BY id"
    )]


def local_review_guard(conn):
    """Bind local state used by review-only Case and identity analysis.

    Pending rows protect live-report impact and deletion eligibility.  The
    complete validated rows protect the signed strict-reconstruction warning:
    they are the exact Case inputs passed to ``render_saved_case`` and keeping
    the full rows also fails closed if that reconstruction path later consumes
    another Case column.
    """
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
        "validated": _validated_case_rows(conn),
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
                if kind == "update" and before and before["is_archived"]:
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
                if block is None or field is None or block["is_archived"] or field["is_archived"]:
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
                if (preset is None or block is None or preset["is_archived"]
                        or block["is_archived"] or block["is_table"]):
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
                         "display_order": key["sort_order"],
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
        if "table" not in op or op.get("op") in _GENERAL_ASSERTIONS:
            continue
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


def _general_review_warnings(conn, operations, before_validated, after_validated):
    """Warnings are part of the signed generalized-review payload."""
    active = [dict(row) for row in conn.execute("SELECT * FROM Presets WHERE is_archived=0")]
    token_sets = {
        preset["id"]: [
            {**dict(row), "lookup_table": json.loads(row["lookup_table"]) if row["lookup_table"] else None}
            for row in conn.execute("SELECT * FROM Quick_Type_Tokens WHERE preset_id=? ORDER BY sort_order", (preset["id"],))
        ] for preset in active
    }
    _, reachability_warnings = quicktype.prefix_reachability(active, token_sets)
    warnings = reachability_warnings
    duplicate_sources = {op["key"] for op in operations
                         if op.get("op") == _GENERAL_PRESET_ASSERTION and op.get("duplicate") is True}
    for operation in operations:
        if (operation.get("op") == "create" and operation.get("table") == "Presets" and duplicate_sources):
            # This is intentionally conservative: the guided duplicate path
            # is the only current Preset-create path carrying a Preset source
            # assertion, and its tokens are deliberately omitted.
            warnings.append(
                "Quick Type tokens are intentionally not copied to a duplicated Preset; only its bare shortcut is available."
            )
    return warnings + _validated_reconstruction_loss_warning(before_validated, after_validated)


def _configuration_review_evidence(before_rows, after_rows, operations, conn, *, owners=()):
    """Small, signed CP1 evidence for complete-set configuration drafts."""
    owners = list(dict.fromkeys(
        [(operation["kind"], operation["owner_key"]) for operation in operations
         if operation.get("op") == _GENERAL_CONFIGURATION_ASSERTION]
        + list(owners)
    ))

    def image(rows, kind, owner_key):
        if kind == "quick_type":
            preset = rows["Presets"].get(owner_key)
            if preset is None:
                return []
            result = []
            for row in rows["Quick_Type_Tokens"].values():
                if row["preset_id"] != preset["id"]:
                    continue
                result.append({"sort_order": row["sort_order"], "block_sort_order": row["block_sort_order"],
                               "field_key": row["field_key"], "token_kind": row["token_kind"],
                               "lookup_table": json.loads(row["lookup_table"]) if row["lookup_table"] else None,
                               "digit_width": row["digit_width"]})
            return sorted(result, key=lambda row: row["sort_order"])
        block = rows["Blocks"].get(owner_key)
        if block is None:
            return []
        result = []
        for row in rows["Field_Consistency_Rules"].values():
            if row["block_id"] != block["id"]:
                continue
            result.append({"field_a_key": row["field_a_key"], "field_a_values": json.loads(row["field_a_values"]),
                           "field_b_key": row["field_b_key"], "field_b_values": json.loads(row["field_b_values"]),
                           "message": row["message"]})
        return sorted(result, key=contract.canonical_json)

    active = [dict(row) for row in conn.execute("SELECT * FROM Presets WHERE is_archived=0")]
    token_sets = {
        preset["id"]: database.get_quick_type_tokens_on_connection(conn, preset["id"])
        for preset in active
    }
    errors, warnings = quicktype.prefix_reachability(active, token_sets)
    return {"before": [{"kind": kind, "owner_key": key, "configuration": image(before_rows, kind, key)}
                       for kind, key in owners],
            "after": [{"kind": kind, "owner_key": key, "configuration": image(after_rows, kind, key)}
                      for kind, key in owners],
            "findings": {"reachability_errors": errors, "reachability_warnings": warnings}}


def _validated_reconstructability(conn):
    """Whether each frozen validated Case could safely return to pending now.

    This intentionally uses the same strict reconstruction path as
    ``return_case_to_pending`` while preserving the validated artifact itself.
    A missing/deleted dependency makes the live draft unavailable, not the
    frozen report invalid.
    """
    result = {}
    for case in _validated_case_rows(conn):
        try:
            editor_preview.render_saved_case(conn, {**case, "status": "pending"}, strict=True)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            result[case["id"]] = False
        else:
            result[case["id"]] = True
    return result


def _validated_reconstruction_loss_warning(before, after):
    lost = [case_id for case_id, reconstructable in before.items()
            if reconstructable and not after.get(case_id, False)]
    if not lost:
        return []
    return [
        f"This reviewed change makes {len(lost)} currently reconstructable validated Case(s) "
        "unavailable for a future Return to pending. Frozen validated reports remain available."
    ]


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


def review_candidate(operations, base_hash, *, package_hash=None, summary="", db_name=None,
                     internal=False, evidence_factory=None):
    """One SQLite backup, one private memory candidate, no live write transaction."""
    if internal:
        return _review_generalized_candidate(operations, base_hash, summary=summary, db_name=db_name,
                                             evidence_factory=evidence_factory)
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
    "Preset_Blocks": {"preset_id", "block_id", "sort_order", "display_order", "field_overrides"},
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
                    legacy_columns = set(expected_columns)
                    if table in content_snapshot.BASE_TABLES:
                        legacy_columns.discard("is_archived")
                    elif table == "Preset_Blocks":
                        legacy_columns.discard("display_order")
                    if not isinstance(image, dict) or frozenset(image) not in {frozenset(expected_columns), frozenset(legacy_columns)}:
                        raise ValueError("Invalid audit image.")
                    # Stage 6 adds only non-rendering storage.  Old audited
                    # images retain their original hash, then receive the
                    # deterministic active/display defaults in memory so the
                    # existing inverse machinery can still compare physical
                    # rows after the additive migration.
                    legacy = set(image) == legacy_columns and legacy_columns != expected_columns
                    expected_hash_image = image
                    if legacy:
                        image = dict(image)
                        if table in content_snapshot.BASE_TABLES:
                            image["is_archived"] = 0
                        else:
                            image["display_order"] = image["sort_order"]
                    ids = [c for c in image if c == "id" or c.endswith("_id")]
                    if any(type(image[c]) is not int or image[c] <= 0 for c in ids):
                        raise ValueError("Invalid audit row ID.")
                    if table in content_snapshot.BASE_TABLES and image[content_snapshot.BASE_TABLES[table][0]] != key:
                        raise ValueError("Invalid audit stable key.")
                    if table == "Preset_Blocks" and image["sort_order"] != key["sort_order"]:
                        raise ValueError("Invalid audit position.")
                    if table == "Blocks" and image["is_table"]:
                        raise ValueError("Table Blocks cannot be reverted here.")
                if record[side + "_hash"] != (content_editing.row_hash(expected_hash_image) if image is not None else None):
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
            revision = candidate.execute("SELECT origin FROM Content_Revisions WHERE id=?", (revision_id,)).fetchone()
            if revision is not None and revision["origin"] in {"content_studio", "revision_revert"} and (
                revision["origin"] == "content_studio" or candidate.execute(
                    "SELECT 1 FROM Case_Content_Reference_Changes WHERE revision_id=?", (revision_id,)
                ).fetchone() or candidate.execute(
                    "SELECT 1 FROM Content_Changes WHERE revision_id=? AND table_name IN ('Preset_Block_Rows','Quick_Type_Tokens','Field_Consistency_Rules','Conclusion_Group_Labels')",
                    (revision_id,),
                ).fetchone()
            ):
                return _review_generalized_inverse(candidate, revision_id)
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
    if review.data.get("engine") == "generalized_v1":
        return _apply_generalized_review(review, db_name=db_name)
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


# Stage 6 internal candidate service ---------------------------------------
#
# The package path above deliberately remains constrained by change_packages.
# Guided authoring has a separate, stable-key grammar and records full physical
# rows.  Keeping this code beside the established review/apply machinery gives
# both sources the same copy, guard, report impact and transaction boundary.

_GENERAL_TABLES = tuple(content_snapshot.BASE_TABLES) + tuple(content_snapshot.RELATION_TABLES)
_GENERAL_RELATIONS = frozenset(content_snapshot.RELATION_TABLES)
_GENERAL_KEYS = {
    "Block_Fields": ("block_key", "field_key"),
    "Preset_Blocks": ("preset_code", "block_key", "sort_order"),
    "Preset_Block_Rows": ("preset_code", "block_key", "sort_order"),
    "Quick_Type_Tokens": ("preset_code", "sort_order"),
    "Field_Consistency_Rules": ("block_key", "field_a_key", "field_a_values", "field_b_key", "field_b_values", "message"),
    "Conclusion_Group_Labels": ("block_key_set",),
}
_GENERAL_ACTIONS = frozenset({"create", "update", "archive", "restore", "delete", "link", "unlink", "reorder"})
_GENERAL_ASSERTION = "assert_block_draft"
_GENERAL_FIELD_ASSERTION = "assert_field_endpoints"
_GENERAL_PRESET_ASSERTION = "assert_preset_draft"
_GENERAL_PRESET_ENDPOINT_ASSERTION = "assert_preset_endpoints"
_GENERAL_SOURCE_ASSERTION = "assert_source_draft"
_GENERAL_CONFIGURATION_ASSERTION = "assert_configuration_draft"
_GENERAL_ASSERTIONS = frozenset({_GENERAL_ASSERTION, _GENERAL_FIELD_ASSERTION,
                                 _GENERAL_PRESET_ASSERTION, _GENERAL_PRESET_ENDPOINT_ASSERTION,
                                 _GENERAL_SOURCE_ASSERTION, _GENERAL_CONFIGURATION_ASSERTION})
_GENERAL_PHYSICAL_COLUMNS = {
    "Fields": ("id", "key", "label", "type", "is_archived", "options", "default_value", "conclusion_addendum_template"),
    "Blocks": ("id", "key", "name", "is_archived", "is_table", "site_label", "conclusion_group", "macro_template", "micro_template", "conclusion_template", "context_template", "title_fragment_template", "conclusion_label_template"),
    "Presets": ("id", "short_code", "name", "is_archived", "category", "default_adicap", "default_title"),
    "Snippets": ("id", "shortcut", "expansion", "is_archived", "category"),
    "Block_Fields": ("block_id", "field_id", "sort_order", "label_override", "default_override", "context_section"),
    "Preset_Blocks": ("preset_id", "block_id", "sort_order", "display_order", "field_overrides"),
    "Preset_Block_Rows": ("id", "preset_id", "block_id", "sort_order", "field_overrides"),
    "Quick_Type_Tokens": ("id", "preset_id", "sort_order", "block_sort_order", "field_key", "token_kind", "lookup_table", "digit_width"),
    "Field_Consistency_Rules": ("id", "block_id", "field_a_key", "field_a_values", "field_b_key", "field_b_values", "message"),
    "Conclusion_Group_Labels": ("id", "block_key_set", "combined_label"),
}


def _general_key_columns(table):
    if table in content_snapshot.BASE_TABLES:
        return (content_snapshot.BASE_TABLES[table][0],)
    return _GENERAL_KEYS[table]


def _general_audit_key(table, key):
    return key if table in content_snapshot.BASE_TABLES else contract.canonical_json(key).strip()


def _general_columns(conn, table):
    # Current Stage 6 schema is additive and these are deliberately exact
    # audit images.  A static allowlist also keeps a candidate's restricted
    # SQLite authorizer from needing metadata PRAGMAs during Apply.
    return _GENERAL_PHYSICAL_COLUMNS[table]


def _general_row(conn, table, key):
    """Fetch a physical row from a stable key, including a config row's ID."""
    if table in content_snapshot.BASE_TABLES:
        row = conn.execute(f"SELECT * FROM {table} WHERE {_general_key_columns(table)[0]}=?", (key,)).fetchone()
        return dict(row) if row else None
    if not isinstance(key, dict) or set(key) != set(_general_key_columns(table)):
        raise ChangeError("Content Studio relationship key is invalid.")
    if table == "Block_Fields":
        row = conn.execute("""SELECT bf.* FROM Block_Fields bf JOIN Blocks b ON b.id=bf.block_id
                              JOIN Fields f ON f.id=bf.field_id
                              WHERE b.key=? AND f.key=?""", (key["block_key"], key["field_key"])).fetchone()
    elif table == "Preset_Blocks":
        row = conn.execute("""SELECT pb.* FROM Preset_Blocks pb JOIN Presets p ON p.id=pb.preset_id
                              JOIN Blocks b ON b.id=pb.block_id
                              WHERE p.short_code=? AND b.key=? AND pb.sort_order=?""",
                           (key["preset_code"], key["block_key"], key["sort_order"])).fetchone()
    elif table == "Preset_Block_Rows":
        row = conn.execute("""SELECT pbr.* FROM Preset_Block_Rows pbr JOIN Presets p ON p.id=pbr.preset_id
                              JOIN Blocks b ON b.id=pbr.block_id
                              WHERE p.short_code=? AND b.key=? AND pbr.sort_order=?""",
                           (key["preset_code"], key["block_key"], key["sort_order"])).fetchone()
    elif table == "Quick_Type_Tokens":
        row = conn.execute("""SELECT q.* FROM Quick_Type_Tokens q JOIN Presets p ON p.id=q.preset_id
                              WHERE p.short_code=? AND q.sort_order=?""",
                           (key["preset_code"], key["sort_order"])).fetchone()
    elif table == "Field_Consistency_Rules":
        row = conn.execute("""SELECT r.* FROM Field_Consistency_Rules r JOIN Blocks b ON b.id=r.block_id
                              WHERE b.key=? AND r.field_a_key=? AND r.field_a_values=?
                                AND r.field_b_key=? AND r.field_b_values=? AND r.message=?""",
                           (key["block_key"], key["field_a_key"], key["field_a_values"],
                            key["field_b_key"], key["field_b_values"], key["message"])).fetchone()
    else:
        row = conn.execute("SELECT * FROM Conclusion_Group_Labels WHERE block_key_set=?", (key["block_key_set"],)).fetchone()
    return dict(row) if row else None


def _general_rows(conn):
    """Physical rows keyed by stable content identity, for exact audit checks."""
    result = {}
    for table in _GENERAL_TABLES:
        rows = {}
        if table in content_snapshot.BASE_TABLES or table == "Conclusion_Group_Labels":
            direct = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for row in direct:
                key = row[content_snapshot.BASE_TABLES[table][0]] if table in content_snapshot.BASE_TABLES else {"block_key_set": row["block_key_set"]}
                rows[contract.canonical_json(key) if isinstance(key, dict) else key] = row
        elif table == "Block_Fields":
            for row in conn.execute("""SELECT bf.*,b.key block_key,f.key field_key FROM Block_Fields bf
                                      JOIN Blocks b ON b.id=bf.block_id JOIN Fields f ON f.id=bf.field_id"""):
                row = dict(row); key = {k: row[k] for k in _GENERAL_KEYS[table]}; rows[contract.canonical_json(key)] = {k: row[k] for k in _GENERAL_PHYSICAL_COLUMNS[table]}
        elif table == "Preset_Blocks":
            for row in conn.execute("""SELECT pb.*,p.short_code preset_code,b.key block_key FROM Preset_Blocks pb
                                      JOIN Presets p ON p.id=pb.preset_id JOIN Blocks b ON b.id=pb.block_id"""):
                row = dict(row); key = {k: row[k] for k in _GENERAL_KEYS[table]}; rows[contract.canonical_json(key)] = {k: row[k] for k in _GENERAL_PHYSICAL_COLUMNS[table]}
        elif table == "Preset_Block_Rows":
            for row in conn.execute("""SELECT pbr.*,p.short_code preset_code,b.key block_key FROM Preset_Block_Rows pbr
                                      JOIN Presets p ON p.id=pbr.preset_id JOIN Blocks b ON b.id=pbr.block_id"""):
                row = dict(row); key = {k: row[k] for k in _GENERAL_KEYS[table]}; rows[contract.canonical_json(key)] = {k: row[k] for k in _GENERAL_PHYSICAL_COLUMNS[table]}
        elif table == "Quick_Type_Tokens":
            for row in conn.execute("""SELECT q.*,p.short_code preset_code FROM Quick_Type_Tokens q
                                      JOIN Presets p ON p.id=q.preset_id"""):
                row = dict(row); key = {k: row[k] for k in _GENERAL_KEYS[table]}; rows[contract.canonical_json(key)] = {k: row[k] for k in _GENERAL_PHYSICAL_COLUMNS[table]}
        else:
            for row in conn.execute("""SELECT r.*,b.key block_key FROM Field_Consistency_Rules r
                                      JOIN Blocks b ON b.id=r.block_id"""):
                row = dict(row); key = {k: row[k] for k in _GENERAL_KEYS[table]}; rows[contract.canonical_json(key)] = {k: row[k] for k in _GENERAL_PHYSICAL_COLUMNS[table]}
        result[table] = rows
    return result


def _general_normalize(operations):
    if not isinstance(operations, list) or not operations or len(operations) > contract.MAX_OPERATIONS:
        raise ChangeError("Content Studio operations are invalid.")
    configuration_owners = {
        operation.get("owner_key") for operation in operations
        if isinstance(operation, dict) and operation.get("op") == _GENERAL_CONFIGURATION_ASSERTION
        and operation.get("kind") == "quick_type"
    }
    duplicate_rule_sources = {
        operation.get("key") for operation in operations
        if isinstance(operation, dict) and operation.get("op") == _GENERAL_ASSERTION
        and operation.get("table") == "Blocks" and isinstance(operation.get("key"), str)
        and operation.get("copied_rules_baseline") is not None
    }
    created_blocks = {
        operation.get("key") for operation in operations
        if isinstance(operation, dict) and operation.get("op") == "create"
        and operation.get("table") == "Blocks" and isinstance(operation.get("key"), str)
    }
    cleaned, targets = [], set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise ChangeError("Content Studio operations are invalid.")
        action, table, key = operation.get("op"), operation.get("table"), operation.get("key")
        if action == _GENERAL_ASSERTION:
            if (set(operation) != {"op", "table", "key", "baseline", "copied_rules_baseline"}
                    or table != "Blocks" or not isinstance(key, str) or not key
                    or not isinstance(operation["baseline"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", operation["baseline"])
                    or (operation["copied_rules_baseline"] is not None and (
                        not isinstance(operation["copied_rules_baseline"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", operation["copied_rules_baseline"])
                    ))):
                raise ChangeError("Content Studio Block draft assertion is invalid.")
            marker = (action, key)
            if marker in targets:
                raise ChangeError("Content Studio targets must be unique.")
            targets.add(marker)
            cleaned.append(dict(operation))
            continue
        if action == _GENERAL_FIELD_ASSERTION:
            field_keys = operation.get("field_keys")
            if (set(operation) != {"op", "field_keys", "baseline"}
                    or not isinstance(field_keys, list) or not field_keys
                    or any(not isinstance(field_key, str) or not field_key for field_key in field_keys)
                    or field_keys != sorted(set(field_keys))
                    or not isinstance(operation.get("baseline"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", operation["baseline"])):
                raise ChangeError("Content Studio Field endpoint assertion is invalid.")
            marker = (action, tuple(field_keys))
            if marker in targets:
                raise ChangeError("Content Studio targets must be unique.")
            targets.add(marker)
            cleaned.append({"op": action, "field_keys": list(field_keys),
                            "baseline": operation["baseline"]})
            continue
        if action == _GENERAL_PRESET_ASSERTION:
            if (set(operation) != {"op", "key", "baseline", "endpoint_baseline", "duplicate"}
                    or not isinstance(key, str) or not key
                    or not isinstance(operation["baseline"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", operation["baseline"])
                    or operation["endpoint_baseline"] is not None
                    or type(operation["duplicate"]) is not bool):
                raise ChangeError("Content Studio Preset draft assertion is invalid.")
            marker = (action, key)
            if marker in targets:
                raise ChangeError("Content Studio targets must be unique.")
            targets.add(marker); cleaned.append(dict(operation)); continue
        if action == _GENERAL_PRESET_ENDPOINT_ASSERTION:
            endpoints = operation.get("endpoints")
            if (set(operation) != {"op", "endpoints"}
                    or not isinstance(endpoints, list) or not endpoints
                    or any(not isinstance(item, dict) or set(item) != {"block_key", "instance_no", "baseline"}
                           or not isinstance(item["block_key"], str) or not item["block_key"]
                           or type(item["instance_no"]) is not int or not 0 <= item["instance_no"] < 1000
                           or not isinstance(item["baseline"], str)
                           or not re.fullmatch(r"[0-9a-f]{64}", item["baseline"])
                           for item in endpoints)
                    or len({(item["block_key"], item["instance_no"]) for item in endpoints}) != len(endpoints)):
                raise ChangeError("Content Studio Preset endpoint assertion is invalid.")
            marker = (action, tuple((item["block_key"], item["instance_no"]) for item in endpoints))
            if marker in targets:
                raise ChangeError("Content Studio targets must be unique.")
            targets.add(marker); cleaned.append({"op": action, "endpoints": copy.deepcopy(endpoints)}); continue
        if action == _GENERAL_SOURCE_ASSERTION:
            if (set(operation) != {"op", "table", "key", "baseline"}
                    or table not in {"Fields", "Snippets", "Conclusion_Group_Labels"}
                    or not isinstance(operation["baseline"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", operation["baseline"])):
                raise ChangeError("Content Studio source draft assertion is invalid.")
            key_columns = _general_key_columns(table)
            valid_key = (isinstance(key, str) and bool(key)) if table in content_snapshot.BASE_TABLES else (
                isinstance(key, dict) and set(key) == set(key_columns)
                and all(isinstance(value, str) and value for value in key.values())
            )
            if not valid_key:
                raise ChangeError("Content Studio source draft assertion is invalid.")
            marker = (action, table, _general_audit_key(table, key))
            if marker in targets:
                raise ChangeError("Content Studio targets must be unique.")
            targets.add(marker)
            cleaned.append({"op": action, "table": table,
                            "key": dict(key) if isinstance(key, dict) else key,
                            "baseline": operation["baseline"]})
            continue
        if action == _GENERAL_CONFIGURATION_ASSERTION:
            if (set(operation) != {"op", "kind", "owner_key", "baseline"}
                    or operation["kind"] not in {"quick_type", "consistency"}
                    or not isinstance(operation["owner_key"], str) or not operation["owner_key"]
                    or not isinstance(operation["baseline"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", operation["baseline"])):
                raise ChangeError("Content Studio configuration draft assertion is invalid.")
            marker = (action, operation["kind"], operation["owner_key"])
            if marker in targets:
                raise ChangeError("Content Studio targets must be unique.")
            targets.add(marker)
            cleaned.append(dict(operation))
            continue
        if action == "case_preset_reference":
            if set(operation) != {"op", "case_id", "before_preset_id", "after_preset_id"}:
                raise ChangeError("Content Studio Case reference is invalid.")
            if type(operation["case_id"]) is not int or operation["case_id"] <= 0:
                raise ChangeError("Content Studio Case reference is invalid.")
            if any(v is not None and (type(v) is not int or v <= 0)
                   for v in (operation["before_preset_id"], operation["after_preset_id"])):
                raise ChangeError("Content Studio Case reference is invalid.")
            marker = (action, operation["case_id"])
            if marker in targets:
                raise ChangeError("Content Studio targets must be unique.")
            targets.add(marker); cleaned.append(dict(operation)); continue
        if action not in _GENERAL_ACTIONS or table not in _GENERAL_TABLES:
            raise ChangeError("Content Studio operation is unsupported.")
        key_columns = _general_key_columns(table)
        if table in content_snapshot.BASE_TABLES:
            valid_key = isinstance(key, str) and bool(key)
        else:
            valid_key = isinstance(key, dict) and set(key) == set(key_columns) and all(
                isinstance(value, str) and value != "" for column, value in key.items()
                if not (column == "sort_order" and type(value) is int)
            )
        if not valid_key:
            raise ChangeError("Content Studio relationship key is invalid.")
        has_values = "values" in operation
        has_copy_source = "copy_source" in operation
        if action in {"create", "update", "link", "reorder"}:
            expected = {"op", "table", "key", "values"} | ({"copy_source"} if has_copy_source else set())
            if set(operation) != expected or not isinstance(operation["values"], dict):
                raise ChangeError("Content Studio operation values are invalid.")
        elif set(operation) != {"op", "table", "key"}:
            raise ChangeError("Content Studio operation is invalid.")
        if table in content_snapshot.BASE_TABLES and action in {"link", "unlink", "reorder"}:
            raise ChangeError("Content Studio operation is invalid.")
        if table in _GENERAL_RELATIONS and action in {"archive", "restore"}:
            raise ChangeError("Content Studio operation is invalid.")
        if has_copy_source:
            source = operation["copy_source"]
            if (action != "link" or table != "Field_Consistency_Rules"
                    or not isinstance(source, dict) or set(source) != {"block_key", "id"}
                    or not isinstance(source["block_key"], str) or not source["block_key"]
                    or type(source["id"]) is not int or source["id"] <= 0
                    or source["block_key"] == key["block_key"]
                    or source["block_key"] not in duplicate_rule_sources
                    or key["block_key"] not in created_blocks):
                raise ChangeError("Consistency-rule copy provenance is invalid.")
        marker = (table, _general_audit_key(table, key))
        # A complete token-set replacement deliberately unlinks then recreates
        # a stable position.  It is safe only under the signed complete-set
        # configuration assertion; other double-targeted intents remain
        # rejected.  This is needed for a unique position index and exact
        # reorder semantics without temporarily retargeting a token.
        prior = next((item for item in cleaned
                      if item.get("table") == table and _general_audit_key(table, item.get("key")) == marker[1]), None)
        if marker in targets:
            allowed_replacement = (table == "Quick_Type_Tokens" and key.get("preset_code") in configuration_owners
                                   and {action, prior.get("op") if prior else None} == {"link", "unlink"})
            if not allowed_replacement:
                raise ChangeError("Content Studio targets must be unique.")
        else:
            targets.add(marker)
        clean = {"op": action, "table": table, "key": dict(key) if isinstance(key, dict) else key}
        if has_values:
            clean["values"] = dict(operation["values"])
        if has_copy_source:
            clean["copy_source"] = dict(operation["copy_source"])
        cleaned.append(clean)
    # Makes review input order irrelevant while preserving source action data.
    def phase(operation):
        if operation["op"] in _GENERAL_ASSERTIONS: return -2
        if operation["op"] == "case_preset_reference": return -1
        if operation["op"] == "create": return 0 if operation["table"] in content_snapshot.BASE_TABLES else 3
        if operation["op"] in {"update", "archive", "restore", "reorder"}: return 1
        if operation["op"] in {"unlink", "delete"} and operation["table"] in _GENERAL_RELATIONS: return 2
        return 4
    return sorted(cleaned, key=lambda o: (phase(o), o.get("table", ""),
                                           contract.canonical_json(o.get("key", o.get("case_id")))))


def _general_endpoint_ids(conn, table, key):
    def base(name, stable):
        row = _fetch(conn, name, stable)
        if row is None:
            raise ChangeError("Content Studio relationship endpoint is unavailable.")
        return row["id"]
    if table == "Block_Fields":
        return {"block_id": base("Blocks", key["block_key"]), "field_id": base("Fields", key["field_key"])}
    if table in {"Preset_Blocks", "Preset_Block_Rows"}:
        return {"preset_id": base("Presets", key["preset_code"]), "block_id": base("Blocks", key["block_key"])}
    if table == "Quick_Type_Tokens":
        return {"preset_id": base("Presets", key["preset_code"])}
    if table == "Field_Consistency_Rules":
        return {"block_id": base("Blocks", key["block_key"])}
    return {}


def _general_insert_from_intent(conn, op):
    table, key, values = op["table"], op["key"], dict(op["values"])
    if table in content_snapshot.BASE_TABLES:
        values[content_snapshot.BASE_TABLES[table][0]] = key
        if table == "Blocks":
            values.setdefault("is_table", 0); values.setdefault("site_label", None); values.setdefault("conclusion_group", None)
        if table == "Presets":
            values.setdefault("category", None); values.setdefault("default_adicap", None); values.setdefault("default_title", None)
        if table == "Fields":
            values.setdefault("options", None); values.setdefault("conclusion_addendum_template", None)
        if table == "Snippets":
            values.setdefault("category", None)
        values.setdefault("is_archived", 0)
        if table == "Fields":
            if values["options"] is not None:
                values["options"] = contract.canonical_json(values["options"]).strip()
            values["default_value"] = _field_storage(values, values["default_value"], "content_studio.default", nullable_global=True)
        content_editing._validate_row(table, values)
    else:
        values.update(_general_endpoint_ids(conn, table, key))
        if table == "Block_Fields":
            values.setdefault("label_override", None); values.setdefault("default_override", None); values.setdefault("context_section", 0)
            block = conn.execute("SELECT is_archived,is_table FROM Blocks WHERE id=?", (values["block_id"],)).fetchone()
            field = conn.execute("SELECT * FROM Fields WHERE id=?", (values["field_id"],)).fetchone()
            if block is None or field is None or block["is_archived"] or block["is_table"]:
                raise ChangeError("Block Field endpoints must be active non-table content.")
            if values["label_override"] is not None and (not isinstance(values["label_override"], str)
                                                          or not values["label_override"].strip()):
                raise ChangeError("Block Field label override cannot be blank.")
            if type(values["context_section"]) is bool:
                values["context_section"] = int(values["context_section"])
            if values["context_section"] not in (0, 1):
                raise ChangeError("Block Field context-section state is invalid.")
            if type(values.get("sort_order")) is not int or values["sort_order"] < 0:
                raise ChangeError("Block Field order is invalid.")
            if values["default_override"] is not None:
                values["default_override"] = _field_storage(dict(field), values["default_override"], "content_studio.default_override")
        elif table == "Preset_Blocks":
            values.setdefault("display_order", key["sort_order"]); values.setdefault("field_overrides", "{}")
        elif table == "Preset_Block_Rows":
            values.setdefault("field_overrides", "{}")
        elif table == "Quick_Type_Tokens":
            owner = conn.execute("SELECT is_archived FROM Presets WHERE id=?", (values["preset_id"],)).fetchone()
            if owner is None or owner["is_archived"]:
                raise ChangeError("New Quick Type configuration requires an active Preset owner.")
        elif table == "Field_Consistency_Rules":
            owner = conn.execute("SELECT is_archived,is_table FROM Blocks WHERE id=?", (values["block_id"],)).fetchone()
            if owner is None or owner["is_archived"] or owner["is_table"]:
                raise ChangeError("New consistency configuration requires an active non-table Block owner.")
            rule_fields = {row["key"]: dict(row) for row in conn.execute(
                """SELECT f.* FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id
                   WHERE bf.block_id=?""", (values["block_id"],)
            )}
            try:
                canonical = consistency.canonicalize_rule({
                    "field_a_key": values["field_a_key"], "field_a_values": values["field_a_values"],
                    "field_b_key": values["field_b_key"], "field_b_values": values["field_b_values"],
                    "message": values["message"],
                }, rule_fields)
            except ValueError as error:
                raise ChangeError(str(error)) from None
            source = op.get("copy_source")
            if source is not None:
                source_row = conn.execute(
                    """SELECT r.* FROM Field_Consistency_Rules r JOIN Blocks b ON b.id=r.block_id
                       WHERE b.key=? AND r.id=?""", (source["block_key"], source["id"])
                ).fetchone()
                copied_columns = ("field_a_key", "field_a_values", "field_b_key", "field_b_values", "message")
                if source_row is None or any(values[name] != source_row[name] for name in copied_columns):
                    raise ChangeError("Consistency-rule copy source changed or is unavailable.")
                # The normalized intent grammar proved that this link belongs
                # to a signed Block duplicate.  Preserve its already-audited
                # storage image, while the canonical call above validates its
                # typed semantic meaning.
            else:
                values.update(canonical)
        elif table == "Conclusion_Group_Labels":
            values.setdefault("block_key_set", key["block_key_set"])
        for name in ("sort_order",):
            if name in key:
                values.setdefault(name, key[name])
        if table in {"Preset_Blocks", "Preset_Block_Rows"} and isinstance(values["field_overrides"], dict):
            values["field_overrides"] = contract.canonical_json(values["field_overrides"]).strip()
        if table == "Quick_Type_Tokens" and isinstance(values.get("lookup_table"), dict):
            values["lookup_table"] = contract.canonical_json(values["lookup_table"]).strip()
        if table == "Field_Consistency_Rules":
            for name in ("field_a_values", "field_b_values"):
                if isinstance(values.get(name), list):
                    values[name] = contract.canonical_json(values[name]).strip()
    allowed = set(_general_columns(conn, table)) - {"id"}
    if set(values) - allowed or set(values) != allowed:
        raise ChangeError("Content Studio create values are incomplete.")
    _insert(conn, table, values)


def _general_update_from_intent(conn, op):
    table, key, values = op["table"], op["key"], dict(op["values"])
    before = _general_row(conn, table, key)
    if before is None:
        raise ChangeError("Content Studio target is unavailable.")
    forbidden = {"id", "is_archived"} | ({content_snapshot.BASE_TABLES[table][0]} if table in content_snapshot.BASE_TABLES else set())
    if not values or set(values) & forbidden or set(values) - set(before):
        raise ChangeError("Content Studio update values are invalid.")
    if table == "Fields" and set(values) & {"type", "options"}:
        raise ChangeError("Field type and options require a separate reviewed migration.")
    if table == "Blocks" and "is_table" in values:
        raise ChangeError("Block table state is immutable.")
    if table == "Blocks" and before["is_table"]:
        raise ChangeError("Table Blocks are read-only in Content Studio.")
    if table == "Quick_Type_Tokens":
        owner = conn.execute("SELECT is_archived FROM Presets WHERE id=?", (before["preset_id"],)).fetchone()
        if owner is None or owner["is_archived"]:
            raise ChangeError("Modified Quick Type configuration requires an active Preset owner.")
    if table == "Field_Consistency_Rules":
        owner = conn.execute("SELECT is_archived,is_table FROM Blocks WHERE id=?", (before["block_id"],)).fetchone()
        if owner is None or owner["is_archived"] or owner["is_table"]:
            raise ChangeError("Modified consistency configuration requires an active non-table Block owner.")
    if table == "Preset_Blocks" and "sort_order" in values:
        raise ChangeError("Preset instance identity is immutable.")
    immutable_relation_columns = {
        "Block_Fields": {"block_id", "field_id"},
        "Preset_Blocks": {"preset_id", "block_id", "sort_order"},
        "Preset_Block_Rows": {"preset_id", "block_id", "sort_order"},
        "Quick_Type_Tokens": {"preset_id", "sort_order"},
        "Field_Consistency_Rules": {"block_id", "field_a_key", "field_a_values", "field_b_key", "field_b_values", "message"},
        "Conclusion_Group_Labels": {"block_key_set"},
    }
    if set(values) & immutable_relation_columns.get(table, set()):
        raise ChangeError("Relationship identity is immutable; unlink and create the replacement instead.")
    if before.get("is_archived") and table in content_snapshot.BASE_TABLES:
        raise ChangeError("Archived content must be restored before it can be edited.")
    if op["op"] == "reorder":
        allowed = {"sort_order"} if table == "Block_Fields" else {"display_order"} if table == "Preset_Blocks" else set()
        if set(values) != allowed:
            raise ChangeError("This Content Studio relationship cannot be reordered.")
    if table == "Block_Fields":
        block = conn.execute("SELECT is_archived,is_table FROM Blocks WHERE id=?", (before["block_id"],)).fetchone()
        field = conn.execute("SELECT * FROM Fields WHERE id=?", (before["field_id"],)).fetchone()
        if block is None or field is None or block["is_archived"] or block["is_table"]:
            raise ChangeError("Block Field endpoints must be active non-table content.")
        if "label_override" in values and values["label_override"] is not None and (
                not isinstance(values["label_override"], str) or not values["label_override"].strip()):
            raise ChangeError("Block Field label override cannot be blank.")
        if "context_section" in values:
            if type(values["context_section"]) is bool:
                values["context_section"] = int(values["context_section"])
            if values["context_section"] not in (0, 1):
                raise ChangeError("Block Field context-section state is invalid.")
        if "sort_order" in values and (type(values["sort_order"]) is not int or values["sort_order"] < 0):
            raise ChangeError("Block Field order is invalid.")
        if "default_override" in values and values["default_override"] is not None:
            values["default_override"] = _field_storage(dict(field), values["default_override"],
                                                          "content_studio.default_override")
    if table == "Fields" and "default_value" in values:
        candidate_field = {**before, **values}
        requested = values["default_value"]
        stored = _field_storage(candidate_field, requested, "content_studio.default", nullable_global=True)
        # Widgets expose native values, while historic SQLite storage retains
        # compatible spellings such as checkbox "0"/"1" and decimal "8".
        # Preserve that exact physical spelling when the native value did not
        # change, otherwise a label-only edit would create a fake default edit.
        try:
            unchanged_default = _native_stored(candidate_field, before["default_value"]) == requested
        except (TypeError, ValueError):
            unchanged_default = False
        values["default_value"] = before["default_value"] if unchanged_default else stored
    if all(before[name] == value for name, value in values.items()):
        raise ChangeError("Content Studio operation has no persisted change.")
    candidate = {**before, **values}
    if table in {"Preset_Blocks", "Preset_Block_Rows"} and isinstance(values.get("field_overrides"), dict):
        values["field_overrides"] = contract.canonical_json(values["field_overrides"]).strip()
    if table == "Quick_Type_Tokens" and isinstance(values.get("lookup_table"), dict):
        values["lookup_table"] = contract.canonical_json(values["lookup_table"]).strip()
    if table == "Field_Consistency_Rules":
        for name in ("field_a_values", "field_b_values"):
            if isinstance(values.get(name), list): values[name] = contract.canonical_json(values[name]).strip()
    if table in content_snapshot.BASE_TABLES:
        content_editing._validate_row(table, candidate, original=before)
    columns, target = _physical_target_general(conn, table, key)
    conn.execute(f"UPDATE {table} SET {', '.join(name + '=?' for name in values)} WHERE " +
                 " AND ".join(name + "=?" for name in columns), (*values.values(), *target))


def _physical_target_general(conn, table, key):
    row = _general_row(conn, table, key)
    if row is None:
        return (), None
    if "id" in row:
        return ("id",), (row["id"],)
    # Only the two old join tables have no surrogate key.
    if table == "Block_Fields":
        return ("block_id", "field_id"), (row["block_id"], row["field_id"])
    return ("preset_id", "block_id", "sort_order"), (row["preset_id"], row["block_id"], row["sort_order"])


def _general_delete_from_intent(conn, table, key):
    columns, values = _physical_target_general(conn, table, key)
    if values is None:
        raise ChangeError("Content Studio target is unavailable.")
    conn.execute(f"DELETE FROM {table} WHERE " + " AND ".join(name + "=?" for name in columns), values)


def _general_materialize(conn, operations):
    """Apply an internal intent list in deterministic FK-safe phases."""
    for op in operations:
        if op["op"] == "case_preset_reference" or op["op"] in _GENERAL_ASSERTIONS:
            continue
        action, table, key = op["op"], op["table"], op["key"]
        before = _general_row(conn, table, key)
        if action in {"create", "link"}:
            if before is not None:
                raise ChangeError("Content Studio target already exists.")
            _general_insert_from_intent(conn, op)
        elif action in {"update", "reorder"}:
            _general_update_from_intent(conn, op)
        elif action in {"archive", "restore"}:
            if table not in content_snapshot.BASE_TABLES or before is None:
                raise ChangeError("Content Studio archive target is unavailable.")
            value = 1 if action == "archive" else 0
            if before["is_archived"] == value:
                raise ChangeError("Content Studio archive state is unchanged.")
            conn.execute(f"UPDATE {table} SET is_archived=? WHERE id=?", (value, before["id"]))
        else:  # unlink/delete
            if before is None:
                raise ChangeError("Content Studio target is unavailable.")
            _general_delete_from_intent(conn, table, key)


def _general_changes(before, after):
    changes = []
    for table in _GENERAL_TABLES:
        for encoded in sorted(set(before[table]) | set(after[table])):
            old, new = before[table].get(encoded), after[table].get(encoded)
            if old == new:
                continue
            key = (old or new)
            if table in content_snapshot.BASE_TABLES:
                key = key[content_snapshot.BASE_TABLES[table][0]]
            else:
                # The stable key is carried in the encoded index; it is never
                # inferred from mutable physical row columns.
                key = json.loads(encoded)
            changes.append({"table": table, "key": key,
                            "operation": "create" if old is None else "delete" if new is None else "update",
                            "before": old, "after": new})
    return changes


def _same_general_changes(left, right):
    """Compare exact rows while allowing an inverse's audit verb to be `revert`."""
    return [{k: value for k, value in change.items() if k != "operation"} for change in left] == [
        {k: value for k, value in change.items() if k != "operation"} for change in right
    ]


def _validate_general_configuration(conn):
    """Validate the complete final configuration, not just changed rows.

    Internal cleanup may remove a row that another configuration still names.
    SQLite foreign keys cannot express every one of those logical ownership
    relationships, so this is deliberately candidate-wide.
    """
    # Active orphan/ad-hoc Blocks are eligible for new work just like Blocks
    # reached through a Preset.  Validate their availability graph globally;
    # checking only active Presets would allow a future ad-hoc composition to
    # expose archived Fields or Snippets.
    for block in conn.execute("SELECT id FROM Blocks WHERE is_archived=0"):
        inactive_fields = conn.execute(
            """SELECT 1 FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id
               WHERE bf.block_id=? AND f.is_archived=1 LIMIT 1""", (block["id"],)
        ).fetchone()
        template_sources = [row[0] for row in conn.execute(
            """SELECT macro_template FROM Blocks WHERE id=?
               UNION ALL SELECT micro_template FROM Blocks WHERE id=?
               UNION ALL SELECT conclusion_template FROM Blocks WHERE id=?
               UNION ALL SELECT context_template FROM Blocks WHERE id=?
               UNION ALL SELECT title_fragment_template FROM Blocks WHERE id=?
               UNION ALL SELECT conclusion_label_template FROM Blocks WHERE id=?""",
            (block["id"],) * 6,
        )]
        template_sources.extend(row[0] for row in conn.execute(
            """SELECT f.conclusion_addendum_template FROM Block_Fields bf
               JOIN Fields f ON f.id=bf.field_id WHERE bf.block_id=?""", (block["id"],)
        ))
        archived_snippets = database._snippet_shortcuts(template_sources)
        if inactive_fields or any(
            row["is_archived"] for shortcut in archived_snippets
            if (row := conn.execute("SELECT is_archived FROM Snippets WHERE shortcut=?", (shortcut,)).fetchone())
        ):
            raise ChangeError("An active Block cannot resolve archived Fields or Snippets.")

    preset_blocks = {}
    for preset in conn.execute("SELECT id FROM Presets WHERE is_archived=0"):
        links = [dict(row) for row in conn.execute(
            """SELECT pb.*, b.is_table, b.is_archived AS block_is_archived FROM Preset_Blocks pb JOIN Blocks b ON b.id=pb.block_id
               WHERE pb.preset_id=? ORDER BY pb.sort_order""", (preset["id"],)
        )]
        if not links:
            raise ChangeError("A Preset must retain at least one Block.")
        if any(link["block_is_archived"] for link in links):
            raise ChangeError("An active Preset cannot resolve archived Blocks.")
        positions = [link["sort_order"] for link in links]
        display = [link["display_order"] for link in links]
        # These are instance identifiers / display positions, not list array
        # indexes.  Stage 5 packages and migrated databases legitimately use
        # sparse positions (for example 999), so require uniqueness only.
        if len(set(positions)) != len(positions) or len(set(display)) != len(display):
            raise ChangeError("Preset Block instance or display order is invalid.")
        preset_blocks[preset["id"]] = {link["sort_order"]: link for link in links}
        for block in database.get_preset_blocks_on_connection(conn, preset["id"]):
            _check_widget_defaults(block)

    active_presets = [dict(row) for row in conn.execute("SELECT * FROM Presets WHERE is_archived=0")]
    token_sets = {}
    for preset in active_presets:
        tokens = []
        for token in conn.execute("SELECT * FROM Quick_Type_Tokens WHERE preset_id=? ORDER BY sort_order", (preset["id"],)):
            token = dict(token)
            token["lookup_table"] = json.loads(token["lookup_table"]) if token["lookup_table"] else None
            target = preset_blocks[preset["id"]].get(token["block_sort_order"])
            if target is None or target["is_table"]:
                raise ChangeError("Quick Type token targets an unavailable Block instance.")
            field = conn.execute(
                """SELECT f.* FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id
                   WHERE bf.block_id=? AND f.key=?""", (target["block_id"], token["field_key"])
            ).fetchone()
            if field is None or field["is_archived"]:
                raise ChangeError("Quick Type token targets an unavailable Field.")
            field = dict(field)
            if token["token_kind"] == "measurement":
                if field["type"] not in {"number", "decimal"}:
                    raise ChangeError("Quick Type measurement must target a numeric Field.")
                if token["digit_width"] is not None and (type(token["digit_width"]) is not int or token["digit_width"] <= 0):
                    raise ChangeError("Quick Type digit width is invalid.")
            elif token["token_kind"] == "lookup":
                if token["lookup_table"] is None:
                    raise ChangeError("Quick Type lookup table is unavailable.")
                for value in token["lookup_table"].values():
                    contract.field_value(field, value)
            tokens.append(token)
        quicktype.validate_quick_type_config(tokens)
        token_sets[preset["id"]] = tokens
    reachability_errors, _ = quicktype.prefix_reachability(active_presets, token_sets)
    if reachability_errors:
        raise ChangeError(reachability_errors[0])

    rows_seen = set()
    for row in conn.execute("""SELECT pbr.*, b.is_table, p.is_archived AS preset_is_archived,
                                      EXISTS(SELECT 1 FROM Preset_Blocks pb
                                             WHERE pb.preset_id=pbr.preset_id AND pb.block_id=pbr.block_id)
                                             AS has_owner
                               FROM Preset_Block_Rows pbr
                               JOIN Blocks b ON b.id=pbr.block_id
                               JOIN Presets p ON p.id=pbr.preset_id"""):
        row = dict(row)
        if row["preset_is_archived"]:
            continue
        identity = (row["preset_id"], row["block_id"], row["sort_order"])
        if identity in rows_seen or not row["is_table"] or not row["has_owner"]:
            raise ChangeError("Table row ownership is invalid.")
        rows_seen.add(identity)
        overrides = json.loads(row["field_overrides"] or "{}")
        if not isinstance(overrides, dict):
            raise ChangeError("Table row overrides are invalid.")
        fields = [dict(field) for field in conn.execute(
            """SELECT f.*, bf.default_override FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id
               WHERE bf.block_id=?""", (row["block_id"],)
        )]
        by_key = {field["key"]: field for field in fields}
        if set(overrides) - set(by_key):
            raise ChangeError("Table row override targets an unavailable Field.")
        for key, field in by_key.items():
            value = overrides.get(key, field["default_override"] if field["default_override"] is not None else field["default_value"])
            if value is None and field["type"] in {"number", "select", "checkbox"}:
                raise ChangeError("Table row has no effective Field default.")
            contract.field_value(field, _native_stored(field, value))

    for block in conn.execute("SELECT id FROM Blocks WHERE is_archived=0"):
        block_row = dict(conn.execute("SELECT * FROM Blocks WHERE id=?", (block["id"],)).fetchone())
        bindings = [dict(row) for row in conn.execute("""SELECT f.key, bf.sort_order FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id
                                                        WHERE bf.block_id=?""", (block["id"],))]
        fields = {row["key"] for row in bindings}
        if len({row["sort_order"] for row in bindings}) != len(bindings):
            raise ChangeError("Block Field order is invalid.")
        if not block_row["is_table"]:
            context_fields = [dict(row) for row in conn.execute(
                """SELECT f.key,f.type,bf.context_section FROM Block_Fields bf
                   JOIN Fields f ON f.id=bf.field_id WHERE bf.block_id=?""", (block["id"],)
            )]
            context_keys = {row["key"] for row in context_fields if row["context_section"]}
            context_aliases = context_keys | {
                f"{row['key']}_display" for row in context_fields
                if row["context_section"] and row["type"] == "decimal"
            }
            if "fragments" in context_keys:
                context_aliases.add("fragment_text")
            for column in ("context_template", "title_fragment_template"):
                source = block_row[column]
                if source:
                    variables, _ = content_editing._template_variables(source)
                    if variables - context_aliases - {"snippet"}:
                        raise ChangeError("Context and title templates may use only context-section Fields.")
        rule_fields = {row["key"]: dict(row) for row in conn.execute(
            """SELECT f.* FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id
               WHERE bf.block_id=?""", (block["id"],)
        )}
        rules = []
        for rule in conn.execute("SELECT * FROM Field_Consistency_Rules WHERE block_id=?", (block["id"],)):
            stored = dict(rule)
            rule = {"field_a_key": stored["field_a_key"], "field_a_values": json.loads(stored["field_a_values"]),
                    "field_b_key": stored["field_b_key"], "field_b_values": json.loads(stored["field_b_values"]),
                    "message": stored["message"]}
            for key, values in ((rule["field_a_key"], rule["field_a_values"]),
                                (rule["field_b_key"], rule["field_b_values"])):
                field = rule_fields.get(key)
                if field is None or field["is_archived"]:
                    raise ChangeError("Consistency rule Field is unavailable.")
                for value in values:
                    contract.field_value(field, value)
            rules.append(rule)
        try:
            consistency.canonicalize_rules(rule_fields, rules)
        except ValueError as error:
            raise ChangeError(str(error)) from None
    for label in conn.execute("SELECT block_key_set FROM Conclusion_Group_Labels"):
        keys = label["block_key_set"].split(",")
        if keys != sorted(set(keys)) or any(not _fetch(conn, "Blocks", key) for key in keys):
            raise ChangeError("Conclusion group label configuration is invalid.")


def _validate_general_final_graph(conn):
    try:
        content_snapshot.validate_content_snapshot(content_snapshot.snapshot_from_connection(conn))
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ChangeError("Candidate graph has invalid foreign-key relationships.")
        _validate_stored_field_configuration(conn)
        _validate_general_configuration(conn)
        content_editing.validate_content_templates(conn)
        content_editing.validate_standalone_content(conn)
        content_editing._validate_discrete_branches(conn)
    except contract.PackageError as error:
        raise ChangeError("Content Studio candidate graph is invalid.", local=error.local) from None


def _general_case_references(conn, operations):
    """Allow only the complete detachments accompanying one Preset deletion."""
    result = []
    deleted_presets = {}
    for op in operations:
        if op.get("op") == "delete" and op.get("table") == "Presets":
            preset = _fetch(conn, "Presets", op["key"])
            if preset is not None:
                deleted_presets[preset["id"]] = op["key"]
    for op in operations:
        if op["op"] != "case_preset_reference":
            continue
        before, after = op["before_preset_id"], op["after_preset_id"]
        if before is None or after is not None or before not in deleted_presets:
            raise ChangeError("Validated Case references may only detach for a reviewed Preset deletion.")
        preset = _fetch(conn, "Presets", deleted_presets[before])
        row = conn.execute("""SELECT id,preset_id,status,preset_short_code_snapshot
                              FROM Cases WHERE id=?""", (op["case_id"],)).fetchone()
        if (preset is None or row is None or row["preset_id"] != before or row["status"] != "validated"
                or row["preset_short_code_snapshot"] != preset["short_code"]):
            raise ChangeError("Validated Case reference changed since review.")
        result.append({"case_id": op["case_id"], "reference_kind": "preset_id",
                       "before_preset_id": before, "before_preset_key": preset["short_code"],
                       "before_preset_name": preset["name"],
                       "after_preset_id": after, "after_preset_key": None,
                       "after_preset_name": None})
    for preset_id in deleted_presets:
        expected = {row["id"] for row in conn.execute(
            "SELECT id FROM Cases WHERE status='validated' AND preset_id=?", (preset_id,)
        )}
        actual = {ref["case_id"] for ref in result if ref["before_preset_id"] == preset_id}
        if actual != expected:
            raise ChangeError("Every validated Case using a deleted Preset must detach in the same review.")
    return result


def _general_apply_case_references(conn, references, *, attaching, inverse=False):
    for ref in references:
        # Detach before a possible Preset deletion; attach only after a
        # possible inverse recreation.  The two phases must not be reversed.
        if (ref["after_preset_id"] is not None) != attaching:
            continue
        _validate_general_case_reference(conn, ref, inverse=inverse)
        conn.execute("UPDATE Cases SET preset_id=? WHERE id=? AND preset_id IS ?",
                     (ref["after_preset_id"], ref["case_id"], ref["before_preset_id"]))
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise ChangeError("Validated Case reference changed since review.")


def _validate_general_case_reference(conn, ref, *, inverse=False):
    """Bind a Case reference to its frozen Preset identity, not merely an ID.

    The Case snapshots are the only Case identity data used here.  They let an
    inverse reject a substituted Preset record even if another Preset in the
    same audited revision happens to have a valid ID/key pair.
    """
    row = conn.execute("""SELECT preset_id,status,preset_short_code_snapshot
                          FROM Cases WHERE id=?""", (ref["case_id"],)).fetchone()
    if row is None or row["preset_id"] != ref["before_preset_id"] or row["status"] != "validated":
        raise ChangeError("Revert refused: a validated Case reference changed." if inverse
                          else "Validated Case reference changed since review.")
    identity = "before" if ref["before_preset_id"] is not None else "after"
    preset_id = ref[identity + "_preset_id"]
    preset_key = ref[identity + "_preset_key"]
    if (preset_id is None or not isinstance(preset_key, str)
            or row["preset_short_code_snapshot"] != preset_key):
        raise ChangeError("Revert refused: a validated Case reference identity changed." if inverse
                          else "Validated Case reference changed since review.")
    if ref["after_preset_id"] is not None:
        endpoint = conn.execute("SELECT short_code FROM Presets WHERE id=?", (ref["after_preset_id"],)).fetchone()
        if endpoint is None or endpoint["short_code"] != ref["after_preset_key"]:
            raise ChangeError("Revert refused: a validated Case reference endpoint is unavailable." if inverse
                              else "Validated Case reference endpoint is unavailable.")


def _general_pending_instances(conn, case):
    structured = json.loads(case["structured_input"] or "{}")
    instances = structured.get("block_instances")
    if instances is None:
        instances = [dict(row) for row in conn.execute(
            "SELECT block_id, sort_order AS instance_no FROM Preset_Blocks WHERE preset_id=?",
            (case["preset_id"],),
        )]
    return instances


def _check_general_pending_removals(conn, changes):
    """Refuse an inverse that would remove content a current draft uses."""
    removals = [change for change in changes if change["after"] is None]
    if not removals:
        return
    for case in conn.execute("SELECT id,preset_id,structured_input FROM Cases WHERE status='pending'"):
        try:
            instances = _general_pending_instances(conn, case)
            block_ids = {instance["block_id"] for instance in instances}
            links = [dict(row) for block_id in block_ids for row in conn.execute(
                "SELECT * FROM Block_Fields WHERE block_id=?", (block_id,)
            )]
            field_ids = {link["field_id"] for link in links}
            templates = [row[column] for block_id in block_ids for row in conn.execute(
                "SELECT * FROM Blocks WHERE id=?", (block_id,)
            ) for column in content_editing.BLOCK_TEMPLATE_COLUMNS]
            templates.extend(row[0] for field_id in field_ids for row in conn.execute(
                "SELECT conclusion_addendum_template FROM Fields WHERE id=?", (field_id,)
            ))
            snippets = database._snippet_shortcuts(templates)
            for change in removals:
                table, image = change["table"], change["before"]
                needed = (
                    (table == "Presets" and image["id"] == case["preset_id"])
                    or (table == "Blocks" and image["id"] in block_ids)
                    or (table == "Fields" and image["id"] in field_ids)
                    or (table == "Snippets" and change["key"] in snippets)
                    or (table == "Block_Fields" and image["block_id"] in block_ids)
                    or (table == "Preset_Blocks" and image["preset_id"] == case["preset_id"] and any(
                        instance["block_id"] == image["block_id"] and instance.get("instance_no") == image["sort_order"]
                        for instance in instances
                    ))
                    or (table == "Preset_Block_Rows" and image["preset_id"] == case["preset_id"] and image["block_id"] in block_ids)
                )
                if needed:
                    raise ChangeError("Revert refused: a pending case depends on content being removed.",
                                      local={"case_id": case["id"], "table": table})
        except ChangeError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ChangeError("Revert refused: a pending case cannot be checked safely.",
                              local={"case_id": case["id"]}) from None


def _general_pending_impact(before_pending, after_pending):
    return [{"id": case_id, "case_number": after["case_number"],
             "before_label": "Current before this change", "after_label": "Candidate after this change",
             "saved_label": "Last saved report", "saved_html": after["saved_html"],
             "already_stale": before_pending[case_id].get("already_stale"),
             "before": before_pending[case_id], "after": after}
            for case_id, after in after_pending.items()
            if before_pending[case_id].get("fingerprint") != after["fingerprint"]
             or "error" in before_pending[case_id]]


def _validate_general_assertions(conn, operations):
    """Bind guided draft operations to this exact review/apply source."""
    import content_studio
    for operation in operations:
        try:
            if operation["op"] == _GENERAL_ASSERTION:
                content_studio._assert_block_draft_baseline(
                    conn, operation["key"], operation["baseline"],
                    operation["copied_rules_baseline"],
                )
            elif operation["op"] == _GENERAL_FIELD_ASSERTION:
                content_studio._assert_field_endpoints(
                    conn, operation["field_keys"], operation["baseline"]
                )
            elif operation["op"] == _GENERAL_PRESET_ASSERTION:
                content_studio._assert_preset_draft_baseline(
                    conn, operation["key"], operation["baseline"], operation["endpoint_baseline"]
                )
            elif operation["op"] == _GENERAL_PRESET_ENDPOINT_ASSERTION:
                content_studio._assert_preset_endpoints_baseline(
                    conn, operation["endpoints"]
                )
            elif operation["op"] == _GENERAL_SOURCE_ASSERTION:
                content_studio._assert_source_draft_baseline(
                    conn, operation["table"], operation["key"], operation["baseline"]
                )
            elif operation["op"] == _GENERAL_CONFIGURATION_ASSERTION:
                content_studio._assert_configuration_draft_baseline(
                    conn, operation["kind"], operation["owner_key"], operation["baseline"]
                )
        except (content_studio.StaleBlockDraftError, content_studio.StalePresetDraftError,
                content_studio.StaleSourceDraftError) as error:
            raise StaleDraftReviewError(str(error)) from None


def _review_generalized_candidate(operations, base_hash, *, summary="", db_name=None,
                                  evidence_factory=None):
    operations = _general_normalize(operations)
    try:
        with _candidate_copy(db_name) as candidate:
            # Check source-bound draft guards on the review's own immutable
            # database image before the broader snapshot check. This both
            # identifies a changed Block precisely and covers a write racing
            # between the caller's snapshot export and this candidate copy.
            with _access_scope(candidate):
                _validate_general_assertions(candidate, operations)
            if content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(candidate)) != base_hash:
                raise contract.PackageError("stale")
            guard = local_review_guard(candidate)
            # Lifecycle intents are expanded on the private candidate before
            # anything is written: archive has its upward closure, restore
            # includes required archived dependencies, and deletion lists its
            # mechanical cleanup/detachments.  This is deliberately enforced
            # below the future UI layer as well as exposed by content_studio.
            import content_studio
            operations = _general_normalize(content_studio.expand_lifecycle_operations(operations, candidate))
            with _access_scope(candidate):
                before_rows = _general_rows(candidate)
                before_presets, before_pending = _capture(candidate)
                before_validated = _validated_reconstructability(candidate)
            refs = _general_case_references(candidate, operations)
            with _access_scope(candidate, {"Cases"}):
                _general_apply_case_references(candidate, refs, attaching=False)
            with _access_scope(candidate, set(_GENERAL_TABLES) | {"sqlite_sequence"}):
                _general_materialize(candidate, operations)
            with _access_scope(candidate, {"Cases"}):
                _general_apply_case_references(candidate, refs, attaching=True)
            with _access_scope(candidate):
                after_rows = _general_rows(candidate)
                changes = _general_changes(before_rows, after_rows)
                if not changes and not refs:
                    raise ChangeError("Content Studio operation has no persisted change.")
                _validate_general_final_graph(candidate)
                after_presets, after_pending = _capture(candidate, candidate=True)
                after_validated = _validated_reconstructability(candidate)
                standalone = _standalone_previews(candidate, operations)
                result_hash = content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(candidate))
            presets = []
            for code in sorted(set(before_presets) | set(after_presets)):
                before, after = before_presets.get(code), after_presets.get(code)
                presets.append({"code": code, "affected": before != after, "added": before is None,
                                "removed": after is None,
                                "output_changed": (before or {}).get("report") != (after or {}).get("report"),
                                "before": before, "after": after})
            pending = _general_pending_impact(before_pending, after_pending)
            evidence = evidence_factory(candidate) if evidence_factory is not None else None
            # Evidence is calculated only after final-graph validation on the
            # private candidate and is serialized into the issued review.
            # It is therefore frozen alongside the exact operations it shows.
            if evidence is not None and not isinstance(evidence, dict):
                raise ChangeError("Content Studio review evidence is invalid.")
            payload = {"engine": "generalized_v1", "summary": summary, "operations": operations,
                       "changes": changes, "case_references": refs, "presets": presets,
                       "configuration_review": _configuration_review_evidence(before_rows, after_rows, operations, candidate),
                       "unaffected_presets": sum(not item["affected"] for item in presets),
                       "pending_cases": pending, "validated_pending_count": len(after_pending),
                       "warnings": _general_review_warnings(candidate, operations, before_validated, after_validated), "branch_warnings": [], "standalone": standalone, "before_standalone": [],
                       "inverse_revision_id": None, "inverse_source_hash": None,
                       "guided_evidence": evidence}
            return _issued_review(None, base_hash, result_hash, guard, contract.canonical_json(payload))
    except (ChangeError, contract.PackageError):
        raise
    except Exception as error:
        if error.__class__.__name__ == "StudioIntentError":
            raise ChangeError(str(error)) from None
        raise ChangeError("Content Studio candidate could not be prepared.", local=str(error)) from None


def _record_generalized_changes(conn, review, changes, references, *, origin="content_studio"):
    revision_id = conn.execute("""INSERT INTO Content_Revisions(origin,summary,package_hash,base_snapshot_hash,result_snapshot_hash)
                                  VALUES (?,?,?,?,?)""",
                               (origin, review.data["summary"], None, review.base_snapshot_hash,
                                review.candidate_snapshot_hash)).lastrowid
    for change in changes:
        before, after = change["before"], change["after"]
        conn.execute("""INSERT INTO Content_Changes(revision_id,table_name,entity_key,operation,before_json,after_json,before_hash,after_hash)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (revision_id, change["table"], _general_audit_key(change["table"], change["key"]), change["operation"],
                      contract.canonical_json(before).strip() if before is not None else None,
                      contract.canonical_json(after).strip() if after is not None else None,
                      content_editing.row_hash(before) if before is not None else None,
                      content_editing.row_hash(after) if after is not None else None))
    for ref in references:
        conn.execute("""INSERT INTO Case_Content_Reference_Changes
                        (revision_id,case_id,reference_kind,before_preset_id,before_preset_key,after_preset_id,after_preset_key)
                        VALUES (?,?,?,?,?,?,?)""",
                     (revision_id, ref["case_id"], ref["reference_kind"], ref["before_preset_id"],
                      ref["before_preset_key"], ref["after_preset_id"], ref["after_preset_key"]))
    return revision_id


def _apply_generalized_review(review, *, db_name=None):
    conn = None
    try:
        conn = database.get_db_connection() if db_name is None else sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON"); conn.execute("BEGIN IMMEDIATE")
        content_editing._require_initial_snapshot(conn)
        with _access_scope(conn):
            if content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(conn)) != review.base_snapshot_hash:
                raise StaleReviewError("Content changed since review. Prepare a new review.")
            if local_review_guard(conn) != review.local_guard:
                raise StaleReviewError("Local state changed since review. Prepare a new review.")
            before_rows = _general_rows(conn)
            inverse_id = review.data["inverse_revision_id"]
            if inverse_id is None:
                operations = _general_normalize(review.operations)
                if operations != review.operations:
                    raise ChangeError("This review is invalid. Run a new review.")
                import content_studio
                if _general_normalize(content_studio.expand_lifecycle_operations(operations, conn)) != operations:
                    raise ChangeError("This review is invalid. Run a new review.")
                _validate_general_assertions(conn, operations)
                refs = _general_case_references(conn, operations)
            else:
                source = _read_generalized_audit(conn, inverse_id)
                if source["source_hash"] != review.data["inverse_source_hash"] or source["changes"] != review.changes:
                    raise StaleReviewError("The revision audit changed since review. Prepare a new inverse review.")
                _check_general_pending_removals(conn, source["changes"])
                operations, refs = None, source["references"]
        if operations is None:
            with _access_scope(conn, {"Cases"}):
                _general_apply_case_references(conn, refs, attaching=False, inverse=True)
            with _access_scope(conn, set(_GENERAL_TABLES) | {"sqlite_sequence"}):
                _general_apply_images(conn, review.changes)
            with _access_scope(conn, {"Cases"}):
                _general_apply_case_references(conn, refs, attaching=True, inverse=True)
        else:
            with _access_scope(conn, {"Cases"}):
                _general_apply_case_references(conn, refs, attaching=False)
            with _access_scope(conn, set(_GENERAL_TABLES) | {"sqlite_sequence"}):
                _general_materialize(conn, operations)
            with _access_scope(conn, {"Cases"}):
                _general_apply_case_references(conn, refs, attaching=True)
        with _access_scope(conn):
            applied = _general_changes(before_rows, _general_rows(conn))
            if not _same_general_changes(applied, review.changes) or refs != review.data["case_references"]:
                raise ChangeError("The candidate differs from the reviewed result. Run a new review.")
            _validate_general_final_graph(conn); _capture(conn, candidate=True)
            if content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(conn)) != review.candidate_snapshot_hash:
                raise ChangeError("The candidate differs from the reviewed result. Run a new review.")
        with _access_scope(conn, {"Content_Revisions", "Content_Changes", "Case_Content_Reference_Changes", "sqlite_sequence"}, insert_only=True):
            audit_changes = review.changes if review.data["inverse_revision_id"] is not None else applied
            revision_id = _record_generalized_changes(conn, review, audit_changes, refs,
                                                       origin="revision_revert" if review.data["inverse_revision_id"] is not None else "content_studio")
        conn.commit(); return revision_id
    except (ChangeError, contract.PackageError, content_editing.ContentEditError):
        if conn is not None: conn.rollback()
        raise
    except Exception as error:
        if conn is not None: conn.rollback()
        raise ChangeError("Apply failed. No content or audit changes were saved.", local=str(error)) from None
    finally:
        if conn is not None: conn.close()


def _read_generalized_audit(conn, revision_id):
    revision = conn.execute("SELECT * FROM Content_Revisions WHERE id=?", (revision_id,)).fetchone()
    records = [dict(row) for row in conn.execute("SELECT * FROM Content_Changes WHERE revision_id=? ORDER BY id", (revision_id,))]
    refs = [dict(row) for row in conn.execute("SELECT * FROM Case_Content_Reference_Changes WHERE revision_id=? ORDER BY id", (revision_id,))]
    if revision is None or (not records and not refs):
        raise ChangeError("That revision has no reversible content changes.")
    changes, seen = [], set()
    try:
        for record in records:
            table = record["table_name"]
            if table not in _GENERAL_TABLES:
                raise ValueError("unsupported table")
            key = record["entity_key"] if table in content_snapshot.BASE_TABLES else json.loads(record["entity_key"])
            marker = (table, _general_audit_key(table, key))
            if marker in seen: raise ValueError("duplicate key")
            seen.add(marker)
            images = []
            for side in ("before", "after"):
                image = json.loads(record[side + "_json"]) if record[side + "_json"] else None
                if image is not None and set(image) != set(_general_columns(conn, table)):
                    raise ValueError("row shape")
                if record[side + "_hash"] != (content_editing.row_hash(image) if image is not None else None):
                    raise ValueError("row hash")
                images.append(image)
            if images[0] == images[1]: raise ValueError("empty")
            changes.append({"table": table, "key": key, "operation": "revert",
                            "before": images[1], "after": images[0]})
        preset_images = [change for change in changes if change["table"] == "Presets"]
        inverse_refs, reference_cases = [], set()
        for ref in refs:
            if (ref["reference_kind"] != "preset_id" or type(ref["case_id"]) is not int
                    or ref["case_id"] <= 0 or ref["case_id"] in reference_cases):
                raise ValueError("reference identity")
            reference_cases.add(ref["case_id"])
            before_id, after_id = ref["before_preset_id"], ref["after_preset_id"]
            before_key, after_key = ref["before_preset_key"], ref["after_preset_key"]
            if before_id is not None and after_id is None:
                matches = [change["after"] for change in preset_images
                           if change["before"] is None and change["after"] is not None
                           and change["after"]["id"] == before_id
                           and change["after"]["short_code"] == before_key]
                inverse_before, inverse_after = None, matches[0] if len(matches) == 1 else None
            elif before_id is None and after_id is not None:
                matches = [change["before"] for change in preset_images
                           if change["before"] is not None and change["after"] is None
                           and change["before"]["id"] == after_id
                           and change["before"]["short_code"] == after_key]
                inverse_before, inverse_after = matches[0] if len(matches) == 1 else None, None
            else:
                matches = []
                inverse_before = inverse_after = None
            if len(matches) != 1:
                raise ValueError("reference Preset identity")
            # Tie this Case to one exact Preset audit image, not simply any
            # Preset record that happens to belong to this revision.
            identity = inverse_before or inverse_after
            case = conn.execute("""SELECT preset_short_code_snapshot
                                   FROM Cases WHERE id=?""", (ref["case_id"],)).fetchone()
            if case is None or case["preset_short_code_snapshot"] != identity["short_code"]:
                raise ValueError("reference Case identity")
            inverse_refs.append({"case_id": ref["case_id"], "reference_kind": "preset_id",
                                 "before_preset_id": after_id, "before_preset_key": after_key,
                                 "before_preset_name": inverse_before["name"] if inverse_before else None,
                                 "after_preset_id": before_id, "after_preset_key": before_key,
                                 "after_preset_name": inverse_after["name"] if inverse_after else None})
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ChangeError("Revert refused: invalid or unavailable audit data.", local=str(error)) from None
    return {"revision": dict(revision), "changes": changes, "references": inverse_refs,
            "source_hash": contract.digest({"revision": dict(revision), "changes": records, "references": refs})}


def _general_apply_images(conn, changes):
    """Apply exact inverse images, preserving original SQLite IDs."""
    # Deletes require config rows first; recreates require base endpoints first.
    def order(change):
        relation = change["table"] in _GENERAL_RELATIONS
        deleting = change["after"] is None
        return (3 if deleting and not relation else 2 if deleting else 0 if not relation else 1,
                change["table"], _general_audit_key(change["table"], change["key"]))
    # Replacing a unique Quick Type position (notably a reorder) intentionally
    # creates a new surrogate row during forward Apply.  An UPDATE cannot put
    # the old ID back, so remove those current images first and insert the
    # audited image explicitly below.  No other relation has this replacement
    # grammar, and all checks still compare exact physical rows.
    replacements = []
    for change in changes:
        if change["table"] != "Quick_Type_Tokens" or change["before"] is None or change["after"] is None:
            continue
        current = _general_row(conn, change["table"], change["key"])
        if current != change["before"]:
            raise ChangeError("Revert refused: later content changes or identities conflict with this revision.")
        if current["id"] != change["after"]["id"]:
            replacements.append(change)
    for change in replacements:
        _general_delete_from_intent(conn, change["table"], change["key"])

    for change in sorted(changes, key=order):
        if change in replacements:
            if _general_row(conn, change["table"], change["key"]) is not None:
                raise ChangeError("Revert refused: restored token position is occupied.")
            _insert(conn, change["table"], change["after"])
            continue
        current = _general_row(conn, change["table"], change["key"])
        if current != change["before"]:
            raise ChangeError("Revert refused: later content changes or identities conflict with this revision.")
        table, after = change["table"], change["after"]
        if after is None:
            _general_delete_from_intent(conn, table, change["key"])
        elif current is None:
            _insert(conn, table, after)
        else:
            columns, target = _physical_target_general(conn, table, change["key"])
            editable = [name for name in after if name not in columns and name != "id"]
            conn.execute(f"UPDATE {table} SET {', '.join(name + '=?' for name in editable)} WHERE " +
                         " AND ".join(name + "=?" for name in columns), (*[after[n] for n in editable], *target))
    if any(_general_row(conn, c["table"], c["key"]) != c["after"] for c in changes):
        raise ChangeError("Revert refused: restored rows do not match their audit images.")


def _review_generalized_inverse(candidate, revision_id):
    inverse = _read_generalized_audit(candidate, revision_id)
    base_hash = content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(candidate))
    guard = local_review_guard(candidate)
    with _access_scope(candidate):
        before_rows = _general_rows(candidate)
        before_presets, before_pending = _capture(candidate)
        before_validated = _validated_reconstructability(candidate)
        _check_general_pending_removals(candidate, inverse["changes"])
    with _access_scope(candidate, {"Cases"}):
        _general_apply_case_references(candidate, inverse["references"], attaching=False, inverse=True)
    with _access_scope(candidate, set(_GENERAL_TABLES) | {"sqlite_sequence"}):
        _general_apply_images(candidate, inverse["changes"])
    with _access_scope(candidate, {"Cases"}):
        _general_apply_case_references(candidate, inverse["references"], attaching=True, inverse=True)
    with _access_scope(candidate):
        _validate_general_final_graph(candidate)
        after_rows = _general_rows(candidate)
        after_presets, after_pending = _capture(candidate, candidate=True)
        after_validated = _validated_reconstructability(candidate)
        result_hash = content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(candidate))
    presets = [{"code": code, "affected": before_presets.get(code) != after_presets.get(code),
                "added": code not in before_presets, "removed": code not in after_presets,
                "output_changed": (before_presets.get(code) or {}).get("report") != (after_presets.get(code) or {}).get("report"),
                "before": before_presets.get(code), "after": after_presets.get(code)}
               for code in sorted(set(before_presets) | set(after_presets))]
    pending = _general_pending_impact(before_pending, after_pending)
    configuration_owners = []
    for change in inverse["changes"]:
        if change["table"] == "Quick_Type_Tokens":
            configuration_owners.append(("quick_type", change["key"]["preset_code"]))
        elif change["table"] == "Field_Consistency_Rules":
            configuration_owners.append(("consistency", change["key"]["block_key"]))
    configuration_owners = list(dict.fromkeys(configuration_owners))
    quick_type_owners = [key for kind, key in configuration_owners if kind == "quick_type"]
    if quick_type_owners:
        import content_studio
        guided_evidence = content_studio.quick_type_generated_review_evidence(
            candidate, quick_type_owners,
        )
    else:
        guided_evidence = None
    payload = {"engine": "generalized_v1", "summary": f"Reverted revision {revision_id}", "operations": [],
               "changes": inverse["changes"], "case_references": inverse["references"], "presets": presets,
               "configuration_review": _configuration_review_evidence(
                   before_rows, after_rows, [], candidate, owners=configuration_owners,
               ),
               "unaffected_presets": sum(not item["affected"] for item in presets), "pending_cases": pending,
               "validated_pending_count": len(after_pending), "warnings": _validated_reconstruction_loss_warning(before_validated, after_validated), "branch_warnings": [],
               "standalone": [], "before_standalone": [], "inverse_revision_id": revision_id,
               "inverse_source_hash": inverse["source_hash"], "guided_evidence": guided_evidence}
    return _issued_review(None, base_hash, result_hash, guard, contract.canonical_json(payload))
