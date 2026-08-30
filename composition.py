"""Per-case Block-instance composition helpers.

Stage 0 only establishes the default case composition. Later stages will
add the pure add/remove/reorder operations here.
"""


def derive_block_instances(preset_blocks):
    """Return a Preset's ordered default instances for a new Case.

    A preset's current ``sort_order`` remains both the initial display
    position and the immutable instance identity. Later composition stages
    may move instances in this list, but must never change ``instance_no``.
    """
    return [
        {"block_id": block["block_id"], "instance_no": block["sort_order"]}
        for block in preset_blocks
    ]
