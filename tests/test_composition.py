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


def test_remove_and_move_preserve_remaining_instance_identity():
    instances = [
        {"block_id": 1, "instance_no": 10},
        {"block_id": 2, "instance_no": 20},
        {"block_id": 3, "instance_no": 30},
    ]

    assert composition.move_instance(instances, 2, -1) == [
        {"block_id": 1, "instance_no": 10},
        {"block_id": 3, "instance_no": 30},
        {"block_id": 2, "instance_no": 20},
    ]
    assert composition.remove_instance(instances, 1) == [
        {"block_id": 1, "instance_no": 10},
        {"block_id": 3, "instance_no": 30},
    ]
