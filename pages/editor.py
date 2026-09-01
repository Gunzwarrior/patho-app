import json

import streamlit as st

import database as db
from editor_preview import render_preset_defaults


def _preset_label(preset):
    return f"{preset['name']} ({preset['short_code']})"


def _show_rows(rows, empty_message):
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption(empty_message)


def _show_block_templates(block):
    templates = {
        "Macro": block.get("macro_template"),
        "Microscopy": block.get("micro_template"),
        "Conclusion": block.get("conclusion_template"),
        "Context": block.get("context_template"),
        "Title fragment": block.get("title_fragment_template"),
        "Conclusion label": block.get("conclusion_label_template"),
    }
    for label, template in templates.items():
        if template:
            with st.expander(label, expanded=label in ("Microscopy", "Conclusion")):
                st.code(template, language="jinja2")


def _show_preset_preview(preset_id):
    preview = render_preset_defaults(preset_id)
    if preview is None:
        st.error("This Preset no longer exists.")
        return
    st.subheader("Default report preview", anchor=False)
    st.caption("Resolved stored defaults, rendered through the same report pipeline as Workspace.")
    if preview["conflicts"]:
        st.warning(
            f"{', '.join(preview['conflicts'])} differs between default Blocks and is not auto-added to the conclusion."
        )
    st.markdown(preview["html"], unsafe_allow_html=True)
    with st.expander("Plain-text rendering", expanded=False):
        st.code(
            f"MICROSCOPY\n{preview['micro_plain']}\n\nCONCLUSION\n{preview['conclusion_plain']}",
            language=None,
        )


st.title("✏️ Editor")
st.caption("Read-only content navigator — editing and imports remain disabled until later stages.")

presets = db.get_all_presets()
blocks = db.get_all_editor_blocks()
fields = db.get_all_fields()
snippets = db.get_all_snippets()

tab_presets, tab_blocks, tab_fields, tab_snippets = st.tabs(["Presets", "Blocks", "Fields", "Snippets"])

with tab_presets:
    if not presets:
        st.info("No Presets are configured.")
    else:
        preset_by_id = {preset["id"]: preset for preset in presets}
        preset_id = st.selectbox(
            "Preset", list(preset_by_id), format_func=lambda item: _preset_label(preset_by_id[item]),
            key="editor_preset_select",
        )
        preset = preset_by_id[preset_id]
        st.caption(f"{preset.get('category') or 'Uncategorised'} · shortcut: `{preset['short_code']}`")
        st.write(f"Default title: {preset.get('default_title') or preset['name']}")
        pending_count = db.get_preset_pending_case_count(preset_id)
        st.info(f"Impact: {pending_count} pending case(s) currently saved with this Preset.")

        st.subheader("Ordered Blocks", anchor=False)
        preset_blocks = db.get_preset_usage(preset_id)
        for position, block in enumerate(preset_blocks, start=1):
            with st.expander(f"{position}. {block['name']} (`{block['key']}`)"):
                st.caption(
                    f"site label: {block.get('site_label') or '—'} · "
                    f"conclusion group: {block.get('conclusion_group') or '—'}"
                )
                _show_rows(block["fields"], "This Block has no Fields.")
        _show_preset_preview(preset_id)

with tab_blocks:
    if not blocks:
        st.info("No Blocks are configured.")
    else:
        block_by_id = {block["id"]: block for block in blocks}
        block_id = st.selectbox(
            "Block", list(block_by_id),
            format_func=lambda item: f"{block_by_id[item]['name']} ({block_by_id[item]['key']})",
            key="editor_block_select",
        )
        block = block_by_id[block_id]
        st.caption(
            f"key: `{block['key']}` · table: {'yes' if block['is_table'] else 'no'} · "
            f"site label: {block.get('site_label') or '—'} · "
            f"conclusion group: {block.get('conclusion_group') or '—'}"
        )
        usage = db.get_block_usage(block_id)
        st.info(f"Impact: {usage['pending_case_count']} pending case(s) may use this Block.")
        st.subheader("Templates", anchor=False)
        _show_block_templates(block)
        st.subheader("Fields used", anchor=False)
        _show_rows(usage["fields"], "This Block has no Fields.")
        st.subheader("Used by Presets", anchor=False)
        _show_rows(usage["presets"], "No Preset currently uses this Block.")

with tab_fields:
    if not fields:
        st.info("No Fields are configured.")
    else:
        field_by_id = {field["id"]: field for field in fields}
        field_id = st.selectbox(
            "Field", list(field_by_id),
            format_func=lambda item: f"{field_by_id[item]['label']} ({field_by_id[item]['key']})",
            key="editor_field_select",
        )
        field = field_by_id[field_id]
        st.caption(f"key: `{field['key']}` · type: `{field['type']}`")
        st.write(f"Default value: `{field.get('default_value')}`")
        if field.get("options"):
            st.write(f"Options: {', '.join(json.loads(field['options']))}")
        if field.get("conclusion_addendum_template"):
            st.code(field["conclusion_addendum_template"], language="jinja2")
        usage = db.get_field_usage(field_id)
        st.info(f"Impact: {usage['pending_case_count']} pending case(s) may use this Field.")
        st.subheader("Used by Blocks", anchor=False)
        _show_rows(usage["blocks"], "No Block currently uses this Field.")
        st.subheader("Reachable from Presets", anchor=False)
        _show_rows(usage["presets"], "No Preset currently reaches this Field.")

with tab_snippets:
    if not snippets:
        st.info("No Snippets are configured.")
    else:
        snippet_by_shortcut = {snippet["shortcut"]: snippet for snippet in snippets}
        shortcut = st.selectbox(
            "Snippet", list(snippet_by_shortcut),
            format_func=lambda item: f"{item} — {snippet_by_shortcut[item].get('category') or 'Uncategorised'}",
            key="editor_snippet_select",
        )
        snippet = snippet_by_shortcut[shortcut]
        st.caption(f"category: {snippet.get('category') or '—'}")
        st.code(snippet["expansion"], language=None)
        usage = db.get_snippet_usage(shortcut)
        st.info(f"Impact: {usage['pending_case_count']} pending case(s) may use this Snippet.")
        st.subheader("Called by Blocks", anchor=False)
        _show_rows(usage["blocks"], "No Block currently calls this Snippet.")
