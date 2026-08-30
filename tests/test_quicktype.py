"""
Starter unit tests for quicktype.py. This file is intentionally partial
-- it covers the config validator and the pure parsing functions, not
yet the DB-backed parse_quick_type() entry point or every rejection
path validate_quick_type_config() checks. Left as an explicit gap for
Checkpoint 2 (see TESTING.md) rather than filled in here, so a model
picking that checkpoint up has a real, separable piece of work.
"""

import pytest
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