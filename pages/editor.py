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
import quicktype
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


def _snapshot_gate():
    snapshot = content_snapshot.export_content_snapshot()
    payload = content_snapshot.content_snapshot_json(snapshot)
    digest = content_snapshot.content_snapshot_hash(snapshot)
    state = content_editing.initial_snapshot_status()
    if state["initial_snapshot_hash"]:
        st.caption(f"Initial manual snapshot recorded at {state['initial_snapshot_at']}. Optional exports remain on demand.")
        st.download_button("Download current content snapshot", payload, "pathopilot-content-snapshot.json", "application/json", key="editor_snapshot_download")
        return True
    st.warning("Content changes are locked until you create and save one manual recovery snapshot.")
    st.download_button("Download initial content snapshot", payload, "pathopilot-initial-content-snapshot.json", "application/json", key="editor_initial_snapshot_download")
    acknowledged = st.checkbox("I have saved this initial snapshot outside PathoPilot", key="editor_initial_snapshot_ack")
    if st.button("Enable reviewed content changes", disabled=not acknowledged, key="editor_enable_direct_editing"):
        content_editing.record_initial_snapshot(digest)
        st.session_state["_editor_message"] = "Initial recovery snapshot recorded. Reviewed content changes are now enabled."
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
                                                   "assert_preset_draft", "assert_preset_endpoints",
                                                   "assert_source_draft", "assert_configuration_draft"}]
    st.subheader(f"Normalized operations ({len(operations)})", anchor=False)
    for position, operation in enumerate(operations, 1):
        if operation.get("op") == "case_preset_reference":
            before, after = operation["before_preset_id"], operation["after_preset_id"]
            if after is None:
                label = f"Detach validated Case preset — Case ID {operation['case_id']} (Preset ID {before})"
            else:
                label = f"Reattach validated Case preset — Case ID {operation['case_id']} (Preset ID {after})"
        else:
            key = operation.get("key")
            identity = (key if isinstance(key, str) else
                        ", ".join(f"{name}={value}" for name, value in (key or {}).items()))
            label = f"{operation.get('op', 'operation')} {operation.get('table', 'content')} — {identity}"
        with st.expander(f"{position}. {label}"):
            if "index" in operation:
                st.caption(f"Original uploaded operation index: {operation['index']}")
            else:
                st.caption("Content Studio or reviewed-inverse operation")
            values = operation.get("set", operation.get("values", {}))
            if values:
                _show_mapping(values)
            if operation.get("op") == "case_preset_reference":
                _show_mapping({
                    "case_id": operation["case_id"],
                    "before_preset_id": operation["before_preset_id"],
                    "after_preset_id": operation["after_preset_id"],
                })


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


def _show_configuration_review(data):
    """Render the complete signed configuration image, not only its delta."""
    configuration = data.get("configuration_review") or {}
    before = configuration.get("before") or []
    after = configuration.get("after") or []
    if not before and not after:
        return
    st.subheader("Complete configuration", anchor=False)
    before_col, after_col = st.columns(2)
    for column, heading, rows in (
        (before_col, "Current grammar", before),
        (after_col, "Reviewed candidate grammar", after),
    ):
        with column:
            st.markdown(f"**{heading}**")
            st.code(json.dumps(rows, ensure_ascii=False, indent=2), language="json")
    findings = configuration.get("findings") or {}
    for error in findings.get("reachability_errors", []):
        st.error(error)
    for warning in findings.get("reachability_warnings", []):
        st.warning(warning)


def _show_guided_evidence(data):
    """Show candidate-bound interpretations before any approval controls."""
    evidence = data.get("guided_evidence")
    if not evidence:
        return
    if evidence.get("kind") == "quick_type":
        st.subheader("Frozen Quick Type interpretations", anchor=False)
        for item in evidence.get("examples", []):
            with st.expander(f"{item['label']}: {item['code']}"):
                expected = "successful parse" if item["positive"] else "rejection"
                actual = "rejected" if item["error"] else f"routed to {item.get('preset')}"
                st.caption(f"Expected: {expected} · Actual: {actual}")
                if item["error"]:
                    st.info(item["error"])
                else:
                    _show_rows(item.get("decoded", []), "Bare code: no field overrides.")
                    _show_report(item.get("report"), "Production-path report")
    elif evidence.get("kind") == "consistency":
        st.subheader("Frozen consistency-rule probes", anchor=False)
        for item in evidence.get("blocks", []):
            with st.expander(f"{item['block_name']} — rule {item['position']}"):
                st.caption(item["rule"]["message"])
                left, right = st.columns(2)
                with left:
                    st.markdown("**Matching values — warning fires**")
                    _show_mapping(item["matching"])
                with right:
                    st.markdown("**Nonmatching values — warning stops**")
                    _show_mapping(item["nonmatching"])


def _show_consistency_warning_impact(data):
    """Keep CP3 warning-only deltas visibly separate from Case/render deltas."""
    impact = data.get("consistency_warning_impact")
    if not impact:
        return
    st.subheader("Consistency warning impact", anchor=False)
    st.caption("These warnings are evaluated on the reviewed candidate. Warning-only changes do not edit Cases or their fingerprints.")
    defaults = impact.get("default_presets", [])
    pending = impact.get("pending_cases", [])
    default_changed = sum(item["warning_changed"] for item in defaults)
    pending_changed = sum(item["warning_changed"] for item in pending)
    st.caption(f"{default_changed}/{len(defaults)} default Preset warning sets changed · "
               f"{pending_changed}/{len(pending)} pending Case warning sets changed")
    for heading, rows, identifier in (
        ("Default Presets", defaults, "preset_code"),
        ("Pending Cases", pending, "case_number"),
    ):
        st.markdown(f"**{heading}**")
        for item in rows:
            label = item.get(identifier) or str(item.get("id"))
            marker = "changed" if item["warning_changed"] else "unchanged"
            with st.expander(f"{label} — warnings {marker}"):
                st.caption("Rendering changed: " + ("yes" if item["rendering_changed"] else "no")
                           + " · fingerprint changed: " + ("yes" if item["fingerprint_changed"] else "no"))
                before_col, after_col = st.columns(2)
                with before_col:
                    st.markdown("**Before**")
                    _show_rows([{"warning": value} for value in item["before"]], "No warnings.")
                with after_col:
                    st.markdown("**Candidate after**")
                    _show_rows([{"warning": value} for value in item["after"]], "No warnings.")


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
    st.caption("Review summary")
    st.code(data["summary"], language=None)
    for warning in data.get("warnings", []):
        st.warning(warning)
    for warning in data.get("branch_warnings", []):
        with st.expander("Candidate branch warning"):
            _show_mapping(warning)
    _show_configuration_review(data)
    _show_guided_evidence(data)
    _show_consistency_warning_impact(data)
    _show_operations(review)
    _show_changes(review)
    _show_review_reports(review, key_prefix)
    with st.expander("Content hashes"):
        st.code(
            f"Base SHA-256: {review.base_snapshot_hash}\n"
            f"Candidate SHA-256: {review.candidate_snapshot_hash}\n"
            f"Imported package SHA-256: {review.package_hash or 'not applicable'}",
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


def _advance_studio_draft_generation():
    """Retire every guided-form widget identity after a content write."""
    _clear_studio_review()
    st.session_state.pop("_editor_studio_widget_stash", None)
    st.session_state["_editor_studio_form_generation"] = (
        st.session_state.get("_editor_studio_form_generation", 0) + 1
    )


def _stash_studio_widget_state():
    """Preserve unsubmitted guided form values across ordinary navigation.

    Streamlit prunes widget-owned keys after a run where the corresponding
    branch is not rendered.  Quick Type and Presets already have explicit
    draft objects, but the older guided forms deliberately use normal form
    widgets.  Snapshot those widget values before branch selection can hide
    them; restoration happens before any editor widget is instantiated.
    """
    button_suffixes = ("_archive", "_restore", "_delete", "_up", "_down", "_add", "_remove")
    state = {
        key: value for key, value in st.session_state.items()
        if key.startswith("editor_studio_") and not key.endswith(button_suffixes)
    }
    if state:
        st.session_state["_editor_studio_widget_stash"] = state


def _restore_studio_widget_state():
    """Re-seed navigated-away guided widgets with their unchanged draft."""
    button_suffixes = ("_archive", "_restore", "_delete", "_up", "_down", "_add", "_remove")
    for key, value in st.session_state.get("_editor_studio_widget_stash", {}).items():
        # This executes before the page constructs editor widgets.  Do not
        # overwrite live current-run state (notably a click/change callback).
        if not key.endswith(button_suffixes) and key not in st.session_state:
            st.session_state[key] = value


def _current_studio_baseline(state_key, current):
    """Keep navigation-persistent drafts only while their source is exact.

    Each guided editor already captures a physical CP1/Stage 6 source
    baseline for candidate protection.  Recheck that same identity on a later
    render rather than waiting for Prepare to discover an obsolete local
    draft.  The generation change gives every form control fresh keys, while
    navigation with an unchanged source retains its local values.
    """
    previous = st.session_state.get(state_key)
    if previous is None:
        st.session_state[state_key] = current
        return current
    if previous != current:
        _advance_studio_draft_generation()
        st.rerun()
    return previous


def _studio_signature(kind, target, values):
    return json.dumps({"kind": kind, "target": target, "values": values},
                      ensure_ascii=False, sort_keys=True, default=str)


def _prepare_studio_review(intents, signature, summary, *, evidence_factory=None,
                            preserve_stale_draft=False):
    _clear_studio_review()
    try:
        snapshot = content_snapshot.export_content_snapshot()
        review = content_studio.review(
            intents, content_snapshot.content_snapshot_hash(snapshot), summary=summary,
            evidence_factory=evidence_factory,
        )
    except (content_studio.StudioIntentError, content_changes.ChangeError,
            content_editing.ContentEditError, change_packages.PackageError) as error:
        if isinstance(error, content_changes.StaleDraftReviewError) and preserve_stale_draft:
            # CP3 owns a read-only preserved-draft/reload panel.  Do not let
            # the generic race recovery advance its generation first.
            st.session_state["_editor_consistency_review_stale_error"] = str(error)
        else:
            st.session_state["_editor_studio_error"] = str(error)
            st.session_state["_editor_studio_local_error"] = getattr(error, "local", None)
        if isinstance(error, content_changes.StaleDraftReviewError) and not preserve_stale_draft:
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
            _advance_studio_draft_generation()
            st.session_state["_editor_message"] = f"Applied as content revision {revision_id}."
        st.rerun()
    return True


def _filter_rows(rows, mode):
    if mode == "Active":
        return [row for row in rows if not row.get("is_archived")]
    if mode == "Archived":
        return [row for row in rows if row.get("is_archived")]
    return rows


def _simple_source_baseline(table, key, generation):
    """Keep a simple form bound to the exact row it originally loaded."""
    state_key = f"_editor_studio_source_baseline_{table}_{key}_{generation}"
    return _current_studio_baseline(
        state_key, content_studio.source_draft_baseline(table, key)
    )


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


def _quick_type_mapping_draft(items):
    """Give each mapping row a stable identity within its token draft."""
    result = []
    for index, item in enumerate(items or []):
        if isinstance(item, dict) and {"uid", "key", "value"} <= set(item):
            result.append({name: item[name] for name in ("uid", "key", "value")})
        else:
            key, value = item
            result.append({"uid": f"saved-{index}", "key": key, "value": value})
    return result


def _quick_type_lookup_table(mapping_rows):
    """Validate row identity semantics before converting rows to a mapping."""
    seen = set()
    for row in mapping_rows:
        key = row["key"]
        if key and key in seen:
            raise content_studio.StudioIntentError(
                f"Duplicate lookup key '{key}' must be removed or changed before Prepare."
            )
        if key:
            seen.add(key)
    return {row["key"]: row["value"] for row in mapping_rows}


def _quick_type_studio(writes_enabled):
    """CP2's draft-only Quick Type form.  All persistence remains in CP1."""
    presets = db.get_all_presets()
    st.subheader("Guided Quick Type Studio", anchor=False)
    st.caption("This editor changes only a local complete token-list draft. Prepare freezes a reviewed candidate; Apply is the only writer.")
    if not presets:
        st.info("No active Preset is available."); return
    by_code = {p["short_code"]: p for p in presets}
    selected = st.selectbox("Active Preset", list(by_code), format_func=lambda c: _preset_label(by_code[c]),
                            key="editor_quick_type_preset", on_change=_clear_studio_review)
    preset = by_code[selected]
    generation = st.session_state.get("_editor_studio_form_generation", 0)
    widget_scope = f"editor_qt_{selected}_{generation}"
    draft_key = f"_editor_qt_draft_{selected}_{generation}"
    baseline_key = f"_editor_qt_baseline_{selected}_{generation}"
    uid_key = f"_editor_qt_next_uid_{selected}_{generation}"
    current_baseline = content_studio.configuration_draft_baseline("quick_type", selected)
    if baseline_key in st.session_state:
        _current_studio_baseline(baseline_key, current_baseline)
    if draft_key not in st.session_state:
        st.session_state[draft_key] = [
            {**row, "uid": f"saved-{row['sort_order']}",
             "mappings": _quick_type_mapping_draft(list((row.get("lookup_table") or {}).items())),
             "next_mapping_uid": len(row.get("lookup_table") or {})}
            for row in db.get_quick_type_tokens(preset["id"])
        ]
        st.session_state[baseline_key] = current_baseline
        st.session_state[uid_key] = len(st.session_state[draft_key])
    draft = st.session_state[draft_key]
    blocks = [b for b in db.get_preset_blocks(preset["id"]) if not b.get("is_table")]
    targets = {b["sort_order"]: b for b in blocks}
    if _show_studio_review(_studio_signature("quick-type", selected, {"draft": draft}), writes_enabled):
        return
    if not targets:
        st.warning("This Preset has no active non-table Block instances."); return
    if st.button("Add Quick Type token", key=f"{widget_scope}_add"):
        first_block = next(iter(targets))
        first_field = targets[first_block]["fields"][0] if targets[first_block]["fields"] else None
        if first_field is None: st.error("No active bound Field is available.")
        else:
            uid_number = st.session_state[uid_key]
            st.session_state[uid_key] = uid_number + 1
            draft.append({"uid": f"new-{uid_number}",
                          "block_sort_order": first_block, "field_key": first_field["key"], "token_kind": "lookup",
                          "lookup_table": {"x": first_field.get("value")}, "digit_width": None,
                          "mappings": [{"uid": "new-0", "key": "x", "value": first_field.get("value")}],
                          "next_mapping_uid": 1})
            _clear_studio_review(); st.rerun()
    normalized = []
    draft_errors = []
    for index, row in enumerate(draft):
        uid = row["uid"]
        token_widget = f"{widget_scope}_token_{uid}"
        target_options = list(targets)
        current_target = row["block_sort_order"] if row["block_sort_order"] in targets else target_options[0]
        with st.expander(f"{index + 1}. Token", expanded=True):
            block_no = st.selectbox("Target Block instance", target_options, index=target_options.index(current_target),
                                    format_func=lambda n: f"{targets[n]['name']} — instance #{n} (display position shown in report order)",
                                    key=f"{token_widget}_block", on_change=_clear_studio_review)
            fields = targets[block_no]["fields"]; field_by_key = {f["key"]: f for f in fields}
            if not fields: st.error("This Block instance has no active Fields."); continue
            field_key = st.selectbox("Target Field", list(field_by_key),
                                     index=list(field_by_key).index(row["field_key"]) if row["field_key"] in field_by_key else 0,
                                     format_func=lambda k: f"{field_by_key[k]['label']} ({k} · {field_by_key[k]['type']})",
                                     key=f"{token_widget}_field", on_change=_clear_studio_review)
            field = field_by_key[field_key]
            kinds = ["lookup"] + (["measurement"] if field["type"] in {"number", "decimal"} else [])
            kind = st.radio("Token kind", kinds, index=kinds.index(row["token_kind"]) if row["token_kind"] in kinds else 0,
                            horizontal=True, key=f"{token_widget}_kind", on_change=_clear_studio_review)
            mappings = _quick_type_mapping_draft(
                row.get("mappings", list((row.get("lookup_table") or {}).items()))
            )
            next_mapping_uid = int(row.get("next_mapping_uid", len(mappings)))
            if kind == "lookup":
                st.caption("Each lookup key is exactly one character. Values use the selected Field's type.")
                rebuilt = []
                for mi, mapping_row in enumerate(mappings):
                    mapping_uid = mapping_row["uid"]
                    mapping_widget = f"{token_widget}_map_{mapping_uid}"
                    a, b, c = st.columns([1, 3, 1])
                    key = a.text_input("Key", mapping_row["key"], max_chars=1,
                                       key=f"{mapping_widget}_key", label_visibility="collapsed",
                                       on_change=_clear_studio_review)
                    value = b.empty()
                    with value:
                        typed = _field_default_widget(
                            field["type"], field.get("options") or [], mapping_row["value"],
                            f"{mapping_widget}_value_{field['type']}", label="Mapped value",
                        )
                    rebuilt.append({"uid": mapping_uid, "key": key, "value": typed})
                    if c.button("Remove mapping", key=f"{mapping_widget}_remove"):
                        # Retain already-rendered live values and untouched
                        # later rows by stable identity; no positional widget
                        # key is reused for the row that follows this one.
                        row["mappings"] = rebuilt[:-1] + mappings[mi + 1:]
                        row["next_mapping_uid"] = next_mapping_uid
                        st.session_state[draft_key] = draft
                        _clear_studio_review()
                        st.rerun()
                if st.button("Add mapping", key=f"{token_widget}_map_add"):
                    rebuilt.append({"uid": f"new-{next_mapping_uid}", "key": "",
                                    "value": field.get("value")})
                    row["mappings"] = rebuilt
                    row["next_mapping_uid"] = next_mapping_uid + 1
                    _clear_studio_review(); st.rerun()
                try:
                    mapping = _quick_type_lookup_table(rebuilt)
                except content_studio.StudioIntentError as error:
                    mapping = None
                    draft_errors.append(str(error))
                    st.error(error)
                width = None
            else:
                width_enabled = st.checkbox("Set a maximum digit width", value=row.get("digit_width") is not None,
                                            key=f"{token_widget}_width_enabled", on_change=_clear_studio_review)
                width = (st.number_input("Maximum digits", min_value=1, value=int(row.get("digit_width") or 1), step=1,
                                         key=f"{token_widget}_width", on_change=_clear_studio_review)
                         if width_enabled else None)
                mapping, rebuilt = None, []
            up, down, delete = st.columns(3)
            if up.button("Up", key=f"{token_widget}_up", disabled=index == 0):
                draft[index - 1], draft[index] = draft[index], draft[index - 1]; _clear_studio_review(); st.rerun()
            if down.button("Down", key=f"{token_widget}_down", disabled=index == len(draft) - 1):
                draft[index + 1], draft[index] = draft[index], draft[index + 1]; _clear_studio_review(); st.rerun()
            if delete.button("Delete token", key=f"{token_widget}_delete"):
                draft.pop(index); _clear_studio_review(); st.rerun()
            normalized.append({"uid": uid, "sort_order": row.get("sort_order"), "block_sort_order": block_no,
                               "field_key": field_key, "token_kind": kind, "lookup_table": mapping,
                               "digit_width": int(width) if width is not None else None, "mappings": rebuilt,
                               "next_mapping_uid": next_mapping_uid})
    st.session_state[draft_key] = normalized
    user_code = st.text_input("Your positive Quick Type example (optional)",
                              key=f"{widget_scope}_user", on_change=_clear_studio_review)
    # The UI owns one complete ordered list.  Persisted token positions are
    # implementation identities, not a user-facing draft concern: normalize
    # every row together after every add/delete/reorder so CP1 never receives
    # an illegal mixture of positioned and unpositioned tokens.
    token_data = [{"sort_order": position,
                   **{k: row[k] for k in ("block_sort_order", "field_key", "token_kind", "lookup_table", "digit_width")}}
                  for position, row in enumerate(normalized)]
    generated = content_studio.quick_type_generated_examples(selected, token_data, presets)
    st.subheader("Test the current draft", anchor=False)
    test_code = st.text_input("Quick Type code", key=f"{widget_scope}_test_code")
    test_signature = _studio_signature(
        "quick-type-test", selected, {"tokens": token_data, "code": test_code}
    )
    test_state_key = f"_editor_qt_test_{selected}_{generation}"
    if st.button("Test code", key=f"{widget_scope}_test"):
        if draft_errors:
            result = {"code": test_code, "error": draft_errors[0]}
        else:
            result = content_studio.quick_type_test_draft(selected, token_data, test_code)
        st.session_state[test_state_key] = {"signature": test_signature, "result": result}
    tested = st.session_state.get(test_state_key)
    if tested and tested.get("signature") == test_signature:
        result = tested["result"]
        st.subheader("Draft test result", anchor=False)
        if result.get("error"):
            st.error(result["error"])
        else:
            st.success(f"Parsed as {result['preset']} with the current draft.")
            _show_rows(result.get("decoded", []), "Bare code: no field overrides.")
            _show_report(result.get("report"), "Production-path report")
    examples = generated + ([{"label": "Your positive example", "code": user_code,
                              "positive": True, "expected_preset": selected}]
                            if user_code.strip() else [])
    st.caption("Generated probes are frozen into review with their decoded values and reports.")
    if st.button("Prepare Quick Type review", key=f"{widget_scope}_prepare", disabled=not writes_enabled):
        try:
            if draft_errors:
                raise content_studio.StudioIntentError(draft_errors[0])
            quicktype.validate_quick_type_config(token_data)
            if user_code.strip():
                matched, rem = quicktype.find_preset_by_prefix(user_code.strip(), presets)
                _, error = quicktype.parse_tokens(rem, token_data) if matched and matched["short_code"] == selected else (None, "does not route to this Preset")
                if error: raise content_studio.StudioIntentError(f"Your positive example must parse completely: {error}")
            intents = content_studio.quick_type_draft_operations(selected, token_data,
                       baseline=st.session_state[baseline_key])
            signature = _studio_signature("quick-type", selected, {"draft": normalized, "examples": examples})
            _prepare_studio_review(intents, signature, f"Edit Quick Type grammar for {selected}",
                                   evidence_factory=content_studio.quick_type_review_evidence(selected, examples))
        except (ValueError, content_studio.StudioIntentError) as error:
            st.error(f"Review was not prepared: {error}")


def _rule_value_rows(values, prefix):
    """Keep numeric rule-value widgets stable when rows are edited or removed."""
    return [item if isinstance(item, dict) and {"uid", "value"} <= set(item)
            else {"uid": f"{prefix}-{index}", "value": item}
            for index, item in enumerate(values or [])]


def _rule_values_widget(field, values, widget_scope, side):
    """Typed exact-set controls for one side of a consistency predicate."""
    field_type = field["type"]
    if field_type == "checkbox":
        selected = st.multiselect(
            "Triggering values", [False, True], default=[value for value in values if type(value) is bool],
            format_func=lambda value: "Checked" if value else "Unchecked",
            key=f"{widget_scope}_{side}_checkbox", on_change=_clear_studio_review,
        )
        return selected, values
    if field_type == "select":
        options = field.get("options") or []
        selected = st.multiselect(
            "Triggering values", options, default=[value for value in values if value in options],
            key=f"{widget_scope}_{side}_select", on_change=_clear_studio_review,
        )
        return selected, values
    if field_type == "text":
        text = st.text_area(
            "Exact triggering values (one value per line)", value="\n".join(str(value) for value in values),
            key=f"{widget_scope}_{side}_text", on_change=_clear_studio_review,
        )
        # Blank lines are not a useful visible authoring value; the planner
        # still owns canonical typed validation and duplicate refusal.
        return [line for line in text.splitlines() if line], values

    rows = _rule_value_rows(values, f"{side}-saved")
    rebuilt = []
    for index, row in enumerate(rows):
        row_scope = f"{widget_scope}_{side}_value_{row['uid']}"
        left, right = st.columns([4, 1])
        with left:
            if field_type == "number":
                try:
                    current = int(row["value"])
                except (TypeError, ValueError):
                    current = 0
                value = st.number_input("Triggering value", min_value=0, step=1, value=current,
                                        key=row_scope, on_change=_clear_studio_review)
            else:
                try:
                    current = float(row["value"])
                except (TypeError, ValueError):
                    current = 0.0
                value = st.number_input("Triggering value", min_value=0.0, value=current,
                                        key=row_scope, on_change=_clear_studio_review)
        rebuilt.append({"uid": row["uid"], "value": value})
        with right:
            if st.button("Remove value", key=f"{row_scope}_remove"):
                return [item["value"] for item in rebuilt[:-1] + rows[index + 1:]], rebuilt[:-1] + rows[index + 1:]
    if st.button("Add triggering value", key=f"{widget_scope}_{side}_add"):
        next_uid = max((int(item["uid"].rsplit("-", 1)[-1]) for item in rows
                        if item["uid"].rsplit("-", 1)[-1].isdigit()), default=-1) + 1
        rebuilt.append({"uid": f"new-{next_uid}", "value": 0 if field_type == "number" else 0.0})
        return [item["value"] for item in rebuilt], rebuilt
    return [item["value"] for item in rebuilt], rebuilt


def _consistency_rule_studio(writes_enabled):
    """CP3's local complete-rule draft; Content Studio remains the only writer."""
    active = [row for row in db.get_all_editor_blocks() if not row["is_archived"] and not row["is_table"]]
    st.subheader("Guided consistency-rule authoring", anchor=False)
    st.caption("A rule warns when Field A is in its exact set and Field B is in its exact set. Prepare freezes warning probes and pending-Case deltas; Apply is the only writer.")
    if not active:
        st.info("No active non-table Block is available.")
        return
    by_key = {row["key"]: row for row in active}
    selected = st.selectbox("Active non-table Block", list(by_key),
                            format_func=lambda key: f"{by_key[key]['name']} ({key})",
                            key="editor_consistency_block", on_change=_clear_studio_review)
    generation = st.session_state.get("_editor_studio_form_generation", 0)
    target_state_key = f"_editor_consistency_target_{generation}"
    previous_target = st.session_state.get(target_state_key)
    if previous_target is not None and previous_target not in by_key:
        # An archive/table-state/lifecycle change can remove the old owner
        # from the eligible selector before Streamlit renders its saved
        # selection. Treat that as the same explicit stale-source boundary,
        # rather than quietly showing another Block's empty draft.
        _clear_studio_review()
        st.error(
            "This consistency-rule draft's Block is no longer an active non-table source. "
            "The local draft was preserved for reference, but cannot be prepared. "
            "Reload current source and rebuild it."
        )
        with st.expander("Preserved stale local rule draft"):
            st.code(json.dumps(st.session_state.get(
                f"_editor_consistency_draft_{previous_target}_{generation}", []
            ), ensure_ascii=False, indent=2), language="json")
        if st.button("Reload current consistency-rule source", key=f"editor_consistency_stale_owner_{generation}_reload"):
            _advance_studio_draft_generation()
            st.rerun()
        return
    st.session_state[target_state_key] = selected
    widget_scope = f"editor_consistency_{selected}_{generation}"
    draft_key = f"_editor_consistency_draft_{selected}_{generation}"
    baseline_key = f"_editor_consistency_baseline_{selected}_{generation}"
    uid_key = f"_editor_consistency_next_uid_{selected}_{generation}"
    current_baseline = content_studio.configuration_draft_baseline("consistency", selected)
    review_stale_error = st.session_state.pop("_editor_consistency_review_stale_error", None)
    if (baseline_key in st.session_state
            and st.session_state[baseline_key] != current_baseline):
        # Do not pass an old rule form through newly loaded Field widgets:
        # doing so can silently coerce a removed/retyped endpoint into a new
        # selection before CP1 has a chance to refuse its physical baseline.
        # Keep the original draft object intact for reference, but make the
        # owner/endpoint change explicit and require a deliberate reload to
        # create a new generation bound to current source state.
        _clear_studio_review()
        st.error(
            "This consistency-rule draft is stale because its Block, rules, or endpoint Fields "
            "changed in another tab. The local draft was preserved for reference, but cannot be "
            "prepared against the new source. Reload current source and rebuild it."
        )
        with st.expander("Preserved stale local rule draft"):
            st.code(json.dumps(st.session_state.get(draft_key, []), ensure_ascii=False, indent=2), language="json")
        if st.button("Reload current consistency-rule source", key=f"{widget_scope}_reload_stale"):
            _advance_studio_draft_generation()
            st.rerun()
        return
    if review_stale_error:
        # A source write can race after the local precheck but before the
        # candidate service reads its review snapshot.  Its physical
        # assertion refused the candidate; present the same deliberate CP3
        # preserved-draft path on this rerun instead of auto-resetting it.
        _clear_studio_review()
        st.error(
            "This consistency-rule draft became stale while its review was being prepared. "
            "The local draft was preserved for reference, but cannot be prepared. "
            "Reload current source and rebuild it."
        )
        with st.expander("Preserved stale local rule draft"):
            st.code(json.dumps(st.session_state.get(draft_key, []), ensure_ascii=False, indent=2), language="json")
        if st.button("Reload current consistency-rule source", key=f"{widget_scope}_reload_review_stale"):
            _advance_studio_draft_generation()
            st.rerun()
        return
    owner, fields, saved_rules = content_studio.consistency_rule_draft_source(selected)
    if draft_key not in st.session_state:
        st.session_state[draft_key] = [
            {"uid": f"saved-{rule['id']}", "field_a_key": rule["field_a_key"],
             "field_a_values": rule["field_a_values"], "field_b_key": rule["field_b_key"],
             "field_b_values": rule["field_b_values"], "message": rule["message"],
             "a_rows": _rule_value_rows(rule["field_a_values"], "a-saved"),
             "b_rows": _rule_value_rows(rule["field_b_values"], "b-saved")}
            for rule in saved_rules
        ]
        st.session_state[baseline_key] = current_baseline
        st.session_state[uid_key] = len(saved_rules)
    draft = st.session_state[draft_key]
    field_by_key = {field["key"]: field for field in fields}
    if _show_studio_review(_studio_signature("consistency", selected, {"draft": draft}), writes_enabled):
        return
    if len(fields) < 2:
        st.warning("This Block needs two active bound Fields before a consistency rule can be authored.")
        return
    if st.button("Add consistency rule", key=f"{widget_scope}_add"):
        uid_number = st.session_state[uid_key]
        st.session_state[uid_key] = uid_number + 1
        draft.append({"uid": f"new-{uid_number}", "field_a_key": fields[0]["key"], "field_a_values": [],
                      "field_b_key": fields[1]["key"], "field_b_values": [], "message": "",
                      "a_rows": [], "b_rows": []})
        _clear_studio_review()
        st.rerun()

    normalized, draft_errors = [], []
    for index, rule in enumerate(draft):
        uid = rule["uid"]
        rule_scope = f"{widget_scope}_rule_{uid}"
        with st.expander(f"{index + 1}. Consistency warning", expanded=True):
            a_col, b_col = st.columns(2)
            with a_col:
                a_key = st.selectbox("Field A", list(field_by_key),
                                     index=list(field_by_key).index(rule["field_a_key"]) if rule["field_a_key"] in field_by_key else 0,
                                     format_func=lambda key: f"{field_by_key[key]['label']} ({key} · {field_by_key[key]['type']})",
                                     key=f"{rule_scope}_field_a", on_change=_clear_studio_review)
                a_source = rule.get("a_rows", rule.get("field_a_values", [])) if field_by_key[a_key]["type"] in {"number", "decimal"} else rule.get("field_a_values", [])
                a_values, a_rows = _rule_values_widget(field_by_key[a_key], a_source, rule_scope, "a")
            with b_col:
                b_key = st.selectbox("Field B", list(field_by_key),
                                     index=list(field_by_key).index(rule["field_b_key"]) if rule["field_b_key"] in field_by_key else min(1, len(field_by_key) - 1),
                                     format_func=lambda key: f"{field_by_key[key]['label']} ({key} · {field_by_key[key]['type']})",
                                     key=f"{rule_scope}_field_b", on_change=_clear_studio_review)
                b_source = rule.get("b_rows", rule.get("field_b_values", [])) if field_by_key[b_key]["type"] in {"number", "decimal"} else rule.get("field_b_values", [])
                b_values, b_rows = _rule_values_widget(field_by_key[b_key], b_source, rule_scope, "b")
            message = st.text_area("Warning message", value=rule.get("message", ""),
                                   key=f"{rule_scope}_message", on_change=_clear_studio_review)
            if a_key == b_key:
                draft_errors.append("Field A and Field B must be distinct.")
                st.error("Field A and Field B must be distinct.")
            if not a_values or not b_values:
                draft_errors.append("Each Field needs at least one triggering value.")
                st.error("Each Field needs at least one triggering value.")
            if not message.strip():
                draft_errors.append("Consistency rule message cannot be blank.")
                st.error("Consistency rule message cannot be blank.")
            if st.button("Delete consistency rule", key=f"{rule_scope}_delete"):
                draft.pop(index)
                _clear_studio_review()
                st.rerun()
            normalized.append({"uid": uid, "field_a_key": a_key, "field_a_values": a_values,
                               "field_b_key": b_key, "field_b_values": b_values, "message": message,
                               "a_rows": a_rows, "b_rows": b_rows})
    st.session_state[draft_key] = normalized
    if st.button("Prepare consistency-rule review", key=f"{widget_scope}_prepare", disabled=not writes_enabled):
        try:
            if draft_errors:
                raise content_studio.StudioIntentError(draft_errors[0])
            rules = [{name: rule[name] for name in ("field_a_key", "field_a_values", "field_b_key", "field_b_values", "message")}
                     for rule in normalized]
            intents = content_studio.consistency_rule_draft_operations(
                selected, rules, baseline=st.session_state[baseline_key],
            )
            signature = _studio_signature("consistency", selected, {"draft": normalized})
            _prepare_studio_review(intents, signature, f"Edit consistency rules for {owner['name']} ({selected})",
                                   evidence_factory=content_studio.consistency_rule_review_evidence(selected),
                                   preserve_stale_draft=True)
        except content_studio.StudioIntentError as error:
            st.error(f"Review was not prepared: {error}")


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
    baseline = _simple_source_baseline("Fields", field["key"], generation)
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
        _prepare_studio_review([content_studio.source_draft_assertion("Fields", field["key"], baseline),
                                content_studio.operation("update", "Fields", field["key"], values)], signature,
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
    baseline = _simple_source_baseline("Snippets", snippet["shortcut"], generation)
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
        _prepare_studio_review([content_studio.source_draft_assertion("Snippets", snippet["shortcut"], baseline),
                                content_studio.operation("update", "Snippets", snippet["shortcut"], values)], signature,
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
        baseline = _current_studio_baseline(
            baseline_key, content_studio.block_draft_baseline(block, existing_bindings)
        )
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
    if preset is not None:
        # Capture once per loaded form generation. Recomputing at submission
        # would bless a second tab's composition update before the stale-draft
        # guard gets a chance to report it.
        source_baseline = _current_studio_baseline(
            source_baseline_key, content_studio.preset_draft_baseline(preset, source_links)
        )
    else:
        source_baseline = None
    endpoint_baseline_key = f"_editor_studio_preset_endpoints_{action}_{stable_key}_{generation}"
    if endpoint_baseline_key not in st.session_state:
        # Only draft-only additions appear here. Existing Preset instances are
        # permanently bound by the source baseline above and must never be
        # silently rebased when another instance is added or removed.
        st.session_state[endpoint_baseline_key] = []
    else:
        # Draft-only added instances are not part of the persisted Preset
        # baseline above, so keep their selected Block/Field endpoints fresh
        # independently.  This is the same physical identity CP1 asserts at
        # Prepare, moved earlier to avoid showing an obsolete draft on return.
        for endpoint in st.session_state[endpoint_baseline_key]:
            if content_studio.preset_instance_endpoint_baseline(
                    endpoint["block_key"], endpoint["instance_no"]
            ) != endpoint["baseline"]:
                _advance_studio_draft_generation()
                st.rerun()

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
        baseline = source_baseline
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
    baseline = (_simple_source_baseline("Conclusion_Group_Labels", {"block_key_set": current["block_key_set"]}, generation)
                if current else None)
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
                intents = [content_studio.source_draft_assertion("Conclusion_Group_Labels", {"block_key_set": current["block_key_set"]}, baseline),
                           content_studio.operation("unlink", "Conclusion_Group_Labels", {"block_key_set": current["block_key_set"]}),
                           content_studio.operation("link", "Conclusion_Group_Labels", {"block_key_set": canonical}, values)]
            else:
                intents = (([content_studio.source_draft_assertion("Conclusion_Group_Labels", {"block_key_set": current["block_key_set"]}, baseline)]
                            if current else []) +
                           [content_studio.operation("update" if current else "link", "Conclusion_Group_Labels", {"block_key_set": canonical}, values)])
            signature = _studio_signature("group-label", selected_key, {"blocks": sorted(block_keys), "label": combined, "existing": selected_key})
            _prepare_studio_review(intents, signature, f"{'Edit' if current else 'Create'} conclusion group label {canonical}")
    if current and st.button("Prepare group-label deletion review", key=f"editor_studio_group_delete_{current['id']}", disabled=not writes_enabled):
        signature = _studio_signature("group-label-delete", current["block_key_set"], {})
        _prepare_studio_review([content_studio.source_draft_assertion("Conclusion_Group_Labels", {"block_key_set": current["block_key_set"]}, baseline),
                                content_studio.operation("unlink", "Conclusion_Group_Labels", {"block_key_set": current["block_key_set"]})], signature,
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
            _clear_ai_review()
            _advance_studio_draft_generation()
            st.session_state["_editor_ai_reset_widgets"] = True
            st.session_state["_editor_message"] = (
                f"Applied as content revision {revision_id}. Review it under Recent revisions."
            )
        st.rerun()


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
                "I confirm this exact reviewed candidate and its local pending-Case impact",
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
                    st.session_state.pop("_editor_inverse_review", None)
                    st.session_state.pop("_editor_inverse_revision", None)
                    _advance_studio_draft_generation()
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

# Must run before any page widget is constructed: on a navigation rerun the
# previous Content Studio widgets are still readable, but will be pruned when
# their branch is skipped.  Restore cached values before a later return builds
# their widgets again.
_stash_studio_widget_state()
_restore_studio_widget_state()

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
    studio_kind = st.radio("Content Studio area", ["Fields", "Blocks", "Presets", "Quick Type", "Consistency rules", "Snippets", "Group labels"], horizontal=True,
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
    elif studio_kind == "Quick Type":
        _quick_type_studio(writes_enabled)
    elif studio_kind == "Consistency rules":
        _consistency_rule_studio(writes_enabled)
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
