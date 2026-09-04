"""Read-only default-report previews for the Editor.

The Editor must not grow a second rendering implementation.  This module
uses the same Block, grouping, and report-shell functions as Workspace,
with the resolved defaults that a newly selected Preset starts with.
"""

import database as db
import grouping
import rendering


def render_preset_defaults(preset_id, conn=None, strict=False):
    """Return the complete default report and its plain-text components."""
    preset = db.get_preset_by_id_on_connection(conn, preset_id) if conn else db.get_preset_by_id(preset_id)
    if preset is None:
        return None

    blocks = db.get_preset_blocks_on_connection(conn, preset_id) if conn else db.get_preset_blocks(preset_id)
    snippet_resolver = None if conn is None else lambda shortcut: _snippet_from_connection(conn, shortcut)
    label_lookup = None if conn is None else lambda keys: _label_from_connection(conn, keys)
    total_specimens = len(blocks)
    micro_blocks = []
    conclusion_entries = []

    for block in blocks:
        micro_txt, conclusion_txt = rendering.render_block(
            block, total_specimens=total_specimens, snippet_resolver=snippet_resolver, strict=strict
        )
        header_context, _, _ = rendering.render_context_fragments(block, snippet_resolver=snippet_resolver, strict=strict)
        micro_blocks.append((header_context or block["name"], micro_txt))
        conclusion_entries.append({"block": block, "overrides": {}, "conc_txt": conclusion_txt})

    clinical_info = ""
    title = preset.get("default_title") or preset["name"]
    if total_specimens == 1:
        clinical_info, title_fragment, _ = rendering.render_context_fragments(blocks[0], snippet_resolver=snippet_resolver, strict=strict)
        if title_fragment:
            title = f"{title} {title_fragment}"

    micro_plain = rendering.format_micro_plain(micro_blocks)
    conclusion_plain, conflicts = grouping.render_conclusion_plain(conclusion_entries, snippet_resolver, label_lookup, strict)
    html = rendering.assemble_report_html(
        clinical_info,
        title,
        rendering.text_to_html(micro_plain),
        rendering.text_to_html(conclusion_plain),
    )
    return {
        "preset": preset,
        "title": title,
        "clinical_info": clinical_info,
        "micro_plain": micro_plain,
        "conclusion_plain": conclusion_plain,
        "conflicts": conflicts,
        "html": html,
    }


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
