"""
Golden-output regression tests. Each fixture pair in golden_fixtures/
is a frozen, known-good render of one preset at its default field
values -- catches any silent change to a template, to build_context's
resolution logic, or to the grouping/numbering engine, for a case type
nobody happens to be actively working on right now.

This file currently covers ONE preset (dai / Appendice) as the worked
example. Extending it to the other 8 real presets, plus a handful of
deliberately-chosen non-default variations (Appendix with
false_membranes=True + phlegmoneuse; a synthetic 2-specimen case to
lock in the multi-specimen numbering/header logic) is Checkpoint 5 in
TESTING.md -- left as a real, separable piece of work rather than
filled in here.

If a fixture legitimately needs to change (a real, intentional content
edit, not a regression): run
`python3 tests/regenerate_golden.py <short_code>`, then read the `git
diff` on the fixture file itself before committing -- that diff IS the
human-readable record of exactly how the rendered report changed.
Never regenerate a fixture just because a test is failing without first
confirming the NEW output is actually correct.
"""

import pathlib
import pytest

from golden_helpers import render_preset, render_preset_defaults

FIXTURES_DIR = pathlib.Path(__file__).parent / "golden_fixtures"


def _read_fixture(name):
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


@pytest.mark.usefixtures("db")
class TestGoldenOutputDefaults:
    def test_appendice_default_micro(self):
        micro, _ = render_preset_defaults("dai")
        assert micro == _read_fixture("dai_default_micro.txt")

    def test_appendice_default_conclusion(self):
        _, conclusion = render_preset_defaults("dai")
        assert conclusion == _read_fixture("dai_default_conclusion.txt")

    @pytest.mark.parametrize("short_code", ["gt", "vb"])
    def test_other_stable_default_micro(self, short_code):
        micro, _ = render_preset_defaults(short_code)
        assert micro == _read_fixture(f"{short_code}_default_micro.txt")

    @pytest.mark.parametrize("short_code", ["gt", "vb"])
    def test_other_stable_default_conclusion(self, short_code):
        _, conclusion = render_preset_defaults(short_code)
        assert conclusion == _read_fixture(f"{short_code}_default_conclusion.txt")


class TestGoldenOutputVariations:
    def test_appendice_phlegmoneuse_with_false_membranes(self):
        micro, conclusion, conflicts = render_preset("dai", {
            0: {"appendicite_type": "phlegmoneuse", "false_membranes": "1"}
        })
        assert micro == _read_fixture("dai_phlegmoneuse_false_membranes_micro.txt")
        assert conclusion == _read_fixture("dai_phlegmoneuse_false_membranes_conclusion.txt")
        assert conflicts == []
