from jinja2 import Template
import database as db
from rendering import build_context, coerce_field_value

# ---------------------------------------------------------------------------
# Grouping engine
#
# Two adjacent blocks' conclusions are safe to merge if they'd render
# identically once each block's own site_label is blanked out to a shared
# placeholder. That's the whole rule: no per-case-type logic lives here.
#
# Blocks without a site_label (site_label is None) just compare their full
# text verbatim — which is exactly literal-duplicate detection, so this same
# mechanism covers both "same diagnosis, different site" (Antrum+Fundus) and
# "identical text, no site word at all" (e.g. repeated colon polyps) without
# needing two separate code paths.
# ---------------------------------------------------------------------------

GROUP_SENTINEL = "\u241fSITE\u241f"  # a control-picture character: never
                                      # appears in real report text, so it's
                                      # safe as a temporary placeholder.


def render_conclusion_signature(block, field_values_override=None):
    """Renders this block's conclusion with site_label replaced by a fixed
    sentinel. Identical signatures between two blocks mean their conclusions
    agree on everything except which site they name."""
    context = build_context(block, field_values_override)
    if block.get("site_label") is not None:
        context["site_label"] = GROUP_SENTINEL
    return Template(block["conclusion_template"]).render(**context).strip()


def get_combined_label(run):
    """Looks up the registered French combined label for this run of blocks.
    Falls back to a plain comma-joined list of each block's own site_label
    (or name, if it has none) when no combo is registered — this is the
    common case for combos too numerous to pre-register (e.g. cervix clock
    positions: 3h+6h, 3h+9h, 3h+6h+9h...). Cases that want grammatical "et"
    phrasing (e.g. "antrale et fundique") register it explicitly in
    Conclusion_Group_Labels rather than having it guessed."""
    block_keys = [entry["block"]["key"] for entry in run]
    label = db.get_conclusion_group_label(block_keys)
    if label:
        return label
    parts = [entry["block"].get("site_label") or entry["block"]["name"] for entry in run]
    return ", ".join(parts)


def group_conclusions(entries):
    """
    entries: ordered list of {'block': block_dict, 'overrides': {...},
    'conc_txt': already-rendered text} — one per block instance, in final
    display order.

    Merges contiguous runs sharing an identical signature into one entry.
    Returns an ordered list of (number_label, text) tuples ready for
    rendering.format_conc_plain, e.g. [("1", "..."), ("2-3", "...")].
    """
    results = []
    i, n = 0, len(entries)
    while i < n:
        run = [entries[i]]
        signature = render_conclusion_signature(entries[i]["block"], entries[i]["overrides"])
        j = i + 1
        while j < n:
            sig_j = render_conclusion_signature(entries[j]["block"], entries[j]["overrides"])
            if sig_j != signature:
                break
            run.append(entries[j])
            j += 1

        numbers = "-".join(str(k + 1) for k in range(i, j))

        if len(run) == 1:
            results.append((numbers, entries[i]["conc_txt"]))
        else:
            combined_label = get_combined_label(run)
            merged_text = signature.replace(GROUP_SENTINEL, combined_label)
            results.append((numbers, merged_text))

        i = j

    return results


def compute_conclusion_addenda(entries):
    """
    Scans every field used by any block in this case that has a
    conclusion_addendum_template set. If every block using that field
    currently agrees on its value, renders the addendum once. If blocks
    disagree (e.g. HP was synced via Global Modifiers, then manually
    overridden on just one block), that field is skipped rather than
    guessing which value — or whether to combine them — is correct.

    Returns (addendum_lines, conflicting_field_labels).
    """
    seen = {}       # field_key -> [coerced values, one per block using it]
    templates = {}  # field_key -> its addendum template
    labels = {}     # field_key -> its display label (for the conflict warning)

    for entry in entries:
        for field in entry["block"]["fields"]:
            if not field.get("conclusion_addendum_template"):
                continue
            raw = entry["overrides"].get(field["key"], field["value"])
            coerced = coerce_field_value(field["type"], raw)
            seen.setdefault(field["key"], []).append(coerced)
            templates[field["key"]] = field["conclusion_addendum_template"]
            labels[field["key"]] = field["label"]

    addendum_lines, conflicts = [], []
    for field_key, values in seen.items():
        if len(set(values)) == 1:
            text = Template(templates[field_key]).render(value=values[0]).strip()
            addendum_lines.append(text)
        else:
            conflicts.append(labels[field_key])

    return addendum_lines, conflicts