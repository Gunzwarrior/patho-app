"""
Unit tests for rendering.py. Pure-function tests need no database;
build_context() and render_block() use a real Block from the isolated
seeded test database.
"""

import pytest
import database as db_module
from rendering import (
    build_context,
    coerce_field_value,
    format_decimal_display,
    format_fragment_text,
    render_block,
    text_to_html,
)


def _block_for_preset(short_code, db, block_key=None):
    preset = next(p for p in db_module.get_all_presets() if p["short_code"] == short_code)
    blocks = db_module.get_preset_blocks(preset["id"])
    if block_key is None:
        return blocks[0]
    return next(block for block in blocks if block["key"] == block_key)


class TestFormatDecimalDisplay:
    """Regression coverage for the bug PROGRESS.md documents by name:
    a whole-number decimal (20.0) rendering with a spurious trailing
    '.0' (Gallbladder's specimen_size_cm, Appendix's appendix_size_cm,
    Thyroid's liquid_volume_ml all hit this before the _display
    companion existed)."""

    def test_whole_number_drops_trailing_zero(self):
        assert format_decimal_display(20.0) == "20"

    def test_real_fraction_is_preserved(self):
        assert format_decimal_display(15.5) == "15.5"

    def test_none_passes_through(self):
        assert format_decimal_display(None) is None


class TestFormatFragmentText:
    """French singular/plural grammar for a biopsy fragment count."""

    def test_singular(self):
        assert format_fragment_text(1) == "Un fragment biopsique inclus en totalité."

    def test_plural(self):
        assert format_fragment_text(3) == "3 fragments biopsiques inclus en totalité."

    def test_accepts_a_string_count(self):
        # coerce_field_value hands this a stored string in real use --
        # confirm the int(count) coercion inside actually happens.
        assert format_fragment_text("2") == "2 fragments biopsiques inclus en totalité."


class TestTextToHtml:
    def test_bold_markers_become_b_tags(self):
        assert text_to_html("**Examen macroscopique**") == "<b>Examen macroscopique</b>"

    def test_newlines_become_br(self):
        assert text_to_html("line one\nline two") == "line one<br>line two"

    def test_plain_text_untouched(self):
        assert text_to_html("Absence de signe de malignité.") == "Absence de signe de malignité."


class TestCoerceFieldValue:
    def test_checkbox_truthy_strings(self):
        for raw in ("1", "True", "true"):
            assert coerce_field_value("checkbox", raw) is True

    def test_checkbox_falsy_string(self):
        assert coerce_field_value("checkbox", "0") is False

    def test_number_becomes_int(self):
        assert coerce_field_value("number", "7") == 7
        assert isinstance(coerce_field_value("number", "7"), int)

    def test_decimal_becomes_float(self):
        assert coerce_field_value("decimal", "8") == 8.0
        assert isinstance(coerce_field_value("decimal", "8"), float)

    def test_select_passes_through_unchanged(self):
        assert coerce_field_value("select", "endo") == "endo"

    def test_none_passes_through_regardless_of_type(self):
        assert coerce_field_value("number", None) is None


class TestBuildContext:
    def test_resolves_seeded_values_and_decimal_display(self, db):
        appendice = _block_for_preset("dai", db)

        context = build_context(appendice)

        assert context["appendicite_type"] == "endo"
        assert context["appendix_size_cm"] == 8.0
        assert context["appendix_size_cm_display"] == "8"
        assert context["false_membranes"] is False

    def test_live_overrides_take_priority_and_are_coerced(self, db):
        appendice = _block_for_preset("dai", db)

        context = build_context(appendice, {
            "appendicite_type": "phlegmoneuse",
            "appendix_size_cm": "7.5",
            "false_membranes": "1",
        })

        assert context["appendicite_type"] == "phlegmoneuse"
        assert context["appendix_size_cm"] == 7.5
        assert context["appendix_size_cm_display"] == "7.5"
        assert context["false_membranes"] is True

    def test_adds_fragment_text_and_site_label(self, db):
        antrum = _block_for_preset("gt", db, "antrum")

        context = build_context(antrum)

        assert context["fragments"] == 3
        assert context["fragment_text"] == "3 fragments biopsiques inclus en totalité."
        assert context["site_label"] == "antrale"


class TestRenderBlock:
    def test_single_specimen_uses_macro_and_micro_headers(self, db):
        appendice = _block_for_preset("dai", db)

        micro, conclusion = render_block(appendice, total_specimens=1)

        assert micro.startswith(
            "**Examen macroscopique**\nIl s'agit d'un appendice mesurant 8 cm de longueur."
        )
        assert micro.count("**Examen macroscopique**") == 1
        assert micro.count("**Examen microscopique**") == 1
        assert "\n\n\n**Examen microscopique**\n" in micro
        assert conclusion == "Endo-appendicite aiguë."

    def test_multi_specimen_omits_exam_headers_and_applies_overrides(self, db):
        appendice = _block_for_preset("dai", db)

        micro, conclusion = render_block(appendice, {
            "appendicite_type": "phlegmoneuse",
            "appendix_size_cm": "7.5",
            "false_membranes": "1",
        }, total_specimens=2)

        assert micro.startswith(
            "Il s'agit d'un appendice mesurant 7.5 cm de longueur. Présence de fausses membranes.\n\n"
        )
        assert "Examen macroscopique" not in micro
        assert "Examen microscopique" not in micro
        assert conclusion == "Appendicite aiguë phlegmoneuse avec péri-appendicite."

    @pytest.mark.parametrize("total_specimens", [1, 2])
    def test_block_without_macro_template_renders_micro_only(self, total_specimens):
        block = {
            "fields": [],
            "macro_template": None,
            "micro_template": "Microscopie seule.",
            "conclusion_template": "Conclusion seule.",
        }

        assert render_block(block, total_specimens=total_specimens) == (
            "Microscopie seule.", "Conclusion seule."
        )
