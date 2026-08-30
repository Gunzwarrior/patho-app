"""
Unit tests for consistency.py -- formalizes the ad-hoc script this
project's field-consistency validation feature was actually verified
with (see PROGRESS.md, "Field-consistency validation"). Needs the `db`
fixture: check_block() and validate_consistency_rules() both need a
real Block dict, which only database.get_preset_blocks() produces.
"""

import pytest
import database as db_module
import consistency


@pytest.fixture
def appendice_block(db):
    presets = db_module.get_all_presets()
    dai = next(p for p in presets if p["short_code"] == "dai")
    blocks = db_module.get_preset_blocks(dai["id"])
    return blocks[0]


@pytest.fixture
def gallbladder_block(db):
    presets = db_module.get_all_presets()
    vb = next(p for p in presets if p["short_code"] == "vb")
    blocks = db_module.get_preset_blocks(vb["id"])
    return blocks[0]


class TestCheckBlock:
    @pytest.mark.parametrize("conflicting_type", ["endo", "suppuree", "intervalle"])
    def test_false_membranes_fires_against_each_conflicting_type(self, appendice_block, conflicting_type):
        warnings = consistency.check_block(
            appendice_block, {"false_membranes": "1", "appendicite_type": conflicting_type}
        )
        assert len(warnings) == 1

    def test_false_membranes_does_not_fire_against_compatible_type(self, appendice_block):
        warnings = consistency.check_block(
            appendice_block, {"false_membranes": "1", "appendicite_type": "phlegmoneuse"}
        )
        assert warnings == []

    def test_unchecked_false_membranes_never_fires(self, appendice_block):
        warnings = consistency.check_block(
            appendice_block, {"false_membranes": "0", "appendicite_type": "endo"}
        )
        assert warnings == []

    def test_block_with_no_rules_configured_returns_empty_list(self, gallbladder_block):
        assert consistency.check_block(gallbladder_block) == []


class TestValidateConsistencyRules:
    FIELDS = {"appendix_size_cm", "false_membranes", "appendicite_type"}

    def _rule(self, **overrides):
        base = {
            "field_a_key": "false_membranes", "field_a_values": [True],
            "field_b_key": "appendicite_type", "field_b_values": ["endo"],
        }
        base.update(overrides)
        return base

    def test_valid_config_does_not_raise(self):
        consistency.validate_consistency_rules(self.FIELDS, [self._rule()])

    def test_rejects_unknown_field_a_key(self):
        with pytest.raises(ValueError, match="typo_field"):
            consistency.validate_consistency_rules(self.FIELDS, [self._rule(field_a_key="typo_field")])

    def test_rejects_unknown_field_b_key(self):
        with pytest.raises(ValueError, match="nope"):
            consistency.validate_consistency_rules(self.FIELDS, [self._rule(field_b_key="nope")])

    def test_rejects_field_compared_against_itself(self):
        with pytest.raises(ValueError, match="against itself"):
            consistency.validate_consistency_rules(
                self.FIELDS, [self._rule(field_b_key="false_membranes", field_b_values=[False])]
            )

    def test_rejects_empty_field_a_values(self):
        with pytest.raises(ValueError, match="field_a_values"):
            consistency.validate_consistency_rules(self.FIELDS, [self._rule(field_a_values=[])])

    def test_rejects_empty_field_b_values(self):
        with pytest.raises(ValueError, match="field_b_values"):
            consistency.validate_consistency_rules(self.FIELDS, [self._rule(field_b_values=[])])