"""PR1 Worklist deletion controls, exercised against an isolated database."""

from streamlit.testing.v1 import AppTest
import streamlit as st

import database


def _save(case_number, *, status):
    preset = next(row for row in database.get_all_presets() if row["short_code"] == "dai")
    assert database.save_case(case_number, preset["id"], "", {}, "<p>report</p>", status=status)


def test_worklist_offers_confirmed_deletion_only_for_pending_cases(mutable_db, monkeypatch):
    _save("20001", status="pending")
    _save("20002", status="validated")
    pending = database.get_case_by_number("20001")
    validated = database.get_case_by_number("20002")

    # AppTest does not populate multipage metadata required by the existing
    # cross-page Reopen link. It is outside this control's behavior.
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)
    app = AppTest.from_file("pages/worklist.py").run()
    assert not app.exception
    delete_key = f"worklist_delete_{pending['id']}"
    assert app.button(key=delete_key).disabled
    assert not any(button.key == f"worklist_delete_{validated['id']}" for button in app.button)
    assert any(pending["case_number"] in warning.value for warning in app.warning)

    app.checkbox(key=f"worklist_delete_confirm_{pending['id']}").set_value(True).run()
    assert not app.button(key=delete_key).disabled
    app.button(key=delete_key).click().run()

    assert not app.exception
    assert database.get_case_by_number("20001") is None
    assert database.get_case_by_number("20002")["status"] == "validated"
    assert any("permanently deleted" in success.value for success in app.success)


def test_consecutive_deletions_keep_the_next_confirmation_control_open(mutable_db, monkeypatch):
    _save("20003", status="pending")
    _save("20004", status="pending")
    first = database.get_case_by_number("20003")
    second = database.get_case_by_number("20004")

    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)
    app = AppTest.from_file("pages/worklist.py").run()
    app.checkbox(key=f"worklist_delete_confirm_{first['id']}").set_value(True).run()
    app.button(key=f"worklist_delete_{first['id']}").click().run()
    assert database.get_case_by_number("20003") is None

    app.checkbox(key=f"worklist_delete_confirm_{second['id']}").set_value(True).run()
    assert app.session_state["_worklist_open_delete_case_id"] == second["id"]
    assert next(expander for expander in app.expander if expander.label == "Permanent deletion").proto.expanded
    app.button(key=f"worklist_delete_{second['id']}").click().run()

    assert not app.exception
    assert database.get_case_by_number("20004") is None
