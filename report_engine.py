import re
from jinja2 import Template

# Lightweight Markdown-style bold convention for Master Lock manual edits.
# Plain text areas have no concept of bold, and guessing which lines
# "should" be bold from structure (numbered headers vs. bulleted labels vs.
# whatever the next case type needs) is a losing game. Instead, the user
# marks bold explicitly with **like this**, same as Markdown — works for
# any report structure without special-casing any of them.
BOLD_MARKUP_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def text_to_html(text):
    """Converts Master Lock manual-edit plain text into HTML: **bold** markup
    becomes <b>, newlines become <br>."""
    html = BOLD_MARKUP_RE.sub(r"<b>\1</b>", text)
    return html.replace("\n", "<br>")


def format_fragment_text(count):
    """Grammatically correct French description for a biopsy fragment count."""
    count = int(count)
    if count == 1:
        return "Un fragment biopsique inclus en totalité."
    return f"{count} fragments biopsiques inclus en totalité."


def coerce_field_value(field_type, raw_value):
    """Converts a stored/override string value into the right Python type
    for use inside a Jinja2 template context (checkboxes need real bools,
    numbers need real ints, otherwise Jinja truthiness/formatting breaks)."""
    if raw_value is None:
        return None
    if field_type == "checkbox":
        return str(raw_value) in ("1", "True", "true")
    if field_type == "number":
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return raw_value
    return raw_value


def build_context(block, field_values_override=None):
    """
    block: a block dict from database.get_preset_blocks(), including 'fields'.
    field_values_override: optional {field_key: value} from live UI widgets,
    which takes priority over the block's already-resolved defaults.

    Returns the dict passed into the Jinja2 template as render context.
    """
    context = {}
    for field in block["fields"]:
        value = field["value"]
        if field_values_override and field["key"] in field_values_override:
            value = field_values_override[field["key"]]
        context[field["key"]] = coerce_field_value(field["type"], value)

    if "fragments" in context and context["fragments"] is not None:
        context["fragment_text"] = format_fragment_text(context["fragments"])

    if block.get("site_label") is not None:
        context["site_label"] = block["site_label"]

    return context


def render_block(block, field_values_override=None):
    """Renders one block instance's microscopy and conclusion text."""
    context = build_context(block, field_values_override)
    micro_txt = Template(block["micro_template"]).render(**context)
    conc_txt = Template(block["conclusion_template"]).render(**context)
    return micro_txt.strip(), conc_txt.strip()


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
    import database as db
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
    format_conc_plain, e.g. [("1", "..."), ("2-3", "...")].
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


def format_micro_plain(micro_blocks):
    """
    Plain text for the microscopy section, with **bold** already at the
    block-header spots — matches the sample reports: header line bold,
    body text plain. This is the single source of truth: used for the
    auto-render preview AND as the exact pre-fill when switching into
    Master Lock, so existing bold never needs retyping.
    """
    parts = []
    for i, (name, text) in enumerate(micro_blocks):
        parts.append(f"**{i+1}. {name}**\n{text}")
    return "\n\n".join(parts)


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


def format_conc_plain(grouped_entries, addendum_lines=None):
    """
    grouped_entries: ordered list of (number_label, text) tuples — already
    passed through group_conclusions, so number_label may be "1" or a
    merged range like "2-3". Each physical line of text gets its own
    **bold** wrap, matching the sample reports.

    addendum_lines: optional list of case-level strings (from
    compute_conclusion_addenda) appended at the end, unnumbered — matching
    how the real reports state e.g. overall HP status as a final line.
    """
    lines = []
    for number_label, text in grouped_entries:
        text_lines = text.split("\n")
        for j, line in enumerate(text_lines):
            prefix = f"{number_label}. " if j == 0 else ""
            lines.append(f"**{prefix}{line}**")
    for line in (addendum_lines or []):
        lines.append(f"**{line}**")
    return "\n".join(lines)


def assemble_report_html(case_id, clinical_info, preset_title, micro_html, conc_html):
    """
    Final report shell. Takes already-converted HTML for microscopy/
    conclusion — call text_to_html() on the plain text first. Bold comes
    entirely from **markers** in that plain text now; nothing here forces
    extra bolding, so auto-render and manual edits stay visually identical
    for identical content.
    """
    return f"""
    <div style="font-family: 'Times New Roman', Times, serif; font-size: 11pt; padding: 15px; background-color: #fff; color: #000;">
        N° {case_id}<br>
        <b><i>Renseignements cliniques :</i></b> <i>{clinical_info}</i><br><br><br>
        <div style="text-align: center;"><b>{preset_title.upper()}</b></div><br><br>
        <u>MICROSCOPY:</u><br>{micro_html}<br><br><br>
        <b>CONCLUSION</b><br><br>
        {conc_html}
    </div>
    """


def compile_final_html(case_id, clinical_info, preset_title, micro_blocks, grouped_conc_entries):
    """Convenience wrapper for the auto-render path. grouped_conc_entries
    must already be the output of group_conclusions()."""
    micro_html = text_to_html(format_micro_plain(micro_blocks))
    conc_html = text_to_html(format_conc_plain(grouped_conc_entries))
    return assemble_report_html(case_id, clinical_info, preset_title, micro_html, conc_html)