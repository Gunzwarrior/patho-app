"""Connection-aware reports using the same assembly primitives as Workspace."""

import json

import database as db
import composition
import consistency
import grouping
import rendering


def render_preset_defaults(preset_id, conn=None, strict=False):
    """Return the complete default report and its plain-text components."""
    if conn is None:
        conn = db.get_db_connection()
        try:
            return render_preset_defaults(preset_id, conn, strict)
        finally:
            conn.close()
    preset = db.get_preset_by_id_on_connection(conn, preset_id)
    if preset is None:
        return None
    blocks = db.get_preset_blocks_on_connection(conn, preset_id)
    report = render_report(conn, preset, blocks, [{} for _ in blocks], strict=strict)
    return {"preset": preset, **report}


def render_report(conn, preset, blocks, overrides, *, clinical_info="", structured=None, strict=True):
    """Render automatic content first, then preserve the saved manual locks."""
    if len(blocks) != len(overrides):
        raise ValueError("Incomplete report values.")
    structured = structured or {}
    resolver = lambda shortcut: _snippet_from_connection(conn, shortcut)
    labels = lambda keys: _label_from_connection(conn, keys)
    micro_blocks, entries, warnings = [], [], []
    for block, values in zip(blocks, overrides):
        micro, entry, messages = render_block_entry(block, values, len(blocks), conn, strict)
        micro_blocks.append(micro)
        entries.append(entry)
        warnings.extend(messages)
    title, clinical_info = automatic_context(
        preset, blocks, overrides, clinical_info, resolver, strict,
    )
    micro, conclusion, conflicts = compile_report_parts(
        micro_blocks, entries, structured.get("wildcard_notes", []), resolver, labels, strict,
    )
    if structured.get("master_lock", False):
        micro = structured.get("final_micro_edit", "")
        conclusion = structured.get("final_conc_edit", "")
    if structured.get("context_title_lock", False):
        title = structured.get("final_title_edit", "")
        clinical_info = structured.get("_saved_clinical_info", clinical_info)
    return {
        "title": title, "clinical_info": clinical_info, "micro_plain": micro,
        "conclusion_plain": conclusion, "conflicts": conflicts, "warnings": warnings,
        "html": rendering.assemble_report_html(
            clinical_info, title, rendering.text_to_html(micro), rendering.text_to_html(conclusion),
        ),
        "locks": [name for name in ("master_lock", "context_title_lock") if structured.get(name)],
    }


def render_block_entry(block, values, total, conn=None, strict=False):
    """Workspace and previews share header, body, conclusion and rule evaluation."""
    resolver = None if conn is None else lambda key: _snippet_from_connection(conn, key)
    micro, conclusion = rendering.render_block(block, values, total, resolver, strict)
    header, _, _ = rendering.render_context_fragments(block, values, resolver, strict)
    return (
        (header or block["name"], micro),
        {"block": block, "overrides": values, "conc_txt": conclusion},
        consistency.check_block(block, values, conn),
    )


def automatic_context(preset, blocks, overrides, clinical_info="", resolver=None, strict=False):
    title = preset.get("default_title") or preset["name"]
    if len(blocks) == 1:
        # Workspace composes these before medical widgets, from context fields only.
        values = {f["key"]: overrides[0][f["key"]] for f in blocks[0]["fields"]
                  if f.get("context_section") and f["key"] in overrides[0]}
        context, fragment, _ = rendering.render_context_fragments(blocks[0], values, resolver, strict)
        if fragment:
            title = f"{title} {fragment}"
        if blocks[0].get("context_template"):
            clinical_info = context
    return title, clinical_info


def compile_report_parts(micro_blocks, entries, notes=(), resolver=None, labels=None, strict=False):
    micro_blocks = list(micro_blocks)
    for note in notes:
        index = note["target_idx"]
        if 0 <= index < len(micro_blocks):
            name, text = micro_blocks[index]
            micro_blocks[index] = (name, text + "\n\n" + note["text"])
    conclusion, conflicts = grouping.render_conclusion_plain(entries, resolver, labels, strict)
    return rendering.format_micro_plain(micro_blocks), conclusion, conflicts


def render_saved_case(conn, case, strict=True):
    """Reconstruct a pending saved draft, including explicit empty composition."""
    case = dict(case)
    if case.get("status", "pending") != "pending":
        raise ValueError("Validated Cases are frozen artifacts.")
    structured = case["structured_input"]
    if isinstance(structured, str):
        structured = json.loads(structured)
    if not isinstance(structured, dict):
        raise ValueError("Invalid saved inputs.")
    preset = db.get_preset_by_id_on_connection(conn, case["preset_id"])
    if preset is None:
        raise ValueError("Saved Preset is unavailable.")
    defaults = db.get_preset_blocks_on_connection(conn, case["preset_id"])
    instances = structured.get("block_instances", composition.derive_block_instances(defaults))
    if not isinstance(instances, list):
        raise ValueError("Invalid saved composition.")
    blocks, values, seen = [], [], set()
    saved = structured.get("blocks", {})
    if not isinstance(saved, dict):
        raise ValueError("Invalid saved values.")
    for instance in instances:
        if (not isinstance(instance, dict) or set(instance) != {"block_id", "instance_no"}
                or any(type(instance[k]) is not int or instance[k] < 0 for k in instance)):
            raise ValueError("Invalid saved instance.")
        identity = (instance["block_id"], instance["instance_no"])
        if identity in seen:
            raise ValueError("Duplicate saved instance.")
        seen.add(identity)
        block = db.get_block_on_connection(conn, instance["block_id"], case["preset_id"], instance["instance_no"])
        if block is None:
            raise ValueError("Saved Block is unavailable.")
        block["instance_no"] = instance["instance_no"]
        raw = saved.get(f"{block['key']}#{instance['instance_no']}", {})
        if not isinstance(raw, dict):
            raise ValueError("Invalid saved field values.")
        normalized = {}
        for field in block["fields"]:
            value = raw.get(field["key"], field["value"])
            if field["type"] == "decimal":
                value = rendering.normalize_decimal_widget(value)
            elif field["type"] == "number":
                if (field["key"] in raw and type(value) is not int) or value is None:
                    raise ValueError("Invalid saved number.")
                value = int(value)
                if value < 0:
                    raise ValueError("Invalid saved number.")
            elif field["type"] == "select":
                if field["key"] not in raw and value not in (field["options"] or []):
                    value = (field["options"] or [None])[0]
                if value not in (field["options"] or []):
                    raise ValueError("Invalid saved selection.")
            elif field["type"] == "checkbox":
                if field["key"] in raw and type(value) is not bool:
                    raise ValueError("Invalid saved checkbox.")
                value = rendering.coerce_field_value("checkbox", value)
            elif field["type"] == "text":
                if value is not None and not isinstance(value, str):
                    raise ValueError("Invalid saved text.")
                value = value or ""
            normalized[field["key"]] = value
        blocks.append(block)
        values.append(normalized)
    for lock in ("master_lock", "context_title_lock"):
        if lock in structured and type(structured[lock]) is not bool:
            raise ValueError("Invalid saved lock.")
    for key in ("final_micro_edit", "final_conc_edit", "final_title_edit"):
        if key in structured and not isinstance(structured[key], str):
            raise ValueError("Invalid saved manual text.")
    notes = structured.get("wildcard_notes", [])
    if not isinstance(notes, list) or any(
        not isinstance(n, dict) or type(n.get("target_idx")) is not int
        or any(not isinstance(n.get(k), str) for k in ("text", "target_name", "note_type")) for n in notes
    ):
        raise ValueError("Invalid saved notes.")
    report = render_report(
        conn, preset, blocks, values, clinical_info=case.get("clinical_info") or "",
        structured={**structured, "_saved_clinical_info": case.get("clinical_info") or ""}, strict=strict,
    )
    report["instances"] = instances
    return report


def _snippet_from_connection(conn, shortcut):
    row = conn.execute("SELECT expansion FROM Snippets WHERE shortcut = ?", (shortcut,)).fetchone()
    if row is None:
        raise ValueError(f"Unresolved snippet '{shortcut}'")
    return row["expansion"]


def _label_from_connection(conn, keys):
    row = conn.execute(
        "SELECT combined_label FROM Conclusion_Group_Labels WHERE block_key_set = ?",
        (",".join(sorted(keys)),),
    ).fetchone()
    return row["combined_label"] if row else None
