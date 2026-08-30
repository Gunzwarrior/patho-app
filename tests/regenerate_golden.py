"""
Deliberately regenerate one or more golden fixtures against a preset's
CURRENT default-value output. Never run this because a test is failing
and you want it to pass -- run it only after confirming by other means
(a real docx sample, or your own explicit judgment reading the new
output) that the new rendered text is actually correct. Then read the
`git diff` on the fixture file before committing: that diff is the
record of exactly what changed and why it's safe.

Usage (from the project root, with the venv active):
    python3 tests/regenerate_golden.py dai
    python3 tests/regenerate_golden.py dai vb gt
    python3 tests/regenerate_golden.py dai_phlegmoneuse_false_membranes
    python3 tests/regenerate_golden.py synthetic_gallbladder_appendix
"""

import sys
import pathlib
import difflib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import init_db
import database as db
from golden_helpers import get_preset_blocks, render_blocks, render_preset, render_preset_defaults

FIXTURES_DIR = pathlib.Path(__file__).parent / "golden_fixtures"

# Deliberately chosen non-default baselines. The key is also the fixture
# filename prefix; values mirror live widget overrides by block sort_order.
VARIATIONS = {
    "dai_phlegmoneuse_false_membranes": (
        "dai",
        {0: {"appendicite_type": "phlegmoneuse", "false_membranes": "1"}},
    ),
}

# Deliberate multi-specimen regression cases. These are not Presets: keeping
# them synthetic avoids a testing-only item in the clinical dropdown.
SCENARIOS = {
    "synthetic_gallbladder_appendix": (("vb", "vesicule_biliaire"), ("dai", "appendice")),
    "reordered_gt_break_merge": (("gt", "antrum"), ("gt", "duodenum"), ("gt", "fundus")),
    "reordered_gt_create_merge": (("gt", "duodenum"), ("gt", "fundus"), ("gt", "antrum")),
}


def _show_diff(label, old_text, new_text):
    if old_text == new_text:
        print(f"  {label}: unchanged")
        return
    print(f"  {label}: CHANGED —")
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="before", tofile="after",
    )
    print("".join(diff))


def regenerate(fixture_key):
    if fixture_key in VARIATIONS:
        short_code, overrides_by_block = VARIATIONS[fixture_key]
        micro, conclusion, conflicts = render_preset(short_code, overrides_by_block)
        if conflicts:
            raise ValueError(f"{fixture_key} has unexpected addendum conflicts: {conflicts}")
    elif fixture_key in SCENARIOS:
        blocks = []
        for short_code, block_key in SCENARIOS[fixture_key]:
            block = next(block for block in get_preset_blocks(short_code) if block["key"] == block_key)
            blocks.append((block, {}))
        micro, conclusion, conflicts = render_blocks(blocks)
        if conflicts:
            raise ValueError(f"{fixture_key} has unexpected addendum conflicts: {conflicts}")
    else:
        micro, conclusion = render_preset_defaults(fixture_key)

    suffix = f"{fixture_key}_default" if fixture_key not in VARIATIONS | SCENARIOS else fixture_key
    micro_path = FIXTURES_DIR / f"{suffix}_micro.txt"
    conclusion_path = FIXTURES_DIR / f"{suffix}_conclusion.txt"

    old_micro = micro_path.read_text(encoding="utf-8") if micro_path.exists() else ""
    old_conclusion = conclusion_path.read_text(encoding="utf-8") if conclusion_path.exists() else ""

    print(f"{fixture_key}:")
    _show_diff("micro", old_micro, micro)
    _show_diff("conclusion", old_conclusion, conclusion)

    micro_path.write_text(micro, encoding="utf-8")
    conclusion_path.write_text(conclusion, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 tests/regenerate_golden.py <short_code> [<short_code> ...]")
        sys.exit(1)

    tmp_db = "pathology_golden_regen.db"
    init_db.setup_database(db_name=tmp_db)
    db.DB_NAME = tmp_db

    for short_code in sys.argv[1:]:
        regenerate(short_code)

    pathlib.Path(tmp_db).unlink(missing_ok=True)
    print("\nDone. Review the diffs above, then `git diff tests/golden_fixtures/` before committing.")
