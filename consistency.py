"""
PathoPilot — Field-consistency validation.

Flags clinically-inconsistent field-value combinations within one Block
(e.g. Appendix: "fausses membranes" checked alongside a mild
appendicite_type). Warn-and-confirm, never a hard block — see PROGRESS.md
("Field-consistency validation") for the full design discussion and why
dynamic option-restriction was rejected in favor of this.

Design in one line: validate the block's *resolved* field values, at the
same point rendering.build_context() already resolves them, generically —
not by restricting widget options. This makes the check identical
regardless of whether a value arrived via manual widget selection, a
Preset default, or Quick Type: all three end up as the same resolved
dict by the time check_block() runs, so none of them can bypass it the
way a widget-layer restriction would let two of the three do.

Rules are data (Field_Consistency_Rules), authored by hand in
seed_data.py, same as Quick_Type_Tokens — no Editor UI needed yet.
"""

import database as db
from rendering import build_context


def check_block(block, field_values_override=None):
    """
    block: a block dict from database.get_preset_blocks(), including
    'fields' and 'block_id'.
    field_values_override: optional {field_key: value} from live UI
    widgets -- same parameter this project already passes to
    build_context()/render_block()/render_context_fragments() everywhere
    else, so callers don't need to pre-resolve anything themselves.

    Returns a list of message strings, one per Field_Consistency_Rules
    row that fires for this block's current resolved values. Empty list
    means fully consistent -- the common case, since most blocks have no
    rules configured at all (get_consistency_rules returns [] and this
    function short-circuits before ever calling build_context).
    """
    rules = db.get_consistency_rules(block["block_id"])
    if not rules:
        return []

    context = build_context(block, field_values_override)
    fired = []
    for rule in rules:
        value_a = context.get(rule["field_a_key"])
        value_b = context.get(rule["field_b_key"])
        if value_a in rule["field_a_values"] and value_b in rule["field_b_values"]:
            fired.append(rule["message"])
    return fired


def validate_consistency_rules(block_fields, rules):
    """
    Config-authoring sanity check, same role as
    quicktype.validate_quick_type_config(): called once from seed_data.py
    right after inserting a block's Field_Consistency_Rules rows, never
    at check time -- by the time a real value is being checked, the rule
    config it's checked against is already known good.

    block_fields: the set of field keys this Block actually uses (e.g.
    {f["key"] for f in block["fields"]}, or equivalently the field_key
    values from that block's own Block_Fields wiring in seed_data.py).
    rules: the same list of rule dicts about to be inserted -- each
    needs at least field_a_key, field_a_values, field_b_key,
    field_b_values.

    Raises ValueError with a specific, actionable message on the first
    violation found. Returns None on success.

    Checks:
    1. field_a_key and field_b_key both actually belong to this Block --
       catches a typo'd field key that would otherwise silently never
       fire (check_block() looks the key up in the resolved context via
       .get(), which returns None for a nonexistent key rather than
       raising -- so a typo here fails silent, not loud, unless caught
       here at config time).
    2. field_a_key and field_b_key are not the same field -- a rule
       comparing a field against itself is always either vacuously true
       or vacuously false and is never what was intended.
    3. field_a_values and field_b_values are both non-empty -- an empty
       value list can never match anything, making the rule dead code
       that silently never fires.
    """
    for rule in rules:
        field_a, field_b = rule["field_a_key"], rule["field_b_key"]

        if field_a not in block_fields:
            raise ValueError(
                f"Consistency rule references field '{field_a}', which isn't "
                f"one of this Block's fields ({sorted(block_fields)})."
            )
        if field_b not in block_fields:
            raise ValueError(
                f"Consistency rule references field '{field_b}', which isn't "
                f"one of this Block's fields ({sorted(block_fields)})."
            )
        if field_a == field_b:
            raise ValueError(
                f"Consistency rule compares field '{field_a}' against itself -- "
                f"field_a_key and field_b_key must be two different fields."
            )
        if not rule["field_a_values"]:
            raise ValueError(f"Consistency rule for '{field_a}' has an empty field_a_values -- can never fire.")
        if not rule["field_b_values"]:
            raise ValueError(f"Consistency rule for '{field_b}' has an empty field_b_values -- can never fire.")