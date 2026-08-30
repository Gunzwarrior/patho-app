"""
Unit tests for quicktype.py. Covers configuration validation, pure token
parsing (including measurement caps and multi-block rollover/skip
semantics), and the DB-backed parse_quick_type() entry point.
"""

import pytest
import quicktype
from quicktype import validate_quick_type_config, find_preset_by_prefix, parse_tokens


class TestValidateQuickTypeConfig:
    def test_empty_tokens_is_valid(self):
        validate_quick_type_config([])

    def test_rejects_multi_character_lookup_key(self):
        tokens = [{"field_key": "x", "token_kind": "lookup", "lookup_table": {"ab": "y"}}]
        with pytest.raises(ValueError, match="longer than 1"):
            validate_quick_type_config(tokens)

    def test_rejects_reserved_character_as_lookup_key(self):
        tokens = [{"field_key": "x", "token_kind": "lookup", "lookup_table": {"!": "y"}}]
        with pytest.raises(ValueError, match="reserved"):
            validate_quick_type_config(tokens)

    def test_rejects_empty_lookup_table(self):
        tokens = [{"field_key": "x", "token_kind": "lookup", "lookup_table": {}}]
        with pytest.raises(ValueError, match="empty lookup_table"):
            validate_quick_type_config(tokens)

    def test_rejects_unknown_token_kind(self):
        tokens = [{"field_key": "x", "token_kind": "flag"}]
        with pytest.raises(ValueError, match="Unknown token_kind 'flag'"):
            validate_quick_type_config(tokens)

    def test_rejects_ambiguous_measurement_before_digit_keyed_token(self):
        tokens = [
            {"field_key": "size", "token_kind": "measurement", "block_sort_order": 0},
            {"field_key": "x", "token_kind": "lookup", "lookup_table": {"1": "y"}, "block_sort_order": 0},
        ]
        with pytest.raises(ValueError, match="ambiguous"):
            validate_quick_type_config(tokens)

    def test_measurement_last_in_sequence_is_fine(self):
        tokens = [
            {"field_key": "x", "token_kind": "lookup", "lookup_table": {"1": "y"}, "block_sort_order": 0},
            {"field_key": "size", "token_kind": "measurement", "block_sort_order": 0},
        ]
        validate_quick_type_config(tokens)

    def test_rejects_non_contiguous_block_tokens(self):
        tokens = [
            {"field_key": "first", "token_kind": "lookup", "lookup_table": {"a": "a"}, "block_sort_order": 0},
            {"field_key": "second", "token_kind": "lookup", "lookup_table": {"b": "b"}, "block_sort_order": 1},
            {"field_key": "third", "token_kind": "lookup", "lookup_table": {"c": "c"}, "block_sort_order": 0},
        ]
        with pytest.raises(ValueError, match="reappears non-contiguously"):
            validate_quick_type_config(tokens)


class TestFindPresetByPrefix:
    PRESETS = [{"id": 1, "short_code": "dai"}, {"id": 2, "short_code": "vb"}]

    def test_matches_prefix_and_returns_remainder(self):
        preset, remainder = find_preset_by_prefix("dai37", self.PRESETS)
        assert preset["short_code"] == "dai"
        assert remainder == "37"

    def test_no_match_returns_none_none(self):
        preset, remainder = find_preset_by_prefix("zzz", self.PRESETS)
        assert preset is None and remainder is None


class TestParseTokens:
    TOKENS = [
        {"field_key": "appendicite_type", "token_kind": "lookup", "lookup_table": {"3": "periappendicite"}, "block_sort_order": 0},
        {"field_key": "appendix_size_cm", "token_kind": "measurement", "lookup_table": None, "digit_width": 2, "block_sort_order": 0},
    ]

    def test_no_tokens_configured_and_no_remainder_is_valid(self):
        overrides, error = parse_tokens("", [])
        assert overrides == {} and error is None

    def test_no_tokens_configured_but_remainder_left_is_an_error(self):
        overrides, error = parse_tokens("37", [])
        assert overrides is None and "left over" in error

    def test_bang_with_nothing_to_skip_to_is_an_error(self):
        tokens = [{"field_key": "x", "token_kind": "lookup", "lookup_table": {"1": "y"}, "block_sort_order": 0}]
        overrides, error = parse_tokens("!", tokens)
        assert overrides is None and "no next block" in error

    def test_consumes_lookup_token(self):
        overrides, error = parse_tokens("3", self.TOKENS)
        assert error is None
        assert overrides == {0: {"appendicite_type": "periappendicite"}}

    def test_consumes_measurement_token_after_lookup(self):
        overrides, error = parse_tokens("37", self.TOKENS)
        assert error is None
        assert overrides == {
            0: {"appendicite_type": "periappendicite", "appendix_size_cm": "7"}
        }

    def test_digit_width_leaves_excess_digits_unparsed(self):
        overrides, error = parse_tokens("3123", self.TOKENS)
        assert overrides is None
        assert "'3' left over" in error

    def test_auto_rolls_into_next_block(self):
        tokens = [
            {"field_key": "first", "token_kind": "lookup", "lookup_table": {"a": "one"}, "block_sort_order": 0},
            {"field_key": "second", "token_kind": "lookup", "lookup_table": {"b": "two"}, "block_sort_order": 1},
        ]
        overrides, error = parse_tokens("ab", tokens)
        assert error is None
        assert overrides == {0: {"first": "one"}, 1: {"second": "two"}}

    def test_bang_skips_remaining_tokens_in_current_block(self):
        tokens = [
            {"field_key": "first", "token_kind": "lookup", "lookup_table": {"a": "one"}, "block_sort_order": 0},
            {"field_key": "skipped", "token_kind": "lookup", "lookup_table": {"x": "skip"}, "block_sort_order": 0},
            {"field_key": "second", "token_kind": "lookup", "lookup_table": {"b": "two"}, "block_sort_order": 1},
        ]
        overrides, error = parse_tokens("a!b", tokens)
        assert error is None
        assert overrides == {0: {"first": "one"}, 1: {"second": "two"}}

    def test_bang_after_auto_rollover_skips_the_current_block(self):
        tokens = [
            {"field_key": "first", "token_kind": "lookup", "lookup_table": {"a": "one"}, "block_sort_order": 0},
            {"field_key": "second", "token_kind": "lookup", "lookup_table": {"b": "two"}, "block_sort_order": 1},
            {"field_key": "third", "token_kind": "lookup", "lookup_table": {"c": "three"}, "block_sort_order": 2},
        ]
        overrides, error = parse_tokens("a!c", tokens)
        assert error is None
        assert overrides == {0: {"first": "one"}, 2: {"third": "three"}}


class TestParseQuickType:
    def test_db_backed_appendix_code_resolves_preset_and_overrides(self, db):
        preset, overrides, error = quicktype.parse_quick_type(" dai37 ")
        assert error is None
        assert preset["short_code"] == "dai"
        assert overrides == {
            0: {"appendicite_type": "periappendicite", "appendix_size_cm": "7"}
        }

    def test_db_backed_bare_code_without_tokens_is_valid(self, db):
        preset, overrides, error = quicktype.parse_quick_type("vb")
        assert error is None
        assert preset["short_code"] == "vb"
        assert overrides == {}

    def test_rejects_unknown_db_backed_code(self, db):
        preset, overrides, error = quicktype.parse_quick_type("unknown")
        assert preset is None and overrides is None
        assert "no preset short_code matches" in error
