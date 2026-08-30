"""
Golden-output regression tests. Each fixture pair in golden_fixtures/
is a frozen, known-good render of one preset at its default field
values -- catches any silent change to a template, to build_context's
resolution logic, or to the grouping/numbering engine, for a case type
nobody happens to be actively working on right now.

This file covers the stable current presets (`dai`, `gt`, `vb`), one
deliberately chosen Appendix variation, and a synthetic two-specimen
case that locks the historically fragile numbering/header behavior.
Thyroid fixtures are deliberately deferred until `etc0`–`etc5` have
been consolidated into Quick Type; TESTING.md tracks that boundary.

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

from golden_helpers import get_preset_blocks, render_blocks, render_preset, render_preset_defaults

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

    def test_synthetic_gallbladder_appendix_multi_specimen_case(self):
        gallbladder = get_preset_blocks("vb")[0]
        appendix = get_preset_blocks("dai")[0]
        micro, conclusion, conflicts = render_blocks([(gallbladder, {}), (appendix, {})])

        assert micro == _read_fixture("synthetic_gallbladder_appendix_micro.txt")
        assert conclusion == _read_fixture("synthetic_gallbladder_appendix_conclusion.txt")
        assert conflicts == []
