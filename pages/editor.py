import hashlib
import json

import streamlit as st

import content_editing
import content_snapshot
import database as db
from editor_preview import render_preset_defaults


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
    st.markdown(preview["html"], unsafe_allow_html=True)
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
    payload = content_snapshot.content_snapshot_json(content_snapshot.export_content_snapshot())
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
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
        if revision["origin"] not in {"manual_edit", "revision_revert"} or not revision["changes"]:
            continue
        identity = revision["summary"] or revision["details"]
        with st.expander(f"Revision {revision['id']}: {identity}"):
            st.caption(revision["details"])
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


if st.session_state.pop("_editor_reset_new_snippet", False):
    st.session_state["_editor_new_snippet_generation"] = (
        st.session_state.get("_editor_new_snippet_generation", 0) + 1
    )

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
    ["Presets", "Blocks", "Fields", "Snippets", "Recent revisions"],
    key="editor_section",
    horizontal=True,
    label_visibility="collapsed",
    width="stretch",
)

if section == "Presets":
    if presets:
        preset_by_id = {row["id"]: row for row in presets}
        preset_id = st.selectbox("Preset", list(preset_by_id), format_func=lambda ident: _preset_label(preset_by_id[ident]), key="editor_preset_select")
        preset = preset_by_id[preset_id]
        st.caption(f"{preset.get('category') or 'Uncategorised'} · shortcut: `{preset['short_code']}`")
        st.info(f"Impact: {db.get_preset_pending_case_count(preset_id)} pending case(s) currently saved with this Preset.")
        if writes_enabled: _edit_preset(preset)
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
        if writes_enabled and not block["is_table"]:
            _edit_block(block)
        elif writes_enabled:
            st.info("Table Blocks remain read-only in Stage 3.")
        st.subheader("Templates", anchor=False); _show_templates(block)
        st.subheader("Fields used", anchor=False); _show_rows(usage["fields"], "This Block has no Fields.")
        st.subheader("Used by Presets", anchor=False); _show_rows(usage["presets"], "No Preset currently uses this Block.")
    else: st.info("No Blocks are configured.")

elif section == "Fields":
    if fields:
        field_by_id = {row["id"]: row for row in fields}
        field_id = st.selectbox("Field", list(field_by_id), format_func=lambda ident: f"{field_by_id[ident]['label']} ({field_by_id[ident]['key']})", key="editor_field_select")
        field = field_by_id[field_id]; usage = db.get_field_usage(field_id)
        st.caption(f"key: `{field['key']}` · type: `{field['type']}`")
        if field.get("options"): st.write(f"Options (read-only): {', '.join(json.loads(field['options']))}")
        st.info(f"Impact: {usage['pending_case_count']} pending case(s) may use this Field.")
        if writes_enabled: _edit_field(field)
        st.subheader("Used by Blocks", anchor=False); _show_rows(usage["blocks"], "No Block currently uses this Field.")
        st.subheader("Reachable from Presets", anchor=False); _show_rows(usage["presets"], "No Preset currently reaches this Field.")
    else: st.info("No Fields are configured.")

elif section == "Snippets":
    if snippets:
        by_shortcut = {row["shortcut"]: row for row in snippets}
        shortcut = st.selectbox("Snippet", list(by_shortcut), format_func=lambda value: f"{value} — {by_shortcut[value].get('category') or 'Uncategorised'}", key="editor_snippet_select")
        snippet = by_shortcut[shortcut]; usage = db.get_snippet_usage(shortcut)
        st.info(f"Impact: {usage['pending_case_count']} pending case(s) may use this Snippet.")
        if writes_enabled: _edit_snippet(snippet)
        st.subheader("Called by Blocks", anchor=False); _show_rows(usage["blocks"], "No Block currently calls this Snippet.")
        st.subheader("Called by Field addenda", anchor=False); _show_rows(usage["fields"], "No Field addendum currently calls this Snippet.")
    else: st.info("No Snippets are configured.")
    if writes_enabled:
        st.subheader("Create new Snippet", anchor=False)
        generation = st.session_state.get("_editor_new_snippet_generation", 0)
        with st.form(f"editor_new_snippet_{generation}"):
            shortcut = st.text_input("Shortcut", key=f"editor_new_snippet_shortcut_{generation}")
            expansion = st.text_area("Expansion", key=f"editor_new_snippet_expansion_{generation}")
            category = st.text_input("Category (optional)", key=f"editor_new_snippet_category_{generation}")
            if st.form_submit_button("Create Snippet"):
                try: result = content_editing.create_snippet(shortcut, expansion, category)
                except content_editing.ContentEditError as error: st.error(f"Not created: {error}")
                else:
                    st.session_state["_editor_message"] = f"Snippet created as content revision {result['revision_id']}."
                    st.session_state["_editor_reset_new_snippet"] = True
                    st.rerun()

elif section == "Recent revisions":
    if writes_enabled: _revisions()
    else: st.info("Revision history and reversion unlock after the initial manual snapshot is recorded.")
