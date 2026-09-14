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
import content_editing
import content_snapshot
import database


BASE_TABLES = frozenset(content_snapshot.BASE_TABLES)
CONFIGURATION_TABLES = frozenset(content_snapshot.RELATION_TABLES)
SUPPORTED_TABLES = BASE_TABLES | CONFIGURATION_TABLES


class StudioIntentError(ValueError):
    """A guided form supplied an incomplete or unsupported intent."""


class StaleBlockDraftError(StudioIntentError):
    """A Block draft no longer matches the Block and bindings it loaded."""


class StalePresetDraftError(StudioIntentError):
    """A Preset draft no longer matches its loaded composition."""


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


# Stage 6 checkpoint 5 ------------------------------------------------------

_BLOCK_COLUMNS = (
    "name", "site_label", "conclusion_group", "macro_template", "micro_template",
    "conclusion_template", "context_template", "title_fragment_template",
    "conclusion_label_template",
)
_BLOCK_FIELD_COLUMNS = ("sort_order", "label_override", "default_override", "context_section")


def _none_if_blank(value):
    return None if value is None or (isinstance(value, str) and not value.strip()) else value


def _block_values(draft):
    """Return the editable non-table Block image from a guided draft."""
    if not isinstance(draft, dict):
        raise StudioIntentError("Block draft must be an object.")
    values = {}
    for column in _BLOCK_COLUMNS:
        if column not in draft:
            raise StudioIntentError(f"Block draft is missing {column}.")
        value = draft[column]
        if column in {"name", "macro_template", "micro_template", "conclusion_template"}:
            if not isinstance(value, str):
                raise StudioIntentError(f"Block {column} must be text.")
            if column == "name" and not value.strip():
                raise StudioIntentError("Block name cannot be blank.")
        elif value is not None and not isinstance(value, str):
            raise StudioIntentError(f"Block {column} must be text or empty.")
        if column in {"site_label", "conclusion_group", "context_template",
                      "title_fragment_template", "conclusion_label_template"}:
            value = _none_if_blank(value)
        values[column] = value
    return values


def _block_field_values(binding, position):
    if not isinstance(binding, dict) or not isinstance(binding.get("field_key"), str) or not binding["field_key"]:
        raise StudioIntentError("Every Block Field binding needs an active Field key.")
    label = binding.get("label_override")
    # ``None`` is the explicit Use Field label state.  A present blank value
    # instead means the Override Field label mode was selected without a
    # usable label; never silently translate that into inheritance.
    if label is not None and (not isinstance(label, str) or not label.strip()):
        raise StudioIntentError("Block Field label override cannot be blank.")
    default = binding.get("default_override")
    context = binding.get("context_section", False)
    if type(context) is not bool:
        raise StudioIntentError("Block Field context-section state must be true or false.")
    return {
        "sort_order": position,
        "label_override": label,
        "default_override": default,
        "context_section": context,
    }


def _normal_block_bindings(bindings):
    if not isinstance(bindings, list):
        raise StudioIntentError("Block Field bindings must be an ordered list.")
    result = []
    seen = set()
    for position, binding in enumerate(bindings):
        values = _block_field_values(binding, position)
        key = binding["field_key"]
        if key in seen:
            raise StudioIntentError("A Field can be bound to a Block only once.")
        seen.add(key)
        result.append((key, values))
    return result


def _block_draft_image(block, bindings):
    """Canonical persisted state a guided Block draft is allowed to replace."""
    block = dict(block)

    def binding_image(row, position):
        row = dict(row)
        field_key = row.get("field_key", row.get("key"))
        # The planner query exposes Block_Fields.field_id directly. The UI's
        # joined Field rows expose that same identity as Fields.id. Never hash
        # only the natural key: delete/recreate/rebind under an identical key
        # is a different persisted relationship endpoint.
        field_id = row.get("field_id")
        if field_id is None and row.get("key") == field_key:
            field_id = row.get("id")
        block_id = row.get("block_id", block.get("id"))
        if type(block_id) is not int or type(field_id) is not int:
            raise StudioIntentError(
                "Block draft bindings require their loaded physical identities."
            )
        return {
            "block_id": block_id,
            "field_id": field_id,
            "field_key": field_key,
            "sort_order": row.get("sort_order", position),
            "label_override": row["label_override"],
            "default_override": row["default_override"],
            # The UI exposes a bool, whereas SQLite retains 0/1. This is a
            # physical baseline, so canonicalise only that representation.
            "context_section": int(row["context_section"])
            if type(row["context_section"]) is bool else row["context_section"],
        }

    return {
        "block": block,
        # Test/domain callers may supply the same ordered binding shape as
        # the form (without its redundant persisted sort_order).  Preserve
        # that order as the physical position in that one case.
        "bindings": sorted(
            (binding_image(row, position) for position, row in enumerate(bindings)),
            key=lambda row: row["field_key"],
        ),
    }


def block_draft_baseline(block, bindings):
    """Return the deterministic load baseline for an edit/duplicate draft.

    The base row and relationship rows are deliberately both included.  A
    metadata-only form must not later erase an override added in another tab.
    """
    return content_editing.row_hash(_block_draft_image(block, bindings))


def _block_rule_rows(conn, block_id):
    """Return the exact physical consistency rules copied by Duplicate."""
    return [dict(row) for row in conn.execute(
        "SELECT * FROM Field_Consistency_Rules WHERE block_id=? ORDER BY id", (block_id,)
    )]


def _block_rules_baseline(rows):
    # Wrap the list so row_hash's top-level base-row ``id`` exclusion does not
    # discard the physical rule identities nested in this source image.
    return content_editing.row_hash({"rules": list(rows)})


def _field_endpoints_baseline(rows):
    """Hash complete desired Field rows, including their physical IDs."""
    return content_editing.row_hash({
        "fields": sorted((dict(row) for row in rows), key=lambda row: row["key"]),
    })


def _assert_field_endpoints(conn, field_keys, baseline):
    """Require desired binding endpoints to be the Fields planning read."""
    rows = []
    for field_key in field_keys:
        row = conn.execute("SELECT * FROM Fields WHERE key=?", (field_key,)).fetchone()
        if row is None:
            raise StaleBlockDraftError(
                "A Field selected by this Block draft changed since planning. Current values were reloaded."
            )
        rows.append(dict(row))
    if baseline != _field_endpoints_baseline(rows):
        raise StaleBlockDraftError(
            "A Field selected by this Block draft changed since planning. Current values were reloaded."
        )


def _assert_block_draft_baseline(conn, source_key, baseline, copied_rules_baseline=None):
    """Require one source Block and its bindings to match a loaded draft."""
    if not isinstance(baseline, str) or not baseline:
        raise StudioIntentError("Block edit and duplicate drafts require their loaded baseline.")
    try:
        source = _target(conn, "Blocks", source_key)
    except StudioIntentError:
        # A source that existed when the guided draft was loaded but no longer
        # exists is stale state, not a new lifecycle-planning error.
        raise StaleBlockDraftError(
            "This Block or its Field bindings changed since the draft was loaded. Current values were reloaded."
        ) from None
    current_rows = {
        row["field_key"]: dict(row)
        for row in conn.execute("""SELECT bf.*, f.key AS field_key FROM Block_Fields bf
                                   JOIN Fields f ON f.id=bf.field_id WHERE bf.block_id=?""", (source["id"],))
    }
    if baseline != block_draft_baseline(source, current_rows.values()):
        raise StaleBlockDraftError(
            "This Block or its Field bindings changed since the draft was loaded. Current values were reloaded."
        )
    if copied_rules_baseline is not None and copied_rules_baseline != _block_rules_baseline(
            _block_rule_rows(conn, source["id"])):
        raise StaleBlockDraftError(
            "This Block or its copied consistency rules changed since duplication was planned. "
            "Current values were reloaded."
        )
    return source, current_rows


def block_draft_operations(action, key, draft, bindings, *, source_key=None, baseline=None, db_name=None):
    """Translate one complete Block form into one reviewed candidate.

    The planner is deliberately read-only.  It owns the otherwise easy to
    get wrong relationship replacement: templates and every ``Block_Fields``
    row travel through the candidate service together.  ``duplicate`` copies
    only the Block and its own Field/rule configuration; Preset composition is
    intentionally not an implicit relationship of a duplicate.
    """
    if action not in {"create", "edit", "duplicate"}:
        raise StudioIntentError("Block action must be create, edit, or duplicate.")
    if not isinstance(key, str) or not key.strip():
        raise StudioIntentError("A Block needs a stable key.")
    values = _block_values(draft)
    desired = _normal_block_bindings(bindings)
    conn = _connection(db_name)
    try:
        if action in {"create", "duplicate"}:
            if conn.execute("SELECT 1 FROM Blocks WHERE key=?", (key,)).fetchone() is not None:
                raise StudioIntentError("Block key already exists.")
        if action in {"edit", "duplicate"}:
            if not isinstance(source_key, str) or not source_key:
                raise StudioIntentError("An existing Block is required.")
            source, current_rows = _assert_block_draft_baseline(conn, source_key, baseline)
            if action == "edit" and key != source_key:
                raise StudioIntentError("A Block stable key cannot be changed.")
            if action == "duplicate" and key == source_key:
                raise StudioIntentError("A duplicate needs a new stable key.")
            if action == "duplicate" and values["name"] == source["name"]:
                raise StudioIntentError("A duplicate needs a new Block name.")
            if source["is_table"]:
                raise StudioIntentError("Table Blocks are read-only in Content Studio.")
            if source["is_archived"]:
                raise StudioIntentError("Restore an archived Block before editing or duplicating it.")
        else:
            source, current_rows = None, {}

        active_fields = {row["key"]: dict(row) for row in conn.execute(
            "SELECT * FROM Fields WHERE is_archived=0"
        )}
        missing = sorted(field_key for field_key, _ in desired if field_key not in active_fields)
        if missing:
            raise StudioIntentError("Block Fields must be active: " + ", ".join(missing) + ".")
        # Existing SQLite overrides use the historic text representation.
        # A duplicated Block must preserve their Field-type meaning without
        # asking the generic writer to treat (for example) "2" as a native
        # number widget value.
        normalized_desired = []
        for field_key, binding_values in desired:
            binding_values = dict(binding_values)
            if binding_values["default_override"] is not None:
                try:
                    binding_values["default_override"] = content_changes._native_stored(
                        active_fields[field_key], binding_values["default_override"]
                    )
                except (TypeError, ValueError):
                    raise StudioIntentError("Block Field default override is invalid for its Field type.") from None
            normalized_desired.append((field_key, binding_values))
        desired = normalized_desired

        desired_field_keys = sorted(field_key for field_key, _ in desired)
        field_assertion = ([{
            "op": "assert_field_endpoints",
            "field_keys": desired_field_keys,
            "baseline": _field_endpoints_baseline(
                active_fields[field_key] for field_key in desired_field_keys
            ),
        }] if desired_field_keys else [])
        source_rules = _block_rule_rows(conn, source["id"]) if action == "duplicate" else []
        source_assertion = ([{
            "op": "assert_block_draft", "table": "Blocks", "key": source_key,
            "baseline": baseline,
            "copied_rules_baseline": _block_rules_baseline(source_rules) if action == "duplicate" else None,
        }] if source is not None else [])
        assertions = source_assertion + field_assertion
        if action in {"create", "duplicate"}:
            operations = assertions + [operation("create", "Blocks", key, values)]
            for field_key, binding_values in desired:
                operations.append(operation("link", "Block_Fields", {
                    "block_key": key, "field_key": field_key,
                }, binding_values))
            if action == "duplicate":
                for rule in source_rules:
                    rule_key = {
                        "block_key": key,
                        "field_a_key": rule["field_a_key"],
                        "field_a_values": rule["field_a_values"],
                        "field_b_key": rule["field_b_key"],
                        "field_b_values": rule["field_b_values"],
                        "message": rule["message"],
                    }
                    rule_values = {name: rule[name] for name in (
                        "field_a_key", "field_a_values", "field_b_key", "field_b_values", "message",
                    )}
                    operations.append(operation("link", "Field_Consistency_Rules", rule_key, rule_values))
            return operations

        desired_by_key = dict(desired)
        operations = assertions + [operation("update", "Blocks", key, values)]
        for field_key in sorted(set(current_rows) - set(desired_by_key)):
            operations.append(operation("unlink", "Block_Fields", {"block_key": key, "field_key": field_key}))
        for field_key, binding_values in desired:
            old = current_rows.get(field_key)
            if old is None:
                operations.append(operation("link", "Block_Fields", {
                    "block_key": key, "field_key": field_key,
                }, binding_values))
                continue
            old_values = {column: old[column] for column in _BLOCK_FIELD_COLUMNS}
            # SQLite stores BOOLEAN as an integer; compare the guided state
            # rather than emitting a false update for every checkbox. It also
            # retains numeric/checkbox defaults as text, while guided widgets
            # use native values; compare both sides in that native Field type
            # so a metadata-only edit is not preceded by a false no-op link.
            old_values["context_section"] = bool(old_values["context_section"])
            if old_values["default_override"] is not None:
                try:
                    old_values["default_override"] = content_changes._native_stored(
                        active_fields[field_key], old_values["default_override"]
                    )
                except (TypeError, ValueError):
                    raise StudioIntentError("Stored Block Field default override is invalid.") from None
            if old_values != binding_values:
                changed = {name: binding_values[name] for name in binding_values
                           if old_values[name] != binding_values[name]}
                operations.append(operation(
                    "reorder" if set(changed) == {"sort_order"}
                    else "update",
                    "Block_Fields", {"block_key": key, "field_key": field_key}, changed,
                ))
        # Avoid manufacturing a no-op Block update when only relationships
        # changed. The candidate engine rejects no-op updates by design.
        if all(source[column] == values[column] for column in _BLOCK_COLUMNS):
            operations.pop(len(assertions))
        return operations
    finally:
        conn.close()


# Stage 6 checkpoint 6 ------------------------------------------------------

_PRESET_COLUMNS = ("name", "category", "default_title", "default_adicap")


def _preset_values(draft):
    if not isinstance(draft, dict) or set(draft) != set(_PRESET_COLUMNS):
        raise StudioIntentError("Preset draft has invalid metadata.")
    values = {}
    for column in _PRESET_COLUMNS:
        value = draft[column]
        if column == "name":
            if not isinstance(value, str) or not value.strip():
                raise StudioIntentError("Preset name cannot be blank.")
        elif value is not None and not isinstance(value, str):
            raise StudioIntentError(f"Preset {column} must be text or empty.")
        values[column] = _none_if_blank(value) if column != "name" else value
    return values


def _preset_link_rows(conn, preset_id):
    """Exact linked instance images, including endpoint identities.

    A Preset form controls a composition, not just the Preset row.  Preserve
    the physical IDs of every linked Block and Field so a delete/recreate ABA
    cannot be mistaken for the same source composition.
    """
    result = []
    rows = conn.execute(
        """SELECT pb.*, b.key AS block_key, b.is_archived AS block_is_archived,
                  b.is_table AS block_is_table
           FROM Preset_Blocks pb JOIN Blocks b ON b.id=pb.block_id
           WHERE pb.preset_id=? ORDER BY pb.sort_order""", (preset_id,)
    )
    for row in rows:
        image = dict(row)
        image["block"] = dict(conn.execute("SELECT * FROM Blocks WHERE id=?", (image["block_id"],)).fetchone())
        image["fields"] = [{
            "binding": dict(binding),
            "field": dict(conn.execute("SELECT * FROM Fields WHERE id=?", (binding["field_id"],)).fetchone()),
        } for binding in conn.execute(
            "SELECT * FROM Block_Fields WHERE block_id=? ORDER BY sort_order", (image["block_id"],)
        )]
        result.append(image)
    return result


def _preset_instance_endpoint_image(conn, block_key, instance_no):
    """Complete physical endpoint image for one draft-only Block instance."""
    block = conn.execute("SELECT * FROM Blocks WHERE key=?", (block_key,)).fetchone()
    if block is None:
        raise StudioIntentError("Preset Blocks must be active non-table Blocks.")
    block = dict(block)
    return {
        "block_key": block_key, "instance_no": instance_no, "block": block,
        "fields": [{
            "binding": dict(binding),
            "field": dict(conn.execute("SELECT * FROM Fields WHERE id=?", (binding["field_id"],)).fetchone()),
        } for binding in conn.execute(
            "SELECT * FROM Block_Fields WHERE block_id=? ORDER BY sort_order", (block["id"],)
        )],
    }


def _preset_draft_image(preset, links=None, *, endpoints=None):
    return {
        "preset": dict(preset) if preset is not None else None,
        "links": sorted((copy.deepcopy(links) if links is not None else []),
                        key=lambda row: (row.get("sort_order", 0), row.get("block_id", 0))),
        "endpoints": copy.deepcopy(endpoints) if endpoints is not None else None,
    }


def preset_draft_baseline(preset, links):
    """Hash the complete persisted Preset and all of its live instances."""
    return content_editing.row_hash(_preset_draft_image(preset, links))


def preset_draft_source(short_code, *, db_name=None):
    """Read the one physical source image used by a Preset form."""
    conn = _connection(db_name)
    try:
        preset = _target(conn, "Presets", short_code)
        return preset, _preset_link_rows(conn, preset["id"])
    finally:
        conn.close()


def _preset_instance_endpoint_baseline(conn, block_key, instance_no):
    return content_editing.row_hash(_preset_instance_endpoint_image(conn, block_key, instance_no))


def preset_instance_endpoint_baseline(block_key, instance_no, *, db_name=None):
    """Capture one immutable draft-only instance endpoint at selection time."""
    if not isinstance(block_key, str) or not block_key or type(instance_no) is not int or not 0 <= instance_no < 1000:
        raise StudioIntentError("Preset Block instance identity is invalid.")
    conn = _connection(db_name)
    try:
        return _preset_instance_endpoint_baseline(conn, block_key, instance_no)
    finally:
        conn.close()


def _normal_preset_instances(instances):
    if not isinstance(instances, list) or not instances:
        raise StudioIntentError("A Preset needs at least one Block instance.")
    result, identities, display = [], set(), set()
    for position, instance in enumerate(instances):
        if not isinstance(instance, dict) or set(instance) != {"block_key", "instance_no", "field_overrides"}:
            raise StudioIntentError("Preset Block instances are invalid.")
        block_key, instance_no, overrides = instance["block_key"], instance["instance_no"], instance["field_overrides"]
        if (not isinstance(block_key, str) or not block_key or type(instance_no) is not int
                or not 0 <= instance_no < 1000 or not isinstance(overrides, dict)):
            raise StudioIntentError("Preset Block instances are invalid.")
        identity = (block_key, instance_no)
        if identity in identities or position in display:
            raise StudioIntentError("Preset Block instance identity or display order is duplicated.")
        identities.add(identity); display.add(position)
        result.append({"block_key": block_key, "instance_no": instance_no,
                       "display_order": position, "field_overrides": copy.deepcopy(overrides)})
    return result


def _validate_preset_instances(conn, instances):
    """Validate the exact per-instance Field set and preserve JSON semantics."""
    checked = []
    for instance in instances:
        block = conn.execute("SELECT * FROM Blocks WHERE key=?", (instance["block_key"],)).fetchone()
        if block is None or block["is_archived"] or block["is_table"]:
            raise StudioIntentError("Preset Blocks must be active non-table Blocks.")
        fields = {row["key"]: dict(row) for row in conn.execute(
            """SELECT f.* FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id
               WHERE bf.block_id=?""", (block["id"],)
        )}
        overrides = instance["field_overrides"]
        if set(overrides) - set(fields):
            raise StudioIntentError("Preset override targets a Field not linked to that Block instance.")
        normalized = {}
        for field_key, value in overrides.items():
            try:
                # Preset overrides are JSON-native values. Keep a present null
                # as null (rather than dropping the key), as well as 0, false
                # and the empty string; only Block_Field SQL overrides use
                # the historic text storage conversion.
                content_changes.contract.field_value(
                    fields[field_key], content_changes._native_stored(fields[field_key], value)
                )
                normalized[field_key] = value
            except (TypeError, ValueError):
                raise StudioIntentError("Preset Field override is invalid for its Field type.") from None
        checked.append({**instance, "block_id": block["id"], "field_overrides": normalized})
    return checked


def _assert_preset_draft_baseline(conn, source_key, baseline, endpoint_baseline=None):
    if not isinstance(baseline, str) or not baseline:
        raise StudioIntentError("Preset edit and duplicate drafts require their loaded baseline.")
    try:
        source = _target(conn, "Presets", source_key)
    except StudioIntentError:
        raise StalePresetDraftError("This Preset or its Block instances changed since the draft was loaded. Current values were reloaded.") from None
    links = _preset_link_rows(conn, source["id"])
    if baseline != preset_draft_baseline(source, links):
        raise StalePresetDraftError("This Preset or its Block instances changed since the draft was loaded. Current values were reloaded.")
    return source, links


def _assert_preset_endpoints_baseline(conn, endpoints):
    """Assert each draft-only endpoint independently; never rebase survivors."""
    for endpoint in endpoints:
        current = _preset_instance_endpoint_baseline(
            conn, endpoint["block_key"], endpoint["instance_no"]
        )
        if current != endpoint["baseline"]:
            raise StalePresetDraftError(
                "Preset Block endpoints changed since the draft was loaded. Current values were reloaded."
            )


def _endpoint_assertion(instances, required_identities, endpoint_baselines):
    """Bind only newly selected instances, preserving older draft baselines."""
    if not required_identities:
        return []
    if not isinstance(endpoint_baselines, list):
        raise StalePresetDraftError("Preset Block endpoints changed since the draft was loaded. Current values were reloaded.")
    provided = {(item.get("block_key"), item.get("instance_no")): item
                for item in endpoint_baselines if isinstance(item, dict)}
    if set(provided) != set(required_identities):
        raise StalePresetDraftError("Preset Block endpoints changed since the draft was loaded. Current values were reloaded.")
    endpoints = []
    for block_key, instance_no in sorted(required_identities):
        endpoint = provided[(block_key, instance_no)]
        if (set(endpoint) != {"block_key", "instance_no", "baseline"}
                or not isinstance(endpoint["baseline"], str)):
            raise StalePresetDraftError("Preset Block endpoints changed since the draft was loaded. Current values were reloaded.")
        endpoints.append(copy.deepcopy(endpoint))
    return [{"op": "assert_preset_endpoints", "endpoints": endpoints}]


def preset_draft_operations(action, key, draft, instances, *, source_key=None, baseline=None,
                            endpoint_baselines=None, db_name=None):
    """Translate one atomic Preset metadata/composition draft into intents.

    ``sort_order`` is treated exclusively as immutable instance identity.
    Reordering emits only ``display_order`` changes; removed instances receive
    narrowly-scoped Quick Type cleanup, and duplication intentionally receives
    no tokens at all.
    """
    if action not in {"create", "edit", "duplicate"} or not isinstance(key, str) or not key.strip():
        raise StudioIntentError("Preset action and short code are required.")
    values = _preset_values(draft)
    desired = _normal_preset_instances(instances)
    conn = _connection(db_name)
    try:
        if action in {"create", "duplicate"} and conn.execute(
                "SELECT 1 FROM Presets WHERE short_code=?", (key,)).fetchone():
            raise StudioIntentError("Preset short code already exists.")
        source = None
        source_links = []
        if action in {"edit", "duplicate"}:
            if not isinstance(source_key, str) or not source_key:
                raise StudioIntentError("An existing Preset is required.")
            source, source_links = _assert_preset_draft_baseline(conn, source_key, baseline)
            if source["is_archived"]:
                raise StudioIntentError("Restore an archived Preset before editing or duplicating it.")
            if action == "edit" and key != source_key:
                raise StudioIntentError("Preset short code is immutable.")
            if action == "duplicate" and key == source_key:
                raise StudioIntentError("A duplicate needs a new Preset short code.")
            if action == "duplicate" and values["name"] == source["name"]:
                raise StudioIntentError("A duplicate needs a new Preset name.")
            if any(link["block_is_table"] for link in source_links):
                raise StudioIntentError("Table-bearing Presets are read-only in Content Studio.")
        checked = _validate_preset_instances(conn, desired)
        if action == "duplicate":
            source_shape = [{"block_key": link["block_key"], "instance_no": link["sort_order"],
                             "display_order": link["display_order"],
                             "field_overrides": json.loads(link["field_overrides"] or "{}")}
                            for link in sorted(source_links, key=lambda link: (link["display_order"], link["sort_order"]))]
            if [{k: item[k] for k in ("block_key", "instance_no", "display_order", "field_overrides")} for item in checked] != source_shape:
                raise StudioIntentError("Duplicate copies the source Preset composition and overrides unchanged.")
        if source is not None:
            assertions = [{"op": "assert_preset_draft", "key": source_key, "baseline": baseline,
                           "endpoint_baseline": None, "duplicate": action == "duplicate"}]
            # Existing-Preset drafts need the same physical endpoint guard
            # when they add a new Block instance. The source baseline covers
            # retained instances; this closes the ABA gap for the newly
            # selected Block and its Fields.
            source_identities = {(link["block_key"], link["sort_order"]) for link in source_links}
            new_identities = {(item["block_key"], item["instance_no"]) for item in desired} - source_identities
            assertions.extend(_endpoint_assertion(desired, new_identities, endpoint_baselines))
        else:
            assertions = _endpoint_assertion(
                desired, {(item["block_key"], item["instance_no"]) for item in desired}, endpoint_baselines
            )
        if action in {"create", "duplicate"}:
            operations = assertions + [operation("create", "Presets", key, values)]
            copied_raw_overrides = ({
                (link["block_key"], link["sort_order"]): link["field_overrides"] for link in source_links
            } if action == "duplicate" else {})
            for item in checked:
                raw_overrides = copied_raw_overrides.get((item["block_key"], item["instance_no"]), object())
                operations.append(operation("link", "Preset_Blocks", {
                    "preset_code": key, "block_key": item["block_key"], "sort_order": item["instance_no"],
                }, {"display_order": item["display_order"], "field_overrides":
                    None if raw_overrides is None else item["field_overrides"]}))
            return operations

        old = {(link["block_key"], link["sort_order"]): link for link in source_links}
        new = {(item["block_key"], item["instance_no"]): item for item in checked}
        operations = list(assertions)
        if any(source[column] != values[column] for column in _PRESET_COLUMNS):
            operations.append(operation("update", "Presets", key, values))
        for identity in sorted(set(old) - set(new)):
            link = old[identity]
            # Tokens refer to the immutable instance number, so only cleanup
            # tokens that target the instance actually removed/replaced.
            for token in conn.execute("SELECT sort_order FROM Quick_Type_Tokens WHERE preset_id=? AND block_sort_order=?",
                                      (source["id"], link["sort_order"])):
                operations.append(operation("unlink", "Quick_Type_Tokens", {
                    "preset_code": key, "sort_order": token["sort_order"],
                }))
            operations.append(operation("unlink", "Preset_Blocks", {
                "preset_code": key, "block_key": link["block_key"], "sort_order": link["sort_order"],
            }))
        for identity in sorted(set(new) - set(old)):
            item = new[identity]
            # A removed historical identity may remain in an explicit pending
            # composition even after the relationship disappeared. Do not
            # repurpose it for a newly linked instance.
            for case in conn.execute("SELECT structured_input FROM Cases WHERE status='pending' AND preset_id=?", (source["id"],)):
                structured = json.loads(case["structured_input"] or "{}")
                for saved in structured.get("block_instances", []) or []:
                    if isinstance(saved, dict) and saved.get("block_id") == item["block_id"] and saved.get("instance_no") == item["instance_no"]:
                        raise StudioIntentError("That Block instance number is retained by a pending Case and cannot be reused.")
            operations.append(operation("link", "Preset_Blocks", {
                "preset_code": key, "block_key": item["block_key"], "sort_order": item["instance_no"],
            }, {"display_order": item["display_order"], "field_overrides": item["field_overrides"]}))
        for identity in sorted(set(old) & set(new)):
            before, item = old[identity], new[identity]
            changed = {}
            if before["display_order"] != item["display_order"]:
                changed["display_order"] = item["display_order"]
            before_overrides = json.loads(before["field_overrides"] or "{}")
            if before_overrides != item["field_overrides"]:
                changed["field_overrides"] = item["field_overrides"]
            if changed:
                operations.append(operation("reorder" if set(changed) == {"display_order"} else "update",
                                            "Preset_Blocks", {"preset_code": key, "block_key": identity[0],
                                                              "sort_order": identity[1]}, changed))
        return operations
    finally:
        conn.close()


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
            # Relationship identities use their physical canonical JSON text.
            # Decoding the two value arrays here made a planner-produced key
            # incompatible with the candidate service's exact audit identity.
            ops.append(operation("unlink", "Field_Consistency_Rules",
                                 {name: found[name] for name in _GENERAL_RULE_KEY}))
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
            ops.append(operation("unlink", "Field_Consistency_Rules",
                                 {name: found[name] for name in _GENERAL_RULE_KEY}))
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


def _field_deletion_prerequisites(conn, row):
    """Templates that must be edited before a Field can be removed.

    Deletion may mechanically unlink a Field binding, but it must never rewrite
    a clinical template.  Detect both normal and decimal display aliases here
    so the guided UI can refuse early with a useful prerequisite rather than
    offering a review which can only fail final-graph validation.
    """
    names = {row["key"], f"{row['key']}_display"}
    callers = []
    for found in conn.execute("""SELECT DISTINCT b.* FROM Blocks b
                                 JOIN Block_Fields bf ON bf.block_id=b.id
                                 WHERE bf.field_id=?""", (row["id"],)):
        block = dict(found)
        for column in (
            "macro_template", "micro_template", "conclusion_template", "context_template",
            "title_fragment_template", "conclusion_label_template",
        ):
            source = block.get(column)
            if not source:
                continue
            try:
                variables, _ = content_editing._template_variables(source)
            except content_editing.ContentEditError:
                # A pre-existing invalid template remains the candidate
                # validator's authoritative refusal; do not hide it here.
                continue
            if names & variables:
                callers.append((block["key"], column))
    return callers


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
        prerequisites = _field_deletion_prerequisites(conn, row) if action == "delete" and table == "Fields" else []
        if prerequisites:
            refusal_reasons.append(
                "Permanent deletion requires editing these Block templates first: " +
                ", ".join(f"{key}.{column}" for key, column in prerequisites) + "."
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
            "deletion_prerequisites": [
                {"block_key": key, "template_column": column} for key, column in prerequisites
            ],
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
