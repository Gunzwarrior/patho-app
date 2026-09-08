"""Checkpoint 3 Editor flow and restricted report-presentation coverage."""

import hashlib
import json
import sqlite3

from streamlit.proto.Common_pb2 import FileURLs as FileURLsProto
from streamlit.runtime.uploaded_file_manager import UploadedFile, UploadedFileRec
from streamlit.testing.v1 import AppTest

import change_packages
import content_changes
import content_editing
import content_snapshot
import database
from report_presentation import restricted_report_html
from test_stage5_packages import envelope, graph, raw, save_synthetic_case


def _file(payload, *, file_id="upload", name="package.json"):
    data = payload if isinstance(payload, bytes) else raw(payload)
    return UploadedFile(
        UploadedFileRec(file_id, name, "application/json", data),
        FileURLsProto(),
    )


def _open_ai(file=None):
    app = AppTest.from_file("pages/editor.py")
    app.run()
    app.radio(key="editor_section").set_value("AI package").run()
    if file is not None:
        _rerun_with_file(app, file)
    return app


def _upload_key(app):
    try:
        generation = app.session_state["_editor_ai_generation"]
    except KeyError:
        generation = 0
    return f"editor_ai_upload_{generation}"


def _rerun_with_file(app, file):
    app.session_state[_upload_key(app)] = file
    app.run()
    assert not app.exception
    return app


def _click_with_file(app, key, file):
    app.button(key=key).click()
    app.session_state[_upload_key(app)] = file
    app.run()
    assert not app.exception
    return app


def _set_checkbox_with_file(app, key, value, file):
    app.checkbox(key=key).set_value(value)
    app.session_state[_upload_key(app)] = file
    app.run()
    assert not app.exception
    return app


def _review(app, file):
    try:
        generation = app.session_state["_editor_ai_generation"]
    except KeyError:
        generation = 0
    _click_with_file(app, f"editor_ai_dry_run_{generation}", file)
    assert app.session_state["_editor_ai_review"] is not None
    return app


def _unlock():
    content_editing.record_initial_snapshot("a" * 64)


def _confirmation_key(app):
    try:
        generation = app.session_state["_editor_ai_generation"]
    except KeyError:
        generation = 0
    review_generation = app.session_state["_editor_ai_review_generation"]
    return f"editor_ai_review_{generation}_{review_generation}_confirm"


def _apply_key(app):
    try:
        generation = app.session_state["_editor_ai_generation"]
    except KeyError:
        generation = 0
    review_generation = app.session_state["_editor_ai_review_generation"]
    return f"editor_ai_review_{generation}_{review_generation}_apply"


def test_restricted_report_html_preserves_formatting_and_removes_active_content():
    source = (
        '<div style="font-family: Times; text-align: center; background-image: url(https://tracker.test/x)" '
        'onclick="steal()"><b>Visible</b><br><img src="https://tracker.test/pixel">'
        '<a href="https://tracker.test/visit">link</a><script>alert("PRIVATE")</script>'
        '<iframe srcdoc="PRIVATE"></iframe><table><tr><td colspan="2">Cell</td></tr></table></div>'
    )
    rendered = restricted_report_html(source)

    assert "<div" in rendered and "font-family: Times" in rendered
    assert "text-align: center" in rendered and "<b>Visible</b><br>" in rendered
    assert "<table><tr><td colspan=\"2\">Cell</td></tr></table>" in rendered
    assert "link" in rendered
    for forbidden in ("onclick", "tracker.test", "<img", "<script", "alert", "PRIVATE", "<iframe", "background-image"):
        assert forbidden not in rendered


def test_ai_section_is_persistent_and_describes_exact_private_export(db):
    app = _open_ai()

    assert app.radio(key="editor_section").value == "AI package"
    app.run()
    assert app.radio(key="editor_section").value == "AI package"
    text = "\n".join(
        [item.value for item in (*app.info, *app.warning, *app.caption, *app.code)]
    )
    payload = change_packages.export_ai_context(db)
    snapshot_hash = json.loads(payload)["snapshot_sha256"]
    assert "no network request" in text and "no AI account" in text
    assert "not a recovery snapshot" in text and "recovery snapshot" in text
    assert "Case records" in text and "patient details" in text
    assert f"{len(payload):,} UTF-8 bytes" in text and snapshot_hash in text
    assert hashlib.sha256(payload).hexdigest() in text
    assert app.button(key="editor_ai_dry_run_0").disabled


def test_review_persists_but_same_name_replacement_and_removal_clear_it(mutable_db):
    first = _file(envelope(mutable_db), file_id="one", name="same.json")
    app = _review(_open_ai(first), first)
    confirm_key = _confirmation_key(app)
    _set_checkbox_with_file(app, confirm_key, True, first)
    assert app.session_state["_editor_ai_review"] is not None
    assert app.checkbox(key=confirm_key).value is True

    _rerun_with_file(app, first)
    assert app.session_state["_editor_ai_review"] is not None
    replacement_payload = envelope(mutable_db)
    replacement_payload["summary"] = "Same filename, different bytes"
    replacement = _file(replacement_payload, file_id="two", name="same.json")
    _rerun_with_file(app, replacement)
    assert "_editor_ai_review" not in app.session_state.filtered_state
    assert "_editor_ai_confirmed_review" not in app.session_state.filtered_state

    _review(app, replacement)
    app.run()  # AppTest represents removing the file as a None uploader value.
    assert "_editor_ai_review" not in app.session_state.filtered_state


def test_failed_new_dry_run_clears_success_and_feedback_is_fixed(mutable_db):
    good = _file(envelope(mutable_db), file_id="good", name="same.json")
    app = _review(_open_ai(good), good)
    bad = _file(b'{"format":"PATIENT-PRIVATE-CANARY"}', file_id="bad", name="same.json")
    _click_with_file(app, "editor_ai_dry_run_0", bad)

    assert "_editor_ai_review" not in app.session_state.filtered_state
    assert any("Dry run failed" in item.value for item in app.error)
    feedback = next(item.value for item in app.code if item.language == "json")
    assert "contract" in feedback and "PATIENT-PRIVATE-CANARY" not in feedback
    assert any(item.value == "Structured validation errors" for item in app.subheader)
    assert any(item.value == "Copyable AI feedback" for item in app.subheader)


def test_readable_complete_review_and_recovery_gate(mutable_db):
    package = _file(envelope(mutable_db, graph()))
    app = _review(_open_ai(package), package)

    headings = {item.value for item in app.subheader}
    assert {"Normalized operations (8)", "Exact database changes (8)", "Preset reports",
            "Standalone content (0)", "Affected pending Cases (0)"} <= headings
    assert any("create Fields" in item.label for item in app.expander)
    assert any("synthetic_block" in item.label for item in app.expander)
    assert app.selectbox(key=next(x.key for x in app.selectbox if x.label == "Affected Preset")).value == "synthetic_preset"
    assert any("Phrase. normal." in item.value for item in app.code)
    confirm_key = _confirmation_key(app)
    _set_checkbox_with_file(app, confirm_key, True, package)
    assert app.button(key=_apply_key(app)).disabled
    assert any("recovery snapshot gate" in item.value for item in app.warning)


def test_affected_pending_report_is_readable_and_session_only(mutable_db):
    saved = save_synthetic_case(mutable_db, number="LOCAL-PENDING-UI")
    package = _file(envelope(mutable_db, [{
        "op": "update", "table": "Blocks", "key": "appendice",
        "set": {"micro_template": "Microscopie candidate complète."},
    }]))
    app = _review(_open_ai(package), package)

    assert any(item.value == "Affected pending Cases (1)" for item in app.subheader)
    pending_selector = next(item for item in app.selectbox if item.label == "Pending Case")
    assert pending_selector.value == saved["id"]
    assert any("Current before this change" in item.value for item in app.markdown)
    assert any("Candidate after this change" in item.value for item in app.markdown)
    assert any("Microscopie candidate complète." in item.value for item in app.code)
    assert any(item.label == "Last saved report" for item in app.expander)
    feedback_blocks = [item.value for item in app.code if item.language == "json"]
    assert all("LOCAL-PENDING-UI" not in value for value in feedback_blocks)


def test_successful_apply_clears_forms_preserves_section_and_records_revision(mutable_db):
    _unlock()
    package = _file(envelope(mutable_db))
    app = _review(_open_ai(package), package)
    app.session_state["_editor_loaded_blocks"] = {"entity_key": "appendice", "entity": {}}
    confirm_key = _confirmation_key(app)
    _set_checkbox_with_file(app, confirm_key, True, package)
    before = database.current_content_revision_id()
    _click_with_file(app, _apply_key(app), package)

    assert app.radio(key="editor_section").value == "AI package"
    assert "_editor_loaded_blocks" not in app.session_state.filtered_state
    assert "_editor_ai_review" not in app.session_state.filtered_state
    assert app.session_state["_editor_ai_generation"] == 1
    assert database.current_content_revision_id() == before + 1
    assert any("Review it under Recent revisions" in item.value for item in app.success)
    conn = database.get_db_connection()
    revision = dict(conn.execute("SELECT * FROM Content_Revisions ORDER BY id DESC LIMIT 1").fetchone())
    conn.close()
    assert revision["origin"] == "package_import" and revision["package_hash"]


def test_two_editor_sessions_reject_stale_apply_and_clear_confirmation(mutable_db):
    _unlock()
    package = _file(envelope(mutable_db))
    first = _review(_open_ai(package), package)
    second = _review(_open_ai(package), package)
    for app in (first, second):
        _set_checkbox_with_file(app, _confirmation_key(app), True, package)
    _click_with_file(second, _apply_key(second), package)
    _click_with_file(first, _apply_key(first), package)

    assert "_editor_ai_review" not in first.session_state.filtered_state
    assert any("Not applied" in item.value and "new dry run" in item.value for item in first.error)


def test_failed_apply_clears_confirmation_and_does_not_write(mutable_db, monkeypatch):
    _unlock()
    package = _file(envelope(mutable_db))
    app = _review(_open_ai(package), package)
    confirm_key = _confirmation_key(app)
    _set_checkbox_with_file(app, confirm_key, True, package)
    before = database.current_content_revision_id()

    def fail_apply(review):
        raise content_changes.ChangeError("Synthetic safe Apply refusal.", local="PRIVATE-LOCAL")

    monkeypatch.setattr(content_changes, "apply_review", fail_apply)
    _click_with_file(app, _apply_key(app), package)
    assert database.current_content_revision_id() == before
    assert any("Synthetic safe Apply refusal" in item.value for item in app.error)
    assert app.checkbox(key=confirm_key).value is False


def test_recent_revision_inverse_requires_prepare_confirm_and_apply(mutable_db):
    _unlock()
    package = _file(envelope(mutable_db))
    app = _review(_open_ai(package), package)
    _set_checkbox_with_file(app, _confirmation_key(app), True, package)
    _click_with_file(app, _apply_key(app), package)
    source_revision = database.current_content_revision_id()
    # Start a fresh UI session for revision navigation. AppTest cannot retire
    # a file-uploader-adjacent checkbox cleanly across the explicit success
    # rerun; the preceding test covers same-session section preservation.
    app = AppTest.from_file("pages/editor.py").run()
    app.radio(key="editor_section").set_value("Recent revisions").run()

    prepare = next(button for button in app.button if button.label == "Prepare inverse review")
    assert database.current_content_revision_id() == source_revision
    prepare.click().run()
    assert database.current_content_revision_id() == source_revision
    inverse = app.session_state["_editor_inverse_review"]
    assert inverse is not None and inverse.data["inverse_revision_id"] == source_revision
    confirm = next(box for box in app.checkbox if box.label == "I confirm this exact inverse review")
    apply = next(button for button in app.button if button.label == "Apply reviewed inverse")
    assert apply.disabled
    confirm.set_value(True).run()
    apply = next(button for button in app.button if button.label == "Apply reviewed inverse")
    assert not apply.disabled and database.current_content_revision_id() == source_revision
    apply.click().run()
    assert database.current_content_revision_id() == source_revision + 1
    assert app.radio(key="editor_section").value == "Recent revisions"


def test_editor_review_uses_restricted_html_without_changing_candidate(mutable_db):
    operation = [{
        "op": "update", "table": "Blocks", "key": "appendice",
        "set": {"micro_template": '<b>SAFE</b><img src="https://outside.test/pixel" onerror="x()">'
                                   '<script>PRIVATE-SCRIPT</script>'},
    }]
    package = _file(envelope(mutable_db, operation))
    app = _review(_open_ai(package), package)
    review = app.session_state["_editor_ai_review"]
    candidate_html = next(p for p in review.presets if p["code"] == "dai")["after"]["report"]["html"]

    assert "outside.test" in candidate_html and "PRIVATE-SCRIPT" in candidate_html
    displayed = "\n".join(item.value for item in app.markdown)
    assert "<b>SAFE</b>" in displayed
    assert "outside.test" not in displayed and "onerror" not in displayed
    assert "PRIVATE-SCRIPT" not in displayed and "<script" not in displayed


def test_workspace_validated_artifact_uses_restricted_html(mutable_db):
    preset = next(item for item in database.get_all_presets() if item["short_code"] == "dai")
    dangerous = ('<div style="text-align: center"><b>SAFE-WORKSPACE</b>'
                 '<img src="https://outside.test/pixel" onerror="x()">'
                 '<script>PRIVATE-WORKSPACE</script></div>')
    conn = sqlite3.connect(mutable_db)
    conn.execute(
        """INSERT INTO Cases(case_number,preset_id,clinical_info,structured_input,rendered_html,status)
           VALUES(?,?,?,?,?,'validated')""",
        ("SYNTHETIC-HTML", preset["id"], "", "{}", dangerous),
    )
    conn.commit()
    conn.close()
    app = AppTest.from_file("pages/workspace.py").run()
    next(item for item in app.text_input if item.label == "Case number").set_value("SYNTHETIC-HTML")
    next(button for button in app.button if button.label == "Reopen").click().run()

    displayed = "\n".join(item.value for item in app.markdown)
    assert "SAFE-WORKSPACE" in displayed and "text-align: center" in displayed
    assert "outside.test" not in displayed and "onerror" not in displayed
    assert "PRIVATE-WORKSPACE" not in displayed and "<script" not in displayed
    saved = database.get_case_by_number("SYNTHETIC-HTML")
    assert saved["rendered_html"] == dangerous


def test_download_hash_is_exact_snapshot_hash_not_whole_file_digest(db):
    payload = change_packages.export_ai_context(db)
    context = json.loads(payload)
    assert context["snapshot_sha256"] == content_snapshot.content_snapshot_hash(context["snapshot"])
    assert context["snapshot_sha256"] != hashlib.sha256(payload).hexdigest()
