"""
Unit tests for rendering.py's pure functions -- no DB needed, these
don't touch a Block/Preset at all. Fastest tests in the suite; a good
first file to point a new/unfamiliar model at, since nothing here can
touch real data even by mistake.
"""

from rendering import format_decimal_display, format_fragment_text, text_to_html, coerce_field_value


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