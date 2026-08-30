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


def remove_instance(block_instances, index):
    """Return the composition without one instance (hard removal)."""
    return block_instances[:index] + block_instances[index + 1:]


def move_instance(block_instances, index, offset):
    """Return a copy with one instance swapped with its neighbor."""
    target = index + offset
    if not 0 <= target < len(block_instances):
        return list(block_instances)
    moved = list(block_instances)
    moved[index], moved[target] = moved[target], moved[index]
    return moved


def add_instance(block_instances, block_id):
    """Append an ad hoc Block with a per-case, non-preset identity."""
    ad_hoc_numbers = [item["instance_no"] for item in block_instances if item["instance_no"] >= 1000]
    instance_no = max(ad_hoc_numbers, default=999) + 1
    return [*block_instances, {"block_id": block_id, "instance_no": instance_no}]
