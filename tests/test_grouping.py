"""Unit tests for grouping.py's conclusion merging, sectioning, and
case-level addendum safety rules."""

import grouping


def _block(key, name, template, *, site_label=None, conclusion_group=None, fields=None):
    return {
        "key": key,
        "name": name,
        "site_label": site_label,
        "conclusion_group": conclusion_group,
        "conclusion_template": template,
        "fields": fields or [],
    }


def _entry(block, overrides=None, conc_txt=None):
    return {
        "block": block,
        "overrides": overrides or {},
        "conc_txt": conc_txt if conc_txt is not None else block["conclusion_template"],
    }


class TestMergeSection:
    def test_merges_contiguous_same_signature_with_case_wide_numbers(self, monkeypatch):
        monkeypatch.setattr(grouping, "get_combined_label", lambda run: "antrale et fundique")
        entries = [
            _entry(_block("antrum", "Antre", "Gastrite {{ site_label }}", site_label="antrale")),
            _entry(_block("fundus", "Fundus", "Gastrite {{ site_label }}", site_label="fundique")),
        ]

        assert grouping._merge_section(entries, index_offset=3) == [
            ("4-5", "Gastrite antrale et fundique", None)
        ]

    def test_only_contiguous_matching_entries_merge(self, monkeypatch):
        monkeypatch.setattr(grouping, "get_combined_label", lambda run: "combined")
        entries = [
            _entry(_block("a", "A", "Même {{ site_label }}", site_label="A"), conc_txt="Même A"),
            _entry(_block("b", "B", "Différent", site_label="B"), conc_txt="Différent"),
            _entry(_block("c", "C", "Même {{ site_label }}", site_label="C"), conc_txt="Même C"),
        ]

        merged = grouping._merge_section(entries, index_offset=0)
        assert [item[:2] for item in merged] == [("1", "Même A"), ("2", "Différent"), ("3", "Même C")]

    def test_reordering_can_break_a_merge(self, monkeypatch):
        monkeypatch.setattr(grouping, "get_combined_label", lambda run: "combined")
        a = _entry(_block("a", "A", "Même {{ site_label }}", site_label="A"), conc_txt="Même A")
        b = _entry(_block("b", "B", "Même {{ site_label }}", site_label="B"), conc_txt="Même B")
        other = _entry(_block("other", "Other", "Autre"), conc_txt="Autre")

        assert [item[:2] for item in grouping._merge_section([a, other, b], 0)] == [
            ("1", "Même A"), ("2", "Autre"), ("3", "Même B")
        ]

    def test_reordering_can_create_a_merge(self, monkeypatch):
        monkeypatch.setattr(grouping, "get_combined_label", lambda run: "combined")
        a = _entry(_block("a", "A", "Même {{ site_label }}", site_label="A"), conc_txt="Même A")
        b = _entry(_block("b", "B", "Même {{ site_label }}", site_label="B"), conc_txt="Même B")
        other = _entry(_block("other", "Other", "Autre"), conc_txt="Autre")

        assert [item[:2] for item in grouping._merge_section([a, b, other], 0)] == [
            ("1-2", "Même combined"), ("3", "Autre")
        ]


class TestPartitionIntoSections:
    def test_no_conclusion_groups_keeps_one_section(self):
        entries = [_entry(_block("a", "A", "A")), _entry(_block("b", "B", "B"))]

        sections = grouping._partition_into_sections(entries)
        assert len(sections) == 1
        assert sections[0]["key"] is None
        assert sections[0]["start"] == 0

    def test_group_boundary_starts_a_new_section_and_keeps_case_index(self):
        entries = [
            _entry(_block("a", "A", "A", conclusion_group="gastric")),
            _entry(_block("b", "B", "B", conclusion_group="gastric")),
            _entry(_block("c", "C", "C", conclusion_group="duodenum")),
        ]

        sections = grouping._partition_into_sections(entries)
        assert [(section["key"], section["start"], len(section["entries"])) for section in sections] == [
            ("gastric", 0, 2), ("duodenum", 2, 1)
        ]

        text, conflicts = grouping.render_conclusion_plain(entries)
        assert text == "**1. A**\n**2. B**\n\n**3. C**"
        assert conflicts == []


class TestConclusionAddenda:
    HP_FIELD = {
        "key": "hp_positive",
        "label": "Helicobacter pylori positif",
        "type": "checkbox",
        "value": "1",
        "conclusion_addendum_template": "Helicobacter: {{ value }}",
    }

    def test_agreed_case_level_value_renders_once(self):
        block_a = _block("a", "A", "A", fields=[self.HP_FIELD])
        block_b = _block("b", "B", "B", fields=[self.HP_FIELD])

        addenda, conflicts = grouping.compute_conclusion_addenda([_entry(block_a), _entry(block_b)])
        assert addenda == ["Helicobacter: True"]
        assert conflicts == []

    def test_conflicting_case_level_values_drop_addendum_and_report_label(self):
        block_a = _block("a", "A", "A", fields=[self.HP_FIELD])
        block_b = _block("b", "B", "B", fields=[self.HP_FIELD])

        addenda, conflicts = grouping.compute_conclusion_addenda([
            _entry(block_a, {"hp_positive": "1"}),
            _entry(block_b, {"hp_positive": "0"}),
        ])
        assert addenda == []
        assert conflicts == ["Helicobacter pylori positif"]
