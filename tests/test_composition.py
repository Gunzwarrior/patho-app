"""Stage-0 composition plumbing tests (all against the isolated DB)."""

import composition


def test_derive_block_instances_uses_preset_order_as_initial_identity():
    preset_blocks = [
        {"block_id": 9, "sort_order": 3},
        {"block_id": 4, "sort_order": 8},
    ]

    assert composition.derive_block_instances(preset_blocks) == [
        {"block_id": 9, "instance_no": 3},
        {"block_id": 4, "instance_no": 8},
    ]
