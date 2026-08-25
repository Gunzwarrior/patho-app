from jinja2 import Template
import database as db
from rendering import build_context, coerce_field_value, snippet_lookup, render_context_fragments

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
    return Template(block["conclusion_template"]).render(snippet=snippet_lookup, **context).strip()


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


def _merge_section(section_entries, index_offset):
    """
    Merges contiguous entries within ONE section (all already guaranteed to
    share the same conclusion_group) that also share an identical text
    signature. Numbers are computed from index_offset so they stay correct
    within the whole case, not just this section.

    Returns an ordered list of (number_label, text) tuples, e.g.
    [("1", "..."), ("2-3", "...")].
    """
    results = []
    i, n = 0, len(section_entries)
    while i < n:
        run = [section_entries[i]]
        signature = render_conclusion_signature(section_entries[i]["block"], section_entries[i]["overrides"])
        j = i + 1
        while j < n:
            sig_j = render_conclusion_signature(section_entries[j]["block"], section_entries[j]["overrides"])
            if sig_j != signature:
                break
            run.append(section_entries[j])
            j += 1

        numbers = "-".join(str(index_offset + k + 1) for k in range(i, j))

        if len(run) == 1:
            entry = section_entries[i]
            # A distinct, unmerged entry can optionally identify itself by
            # site — e.g. "Nodule lobaire gauche : Matériel satisfaisant...",
            # confirmed against CR_Sample.docx's two-nodule case. Reuses
            # title_fragment_template (the same short fragment that feeds
            # the report Title in the single-specimen case) rather than
            # inventing a second, parallel "site name" concept — one
            # block-level field serves both roles, depending on where it's
            # consumed. None for a block that doesn't set one (e.g. Gastric
            # Trio, Gallbladder, Appendix) — callers must treat that as "no
            # prefix," not a blank label.
            _, site_fragment = render_context_fragments(entry["block"], entry["overrides"])
            results.append((numbers, entry["conc_txt"], site_fragment or None))
        else:
            combined_label = get_combined_label(run)
            merged_text = signature.replace(GROUP_SENTINEL, combined_label)
            # A merged run already has its combined label folded into the
            # text itself (via the GROUP_SENTINEL replacement above) — a
            # separate prefix here would be redundant.
            results.append((numbers, merged_text, None))

        i = j

    return results


def _partition_into_sections(entries):
    """
    Splits the ordered entries into contiguous runs by Blocks.conclusion_group.
    A block with conclusion_group=None joins a shared default bucket — if
    NO block in the case sets conclusion_group, this produces exactly one
    section covering everything, reproducing the old (pre-sectioning)
    behavior with no forced blank lines. Sectioning only activates once
    blocks are actually tagged with real conclusion_group values.
    """
    sections = []
    current_key = object()  # sentinel: never equals a real key or None on first entry
    for idx, entry in enumerate(entries):
        key = entry["block"].get("conclusion_group")
        if not sections or key != current_key:
            sections.append({"key": key, "start": idx, "entries": []})
            current_key = key
        sections[-1]["entries"].append(entry)
    return sections


def render_conclusion_plain(entries):
    """
    entries: ordered list of {'block': block_dict, 'overrides': {...},
    'conc_txt': already-rendered text} — one per block instance, case order.

    Builds the complete conclusion plain text (with **bold** markers, ready
    for rendering.text_to_html):
    - Merges contiguous blocks sharing both an identical text signature AND
      the same conclusion_group into combined-number lines.
    - Inserts a blank line at every conclusion_group boundary, clustering
      each clinical category visually.
    - Places each case-level addendum (Fields.conclusion_addendum_template)
      right after the section it belongs to, using only that section's
      blocks to determine the value and check for disagreement — not at
      the end of the whole conclusion.

    Returns (plain_text, conflicting_field_labels) — conflicts collected
    across all sections, for a single combined warning to the user.
    """
    # Same "exactly one specimen -> no numbering at all" rule as
    # rendering.format_micro_plain, confirmed against the same real
    # sample. Computed once from the whole case, not per-section: a
    # single-specimen case only ever produces one section with one
    # entry anyway, so there's nothing to distinguish per-section here.
    single_specimen = len(entries) == 1
    sections = _partition_into_sections(entries)

    section_texts = []
    all_conflicts = []
    for section in sections:
        merged = _merge_section(section["entries"], section["start"])
        addenda, conflicts = compute_conclusion_addenda(section["entries"])
        all_conflicts.extend(conflicts)

        lines = []
        for number_label, text, site_fragment in merged:
            for j, line in enumerate(text.split("\n")):
                if j != 0:
                    prefix = ""
                elif single_specimen:
                    prefix = ""
                else:
                    prefix = f"{number_label}. "
                    if site_fragment:
                        prefix += f"{site_fragment} : "
                lines.append(f"**{prefix}{line}**")
        for line in addenda:
            lines.append(f"**{line}**")

        section_texts.append("\n".join(lines))

    return "\n\n".join(section_texts), all_conflicts


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