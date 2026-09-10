"""Stage 5 v1 interchange contract. No content writes or external AI calls."""

import hashlib
import json
import math
import re

import content_editing
import content_snapshot

FORMAT = "pathopilot-content-change-package-v1"
MAX_BYTES, MAX_DEPTH, MAX_OPERATIONS, MAX_ERRORS = 1048576, 20, 200, 20
MAX_INTEGER = 2**53 - 1
CREATE = {
    "Fields": {"required": ["label", "type", "default_value"],
               "defaults": {"options": None, "conclusion_addendum_template": None}},
    "Blocks": {"required": ["name", "macro_template", "micro_template", "conclusion_template"],
               "defaults": {"context_template": None, "title_fragment_template": None,
                            "conclusion_label_template": None}},
    "Presets": {"required": ["name"], "defaults": {"category": None, "default_title": None}},
    "Snippets": {"required": ["expansion"], "defaults": {"category": None}},
}
LINK = {
    "Block_Fields": {"key": ["block_key", "field_key"], "required": ["sort_order"],
                     "defaults": {"label_override": None, "default_override": None, "context_section": False}},
    "Preset_Blocks": {"key": ["preset_code", "block_key", "sort_order"], "required": [],
                      "defaults": {"field_overrides": {}}},
}
FIELD_TYPES = ("text", "number", "decimal", "select", "checkbox")
REQUIRED_TEXT = {"label", "name", "expansion", "macro_template", "micro_template", "conclusion_template"}
OPTIONAL_TEXT = {"category", "default_title", "context_template", "title_fragment_template",
                 "conclusion_label_template", "conclusion_addendum_template", "label_override"}
CORRECTIONS = {
    "invalid_json": "Return valid UTF-8 JSON within the documented limits.",
    "contract": "Use only the documented members, types and operations.",
    "target": "Check targets and supply each logical change exactly once.",
    "value": "Supply a valid value for the final Field type and options.",
    "noop": "Omit unchanged values and empty updates.",
    "graph": "Correct the complete graph, bindings and usable defaults.",
    "candidate": "Correct the candidate templates and defaults.",
    "pending": "Review the local pending-case errors.",
    "stale": "Export fresh context and prepare a package against its hash.",
    "operation_shape": "Use the documented operation shape for this operation and table.",
    "link_key": "Use the documented composite key for this relationship table.",
    "required_member": "Supply the documented required member for this operation and table.",
}


class PackageError(ValueError):
    """Fixed copyable feedback, with optional separate session-local details."""

    def __init__(self, code, path="", *, local=None, errors=None):
        self.errors = tuple(errors or ((code, path),))
        self.local = local
        super().__init__(CORRECTIONS[code])

    def ai_feedback(self):
        return {
            "errors": [{"code": code, "path": path, "correction": CORRECTIONS[code]}
                       for code, path in self.errors[:MAX_ERRORS]],
            "omitted_errors": max(0, len(self.errors) - MAX_ERRORS),
        }


def canonical_json(value):
    return content_snapshot.content_snapshot_json(value)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _fail(code="contract", path=""):
    raise PackageError(code, path)


def _object(value, required, optional=(), path=""):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        _fail(path=path)


def _position(value, path):
    if type(value) is not int or not 0 <= value <= 999:
        _fail("value", path)
    return value


def _key(value, table, creating, path):
    if not isinstance(value, str) or not value:
        _fail(path=path)
    if creating and (len(value) > 80 or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*" if table == "Fields" else r"[A-Za-z0-9_-]+", value
    )):
        _fail(path=path)
    return value


def field_value(field, value, *, nullable_global=False, stored=False, path=""):
    """Strict native value validation, optionally converted to DB text storage."""
    kind = field["type"]
    options = field.get("options")
    if isinstance(options, str):
        options = json.loads(options)
    if value is None:
        if kind in ("text", "decimal") or nullable_global:
            return None
        _fail("value", path)
    if kind == "text":
        valid = isinstance(value, str)
    elif kind == "number":
        valid = type(value) is int and 0 <= value <= MAX_INTEGER
    elif kind == "decimal":
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
        except OverflowError:
            valid = False
    elif kind == "select":
        valid = isinstance(value, str) and value in (options or [])
    elif kind == "checkbox":
        valid = type(value) is bool
    else:
        valid = False
    if not valid:
        _fail("value", path)
    if not stored or kind in ("text", "select"):
        return value
    if kind == "checkbox":
        return "true" if value else "false"
    if kind == "decimal" and float(value).is_integer():
        return str(int(value))
    return str(value)


def _values(table, values, path, creating):
    result = dict(values)
    for column, value in values.items():
        if column in REQUIRED_TEXT:
            if not isinstance(value, str) or not value.strip():
                _fail("value", path + "." + column)
            if table == "Snippets" and creating and column == "expansion":
                result[column] = value.strip()
        elif column in OPTIONAL_TEXT:
            if value is not None and not isinstance(value, str):
                _fail("value", path + "." + column)
            result[column] = value if value is not None and value.strip() else None
    if creating and table == "Fields":
        if values["type"] not in FIELD_TYPES:
            _fail("value", path + ".type")
        options = values.get("options")
        if values["type"] == "select":
            if (not isinstance(options, list) or not options
                    or any(not isinstance(v, str) or not v.strip() for v in options)
                    or len(set(options)) != len(options)):
                _fail("value", path + ".options")
        elif options is not None:
            _fail("value", path + ".options")
        field_value(result, result["default_value"], nullable_global=True, path=path + ".default_value")
    return result


def _required_member(values, required, optional, path):
    """Name a missing known member without reflecting an uploaded value or key."""
    if not isinstance(values, dict) or set(values) - set(required) - set(optional):
        return
    for member in required:
        if member not in values:
            _fail("required_member", path + "." + member)


def _operation_shape(operation, path):
    """Recognize only enough trusted structure for fixed structural feedback."""
    if not isinstance(operation, dict):
        _fail(path=path)
    op, table = operation.get("op"), operation.get("table")
    if (not isinstance(op, str) or op not in ("create", "update", "link")
            or not isinstance(table, str) or table not in set(CREATE) | set(LINK)):
        _fail(path=path)
    if ((op == "link" and table not in LINK)
            or (op != "link" and table not in CREATE)):
        _fail("operation_shape", path)
    member = "set" if op == "update" else "values"
    other_member = "values" if member == "set" else "set"
    if member not in operation or other_member in operation or not isinstance(operation[member], dict):
        _fail("operation_shape", path)
    if not {"op", "table", "key", member} <= set(operation):
        _fail(path=path)
    if set(operation) - {"op", "table", "key", member}:
        _fail(path=path)
    return op, table, member


def normalize_operations(operations):
    """Source-independent v1 operation syntax; final-graph checks happen on a copy."""
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        _fail()
    normalized, errors, targets = [], [], set()
    for index, operation in enumerate(operations):
        path = f"operations[{index}]"
        try:
            op, table, member = _operation_shape(operation, path)
            if op == "link":
                spec = LINK[table]
                if not isinstance(operation["key"], dict):
                    _fail("link_key", path + ".key")
                _object(operation["key"], spec["key"], path=path + ".key")
                key = dict(operation["key"])
                for k, v in key.items():
                    if k == "sort_order":
                        _position(v, path + ".key.sort_order")
                    else:
                        _key(v, table, False, path + ".key")
                _required_member(operation[member], spec["required"], spec["defaults"], path + ".values")
                _object(operation[member], spec["required"], spec["defaults"], path)
                values = {**spec["defaults"], **_values(table, operation[member], path, False)}
                if table == "Block_Fields":
                    _position(values["sort_order"], path + ".values.sort_order")
                    if type(values["context_section"]) is not bool:
                        _fail("value", path + ".values.context_section")
                elif not isinstance(values["field_overrides"], dict):
                    _fail("value", path + ".values.field_overrides")
            else:
                key = _key(operation["key"], table, op == "create", path + ".key")
                if op == "create":
                    spec = CREATE[table]
                    _required_member(operation[member], spec["required"], spec["defaults"], path + ".values")
                    _object(operation[member], spec["required"], spec["defaults"], path)
                    values = _values(table, {**spec["defaults"], **operation[member]}, path + ".values", True)
                else:
                    _object(operation[member], (), content_editing.EDITABLE[table][1], path)
                    if not operation[member]:
                        _fail("noop", path)
                    values = _values(table, operation[member], path + ".set", False)
            target = (table, canonical_json(key))
            if target in targets:
                _fail("target", path)
            targets.add(target)
            normalized.append({"op": op, "table": table, "key": key, member: values, "index": index})
        except PackageError as error:
            errors.extend(error.errors)
    if errors:
        raise PackageError(errors[0][0], errors=errors)
    phase = {("create", "Fields"): 0, ("create", "Snippets"): 0,
             ("create", "Blocks"): 1, ("create", "Presets"): 1,
             ("link", "Block_Fields"): 3, ("link", "Preset_Blocks"): 4}
    return sorted(normalized, key=lambda o: (
        phase.get((o["op"], o["table"]), 2), o["table"],
        (str(o["key"].get("block_key" if o["table"] == "Block_Fields" else "preset_code"))
         if isinstance(o["key"], dict) else o["key"]),
        (o["values"].get("sort_order", o["key"].get("sort_order", 0)) if o["op"] == "link" else 0),
        canonical_json(o["key"]),
    ))


def package_envelope(package):
    """Remove local source indices from the canonical, order-independent envelope."""
    return {**package, "operations": [
        {k: v for k, v in op.items() if k != "index"} for op in package["operations"]
    ]}


def parse_package(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        _fail("invalid_json")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("invalid_json")
            result[key] = value
        return result
    def walk(value, depth=0):
        if depth > MAX_DEPTH:
            _fail("invalid_json")
        if isinstance(value, str):
            value.encode("utf-8")
        if isinstance(value, float) and not math.isfinite(value):
            _fail("invalid_json")
        if isinstance(value, dict):
            for key, child in value.items():
                key.encode("utf-8")
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                             parse_constant=lambda _: _fail("invalid_json"))
        walk(payload)
    except (UnicodeError, ValueError, RecursionError, OverflowError) as error:
        if isinstance(error, PackageError):
            raise
        raise PackageError("invalid_json") from None
    _object(payload, ("format", "base_snapshot_sha256", "summary", "operations"))
    if payload["format"] != FORMAT or not isinstance(payload["base_snapshot_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", payload["base_snapshot_sha256"]):
        _fail()
    summary = payload["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary.strip()) > 500:
        _fail()
    return {**payload, "summary": summary.strip(), "operations": normalize_operations(payload["operations"])}


def authoring_contract():
    """Generate the structural contract from the parser's actual allowlists."""
    update = {table: sorted(spec[1]) for table, spec in content_editing.EDITABLE.items()}
    identities = {
        table: [key] for table, (key, _columns) in content_snapshot.BASE_TABLES.items()
    }
    identities.update({
        table: list(key_columns)
        for table, (_columns, key_columns) in content_snapshot.RELATION_TABLES.items()
    })
    writable_tables = {
        table: ["create", "update"] for table in CREATE
    }
    writable_tables.update({table: ["link"] for table in LINK})
    table_access = {
        table: {"access": "writable", "operations": writable_tables[table]}
        if table in writable_tables else {"access": "read_only", "operations": []}
        for table in (*content_snapshot.BASE_TABLES, *content_snapshot.RELATION_TABLES)
    }
    return {
        "envelope": ["format", "base_snapshot_sha256", "summary", "operations"],
        "format": FORMAT,
        "syntax": {
            "create": {"members": ["op", "table", "key", "values"], "tables": CREATE},
            "update": {"members": ["op", "table", "key", "set"], "tables": update},
            "link": {"members": ["op", "table", "key", "values"], "tables": LINK},
        },
        "create": CREATE, "update": update, "link": LINK, "types": list(FIELD_TYPES),
        "identity": identities,
        "tables": table_access,
        "limits": {
            "raw_utf8_bytes": {"max": MAX_BYTES},
            "json_nesting": {"max": MAX_DEPTH},
            "operations": {"min": 1, "max": MAX_OPERATIONS},
            "summary_characters": {"min": 1, "max": 500},
            "new_key_characters": {"min": 1, "max": 80},
            "sort_order": {"min": 0, "max": 999},
            "number": {"min": 0, "max": MAX_INTEGER},
            "decimal": {"min": 0, "finite": True},
        },
        "value_representation": {
            "package": "Use native JSON: options is an array, field_overrides is an object, decimals are JSON numbers, and checkboxes are JSON booleans.",
            "snapshot": "Snapshot rows preserve database storage: Fields.options and Preset_Blocks.field_overrides are JSON text; number, decimal, and checkbox defaults/Block overrides are text or null.",
        },
        "hashes": {
            "snapshot_sha256": "SHA-256 of the canonical snapshot JSON bytes (UTF-8, sorted keys, compact separators, final newline).",
            "base_snapshot_sha256": "Copy context.snapshot_sha256 exactly into the package; it must equal the exported snapshot hash.",
            "package_sha256": "The server calculates this only after parsing and normalization. Do not calculate or include it.",
        },
        "inheritance": [
            "Block_Fields.default_override null inherits the Field default.",
            "Preset_Blocks.field_overrides: a missing Field key inherits the resolved Block/Field value.",
        ],
        "nulls": [
            "Text and decimal defaults/overrides may be null. Number, select, and checkbox Preset overrides cannot be null.",
            "For global number/select/checkbox defaults, null is allowed only on creation or when already null, and every new effective use must have a usable override; never clear a usable default.",
            "Optional text accepts null; whitespace-only optional text becomes null. Required text is nonblank.",
        ],
        "archived_content": "Archived Fields, Blocks, Presets, and Snippets remain in the snapshot as read-only targets. v1 rejects updates to archived rows and links involving archived endpoints.",
        "incomplete_source": "This is an expressibility rule, separate from clinical grounding: v1 cannot write every snapshot table. Do not invent missing source rows or modify a read-only table. If the requested change requires either, explain that v1 cannot express it and do not return a package.",
        "rules": [
            "Return only changed values; updates target existing rows, links belong to new owners. No deletes.",
            "Use exactly one operation for each logical base target or relationship link. Put all intended changes for that target in its single operation; do not split updates, create then update, or duplicate a link.",
            "New Field keys: [A-Za-z_][A-Za-z0-9_]*; other new keys: [A-Za-z0-9_-]+; case-sensitive.",
            "Select options: nonempty unique nonblank strings; other types: options null.",
            "Defaults/overrides: text string/null; number nonnegative integer; decimal finite nonnegative number/null; select exact option; checkbox boolean.",
            "Each new Block must be usable standalone with resolved Field defaults and Block-level overrides where required; do not rely on Preset_Blocks.field_overrides to make a new Block usable.",
            "Template context: linked Field keys, <decimal_key>_display, fragment_text when fragments is linked, site_label when configured, literal snippet('shortcut'). Addendum: value and snippet only.",
            "Reserved Field names: snippet, value, site_label, fragment_text, Jinja literals, and decimal display aliases.",
            "Context/title template Fields must be context_section=true. Each new Preset needs a non-table Block; positions unique per owner, below 1000.",
            "New Blocks force is_table=0/site_label=null/conclusion_group=null; new Presets force default_adicap=null; omit these columns.",
            "Existing Blocks with is_table=1 are read-only: do not update them or link them into a newly created Preset.",
        ],
        "example": {
            "format": FORMAT, "base_snapshot_sha256": "0" * 64,
            "summary": "Add a specimen-size preset",
            "operations": [
                {"op": "create", "table": "Fields", "key": "example_size_mm",
                 "values": {"label": "Taille (mm)", "type": "decimal", "default_value": 2.5}},
                {"op": "create", "table": "Blocks", "key": "example_size_block",
                 "values": {"name": "Spécimen mesuré", "macro_template": "Taille : {{ example_size_mm_display }} mm.",
                            "micro_template": "Examen microscopique.", "conclusion_template": "Conclusion."}},
                {"op": "create", "table": "Presets", "key": "example_size_preset",
                 "values": {"name": "Examen avec taille"}},
                {"op": "link", "table": "Block_Fields",
                 "key": {"block_key": "example_size_block", "field_key": "example_size_mm"},
                 "values": {"sort_order": 0}},
                {"op": "link", "table": "Preset_Blocks",
                 "key": {"preset_code": "example_size_preset", "block_key": "example_size_block", "sort_order": 0},
                 "values": {"field_overrides": {"example_size_mm": 12.5}}},
            ],
        },
    }


def export_ai_context(db_name=None):
    """Content-only export; no Case/audit/safety queries, migrations or markers."""
    snapshot = content_snapshot.export_content_snapshot(db_name)
    content_snapshot.validate_content_snapshot(snapshot)
    payload = {
        "format": "pathopilot-ai-context-v1",
        "snapshot_sha256": content_snapshot.content_snapshot_hash(snapshot),
        "instructions": {
            "response": "Only when the request is sufficiently grounded in the user's request and exported content, return exactly one pathopilot-content-change-package-v1 JSON object without markdown. Copy the base hash; return only changed/created values, never the source snapshot. If required clinical content is missing, ask the user for it; do not invent it, use placeholders, or return an empty package. If the requested change cannot be expressed by writable v1 operations, explain that limitation and do not return a package.",
            "privacy": "Do not include patient information, case identifiers, report examples, or audit data.",
            "contract": authoring_contract(),
        }, "snapshot": snapshot,
    }
    return canonical_json(payload).encode("utf-8")


def dry_run(raw, db_name=None):
    from content_changes import review_candidate
    package = parse_package(raw)
    return review_candidate(
        package["operations"], package["base_snapshot_sha256"],
        package_hash=digest(package_envelope(package)), summary=package["summary"], db_name=db_name,
    )
