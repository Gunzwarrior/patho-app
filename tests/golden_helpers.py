"""
Shared by test_golden_output.py and regenerate_golden.py -- one place
that knows how to render a preset at its default field values, so the
test and the regeneration script can never quietly drift apart from
each other.
"""

import database as db
import rendering
import grouping


def get_preset_blocks(short_code):
    """Returns the ordered Blocks for one preset from the isolated DB."""
    presets = db.get_all_presets()
    preset = next(p for p in presets if p["short_code"] == short_code)
    return db.get_preset_blocks(preset["id"])


def render_blocks(blocks_with_overrides):
    """Renders an ordered case-shaped sequence of (Block, overrides)
    pairs. This supports both real presets and deliberate synthetic
    multi-specimen regressions without a fake Preset in seed data."""
    total_specimens = len(blocks_with_overrides)

    micro_blocks, conclusion_entries = [], []
    for block, overrides in blocks_with_overrides:
        micro_txt, conc_txt = rendering.render_block(
            block, overrides, total_specimens=total_specimens
        )
        micro_blocks.append((block["name"], micro_txt))
        conclusion_entries.append({"block": block, "overrides": overrides, "conc_txt": conc_txt})

    micro_plain = rendering.format_micro_plain(micro_blocks)
    conclusion_plain, conflicts = grouping.render_conclusion_plain(conclusion_entries)
    return micro_plain, conclusion_plain, conflicts


def render_preset(short_code, overrides_by_block=None):
    """Renders one preset using optional live-style overrides keyed by
    Preset_Blocks.sort_order. Returns (micro_plain, conclusion_plain,
    conflicts) at the same plain-text-with-**bold** layer the app uses
    before HTML conversion."""
    overrides_by_block = overrides_by_block or {}
    return render_blocks([
        (block, overrides_by_block.get(block["sort_order"], {}))
        for block in get_preset_blocks(short_code)
    ])


def render_preset_defaults(short_code):
    """Renders a preset at its stored default field values (no overrides)
    for the standard "just loaded it, typed nothing" golden baseline."""
    micro_plain, conclusion_plain, _ = render_preset(short_code)
    return micro_plain, conclusion_plain
