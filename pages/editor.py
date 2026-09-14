import json
import difflib
import hashlib

import streamlit as st

import content_editing
import content_changes
import content_snapshot
import change_packages
import content_studio
import database as db
import composition
from editor_preview import render_preset_defaults
from report_presentation import restricted_report_html


def _preset_label(preset):
    return f"{preset['name']} ({preset['short_code']})"


def _show_rows(rows, empty_message):
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption(empty_message)


def _show_templates(block):
    for label, column in (("Macro", "macro_template"), ("Microscopy", "micro_template"),
                          ("Conclusion", "conclusion_template"), ("Context", "context_template"),
                          ("Title fragment", "title_fragment_template"), ("Conclusion label", "conclusion_label_template")):
        if block.get(column):
            with st.expander(label, expanded=label in ("Microscopy", "Conclusion")):
                st.code(block[column], language="jinja2")


def _show_preview(preset_id):
    preview = render_preset_defaults(preset_id)
    if preview is None:
        st.error("This Preset no longer exists.")
        return
    st.subheader("Default report preview", anchor=False)
    st.caption("Resolved stored defaults, rendered through the same report pipeline as Workspace.")
    if preview["conflicts"]:
        st.warning(f"{', '.join(preview['conflicts'])} differs between default Blocks and is not auto-added to the conclusion.")
    st.markdown(restricted_report_html(preview["html"]), unsafe_allow_html=True)
    with st.expander("Plain-text rendering"):
        st.code(f"MICROSCOPY\n{preview['micro_plain']}\n\nCONCLUSION\n{preview['conclusion_plain']}")


def _loaded_entity(table, entity_key):
    """Keep one form snapshot stable until its target changes or it saves.

    Without this, a concurrent DB edit changes the hash-derived widget keys at
    the start of the submit rerun, so Streamlit discards the stale submit event
    before the backend can report the conflict.
    """
    state_key = f"_editor_loaded_{table.lower()}"
    loaded = st.session_state.get(state_key)
    if loaded is None or loaded["entity_key"] != entity_key:
        loaded = {
            "entity_key": entity_key,
            "entity": content_editing.get_editable_entity(table, entity_key),
        }
        st.session_state[state_key] = loaded
    return loaded["entity"]


def _clear_loaded_entities(table=None):
    tables = [table] if table else ["Blocks", "Fields", "Snippets", "Presets"]
    for item in tables:
        st.session_state.pop(f"_editor_loaded_{item.lower()}", None)


def _save(table, key, changes, expected_hash):
    try:
        result = content_editing.save_edit(table, key, changes, expected_hash)
    except content_editing.StaleContentError as error:
        _clear_loaded_entities(table)
        st.session_state["_editor_error"] = f"Not saved: {error}"
        st.rerun()
    except content_editing.ContentEditError as error:
        st.error(f"Not saved: {error}")
        return
    _clear_loaded_entities(table)
    st.session_state["_editor_message"] = f"Saved as content revision {result['revision_id']}."
    st.rerun()


def _show_candidate_preview(table, key, changes, expected_hash):
    try:
        result = content_editing.preview_edit(table, key, changes, expected_hash)
    except content_editing.StaleContentError as error:
        _clear_loaded_entities(table)
        st.session_state["_editor_error"] = f"Cannot preview: {error}"
        st.rerun()
    except content_editing.ContentEditError as error:
        st.error(f"Cannot preview: {error}")
        return
    if not result["previews"]:
        st.info("This change does not alter any configured Preset's default report output.")
        return
    st.subheader("Affected default report previews", anchor=False)
    st.caption("Rollback-only candidate render; no content has been saved.")
    for preview in result["previews"]:
        with st.expander(preview["label"], expanded=True):
            before_col, after_col = st.columns(2)
            for column, heading, rendered in (
                (before_col, "Before", preview["before"]),
                (after_col, "Candidate", preview["after"]),
            ):
                with column:
                    st.markdown(f"**{heading}**")
                    if rendered.get("error"):
                        st.error(rendered["error"])
                    else:
                        st.code(
                            f"TITLE\n{rendered['title']}\n\n"
                            f"MICROSCOPY\n{rendered['micro_plain']}\n\n"
                            f"CONCLUSION\n{rendered['conclusion_plain']}",
                            language=None,
                        )


def _snapshot_gate():
    snapshot = content_snapshot.export_content_snapshot()
    payload = content_snapshot.content_snapshot_json(snapshot)
    digest = content_snapshot.content_snapshot_hash(snapshot)
    state = content_editing.initial_snapshot_status()
    if state["initial_snapshot_hash"]:
        st.caption(f"Initial manual snapshot recorded at {state['initial_snapshot_at']}. Optional exports remain on demand.")
        st.download_button("Download current content snapshot", payload, "pathopilot-content-snapshot.json", "application/json", key="editor_snapshot_download")
        return True
    st.warning("Direct editing is locked until you create and save one manual content snapshot.")
    st.download_button("Download initial content snapshot", payload, "pathopilot-initial-content-snapshot.json", "application/json", key="editor_initial_snapshot_download")
    acknowledged = st.checkbox("I have saved this initial snapshot outside PathoPilot", key="editor_initial_snapshot_ack")
    if st.button("Enable safe direct editing", disabled=not acknowledged, key="editor_enable_direct_editing"):
        content_editing.record_initial_snapshot(digest)
        st.session_state["_editor_message"] = "Initial snapshot recorded. Direct editing is now enabled."
        st.rerun()
    return False


def _scalar_text(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _show_mapping(values, prefix=""):
    """Show every value by path without making the user inspect raw JSON."""
    for key, value in values.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            st.markdown(f"**{path}**")
            if value:
                _show_mapping(value, path)
            else:
                st.code("{}", language=None)
        elif isinstance(value, list):
            st.markdown(f"**{path}**")
            if value:
                for index, item in enumerate(value):
                    if isinstance(item, dict):
                        _show_mapping(item, f"{path}[{index}]")
                    else:
                        st.code(f"[{index}] {_scalar_text(item)}", language=None)
            else:
                st.code("[]", language=None)
        else:
            st.markdown(f"**{path}**")
            st.code(_scalar_text(value), language=None)


def _show_report(report, heading):
    st.markdown(f"**{heading}**")
    if not report:
        st.caption("Not present.")
        return
    if report.get("error"):
        st.error(report["error"])
        return
    report = report.get("report", report)
    for label, key in (
        ("Title", "title"), ("Clinical information", "clinical_info"),
        ("Microscopy", "micro_plain"), ("Conclusion", "conclusion_plain"),
    ):
        st.caption(label)
        st.code(report.get(key, ""), language=None)
    if report.get("locks"):
        st.caption("Locks: " + ", ".join(report["locks"]))
    for warning in report.get("warnings", []):
        st.warning(warning)
    for conflict in report.get("conflicts", []):
        st.warning(conflict)
    st.caption("Restricted report presentation")
    st.markdown(restricted_report_html(report.get("html", "")), unsafe_allow_html=True)


def _show_operations(review):
    # Draft assertions are immutable review guards, not proposed database
    # mutations. They remain inside the signed/frozen review for Apply but do
    # not displace the user-facing operation widgets.
    operations = [operation for operation in review.operations
                  if operation.get("op") not in {"assert_block_draft", "assert_field_endpoints",
                                                   "assert_preset_draft", "assert_preset_endpoints"}]
    st.subheader(f"Normalized operations ({len(operations)})", anchor=False)
    for position, operation in enumerate(operations, 1):
        key = operation["key"]
        identity = key if isinstance(key, str) else ", ".join(f"{k}={v}" for k, v in key.items())
        with st.expander(f"{position}. {operation['op']} {operation['table']} — {identity}"):
            if "index" in operation:
                st.caption(f"Original uploaded operation index: {operation['index']}")
            else:
                st.caption("Backend-derived inverse operation")
            values = operation.get("set", operation.get("values", {}))
            if values:
                _show_mapping(values)


def _show_changes(review):
    st.subheader(f"Exact database changes ({len(review.changes)})", anchor=False)
    for position, change in enumerate(review.changes, 1):
        key = change["key"]
        identity = key if isinstance(key, str) else ", ".join(f"{k}={v}" for k, v in key.items())
        with st.expander(f"{position}. {change['table']} — {identity}"):
            before, after = change.get("before"), change.get("after")
            before_col, after_col = st.columns(2)
            with before_col:
                st.markdown("**Before**")
                if before is not None:
                    _show_mapping(before)
                else:
                    st.code("Not present", language=None)
            with after_col:
                st.markdown("**After**")
                if after is not None:
                    _show_mapping(after)
                else:
                    st.code("Not present", language=None)
            for column in sorted(set(before or {}) | set(after or {})):
                old, new = (before or {}).get(column), (after or {}).get(column)
                if old == new or not ("template" in column or isinstance(old, str) and "\n" in old
                                      or isinstance(new, str) and "\n" in new):
                    continue
                diff = "\n".join(difflib.unified_diff(
                    (old or "").splitlines(), (new or "").splitlines(),
                    fromfile=f"before/{column}", tofile=f"after/{column}", lineterm="",
                ))
                st.caption(f"Readable diff — {column}")
                st.code(diff or "(value changed)", language="diff")


def _show_review_reports(review, key_prefix):
    data = review.data
    affected = [item for item in data["presets"] if item["affected"]]
    st.subheader("Preset reports", anchor=False)
    st.caption(
        f"{len(affected)} affected · {data['unaffected_presets']} unaffected · "
        f"{data['validated_pending_count']} pending Cases validated"
    )
    if affected:
        selected_code = st.selectbox(
            "Affected Preset", [item["code"] for item in affected],
            key=f"{key_prefix}_preset_report",
        )
        selected = next(item for item in affected if item["code"] == selected_code)
        st.caption(
            f"Added: {'yes' if selected['added'] else 'no'} · removed: "
            f"{'yes' if selected['removed'] else 'no'} · output changed: "
            f"{'yes' if selected['output_changed'] else 'no'}"
        )
        before_col, after_col = st.columns(2)
        with before_col:
            _show_report(selected.get("before"), "Current before this change")
        with after_col:
            _show_report(selected.get("after"), "Candidate after this change")
    else:
        st.caption("No Preset is affected.")

    standalone = data.get("standalone", [])
    st.subheader(f"Standalone content ({len(standalone)})", anchor=False)
    for item in standalone:
        with st.expander(f"{item.get('table', 'Content')}.{item.get('key', 'unknown')} — {item.get('label', '')}"):
            if item.get("report"):
                _show_report(item["report"], "Candidate standalone report")
            else:
                _show_mapping({k: v for k, v in item.items() if k not in {"table", "key", "label"}})

    pending = data["pending_cases"]
    st.subheader(f"Affected pending Cases ({len(pending)})", anchor=False)
    st.caption("Case identifiers and reports below stay only in this browser session and are never AI feedback.")
    if pending:
        selected_id = st.selectbox(
            "Pending Case", [item["id"] for item in pending],
            format_func=lambda ident: next(item["case_number"] for item in pending if item["id"] == ident),
            key=f"{key_prefix}_pending_report",
        )
        selected = next(item for item in pending if item["id"] == selected_id)
        if selected.get("already_stale"):
            st.warning("This draft was already stale before this package.")
        before_col, after_col = st.columns(2)
        with before_col:
            _show_report(selected["before"], selected["before_label"])
        with after_col:
            _show_report(selected["after"], selected["after_label"])
        with st.expander(selected["saved_label"]):
            st.markdown(restricted_report_html(selected.get("saved_html", "")), unsafe_allow_html=True)
    else:
        st.caption("No pending Case is affected.")


def _show_full_review(review, key_prefix):
    data = review.data
    st.success("Dry run succeeded. Nothing saved.")
    st.caption("Package summary")
    st.code(data["summary"], language=None)
    for warning in data.get("warnings", []):
        st.warning(warning)
    for warning in data.get("branch_warnings", []):
        with st.expander("Candidate branch warning"):
            _show_mapping(warning)
    _show_operations(review)
    _show_changes(review)
    _show_review_reports(review, key_prefix)
    with st.expander("Content hashes"):
        st.code(
            f"Base SHA-256: {review.base_snapshot_hash}\n"
            f"Candidate SHA-256: {review.candidate_snapshot_hash}\n"
            f"Package SHA-256: {review.package_hash or 'not applicable'}",
            language=None,
        )


def _clear_ai_review(*, clear_feedback=True):
    for key in ("_editor_ai_review", "_editor_ai_review_upload_sha", "_editor_ai_confirmed_review"):
        st.session_state.pop(key, None)
    if clear_feedback:
        st.session_state.pop("_editor_ai_feedback", None)
        st.session_state.pop("_editor_ai_local_error", None)


def _clear_confirmation_widgets(prefix):
    for key in list(st.session_state):
        if key.startswith(prefix) and key.endswith("_confirm"):
            st.session_state.pop(key, None)


# Content Studio ------------------------------------------------------------
#
# Keep this state intentionally distinct from the optional AI-package review.
# A guided draft is local to its selected entity and is always replaced by a
# newly prepared immutable candidate before Apply.

def _clear_studio_review():
    for key in ("_editor_studio_review", "_editor_studio_signature",
                "_editor_studio_review_form_generation",
                "_editor_studio_error", "_editor_studio_local_error"):
        st.session_state.pop(key, None)
    # Confirmation is the only approval-bearing review widget. Report selector
    # state is harmless without the immutable review and is left for Streamlit
    # to clean up after this rerun (removing it synchronously leaves stale
    # elements in AppTest's render tree).
    _clear_confirmation_widgets("editor_studio_review_")


def _studio_signature(kind, target, values):
    return json.dumps({"kind": kind, "target": target, "values": values},
                      ensure_ascii=False, sort_keys=True, default=str)


def _prepare_studio_review(intents, signature, summary):
    _clear_studio_review()
    try:
        snapshot = content_snapshot.export_content_snapshot()
        review = content_studio.review(
            intents, content_snapshot.content_snapshot_hash(snapshot), summary=summary,
        )
    except (content_studio.StudioIntentError, content_changes.ChangeError,
            content_editing.ContentEditError, change_packages.PackageError) as error:
        st.session_state["_editor_studio_error"] = str(error)
        st.session_state["_editor_studio_local_error"] = getattr(error, "local", None)
        if isinstance(error, content_changes.StaleDraftReviewError):
            # The assertion was checked on the review's own snapshot. Fresh
            # widget keys now load that persisted source instead of retaining
            # values from the refused draft.
            st.session_state["_editor_studio_form_generation"] = (
                st.session_state.get("_editor_studio_form_generation", 0) + 1
            )
    else:
        st.session_state["_editor_studio_review"] = review
        st.session_state["_editor_studio_signature"] = signature
        # This is intentionally separate from the review-display generation.
        # Any form reset invalidates a frozen candidate, even if a future
        # caller forgets to clear it before advancing the draft generation.
        st.session_state["_editor_studio_review_form_generation"] = (
            st.session_state.get("_editor_studio_form_generation", 0)
        )
        st.session_state["_editor_studio_generation"] = (
            st.session_state.get("_editor_studio_generation", 0) + 1
        )
    st.rerun()


def _show_studio_review(signature, writes_enabled):
    review = st.session_state.get("_editor_studio_review")
    if review is None:
        return False
    if (st.session_state.get("_editor_studio_review_form_generation")
            != st.session_state.get("_editor_studio_form_generation", 0)):
        _clear_studio_review()
        return False
    # A selection change must never leave a review for another stable entity
    # visible. Once frozen, the draft is hidden, so its old values need not
    # equal the form's initial-value fingerprint on the next rerun.
    try:
        review_target = json.loads(st.session_state.get("_editor_studio_signature", "{}"))["target"]
        current_target = json.loads(signature)["target"]
    except (KeyError, TypeError, json.JSONDecodeError):
        review_target = current_target = None
    if review_target != current_target:
        _clear_studio_review()
        return False
    generation = st.session_state.get("_editor_studio_generation", 0)
    prefix = f"editor_studio_review_{generation}"
    st.subheader("Frozen Content Studio review", anchor=False)
    _show_full_review(review, prefix)
    if st.button("Edit draft", key=f"{prefix}_edit"):
        _clear_studio_review()
        st.rerun()
    confirmed = st.checkbox(
        "I confirm this exact reviewed candidate and its local pending-Case impact",
        key=f"{prefix}_confirm",
    )
    if not writes_enabled:
        st.warning("Apply is locked until the separate recovery snapshot gate above is complete.")
    if st.button("Apply reviewed Content Studio change", key=f"{prefix}_apply",
                 disabled=not (confirmed and writes_enabled)):
        try:
            revision_id = content_changes.apply_review(review)
        except content_changes.StaleReviewError as error:
            _clear_studio_review()
            st.session_state["_editor_error"] = f"Not applied: {error} Current values were reloaded; prepare a new review."
        except (content_changes.ChangeError, content_editing.ContentEditError) as error:
            _clear_studio_review()
            st.session_state["_editor_error"] = f"Not applied: {error}"
        else:
            _clear_loaded_entities()
            _clear_studio_review()
            st.session_state["_editor_studio_form_generation"] = (
                st.session_state.get("_editor_studio_form_generation", 0) + 1
            )
            st.session_state["_editor_message"] = f"Applied as content revision {revision_id}."
        st.rerun()
    return True


def _filter_rows(rows, mode):
    if mode == "Active":
        return [row for row in rows if not row.get("is_archived")]
    if mode == "Archived":
        return [row for row in rows if row.get("is_archived")]
    return rows


def _entity_caption(row, key_name, label_name):
    archived = " · archived" if row.get("is_archived") else ""
    return f"{row[label_name]} (`{row[key_name]}` · ID {row['id']}){archived}"


def _field_default_widget(field_type, options, value, key, *, label="Default value"):
    """Render the same typed default intent used by the candidate validator."""
    if field_type == "checkbox":
        current = str(value).lower() in {"1", "true"}
        return st.checkbox(label, value=current, key=key)
    if field_type == "select":
        if not options:
            st.error("A select Field needs options before it can have a default.")
            return None
        selected = value if value in options else options[0]
        return st.selectbox(label, options, index=options.index(selected), key=key)
    if field_type == "number":
        try:
            current = int(value)
        except (TypeError, ValueError):
            current = 0
        return st.number_input(label, min_value=0, value=current, step=1, key=key)
    if field_type == "decimal":
        try:
            current = float(value)
        except (TypeError, ValueError):
            current = 0.0
        return st.number_input(label, min_value=0.0, value=current,
                               key=key, help="Use a non-negative decimal.")
    return st.text_input(label, "" if value is None else str(value), key=key)


def _parse_select_options(raw):
    options = [item.strip() for item in raw.replace("\n", ",").split(",")]
    return [item for item in options if item]


def _lifecycle_panel(table, row, signature, writes_enabled):
    action = "Restore" if row.get("is_archived") else "Archive"
    stable_key = row["shortcut"] if table == "Snippets" else row["short_code"] if table == "Presets" else row["key"]
    plan = content_studio.lifecycle_plan(action.lower(), table,
                                         stable_key)
    st.subheader("Availability and deletion", anchor=False)
    if plan["direct_dependencies"]:
        st.caption("Direct dependents: " + ", ".join(
            f"{item['table']}.{item['key']}" for item in plan["direct_dependencies"]
        ))
    if plan["archive_closure"] or plan["restore_prerequisites"]:
        items = plan["archive_closure"] or plan["restore_prerequisites"]
        st.caption("This reviewed action also affects: " + ", ".join(
            f"{item['table']}.{item['key']}" for item in items
        ))
    lifecycle_signature = _studio_signature("lifecycle", {"table": table, "key": plan["target"]["key"]}, plan)
    if st.button(f"Prepare {action} review", key=f"editor_studio_{table}_{row['id']}_{action.lower()}",
                 disabled=not writes_enabled):
        _prepare_studio_review(plan["operations"], lifecycle_signature,
                                f"{action} {table}.{plan['target']['key']}")
    delete_plan = content_studio.lifecycle_plan("delete", table, plan["target"]["key"])
    if delete_plan["refusal_reasons"]:
        st.info("Permanent deletion unavailable: " + " ".join(delete_plan["refusal_reasons"]))
        for prerequisite in delete_plan.get("deletion_prerequisites", []):
            st.caption(
                "Prerequisite edit: remove the Field from "
                f"`{prerequisite['block_key']}.{prerequisite['template_column']}`."
            )
        if delete_plan["pending_blockers"]:
            st.caption("Pending Cases: " + ", ".join(item["case_number"] for item in delete_plan["pending_blockers"]))
    else:
        if delete_plan["mechanical_deletion_cleanup"]:
            st.caption("Deletion cleanup: " + ", ".join(
                f"{item['action']} {item['table']}" for item in delete_plan["mechanical_deletion_cleanup"]
            ))
        delete_signature = _studio_signature("lifecycle", {"table": table, "key": plan["target"]["key"]}, delete_plan)
        if st.button("Prepare permanent deletion review", key=f"editor_studio_{table}_{row['id']}_delete",
                     disabled=not writes_enabled):
            _prepare_studio_review(delete_plan["operations"], delete_signature,
                                    f"Delete {table}.{plan['target']['key']}")


def _field_studio(rows, mode, writes_enabled):
    st.subheader("Fields", anchor=False)
    selectable = _filter_rows(rows, mode)
    choices = {row["id"]: row for row in selectable}
    generation = st.session_state.get("_editor_studio_form_generation", 0)
    create_mode = st.checkbox("Create a new Field", key="editor_studio_field_create",
                              on_change=_clear_studio_review)
    if create_mode:
        signature = _studio_signature("field-create", "new", {"generation": generation})
        if _show_studio_review(signature, writes_enabled):
            return
        # A widget inside ``st.form`` cannot rerun until the form is submitted.
        # Keep the type picker outside it so changing type immediately rebuilds
        # the form with the appropriate typed default control; distinct keys
        # also avoid carrying a text widget's state into a checkbox/select.
        field_type = st.selectbox(
            "Field type", ["text", "number", "decimal", "checkbox", "select"],
            key=f"editor_studio_field_type_{generation}",
            on_change=_clear_studio_review,
        )
        options_raw = ""
        if field_type == "select":
            options_raw = st.text_area(
                "Select options (one per line or comma-separated)",
                key=f"editor_studio_field_options_{generation}",
            )
        options = _parse_select_options(options_raw) if field_type == "select" else []
        with st.form(f"editor_studio_field_create_{generation}"):
            key = st.text_input("Stable key")
            label = st.text_input("Label")
            default = _field_default_widget(
                field_type, options, None, f"editor_studio_field_create_default_{field_type}"
            )
            addendum = st.text_area("Conclusion addendum template (optional; context: value)")
            prepare = st.form_submit_button("Prepare Field review", disabled=not writes_enabled)
        values = {"key": key, "label": label, "type": field_type, "options": options or None,
                  "default_value": default, "conclusion_addendum_template": addendum or None}
        if prepare:
            if field_type == "select" and (not options or len(options) != len(set(options))):
                st.error("New select Fields require nonblank, unique options.")
            elif default in {None, ""}:
                st.error("New Fields need a usable standalone default.")
            else:
                signature = _studio_signature("field-create", "new", values)
                _prepare_studio_review([content_studio.operation("create", "Fields", key, values)], signature,
                                        f"Create Field.{key}")
        return
    if not choices:
        st.info("No Fields match this lifecycle filter.")
        return
    field_id = st.selectbox("Field", list(choices),
                            format_func=lambda ident: _entity_caption(choices[ident], "key", "label"),
                            key="editor_studio_field_select", on_change=_clear_studio_review)
    field = choices[field_id]
    draft = {"label": field["label"], "default_value": field["default_value"],
             "conclusion_addendum_template": field.get("conclusion_addendum_template")}
    signature = _studio_signature("field-edit", {"table": "Fields", "key": field["key"]}, draft)
    if _show_studio_review(signature, writes_enabled):
        return
    token = f"{field['id']}_{generation}"
    # NULL is meaningful: it must not be rendered as a typed zero/false and
    # then accidentally become a content change while editing another value.
    # Keep this switch outside the form so a deliberate set/unset rerenders the
    # typed widget before Prepare is clicked.
    # Stage 3 compatibility intentionally requires an established nonblank
    # discrete/numeric default. Do not advertise an impossible clear action.
    may_unset_default = field["type"] in {"text", "decimal"} or field["default_value"] in {None, ""}
    if may_unset_default:
        no_default = st.checkbox(
            "No global default", value=field["default_value"] is None,
            key=f"editor_studio_field_no_default_{token}",
            on_change=_clear_studio_review,
        )
    else:
        no_default = False
        st.caption("This established global default is required for this Field type and cannot be cleared.")
    with st.form(f"editor_studio_field_edit_{token}"):
        st.caption(f"Stable key: `{field['key']}` · type: `{field['type']}` (both locked)")
        if field["type"] == "select": st.caption("Options (locked): " + ", ".join(json.loads(field["options"] or "[]")))
        label = st.text_input("Label", field["label"], key=f"editor_studio_field_label_{token}")
        if no_default:
            st.caption("This Field inherits no global default; Block or Preset overrides may still supply one.")
            default = None
        else:
            default = _field_default_widget(field["type"], json.loads(field["options"] or "[]"),
                                            field["default_value"], f"editor_studio_field_default_{token}")
        addendum = st.text_area("Conclusion addendum template (optional; context: value)",
                                 field.get("conclusion_addendum_template") or "", key=f"editor_studio_field_addendum_{token}")
        prepare = st.form_submit_button("Prepare Field review", disabled=not writes_enabled)
    values = {"label": label, "default_value": default, "conclusion_addendum_template": addendum or None}
    if prepare:
        signature = _studio_signature("field-edit", {"table": "Fields", "key": field["key"]}, values)
        _prepare_studio_review([content_studio.operation("update", "Fields", field["key"], values)], signature,
                                f"Edit Field.{field['key']}")
    _lifecycle_panel("Fields", field, signature, writes_enabled)


def _snippet_studio(rows, mode, writes_enabled):
    st.subheader("Snippets", anchor=False)
    selectable = _filter_rows(rows, mode); choices = {row["id"]: row for row in selectable}
    generation = st.session_state.get("_editor_studio_form_generation", 0)
    create_mode = st.checkbox("Create a new Snippet", key="editor_studio_snippet_create",
                              on_change=_clear_studio_review)
    if create_mode:
        signature = _studio_signature("snippet-create", "new", {"generation": generation})
        if _show_studio_review(signature, writes_enabled): return
        with st.form(f"editor_studio_snippet_create_{generation}"):
            shortcut = st.text_input("Shortcut")
            expansion = st.text_area("Expansion")
            category = st.text_input("Category (optional)")
            prepare = st.form_submit_button("Prepare Snippet review", disabled=not writes_enabled)
        values = {"expansion": expansion, "category": category or None}
        if prepare:
            signature = _studio_signature("snippet-create", "new", values)
            _prepare_studio_review([content_studio.operation("create", "Snippets", shortcut, values)], signature,
                                    f"Create Snippet.{shortcut}")
        return
    if not choices:
        st.info("No Snippets match this lifecycle filter."); return
    snippet_id = st.selectbox("Snippet", list(choices),
                              format_func=lambda ident: _entity_caption(choices[ident], "shortcut", "shortcut"),
                              key="editor_studio_snippet_select", on_change=_clear_studio_review)
    snippet = choices[snippet_id]
    draft = {"expansion": snippet["expansion"], "category": snippet.get("category")}
    signature = _studio_signature("snippet-edit", {"table": "Snippets", "key": snippet["shortcut"]}, draft)
    if _show_studio_review(signature, writes_enabled): return
    token = f"{snippet['id']}_{generation}"
    with st.form(f"editor_studio_snippet_edit_{token}"):
        st.caption(f"Stable shortcut: `{snippet['shortcut']}` (locked)")
        expansion = st.text_area("Expansion", snippet["expansion"], key=f"editor_studio_snippet_expansion_{token}")
        category = st.text_input("Category (optional)", snippet.get("category") or "", key=f"editor_studio_snippet_category_{token}")
        prepare = st.form_submit_button("Prepare Snippet review", disabled=not writes_enabled)
    values = {"expansion": expansion, "category": category or None}
    if prepare:
        signature = _studio_signature("snippet-edit", {"table": "Snippets", "key": snippet["shortcut"]}, values)
        _prepare_studio_review([content_studio.operation("update", "Snippets", snippet["shortcut"], values)], signature,
                                f"Edit Snippet.{snippet['shortcut']}")
    _lifecycle_panel("Snippets", snippet, signature, writes_enabled)


def _block_binding_default(field, value, key):
    """One explicit inherit/override control for a Block_Field default."""
    inherited = st.checkbox("Inherit Field default", value=value is None,
                            key=f"{key}_inherit", on_change=_clear_studio_review)
    if inherited:
        return None
    return _field_default_widget(field["type"], json.loads(field["options"] or "[]"),
                                 value if value is not None else field["default_value"], f"{key}_value")


def _block_studio(rows, fields, mode, writes_enabled):
    """CP5's single-draft Block editor.

    Relationship widgets intentionally live beside the template form rather
    than in a separate save path.  Their values are handed to
    ``block_draft_operations`` only when the one Prepare button is pressed.
    """
    st.subheader("Blocks", anchor=False)
    generation = st.session_state.get("_editor_studio_form_generation", 0)
    action = st.radio("Block action", ["Edit existing Block", "Create a new Block", "Duplicate existing Block"],
                      horizontal=True, key="editor_studio_block_action", on_change=_clear_studio_review)
    # The lifecycle filter applies to every source picker.  In particular,
    # ``All``/``Archived`` must make archived Blocks visible in Studio rather
    # than silently resembling a restricted inventory.  Archived Blocks are
    # never valid duplicate sources: restore them first, then duplicate the
    # active configuration.
    selectable = _filter_rows(rows, mode)
    archived_non_table = [row for row in rows if row.get("is_archived") and not row.get("is_table")]
    if mode == "Active" and archived_non_table:
        names = ", ".join(row["name"] for row in archived_non_table)
        st.caption(
            f"{names} {'is' if len(archived_non_table) == 1 else 'are'} archived and hidden by the Active filter. "
            "Choose Archived or All to prepare a reviewed restore."
        )
    if action == "Create a new Block":
        block, source_key, stable_key = None, None, "new"
    else:
        if not selectable:
            st.info("No Blocks are available for this action.")
            return
        choices = {row["id"]: row for row in selectable}
        block_id = st.selectbox("Source Block" if action == "Duplicate existing Block" else "Block", list(choices),
                                format_func=lambda ident: _entity_caption(choices[ident], "key", "name"),
                                key="editor_studio_block_select", on_change=_clear_studio_review)
        block, source_key, stable_key = choices[block_id], choices[block_id]["key"], choices[block_id]["key"]
        if block["is_table"]:
            st.info("Table Blocks are read-only. Table row authoring remains outside Stage 6.")
            _show_templates(block)
            _show_rows(db.get_block_usage(block["id"])["fields"], "This Block has no Fields.")
            return
        if block.get("is_archived"):
            st.info("This Block is archived. Restore it before editing or duplicating it.")
            # Archived Blocks return before the editable draft below.  Give a
            # lifecycle review its own display gate here; otherwise the
            # Restore button can freeze a valid candidate which this branch
            # immediately hides on its rerun.
            restore_plan = content_studio.lifecycle_plan("restore", "Blocks", block["key"])
            restore_signature = _studio_signature(
                "lifecycle", {"table": "Blocks", "key": block["key"]}, restore_plan
            )
            if _show_studio_review(restore_signature, writes_enabled):
                return
            _lifecycle_panel("Blocks", block, restore_signature, writes_enabled)
            return

    defaults = ({
        "name": "", "site_label": None, "conclusion_group": None,
        "macro_template": "", "micro_template": "", "conclusion_template": "",
        "context_template": None, "title_fragment_template": None, "conclusion_label_template": None,
    } if block is None else {name: block.get(name) for name in (
        "name", "site_label", "conclusion_group", "macro_template", "micro_template",
        "conclusion_template", "context_template", "title_fragment_template", "conclusion_label_template",
    )})
    existing_bindings = [] if block is None else db.get_block_usage(block["id"])["fields"]
    active_fields = [field for field in fields if not field.get("is_archived")]
    field_by_key = {field["key"]: field for field in active_fields}
    existing_by_key = {binding["key"]: binding for binding in existing_bindings}
    binding_default = [binding["key"] for binding in existing_bindings if binding["key"] in field_by_key]
    baseline = None
    if block is not None:
        # Do not recalculate this as widgets rerun: it is the exact Block plus
        # Block_Fields image this draft was loaded from.  A changed image must
        # reset stale widget state rather than produce rollback operations.
        # Edit and Duplicate deliberately share widgets, so they must also
        # share the baseline for this loaded draft generation. Recapturing a
        # baseline on action change would bless older retained widget values.
        baseline_key = f"_editor_studio_block_baseline_{block['id']}_{generation}"
        if baseline_key not in st.session_state:
            st.session_state[baseline_key] = content_studio.block_draft_baseline(block, existing_bindings)
        baseline = st.session_state[baseline_key]
    draft_signature = {"action": action, "key": stable_key, "generation": generation}
    if _show_studio_review(_studio_signature("block", {"table": "Blocks", "key": stable_key}, draft_signature), writes_enabled):
        return

    key_token = f"editor_studio_block_{stable_key}_{generation}"
    if action == "Create a new Block" or action == "Duplicate existing Block":
        key = st.text_input("Stable key", value="", key=f"{key_token}_new_key")
    else:
        key = block["key"]
        st.caption(f"Stable key: `{key}` (locked)")

    selected = st.multiselect("Bound Fields", list(field_by_key), default=binding_default,
                              format_func=lambda field_key: f"{field_by_key[field_key]['label']} ({field_key})",
                              key=f"{key_token}_fields", on_change=_clear_studio_review)
    order_key = f"{key_token}_order"
    previous = st.session_state.get(order_key, binding_default)
    order = [field_key for field_key in previous if field_key in selected] + [
        field_key for field_key in selected if field_key not in previous
    ]
    st.session_state[order_key] = order
    if order:
        move_key = f"{key_token}_move"
        # A multiselect removal can leave its companion selectbox's session
        # value pointing at the just-removed Field. Repair that state before
        # Streamlit constructs the selectbox or evaluates its Up/Down state.
        if st.session_state.get(move_key) not in order:
            st.session_state[move_key] = order[0]
        move = st.selectbox("Field to move", order,
                            format_func=lambda field_key: field_by_key[field_key]["label"], key=move_key)
        move_index = order.index(move)  # guarded by the reset above
        up, down = st.columns(2)
        if up.button("Move Field up", key=f"{key_token}_up", disabled=move_index == 0):
            index = move_index; order[index - 1], order[index] = order[index], order[index - 1]
            st.session_state[order_key] = order; _clear_studio_review(); st.rerun()
        if down.button("Move Field down", key=f"{key_token}_down", disabled=move_index == len(order) - 1):
            index = move_index; order[index + 1], order[index] = order[index], order[index + 1]
            st.session_state[order_key] = order; _clear_studio_review(); st.rerun()
    bindings = []
    for position, field_key in enumerate(order):
        field = field_by_key[field_key]
        old = existing_by_key.get(field_key, {})
        with st.expander(f"{position + 1}. {field['label']} ({field_key})", expanded=False):
            label_mode = st.checkbox("Override Field label", value=old.get("label_override") is not None,
                                     key=f"{key_token}_{field_key}_label_mode", on_change=_clear_studio_review)
            label = (st.text_input("Label override", old.get("label_override") or "",
                                   key=f"{key_token}_{field_key}_label") if label_mode else None)
            default = _block_binding_default(field, old.get("default_override"), f"{key_token}_{field_key}_default")
            context = st.checkbox("Place in clinical context", value=bool(old.get("context_section")),
                                  key=f"{key_token}_{field_key}_context", on_change=_clear_studio_review)
            bindings.append({"field_key": field_key, "label_override": label,
                             "default_override": default, "context_section": context})

    groups = sorted({row["conclusion_group"] for row in rows if row.get("conclusion_group")})
    group_options = ["No conclusion group", *groups, "Create a new conclusion group…"]
    current_group = defaults["conclusion_group"]
    group_index = group_options.index(current_group) if current_group in group_options else 0
    # This picker is outside the form so choosing the explicit-new branch
    # immediately reveals its required name rather than submitting a stale
    # empty value from the prior form layout.
    group_choice = st.selectbox("Conclusion group", group_options, index=group_index,
                                key=f"{key_token}_group", on_change=_clear_studio_review)
    custom_group = (st.text_input("New conclusion group", key=f"{key_token}_new_group",
                                  on_change=_clear_studio_review)
                    if group_choice == group_options[-1] else "")
    with st.form(f"editor_studio_block_form_{stable_key}_{generation}"):
        name = st.text_input("Block name", defaults["name"] or "", key=f"{key_token}_name")
        site_label = st.text_input("Site label (optional)", defaults["site_label"] or "", key=f"{key_token}_site")
        macro = st.text_area("Macro template", defaults["macro_template"] or "", key=f"{key_token}_macro")
        micro = st.text_area("Microscopy template", defaults["micro_template"] or "", key=f"{key_token}_micro")
        conclusion = st.text_area("Conclusion template", defaults["conclusion_template"] or "", key=f"{key_token}_conclusion")
        context_template = st.text_area("Context template (optional)", defaults["context_template"] or "", key=f"{key_token}_context_template")
        title = st.text_area("Title fragment template (optional)", defaults["title_fragment_template"] or "", key=f"{key_token}_title")
        conclusion_label = st.text_area("Conclusion label template (optional)", defaults["conclusion_label_template"] or "", key=f"{key_token}_conclusion_label")
        prepare = st.form_submit_button("Prepare Block review", disabled=not writes_enabled)
    group = None if group_choice == "No conclusion group" else custom_group if group_choice == group_options[-1] else group_choice
    values = {"name": name, "site_label": site_label, "conclusion_group": group,
              "macro_template": macro, "micro_template": micro, "conclusion_template": conclusion,
              "context_template": context_template, "title_fragment_template": title,
              "conclusion_label_template": conclusion_label}
    if prepare:
        target_action = "create" if action == "Create a new Block" else "duplicate" if action == "Duplicate existing Block" else "edit"
        # New/duplicate drafts have no persisted target yet. Bind the frozen
        # review to the create/source selection (not a transient text widget)
        # so it survives the rerun that hides the draft.
        signature = _studio_signature("block", {"table": "Blocks", "key": stable_key}, {"action": target_action, "values": values, "bindings": bindings})
        if group_choice == group_options[-1] and not custom_group.strip():
            st.error("Provide a name for the new conclusion group.")
        else:
            try:
                intents = content_studio.block_draft_operations(
                    target_action, key, values, bindings, source_key=source_key, baseline=baseline
                )
            except content_studio.StaleBlockDraftError as error:
                # New widget keys on the next run force Streamlit to load the
                # current source rather than retain this tab's stale values.
                _clear_studio_review()
                st.session_state["_editor_studio_form_generation"] = generation + 1
                st.session_state["_editor_studio_error"] = str(error)
                st.rerun()
            except content_studio.StudioIntentError as error:
                st.error(f"Review was not prepared: {error}")
            else:
                _prepare_studio_review(intents, signature, f"{target_action.title()} Block.{key}")
    if block is not None and action == "Edit existing Block":
        _lifecycle_panel("Blocks", block, _studio_signature("block", {"table": "Blocks", "key": key}, values), writes_enabled)


def _preset_instance_label(instance, position, blocks_by_key):
    block = blocks_by_key.get(instance["block_key"], {})
    return f"{position + 1}. {block.get('name', instance['block_key'])} ({instance['block_key']} · instance {instance['instance_no']})"


def _preset_studio(rows, mode, writes_enabled):
    """CP6's one-draft Preset metadata/composition editor.

    The session list is presentation state only.  Its instance numbers are
    loaded from ``sort_order`` and never renumbered: Up/Down changes the list,
    from which the planner emits only ``display_order`` changes.
    """
    st.subheader("Presets", anchor=False)
    generation = st.session_state.get("_editor_studio_form_generation", 0)
    action = st.radio("Preset action", ["Edit existing Preset", "Create a new Preset", "Duplicate existing Preset"],
                      horizontal=True, key="editor_studio_preset_action", on_change=_clear_studio_review)
    selectable = _filter_rows(rows, mode)
    if action == "Create a new Preset":
        preset, source_key, stable_key, source_links = None, None, "new", []
    else:
        if not selectable:
            st.info("No Presets are available for this action.")
            return
        choices = {row["id"]: row for row in selectable}
        preset_id = st.selectbox("Source Preset" if action == "Duplicate existing Preset" else "Preset", list(choices),
                                 format_func=lambda ident: _entity_caption(choices[ident], "short_code", "name"),
                                 key="editor_studio_preset_select", on_change=_clear_studio_review)
        preset, source_key, stable_key = choices[preset_id], choices[preset_id]["short_code"], choices[preset_id]["short_code"]
        _, source_links = content_studio.preset_draft_source(source_key)
        if preset.get("is_archived"):
            st.info("This Preset is archived. Restore it before editing or duplicating it.")
            restore_plan = content_studio.lifecycle_plan("restore", "Presets", source_key)
            restore_signature = _studio_signature("lifecycle", {"table": "Presets", "key": source_key}, restore_plan)
            if _show_studio_review(restore_signature, writes_enabled):
                return
            _lifecycle_panel("Presets", preset, restore_signature, writes_enabled)
            return
        if any(link["block_is_table"] for link in source_links):
            st.info("Table-bearing Presets are read-only in Content Studio. Table row authoring remains outside Stage 6.")
            _show_preview(preset["id"])
            lifecycle_signature = _studio_signature(
                "lifecycle", {"table": "Presets", "key": source_key},
                content_studio.lifecycle_plan("archive", "Presets", source_key),
            )
            if _show_studio_review(lifecycle_signature, writes_enabled):
                return
            _lifecycle_panel("Presets", preset, lifecycle_signature, writes_enabled)
            return

    signature = _studio_signature("preset", {"table": "Presets", "key": stable_key},
                                  {"action": action, "generation": generation})
    if _show_studio_review(signature, writes_enabled):
        return
    token = f"editor_studio_preset_{action.split()[0].lower()}_{stable_key}_{generation}"
    active_blocks = [row for row in db.get_all_editor_blocks()
                     if not row.get("is_archived") and not row.get("is_table")]
    blocks_by_key = {row["key"]: row for row in active_blocks}
    state_key = f"_editor_studio_preset_instances_{action}_{stable_key}_{generation}"
    if state_key not in st.session_state:
        st.session_state[state_key] = [{
            "block_key": link["block_key"], "instance_no": link["sort_order"],
            "field_overrides": json.loads(link["field_overrides"] or "{}"),
        } for link in sorted(source_links, key=lambda link: (link["display_order"], link["sort_order"]))]
    instances = [dict(item) for item in st.session_state[state_key]]
    source_baseline_key = f"_editor_studio_preset_baseline_{action}_{stable_key}_{generation}"
    if preset is not None and source_baseline_key not in st.session_state:
        # Capture once per loaded form generation. Recomputing at submission
        # would bless a second tab's composition update before the stale-draft
        # guard gets a chance to report it.
        st.session_state[source_baseline_key] = content_studio.preset_draft_baseline(preset, source_links)
    endpoint_baseline_key = f"_editor_studio_preset_endpoints_{action}_{stable_key}_{generation}"
    if endpoint_baseline_key not in st.session_state:
        # Only draft-only additions appear here. Existing Preset instances are
        # permanently bound by the source baseline above and must never be
        # silently rebased when another instance is added or removed.
        st.session_state[endpoint_baseline_key] = []

    # Duplication copies a whole safe composition. It deliberately does not
    # offer a partial structural editor, which could otherwise be mistaken for
    # Stage 7 Quick Type/table authoring.
    allow_composition_edit = action != "Duplicate existing Preset"
    if allow_composition_edit:
        add_options = list(blocks_by_key)
        if add_options:
            add_key = f"{token}_add_block"
            if st.session_state.get(add_key) not in add_options:
                st.session_state[add_key] = add_options[0]
            new_block_key = st.selectbox("Add Block instance", add_options,
                                         format_func=lambda key: f"{blocks_by_key[key]['name']} ({key})", key=add_key)
            if st.button("Add Block instance", key=f"{token}_add"):
                used = {item["instance_no"] for item in instances}
                instance_no = next((number for number in range(1000) if number not in used), None)
                if instance_no is None:
                    st.error("This Preset has no safe instance numbers remaining.")
                else:
                    instances.append({"block_key": new_block_key, "instance_no": instance_no, "field_overrides": {}})
                    st.session_state[state_key] = instances
                    endpoints = list(st.session_state[endpoint_baseline_key])
                    endpoints.append({
                        "block_key": new_block_key, "instance_no": instance_no,
                        "baseline": content_studio.preset_instance_endpoint_baseline(new_block_key, instance_no),
                    })
                    st.session_state[endpoint_baseline_key] = endpoints
                    _clear_studio_review(); st.rerun()
        if instances:
            picker_options = list(range(len(instances)))
            move_key = f"{token}_move_instance"
            if st.session_state.get(move_key) not in picker_options:
                st.session_state[move_key] = picker_options[0]
            chosen = st.selectbox("Block instance to move", picker_options,
                                  format_func=lambda pos: _preset_instance_label(instances[pos], pos, blocks_by_key), key=move_key)
            up, down, remove = st.columns(3)
            if up.button("Move Block up", key=f"{token}_up", disabled=chosen == 0):
                instances = composition.move_instance(instances, chosen, -1)
                st.session_state[state_key] = instances; _clear_studio_review(); st.rerun()
            if down.button("Move Block down", key=f"{token}_down", disabled=chosen == len(instances) - 1):
                instances = composition.move_instance(instances, chosen, 1)
                st.session_state[state_key] = instances; _clear_studio_review(); st.rerun()
            if remove.button("Remove Block instance", key=f"{token}_remove"):
                instances = composition.remove_instance(instances, chosen)
                st.session_state[state_key] = instances
                surviving = {(item["block_key"], item["instance_no"]) for item in instances}
                st.session_state[endpoint_baseline_key] = [
                    endpoint for endpoint in st.session_state[endpoint_baseline_key]
                    if (endpoint["block_key"], endpoint["instance_no"]) in surviving
                ]
                _clear_studio_review(); st.rerun()
    else:
        st.caption("Duplicate copies this Preset's ordered Block instances and overrides exactly. Quick Type tokens are not copied.")

    # Resolve field controls per *instance*, not per Block key: the same Block
    # can occur twice with independent override objects.
    resolved = {}
    if preset is not None:
        for block in db.get_preset_blocks(preset["id"]):
            resolved[(block["key"], block["sort_order"])] = block
    else:
        for item in instances:
            block = blocks_by_key.get(item["block_key"])
            if block is not None:
                # The same loader used by previews gives Field type/options
                # and inherited values without adding a writer path.
                scratch = db.get_db_connection()
                try:
                    resolved[(item["block_key"], item["instance_no"])] = db.get_block_on_connection(
                        scratch, block["id"], include_archived=False
                    )
                finally:
                    scratch.close()
    updated_instances = []
    for position, item in enumerate(instances):
        block = resolved.get((item["block_key"], item["instance_no"]))
        if block is None:
            # A user may have just added this instance to an *existing*
            # Preset, so it is not present in get_preset_blocks() yet. Load
            # the Block's resolved Field metadata directly for the draft;
            # this remains read-only and the endpoint assertion binds it to
            # the frozen candidate before any later Apply.
            block_row = blocks_by_key.get(item["block_key"])
            if block_row is not None:
                scratch = db.get_db_connection()
                try:
                    block = db.get_block_on_connection(scratch, block_row["id"], include_archived=False)
                finally:
                    scratch.close()
        overrides = dict(item["field_overrides"])
        with st.expander(_preset_instance_label(item, position, blocks_by_key), expanded=False):
            st.caption(f"Immutable instance number: `{item['instance_no']}`")
            if not allow_composition_edit:
                st.caption("Copied per-instance overrides")
                st.json(overrides)
                updated_instances.append({"block_key": item["block_key"], "instance_no": item["instance_no"],
                                          "field_overrides": overrides})
                continue
            for field in (block or {}).get("fields", []):
                field_key = field["key"]
                override_key = f"{token}_{position}_{item['instance_no']}_{field_key}"
                st.caption(f"Field: {field['label']} (`{field_key}` · {field['type']})")
                enabled = st.checkbox(f"Override {field['label']} ({field_key})", value=field_key in overrides,
                                      key=f"{override_key}_enabled", on_change=_clear_studio_review)
                if not enabled:
                    overrides.pop(field_key, None)
                    continue
                explicit_null = field["type"] in {"text", "decimal"} and overrides.get(field_key, object()) is None
                if field["type"] in {"text", "decimal"}:
                    null_mode = st.checkbox(f"Override {field['label']} ({field_key}) with null", value=explicit_null,
                                            key=f"{override_key}_null", on_change=_clear_studio_review)
                    if null_mode:
                        overrides[field_key] = None
                        continue
                default = overrides.get(field_key, field.get("value"))
                requested = _field_default_widget(
                    field["type"], field.get("options") or [], default, f"{override_key}_value",
                    label=f"Override value for {field['label']} ({field_key})",
                )
                # Historic JSON overrides can store numeric/checkbox values
                # in their original textual spelling. Retain that exact value
                # when the typed widget means the same thing, so a metadata
                # edit does not manufacture an override-only candidate.
                if field_key in overrides:
                    try:
                        same = content_changes._native_stored(field, overrides[field_key]) == requested
                    except (TypeError, ValueError):
                        same = False
                    overrides[field_key] = overrides[field_key] if same else requested
                else:
                    overrides[field_key] = requested
        updated_instances.append({"block_key": item["block_key"], "instance_no": item["instance_no"],
                                  "field_overrides": overrides})
    instances = updated_instances
    st.session_state[state_key] = instances

    defaults = ({"name": "", "category": None, "default_title": None, "default_adicap": None}
                if preset is None else {column: preset.get(column) for column in
                                         ("name", "category", "default_title", "default_adicap")})
    with st.form(f"editor_studio_preset_form_{action}_{stable_key}_{generation}"):
        if action in {"Create a new Preset", "Duplicate existing Preset"}:
            key = st.text_input("Short code")
        else:
            key = preset["short_code"]
            st.caption(f"Short code: `{key}` (locked)")
        name = st.text_input("Preset name", defaults["name"] or "", key=f"{token}_name")
        category = st.text_input("Category (optional)", defaults["category"] or "", key=f"{token}_category")
        title = st.text_input("Default title (optional)", defaults["default_title"] or "", key=f"{token}_title")
        adicap = st.text_input("Default ADICAP (metadata only; Workspace has no consumer)",
                               defaults["default_adicap"] or "", key=f"{token}_adicap")
        prepare = st.form_submit_button("Prepare Preset review", disabled=not writes_enabled)
    values = {"name": name, "category": category or None, "default_title": title or None,
              "default_adicap": adicap or None}
    if prepare:
        target_action = "create" if action == "Create a new Preset" else "duplicate" if action == "Duplicate existing Preset" else "edit"
        baseline = st.session_state.get(source_baseline_key) if preset is not None else None
        # A new instance can be added to either a brand-new or an existing
        # Preset. In both cases its selected Block/Field endpoints need the
        # captured physical baseline at review time.
        endpoint_baselines = st.session_state.get(endpoint_baseline_key)
        frozen_signature = _studio_signature("preset", {"table": "Presets", "key": stable_key},
                                             {"action": target_action, "values": values, "instances": instances})
        try:
            intents = content_studio.preset_draft_operations(
                target_action, key, values, instances, source_key=source_key, baseline=baseline,
                endpoint_baselines=endpoint_baselines,
            )
        except content_studio.StalePresetDraftError as error:
            _clear_studio_review(); st.session_state["_editor_studio_form_generation"] = generation + 1
            st.session_state["_editor_studio_error"] = str(error); st.rerun()
        except content_studio.StudioIntentError as error:
            st.error(f"Review was not prepared: {error}")
        else:
            _prepare_studio_review(intents, frozen_signature, f"{target_action.title()} Preset.{key}")
    if preset is not None and action == "Edit existing Preset":
        _lifecycle_panel("Presets", preset, signature, writes_enabled)


def _group_label_studio(writes_enabled):
    labels = db.get_all_conclusion_group_labels()
    all_blocks = db.get_all_editor_blocks()
    active_blocks = [row for row in all_blocks if not row.get("is_archived")]
    choices = {row["block_key_set"]: row for row in labels}
    st.subheader("Conclusion group labels", anchor=False)
    st.caption("A label changes merged conclusion wording; it is not lifecycle content.")
    selected_key = st.selectbox("Existing label", ["__new__", *choices],
                                format_func=lambda key: "Create a new group label" if key == "__new__" else f"{choices[key]['combined_label']} ({key})",
                                key="editor_studio_group_select", on_change=_clear_studio_review)
    current = choices.get(selected_key)
    selected_blocks = current["block_key_set"].split(",") if current else []
    # Retained labels can legitimately name archived Blocks for pending-case
    # rendering.  Keep those existing members selectable for this one label,
    # but never offer archived Blocks for a new/unrelated relationship.
    block_by_key = {row["key"]: row for row in all_blocks}
    blocks = [*active_blocks]
    for key in selected_blocks:
        row = block_by_key.get(key)
        if row is not None and row not in blocks:
            blocks.append(row)
    blocks.sort(key=lambda row: row["key"])
    generation = st.session_state.get("_editor_studio_form_generation", 0)
    draft = {"blocks": sorted(selected_blocks), "label": current["combined_label"] if current else "", "existing": selected_key}
    signature = _studio_signature("group-label", selected_key, draft)
    if _show_studio_review(signature, writes_enabled): return
    with st.form(f"editor_studio_group_{selected_key}_{generation}"):
        block_keys = st.multiselect("Blocks", [row["key"] for row in blocks], default=selected_blocks,
                                    format_func=lambda key: next(row["name"] + f" ({key})" for row in blocks if row["key"] == key))
        combined = st.text_input("Combined conclusion label", current["combined_label"] if current else "")
        prepare = st.form_submit_button("Prepare group-label review", disabled=not writes_enabled)
    canonical = ",".join(sorted(block_keys)); values = {"combined_label": combined}
    if prepare:
        if not canonical or not combined.strip():
            st.error("Choose at least one Block and provide a combined label.")
        else:
            if current and canonical != current["block_key_set"]:
                intents = [content_studio.operation("unlink", "Conclusion_Group_Labels", {"block_key_set": current["block_key_set"]}),
                           content_studio.operation("link", "Conclusion_Group_Labels", {"block_key_set": canonical}, values)]
            else:
                intents = [content_studio.operation("update" if current else "link", "Conclusion_Group_Labels", {"block_key_set": canonical}, values)]
            signature = _studio_signature("group-label", selected_key, {"blocks": sorted(block_keys), "label": combined, "existing": selected_key})
            _prepare_studio_review(intents, signature, f"{'Edit' if current else 'Create'} conclusion group label {canonical}")
    if current and st.button("Prepare group-label deletion review", key=f"editor_studio_group_delete_{current['id']}", disabled=not writes_enabled):
        signature = _studio_signature("group-label-delete", current["block_key_set"], {})
        _prepare_studio_review([content_studio.operation("unlink", "Conclusion_Group_Labels", {"block_key_set": current["block_key_set"]})], signature,
                                f"Delete conclusion group label {current['block_key_set']}")


def _show_package_error():
    feedback = st.session_state.get("_editor_ai_feedback")
    if not feedback:
        return
    st.error("Dry run failed. Nothing saved; any earlier successful review and confirmation were cleared.")
    st.subheader("Structured validation errors", anchor=False)
    st.dataframe(feedback["errors"], use_container_width=True, hide_index=True)
    if feedback.get("omitted_errors"):
        st.caption(f"{feedback['omitted_errors']} additional error(s) omitted by the fixed safety cap.")
    st.subheader("Copyable AI feedback", anchor=False)
    st.caption("This fixed allowlisted feedback excludes Case identifiers, reports, uploaded values, and raw exceptions.")
    st.code(change_packages.canonical_json(feedback).strip(), language="json")
    local = st.session_state.get("_editor_ai_local_error")
    if local is not None:
        with st.expander("Local details — session only; do not copy to AI"):
            st.error(str(local))


def _ai_package_section(writes_enabled):
    payload = change_packages.export_ai_context()
    context = json.loads(payload)
    st.header("AI package", anchor=False)
    st.info(
        "Optional workflow: PathoPilot makes no network request and requires no AI account. "
        "You may download the file and use any external assistant, or ignore this section entirely."
    )
    st.warning(
        "Contains reusable content/configuration, not Case records. Review content for patient details before "
        "sharing; de-identify any examples you supply separately. Free-plan capacity is not guaranteed: byte "
        "size is not a token estimate."
    )
    st.caption(
        "This content-only AI context is not a recovery snapshot and cannot be restored. The separate recovery "
        "snapshot above remains the prerequisite for Apply."
    )
    st.code(
        f"Content snapshot SHA-256 (copy into the package): {context['snapshot_sha256']}\n"
        f"Exact download SHA-256: {hashlib.sha256(payload).hexdigest()}\n"
        f"Exact download size: {len(payload):,} UTF-8 bytes",
        language=None,
    )
    st.download_button(
        "Download content-only AI context", payload, "pathopilot-ai-context.json",
        "application/json", key="editor_ai_context_download",
    )

    generation = st.session_state.get("_editor_ai_generation", 0)
    upload_key = f"editor_ai_upload_{generation}"
    uploaded = st.file_uploader("Upload one AI change-package JSON file", type=["json"], key=upload_key)
    uploaded_bytes = uploaded.getvalue() if uploaded is not None else None
    signature = hashlib.sha256(uploaded_bytes).hexdigest() if uploaded_bytes is not None else None
    previous = st.session_state.get("_editor_ai_upload_signature", "__first_run__")
    if previous != signature:
        st.session_state["_editor_ai_upload_signature"] = signature
        _clear_confirmation_widgets("editor_ai_review_")
        _clear_ai_review()
    run_key = f"editor_ai_dry_run_{generation}"
    if st.button("Run dry run", key=run_key, disabled=uploaded_bytes is None):
        _clear_confirmation_widgets("editor_ai_review_")
        _clear_ai_review()
        st.session_state["_editor_ai_review_generation"] = (
            st.session_state.get("_editor_ai_review_generation", 0) + 1
        )
        try:
            review = change_packages.dry_run(uploaded_bytes)
        except change_packages.PackageError as error:
            st.session_state["_editor_ai_feedback"] = error.ai_feedback()
            st.session_state["_editor_ai_local_error"] = error.local
        else:
            st.session_state["_editor_ai_review"] = review
            st.session_state["_editor_ai_review_upload_sha"] = signature

    _show_package_error()
    review = st.session_state.get("_editor_ai_review")
    if review is None or st.session_state.get("_editor_ai_review_upload_sha") != signature:
        return
    review_generation = st.session_state.get("_editor_ai_review_generation", 0)
    prefix = f"editor_ai_review_{generation}_{review_generation}"
    _show_full_review(review, prefix)
    confirmed = st.checkbox(
        "I confirm this exact reviewed candidate and its local pending-Case impact",
        key=f"{prefix}_confirm",
    )
    if not writes_enabled:
        st.warning("Apply is locked until the separate recovery snapshot gate above is complete.")
    if st.button("Apply reviewed package", key=f"{prefix}_apply", disabled=not (confirmed and writes_enabled)):
        try:
            revision_id = content_changes.apply_review(review)
        except content_changes.StaleReviewError as error:
            _clear_ai_review(clear_feedback=False)
            st.session_state["_editor_ai_clear_confirmation"] = True
            st.session_state["_editor_error"] = f"Not applied: {error} Run a new dry run."
        except (content_changes.ChangeError, change_packages.PackageError, content_editing.ContentEditError) as error:
            st.session_state["_editor_ai_clear_confirmation"] = True
            st.session_state["_editor_error"] = f"Not applied: {error}"
        else:
            _clear_loaded_entities()
            _clear_ai_review()
            st.session_state["_editor_ai_reset_widgets"] = True
            st.session_state["_editor_message"] = (
                f"Applied as content revision {revision_id}. Review it under Recent revisions."
            )
        st.rerun()


def _edit_block(block):
    entity = _loaded_entity("Blocks", block["key"])
    token = entity["row_hash"][:12]
    with st.form(f"editor_block_form_{block['id']}_{token}"):
        st.caption("Key, name, table mode, site label, and conclusion group are intentionally read-only.")
        macro = st.text_area("Macro template", entity["macro_template"] or "", key=f"editor_block_macro_{token}")
        micro = st.text_area("Microscopy template", entity["micro_template"], key=f"editor_block_micro_{token}")
        conclusion = st.text_area("Conclusion template", entity["conclusion_template"], key=f"editor_block_conclusion_{token}")
        context = st.text_area("Context template (optional)", entity["context_template"] or "", key=f"editor_block_context_{token}")
        title = st.text_area("Title fragment template (optional)", entity["title_fragment_template"] or "", key=f"editor_block_title_{token}")
        label = st.text_area("Conclusion label template (optional)", entity["conclusion_label_template"] or "", key=f"editor_block_label_{token}")
        preview_requested = st.form_submit_button("Preview Block changes")
        save_requested = st.form_submit_button("Save Block wording")
    changes = {"macro_template": macro, "micro_template": micro,
               "conclusion_template": conclusion, "context_template": context,
               "title_fragment_template": title, "conclusion_label_template": label}
    if preview_requested:
        _show_candidate_preview("Blocks", block["key"], changes, entity["row_hash"])
    if save_requested:
        _save("Blocks", block["key"], changes, entity["row_hash"])


def _edit_field(field):
    entity = _loaded_entity("Fields", field["key"])
    token = entity["row_hash"][:12]
    with st.form(f"editor_field_form_{field['id']}_{token}"):
        label = st.text_input("Label", entity["label"], key=f"editor_field_label_{token}")
        default = st.text_input("Default value", entity["default_value"] or "", key=f"editor_field_default_{token}")
        addendum = st.text_area("Conclusion addendum template (optional; context: value)", entity["conclusion_addendum_template"] or "", key=f"editor_field_addendum_{token}")
        preview_requested = st.form_submit_button("Preview Field changes")
        save_requested = st.form_submit_button("Save Field wording")
    changes = {"label": label, "default_value": default,
               "conclusion_addendum_template": addendum}
    if preview_requested:
        _show_candidate_preview("Fields", field["key"], changes, entity["row_hash"])
    if save_requested:
        _save("Fields", field["key"], changes, entity["row_hash"])


def _edit_snippet(snippet):
    entity = _loaded_entity("Snippets", snippet["shortcut"])
    token = entity["row_hash"][:12]
    with st.form(f"editor_snippet_form_{snippet['id']}_{token}"):
        expansion = st.text_area("Expansion", entity["expansion"], key=f"editor_snippet_expansion_{token}")
        category = st.text_input("Category (optional)", entity["category"] or "", key=f"editor_snippet_category_{token}")
        preview_requested = st.form_submit_button("Preview Snippet changes")
        save_requested = st.form_submit_button("Save Snippet")
    changes = {"expansion": expansion, "category": category}
    if preview_requested:
        _show_candidate_preview("Snippets", snippet["shortcut"], changes, entity["row_hash"])
    if save_requested:
        _save("Snippets", snippet["shortcut"], changes, entity["row_hash"])


def _edit_preset(preset):
    entity = _loaded_entity("Presets", preset["short_code"])
    token = entity["row_hash"][:12]
    with st.form(f"editor_preset_form_{preset['id']}_{token}"):
        name = st.text_input("Preset name", entity["name"], key=f"editor_preset_name_{token}")
        category = st.text_input("Category (optional)", entity["category"] or "", key=f"editor_preset_category_{token}")
        title = st.text_input("Default title (optional)", entity["default_title"] or "", key=f"editor_preset_title_{token}")
        preview_requested = st.form_submit_button("Preview Preset changes")
        save_requested = st.form_submit_button("Save Preset wording")
    changes = {"name": name, "category": category, "default_title": title}
    if preview_requested:
        _show_candidate_preview("Presets", preset["short_code"], changes, entity["row_hash"])
    if save_requested:
        _save("Presets", preset["short_code"], changes, entity["row_hash"])


def _revisions():
    revisions = content_editing.recent_revisions()
    if not revisions:
        st.caption("No content revisions yet.")
        return
    st.dataframe(revisions, use_container_width=True, hide_index=True)
    for revision in revisions:
        if not revision["changes"]:
            continue
        identity = revision["summary"] or revision["details"]
        with st.expander(f"Revision {revision['id']}: {identity}"):
            st.caption(revision["details"])
            if revision.get("result_snapshot_hash") is None:
                if revision["origin"] not in {"manual_edit", "revision_revert"}:
                    continue
                acknowledged = st.checkbox("I understand this is refused if later work conflicts", key=f"editor_revert_confirm_{revision['id']}")
                if st.button("Revert safely", key=f"editor_revert_{revision['id']}", disabled=not acknowledged):
                    try:
                        new_id = content_editing.revert_revision(revision["id"])
                    except content_editing.ContentEditError as error:
                        st.error(f"Not reverted: {error}")
                    else:
                        _clear_loaded_entities()
                        st.session_state["_editor_message"] = f"Revision {revision['id']} safely reverted as revision {new_id}."
                        st.rerun()
                continue

            inverse_generation = st.session_state.get("_editor_inverse_generation", 0)
            prepare_key = f"editor_inverse_prepare_{revision['id']}_{inverse_generation}"
            if st.button("Prepare inverse review", key=prepare_key):
                _clear_confirmation_widgets("editor_inverse_review_")
                for key in ("_editor_inverse_review", "_editor_inverse_revision", "_editor_inverse_error",
                            "_editor_inverse_local_error"):
                    st.session_state.pop(key, None)
                st.session_state["_editor_inverse_generation"] = inverse_generation + 1
                try:
                    inverse = content_changes.review_inverse(revision["id"])
                except (content_changes.ChangeError, change_packages.PackageError) as error:
                    st.session_state["_editor_inverse_error"] = str(error)
                    st.session_state["_editor_inverse_local_error"] = getattr(error, "local", None)
                else:
                    st.session_state["_editor_inverse_review"] = inverse
                    st.session_state["_editor_inverse_revision"] = revision["id"]

            if st.session_state.get("_editor_inverse_revision") != revision["id"]:
                continue
            inverse = st.session_state.get("_editor_inverse_review")
            if inverse is None:
                continue
            review_generation = st.session_state.get("_editor_inverse_generation", 0)
            prefix = f"editor_inverse_review_{revision['id']}_{review_generation}"
            _show_full_review(inverse, prefix)
            confirmed = st.checkbox(
                "I confirm this exact inverse review",
                key=f"{prefix}_confirm",
            )
            if st.button("Apply reviewed inverse", key=f"{prefix}_apply", disabled=not confirmed):
                try:
                    new_id = content_changes.apply_review(inverse)
                except content_changes.StaleReviewError as error:
                    st.session_state.pop("_editor_inverse_review", None)
                    st.session_state.pop("_editor_inverse_revision", None)
                    st.session_state["_editor_inverse_clear_confirmation"] = True
                    st.session_state["_editor_error"] = f"Not reverted: {error} Prepare a new inverse review."
                except (content_changes.ChangeError, change_packages.PackageError, content_editing.ContentEditError) as error:
                    st.session_state["_editor_inverse_clear_confirmation"] = True
                    st.session_state["_editor_error"] = f"Not reverted: {error}"
                else:
                    _clear_loaded_entities()
                    st.session_state.pop("_editor_inverse_review", None)
                    st.session_state.pop("_editor_inverse_revision", None)
                    st.session_state["_editor_message"] = (
                        f"Revision {revision['id']} reverted as reviewed revision {new_id}."
                    )
                st.rerun()

    inverse_error = st.session_state.pop("_editor_inverse_error", None)
    if inverse_error:
        st.error(inverse_error)
        local = st.session_state.pop("_editor_inverse_local_error", None)
        if local is not None:
            with st.expander("Local refusal details — session only"):
                st.error(str(local))


if st.session_state.pop("_editor_ai_clear_confirmation", False):
    _clear_confirmation_widgets("editor_ai_review_")
if st.session_state.pop("_editor_inverse_clear_confirmation", False):
    _clear_confirmation_widgets("editor_inverse_review_")

if st.session_state.pop("_editor_ai_reset_widgets", False):
    st.session_state["_editor_ai_generation"] = st.session_state.get("_editor_ai_generation", 0) + 1
    for key in list(st.session_state):
        if key.startswith("editor_ai_"):
            st.session_state.pop(key, None)
    st.session_state.pop("_editor_ai_upload_signature", None)
    _clear_ai_review()

st.title("✏️ Editor")
message = st.session_state.pop("_editor_message", None)
if message:
    st.success(message)
error_message = st.session_state.pop("_editor_error", None)
if error_message:
    st.error(error_message)
writes_enabled = _snapshot_gate()
presets, blocks, fields, snippets = db.get_all_presets(), db.get_all_editor_blocks(), db.get_all_fields(), db.get_all_snippets()
section = st.radio(
    "Editor section",
    ["Content Studio", "Presets", "Blocks", "AI package", "Recent revisions"],
    key="editor_section",
    on_change=_clear_studio_review,
    horizontal=True,
    label_visibility="collapsed",
    width="stretch",
)

if section == "Content Studio":
    st.header("Content Studio", anchor=False)
    st.caption("Draft changes become a frozen review before they can be applied. Stable keys and Field type/options are immutable.")
    mode = st.radio("Lifecycle filter", ["Active", "Archived", "All"], horizontal=True,
                    key="editor_studio_filter", on_change=_clear_studio_review)
    studio_kind = st.radio("Content Studio area", ["Fields", "Blocks", "Presets", "Snippets", "Group labels"], horizontal=True,
                           key="editor_studio_kind", on_change=_clear_studio_review)
    studio_error = st.session_state.pop("_editor_studio_error", None)
    if studio_error:
        st.error(f"Review was not prepared: {studio_error}")
        studio_local_error = st.session_state.pop("_editor_studio_local_error", None)
        if studio_local_error is not None:
            with st.expander("Local candidate refusal details — session only"):
                st.error(str(studio_local_error))
    all_fields = db.get_all_fields(include_archived=True)
    all_blocks = db.get_all_editor_blocks()
    all_presets = db.get_all_presets(include_archived=True)
    all_snippets = db.get_all_snippets(include_archived=True)
    if studio_kind == "Fields":
        _field_studio(all_fields, mode, writes_enabled)
    elif studio_kind == "Blocks":
        _block_studio(all_blocks, all_fields, mode, writes_enabled)
    elif studio_kind == "Presets":
        _preset_studio(all_presets, mode, writes_enabled)
    elif studio_kind == "Snippets":
        _snippet_studio(all_snippets, mode, writes_enabled)
    else:
        _group_label_studio(writes_enabled)

elif section == "Presets":
    if presets:
        preset_by_id = {row["id"]: row for row in presets}
        preset_id = st.selectbox("Preset", list(preset_by_id), format_func=lambda ident: _preset_label(preset_by_id[ident]), key="editor_preset_select")
        preset = preset_by_id[preset_id]
        st.caption(f"{preset.get('category') or 'Uncategorised'} · shortcut: `{preset['short_code']}`")
        st.info(f"Impact: {db.get_preset_pending_case_count(preset_id)} pending case(s) currently saved with this Preset.")
        st.info("Preset authoring moves to Content Studio in Checkpoint 6. This checkpoint keeps Presets read-only.")
        st.subheader("Ordered Blocks", anchor=False)
        for position, block in enumerate(db.get_preset_usage(preset_id), 1):
            with st.expander(f"{position}. {block['name']} (`{block['key']}`)"):
                st.caption(f"site label: {block.get('site_label') or '—'} · conclusion group: {block.get('conclusion_group') or '—'}")
                _show_rows(block["fields"], "This Block has no Fields.")
        _show_preview(preset_id)
    else: st.info("No Presets are configured.")

elif section == "Blocks":
    if blocks:
        block_by_id = {row["id"]: row for row in blocks}
        block_id = st.selectbox("Block", list(block_by_id), format_func=lambda ident: f"{block_by_id[ident]['name']} ({block_by_id[ident]['key']})", key="editor_block_select")
        block = block_by_id[block_id]; usage = db.get_block_usage(block_id)
        st.caption(f"key: `{block['key']}` · table: {'yes' if block['is_table'] else 'no'} · site label: {block.get('site_label') or '—'} · conclusion group: {block.get('conclusion_group') or '—'}")
        st.info(f"Impact: {usage['pending_case_count']} pending case(s) may use this Block.")
        st.info("Block authoring is available in Content Studio. This legacy view remains read-only.")
        st.subheader("Templates", anchor=False); _show_templates(block)
        st.subheader("Fields used", anchor=False); _show_rows(usage["fields"], "This Block has no Fields.")
        st.subheader("Used by Presets", anchor=False); _show_rows(usage["presets"], "No Preset currently uses this Block.")
    else: st.info("No Blocks are configured.")

elif section == "AI package":
    _ai_package_section(writes_enabled)

elif section == "Recent revisions":
    if writes_enabled: _revisions()
    else: st.info("Revision history and reversion unlock after the initial manual snapshot is recorded.")
