"""Checkpoint 4 Content Studio AppTests.

These assert the UI boundary: no guided action writes until the shared,
server-held candidate is prepared and then explicitly applied.
"""

from streamlit.testing.v1 import AppTest
import pytest

import content_editing
import content_changes
import content_snapshot
import content_studio
import change_packages
import database
from test_stage5_packages import envelope, raw


def _app(mutable_db):
    content_editing.record_initial_snapshot("a" * 64)
    app = AppTest.from_file("pages/editor.py").run()
    assert not app.exception
    return app


def _button(app, label):
    return next(widget for widget in app.button if widget.label == label)


def _select_field(app, key):
    field = next(row for row in database.get_all_fields(include_archived=True) if row["key"] == key)
    app.selectbox(key="editor_studio_field_select").set_value(field["id"]).run()
    return field


def _prepare_snippet(app, shortcut="studio_state_probe"):
    app.radio(key="editor_studio_kind").set_value("Snippets").run()
    app.checkbox(key="editor_studio_snippet_create").set_value(True).run()
    next(widget for widget in app.text_input if widget.label == "Shortcut").set_value(shortcut)
    next(widget for widget in app.text_area if widget.label == "Expansion").set_value("Review state probe")
    _button(app, "Prepare Snippet review").click().run()


def test_content_studio_shell_has_no_legacy_immediate_save_path(mutable_db):
    app = _app(mutable_db)

    assert app.radio(key="editor_section").value == "Content Studio"
    labels = {widget.label for widget in app.button}
    assert "Prepare Field review" in labels
    assert not labels & {"Save Field wording", "Save Snippet", "Create Snippet"}
    assert app.radio(key="editor_studio_filter").value == "Active"


def test_create_snippet_stops_at_frozen_review_until_apply(mutable_db):
    app = _app(mutable_db)
    app.radio(key="editor_studio_kind").set_value("Snippets").run()
    app.checkbox(key="editor_studio_snippet_create").set_value(True).run()
    next(widget for widget in app.text_input if widget.label == "Shortcut").set_value("studio_ui_probe")
    next(widget for widget in app.text_area if widget.label == "Expansion").set_value("Prepared only")
    _button(app, "Prepare Snippet review").click().run()

    assert not app.exception
    assert database.get_snippet_by_shortcut("studio_ui_probe", include_archived=True) is None
    assert "_editor_studio_review" in app.session_state.filtered_state
    assert any(item.value == "Frozen Content Studio review" for item in app.subheader)
    assert "Edit draft" in {widget.label for widget in app.button}


def test_confirmed_content_studio_review_applies_only_after_the_second_action(mutable_db):
    app = _app(mutable_db)
    app.radio(key="editor_studio_kind").set_value("Snippets").run()
    app.checkbox(key="editor_studio_snippet_create").set_value(True).run()
    next(widget for widget in app.text_input if widget.label == "Shortcut").set_value("studio_apply_probe")
    next(widget for widget in app.text_area if widget.label == "Expansion").set_value("Applied after review")
    _button(app, "Prepare Snippet review").click().run()

    assert database.get_snippet_by_shortcut("studio_apply_probe", include_archived=True) is None
    confirmation = next(widget for widget in app.checkbox if widget.label.startswith("I confirm this exact reviewed"))
    confirmation.set_value(True).run()
    _button(app, "Apply reviewed Content Studio change").click().run()

    saved = database.get_snippet_by_shortcut("studio_apply_probe", include_archived=True)
    assert saved is not None
    assert saved["expansion"] == "Applied after review"
    assert "_editor_studio_review" not in app.session_state.filtered_state


def test_field_type_change_rebuilds_typed_default_before_creation(mutable_db):
    app = _app(mutable_db)
    app.checkbox(key="editor_studio_field_create").set_value(True).run()

    type_picker = app.selectbox(key="editor_studio_field_type_0")
    assert type_picker.value == "text"
    type_picker.set_value("checkbox").run()
    assert any(widget.label == "Default value" for widget in app.checkbox)
    assert not any(widget.label == "Default value" for widget in app.text_input)

    app.selectbox(key="editor_studio_field_type_0").set_value("number").run()
    default = next(widget for widget in app.number_input if widget.label == "Default value")
    default.set_value(7).run()
    assert next(widget for widget in app.number_input if widget.label == "Default value").value == 7


def test_decimal_field_default_prepares_review_without_traceback(mutable_db):
    app = _app(mutable_db)
    app.checkbox(key="editor_studio_field_create").set_value(True).run()
    app.selectbox(key="editor_studio_field_type_0").set_value("decimal").run()
    next(widget for widget in app.text_input if widget.label == "Stable key").set_value("ui_decimal_default")
    next(widget for widget in app.text_input if widget.label == "Label").set_value("Mesure décimale")
    next(widget for widget in app.number_input if widget.label == "Default value").set_value(2.5)
    _button(app, "Prepare Field review").click().run()

    assert not app.exception
    assert "_editor_studio_review" in app.session_state.filtered_state
    assert not any("Supply a valid value" in item.value for item in app.error)


def test_nullable_decimal_default_is_not_synthesized_by_noop_or_label_edit(mutable_db):
    app = _app(mutable_db)
    field = _select_field(app, "nodule_size_mm")
    assert field["default_value"] is None
    assert app.checkbox(key=f"editor_studio_field_no_default_{field['id']}_0").value is True

    _button(app, "Prepare Field review").click().run()
    assert "_editor_studio_review" not in app.session_state.filtered_state
    assert next(row for row in database.get_all_fields(include_archived=True)
                if row["key"] == "nodule_size_mm")["default_value"] is None

    _select_field(app, "nodule_size_mm")
    next(widget for widget in app.text_input if widget.label == "Label").set_value("Taille nodulaire")
    _button(app, "Prepare Field review").click().run()
    confirm = next(widget for widget in app.checkbox if widget.label.startswith("I confirm this exact reviewed"))
    confirm.set_value(True).run()
    _button(app, "Apply reviewed Content Studio change").click().run()
    saved = next(row for row in database.get_all_fields(include_archived=True) if row["key"] == "nodule_size_mm")
    assert saved["label"] == "Taille nodulaire" and saved["default_value"] is None


@pytest.mark.parametrize("key, expected", [
    ("nodule_site", ""),       # established blank select default
    ("fragments", "1"),         # number storage
    ("false_membranes", "0"),   # checkbox storage
])
def test_existing_typed_defaults_round_trip_without_synthetic_candidate(mutable_db, key, expected):
    app = _app(mutable_db)
    _select_field(app, key)
    _button(app, "Prepare Field review").click().run()
    assert "_editor_studio_review" not in app.session_state.filtered_state
    assert next(row for row in database.get_all_fields(include_archived=True)
                if row["key"] == key)["default_value"] == expected


def test_nullable_decimal_default_can_be_explicitly_set_and_unset(mutable_db):
    app = _app(mutable_db)
    field = _select_field(app, "nodule_size_mm")
    app.checkbox(key=f"editor_studio_field_no_default_{field['id']}_0").set_value(False).run()
    next(widget for widget in app.number_input if widget.label == "Default value").set_value(2.5)
    _button(app, "Prepare Field review").click().run()
    next(widget for widget in app.checkbox if widget.label.startswith("I confirm this exact reviewed")).set_value(True).run()
    _button(app, "Apply reviewed Content Studio change").click().run()
    assert next(row for row in database.get_all_fields(include_archived=True)
                if row["key"] == "nodule_size_mm")["default_value"] == "2.5"

    app = _app(mutable_db)  # Fresh browser session for the explicit unset path.
    field = _select_field(app, "nodule_size_mm")
    next(widget for widget in app.checkbox if widget.label == "No global default").set_value(True).run()
    _button(app, "Prepare Field review").click().run()
    next(widget for widget in app.checkbox if widget.label.startswith("I confirm this exact reviewed")).set_value(True).run()
    _button(app, "Apply reviewed Content Studio change").click().run()
    assert next(row for row in database.get_all_fields(include_archived=True)
                if row["key"] == "nodule_size_mm")["default_value"] is None


def test_confirmed_studio_review_clears_on_navigation_and_does_not_resurrect(mutable_db):
    app = _app(mutable_db)
    _prepare_snippet(app)
    confirm = next(widget for widget in app.checkbox if widget.label.startswith("I confirm this exact reviewed"))
    confirm.set_value(True).run()
    app.radio(key="editor_studio_filter").set_value("All").run()
    assert "_editor_studio_review" not in app.session_state.filtered_state


def test_confirmed_studio_review_clears_when_draft_generation_advances(mutable_db):
    app = _app(mutable_db)
    _prepare_snippet(app, "studio_generation_probe")
    confirmation = next(widget for widget in app.checkbox if widget.label.startswith("I confirm this exact reviewed"))
    confirmation.set_value(True).run()
    assert not _button(app, "Apply reviewed Content Studio change").disabled

    app.session_state["_editor_studio_form_generation"] = 1
    app.run()

    assert "_editor_studio_review" not in app.session_state.filtered_state
    assert not any(widget.label.startswith("I confirm this exact reviewed") for widget in app.checkbox)
    assert "Apply reviewed Content Studio change" not in {widget.label for widget in app.button}


def test_required_discrete_default_does_not_offer_impossible_unset(mutable_db):
    app = _app(mutable_db)
    _select_field(app, "fragments")
    assert not any(widget.label == "No global default" for widget in app.checkbox)
    assert any("cannot be cleared" in item.value for item in app.caption)


def test_studio_invalidation_preserves_actual_ai_review_and_error_state(mutable_db):
    app = _app(mutable_db)
    ai_review = change_packages.dry_run(raw(envelope(mutable_db)))
    ai_error = {"errors": [{"code": "contract", "path": "package"}], "omitted_errors": 0}
    app.session_state["_editor_ai_review"] = ai_review
    app.session_state["_editor_ai_feedback"] = ai_error
    app.session_state["_editor_ai_local_error"] = RuntimeError("local-only")
    app.session_state["_editor_ai_review_generation"] = 37

    _prepare_snippet(app, "studio_ai_isolation_probe")
    app.radio(key="editor_studio_filter").set_value("All").run()

    assert "_editor_studio_review" not in app.session_state.filtered_state
    assert app.session_state["_editor_ai_review"] is ai_review
    assert app.session_state["_editor_ai_feedback"] == ai_error
    assert str(app.session_state["_editor_ai_local_error"]) == "local-only"
    assert app.session_state["_editor_ai_review_generation"] == 37

    _prepare_snippet(app, "studio_section_probe")
    app.radio(key="editor_section").set_value("Blocks").run()
    app.radio(key="editor_section").set_value("Content Studio").run()
    assert "_editor_studio_review" not in app.session_state.filtered_state


def test_existing_group_label_retains_archived_block_without_offering_it_to_new_labels(mutable_db):
    block = "antrum"
    content_editing.record_initial_snapshot("a" * 64)
    review = content_studio.review(
        [content_studio.operation("archive", "Blocks", block)],
        content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot()),
        summary="archive grouped block",
    )
    content_changes.apply_review(review)
    app = _app(mutable_db)
    app.radio(key="editor_studio_kind").set_value("Group labels").run()
    app.selectbox(key="editor_studio_group_select").set_value("antrum,fundus").run()
    assert not app.exception
    selected = next(widget for widget in app.multiselect if widget.label == "Blocks")
    assert "antrum" in selected.value

    app.selectbox(key="editor_studio_group_select").set_value("__new__").run()
    new_options = next(widget for widget in app.multiselect if widget.label == "Blocks").options
    assert all("antrum" not in option for option in new_options)


def test_archived_filter_and_badge_are_content_studio_local(mutable_db):
    app = _app(mutable_db)
    field = next(row for row in database.get_all_fields() if row["key"] == "appendicite_type")
    # Make this setup change through the already-reviewed backend rather than
    # a direct SQL write, then prove the Studio can expose archived content.
    import content_snapshot
    import content_studio
    import content_changes
    review = content_studio.review(
        [content_studio.operation("archive", "Fields", field["key"])],
        content_snapshot.content_snapshot_hash(content_snapshot.export_content_snapshot()),
        summary="test archive field",
    )
    content_changes.apply_review(review)
    app.run()
    app.radio(key="editor_studio_filter").set_value("Archived").run()

    selector = app.selectbox(key="editor_studio_field_select")
    assert any("appendicite_type" in option and "archived" in option for option in selector.options)
