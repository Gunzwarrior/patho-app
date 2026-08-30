"""
Shared by test_golden_output.py and regenerate_golden.py -- one place
that knows how to render a preset at its default field values, so the
test and the regeneration script can never quietly drift apart from
each other.
"""

import database as db
import rendering
import grouping


def render_preset_defaults(short_code):
    """Renders a preset at its stored default field values (no
    overrides at all -- this is deliberately the "just loaded it, typed
    nothing" baseline). Returns (micro_plain, conclusion_plain), the
    same plain-text-with-**bold** strings rendering.compile_final_html()
    would HTML-ify -- golden fixtures freeze this layer, not the HTML,
    because this is where the actual clinical wording lives and it's
    far more readable to diff.
    """
    presets = db.get_all_presets()
    preset = next(p for p in presets if p["short_code"] == short_code)
    blocks = db.get_preset_blocks(preset["id"])

    micro_blocks, conclusion_entries = [], []
    for block in blocks:
        micro_txt, conc_txt = rendering.render_block(block, total_specimens=len(blocks))
        micro_blocks.append((block["name"], micro_txt))
        conclusion_entries.append({"block": block, "overrides": {}, "conc_txt": conc_txt})

    micro_plain = rendering.format_micro_plain(micro_blocks)
    conclusion_plain, conflicts = grouping.render_conclusion_plain(conclusion_entries)
    return micro_plain, conclusion_plain