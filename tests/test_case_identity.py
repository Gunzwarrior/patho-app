"""PR2 canonical Case accession identity at the operational boundaries."""

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

import bulk_intake
import database
import quicktype


def _dai_preset():
    return next(row for row in database.get_all_presets() if row["short_code"] == "dai")


def test_normalizer_resolves_supported_short_and_explicit_forms_without_losing_zeroes():
    january_2027 = date(2027, 1, 3)

    assert database.normalize_case_number("00123", current_date=january_2027) == "27PR00123"
    assert database.normalize_case_number(" 26pr00123 ", current_date=january_2027) == "26PR00123"
    assert database.normalize_case_number("26PR00123", current_date=january_2027) == "26PR00123"

    for unsupported in ("", "PR123", "2026PR123", "26XX123", "26PR", "26 PR123", "case-123"):
        with pytest.raises(database.CaseNumberError):
            database.normalize_case_number(unsupported, current_date=january_2027)


def test_case_id_normalization_does_not_change_quick_type_grammar(db):
    preset, overrides, error = quicktype.parse_quick_type("DAI37")

    assert preset is None and overrides is None
    assert "no preset short_code matches" in error


def test_database_persists_one_canonical_namespace_for_lookup_and_lifecycle(mutable_db):
    preset = _dai_preset()
    short_id = "00123"
    canonical = database.normalize_case_number(short_id)

    assert not database.save_case("case-123", preset["id"], "", {}, "<p>invalid</p>")
    assert database.save_case(short_id, preset["id"], "", {}, "<p>draft</p>")
    assert database.get_case_by_number(canonical.lower())["case_number"] == canonical
    assert database.get_all_cases(search_term=short_id)[0]["case_number"] == canonical

    # The equivalent explicit spelling names the same pending Case rather
    # than opening a second namespace entry.
    assert database.save_case(canonical.lower(), preset["id"], "updated", {}, "<p>draft</p>")
    assert database.get_case_by_number(short_id)["clinical_info"] == "updated"
    conn = database.get_db_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM Cases").fetchone()[0] == 1
    finally:
        conn.close()

    assert database.delete_pending_case(canonical.lower())
    assert database.get_case_by_number(short_id) is None

    assert database.save_case(short_id, preset["id"], "", {}, "<p>frozen</p>", status="validated")
    assert database.return_case_to_pending(canonical.lower(), "correction")
    assert database.get_case_status_history(short_id)[-1]["transition"] == "validated_to_pending"


def test_workspace_quick_type_save_and_reopen_share_the_canonical_case_id(mutable_db):
    short_id = "00456"
    canonical = database.normalize_case_number(short_id)
    app = AppTest.from_file("pages/workspace.py").run()

    app.text_input(key="case_id_0").set_value(short_id).run()
    app.text_input(key="qt_input_0").set_value("dai37").run()
    assert not app.exception
    generation = app.session_state["_form_generation"]
    assert app.text_input(key=f"case_id_{generation}").value == short_id
    next(button for button in app.button if button.label == "💾 Save as Pending").click().run()
    assert not app.exception
    assert database.get_case_by_number(canonical)["case_number"] == canonical

    reopened = AppTest.from_file("pages/workspace.py").run()
    reopened.session_state["_reopen_case_number"] = short_id
    reopened.session_state["_do_case_reopen"] = True
    reopened.run()
    reopened_generation = reopened.session_state["_form_generation"]
    assert reopened.text_input(key=f"case_id_{reopened_generation}").value == canonical


def test_workspace_duplicate_guard_collides_short_and_explicit_forms(mutable_db):
    preset = _dai_preset()
    short_id = "00789"
    canonical = database.normalize_case_number(short_id)
    assert database.save_case(canonical, preset["id"], "", {}, "<p>saved</p>")

    app = AppTest.from_file("pages/workspace.py").run()
    preset_id = preset["id"]
    app.selectbox(key="preset_select").set_value(preset_id).run()
    generation = app.session_state["_form_generation"]
    app.text_input(key=f"case_id_{generation}").set_value(short_id).run()

    assert any("already exists" in item.value for item in app.warning)
    assert not database.get_case_by_number(short_id) is None


def test_bulk_intake_normalizes_before_duplicate_detection_review_and_apply(mutable_db):
    short_id = "00042"
    canonical = database.normalize_case_number(short_id)
    source = f"{short_id},dai37\n{database.normalize_case_number('00043').lower()},etc2\n"

    review = bulk_intake.prepare_bulk_review(source, ",", False)
    assert review.applicable, review.errors
    assert tuple(row.case_number for row in review.rows) == (
        canonical, database.normalize_case_number("00043"),
    )
    assert bulk_intake.apply_bulk_review(review, source, ",", False, confirmed=True)
    assert database.get_case_by_number(short_id)["case_number"] == canonical

    equivalent = f"{short_id},dai\n{canonical.lower()},etc2\n"
    duplicate = bulk_intake.prepare_bulk_review(equivalent, ",", False)
    assert not duplicate.applicable
    assert duplicate.errors == ("Duplicate Case IDs are not allowed in one batch.",)

    existing = bulk_intake.prepare_bulk_review(f"{short_id},dai\n", ",", False)
    assert not existing.applicable
    assert existing.errors == ("One or more Case IDs already exist and cannot be imported.",)
