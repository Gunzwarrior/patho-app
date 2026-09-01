"""Read-only default-report previews for the Editor.

The Editor must not grow a second rendering implementation.  This module
uses the same Block, grouping, and report-shell functions as Workspace,
with the resolved defaults that a newly selected Preset starts with.
"""

import database as db
import grouping
import rendering


def render_preset_defaults(preset_id):
    """Return the complete default report and its plain-text components."""
    preset = db.get_preset_by_id(preset_id)
    if preset is None:
        return None

    blocks = db.get_preset_blocks(preset_id)
    total_specimens = len(blocks)
    micro_blocks = []
    conclusion_entries = []

    for block in blocks:
        micro_txt, conclusion_txt = rendering.render_block(
            block, total_specimens=total_specimens
        )
        header_context, _, _ = rendering.render_context_fragments(block)
        micro_blocks.append((header_context or block["name"], micro_txt))
        conclusion_entries.append({"block": block, "overrides": {}, "conc_txt": conclusion_txt})

    clinical_info = ""
    title = preset.get("default_title") or preset["name"]
    if total_specimens == 1:
        clinical_info, title_fragment, _ = rendering.render_context_fragments(blocks[0])
        if title_fragment:
            title = f"{title} {title_fragment}"

    micro_plain = rendering.format_micro_plain(micro_blocks)
    conclusion_plain, conflicts = grouping.render_conclusion_plain(conclusion_entries)
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
