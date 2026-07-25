import streamlit as st
import database as db
import rendering
import grouping

st.title("🔬 Workspace")

c1, c2, c3 = st.columns([1, 2, 2])
with c1: case_id = st.text_input("📁 Case ID", key="case_id")
with c2: clinical_info = st.text_input("🩺 Renseignements cliniques", key="clin_info")
with c3:
    presets = db.get_all_presets()
    preset_labels = ["-- Select --"] + [f"{p['name']} ({p['short_code']})" for p in presets]
    selected_label = st.selectbox("📋 Select Preset", preset_labels)

st.markdown("---")

if selected_label != "-- Select --":
    preset = presets[preset_labels.index(selected_label) - 1]
    blocks = db.get_preset_blocks(preset["id"])

    # --- Detect fields shared across 2+ blocks in this preset, with
    # matching current default values (e.g. HP status on Antrum+Fundus).
    # These get a single "global" control as a time-saver; per-block
    # controls stay available underneath for individual overrides.
    shared_candidates = {}
    for block in blocks:
        for field in block["fields"]:
            if field["type"] != "checkbox":
                continue
            shared_candidates.setdefault(field["key"], []).append((block, field))

    shared_fields = {
        key: entries for key, entries in shared_candidates.items()
        if len(entries) >= 2 and len({e[1]["value"] for e in entries}) == 1
    }

    if shared_fields:
        st.subheader("⚙️ Global Modifiers", anchor=False)
        st.caption("Applies to all matching specimens below at once — still editable per specimen.")
        for field_key, entries in shared_fields.items():
            label = entries[0][1]["label"]
            block_names = ", ".join(b["name"] for b, _ in entries)
            default_val = str(entries[0][1]["value"]) in ("1", "True", "true")
            shared_key = f"shared_{field_key}"

            def _apply_shared(field_key=field_key, entries=entries, shared_key=shared_key):
                for b, _ in entries:
                    st.session_state[f"field_{b['block_id']}_{field_key}"] = st.session_state[shared_key]

            st.checkbox(
                f"{label} — {block_names}",
                value=default_val,
                key=shared_key,
                on_change=_apply_shared,
            )
        st.divider()

    st.subheader("1. Medical Variables", anchor=False)
    micro_blocks, conclusion_entries = [], []

    for i, block in enumerate(blocks):
        st.markdown(f"**{i+1}. {block['name']}**")
        cols = st.columns(max(len(block["fields"]), 1))
        overrides = {}

        for col, field in zip(cols, block["fields"]):
            widget_key = f"field_{block['block_id']}_{field['key']}"

            with col:
                if field["type"] == "number":
                    val = st.number_input(
                        field["label"], min_value=0,
                        value=int(field["value"]), key=widget_key,
                    )
                elif field["type"] == "select":
                    options = field["options"] or []
                    idx = options.index(field["value"]) if field["value"] in options else 0
                    val = st.selectbox(field["label"], options, index=idx, key=widget_key)
                elif field["type"] == "checkbox":
                    default_bool = str(field["value"]) in ("1", "True", "true")
                    val = st.checkbox(field["label"], value=default_bool, key=widget_key)
                else:
                    val = st.text_input(field["label"], value=field["value"] or "", key=widget_key)

            overrides[field["key"]] = val

        micro_txt, conc_txt = rendering.render_block(block, overrides)
        micro_blocks.append((block["name"], micro_txt))
        conclusion_entries.append({"block": block, "overrides": overrides, "conc_txt": conc_txt})
        st.divider()

    st.subheader("2. Final Report (Review & Edit)", anchor=False)
    master_lock = st.toggle("🔒 Enable Manual Edit Mode", key="master_lock")

    grouped_conc_text, conflicts = grouping.render_conclusion_plain(conclusion_entries)
    if conflicts:
        st.warning(
            f"⚠️ {', '.join(conflicts)} differs between specimens — not auto-added to the "
            "conclusion. Add a summary line yourself via Manual Edit Mode below."
        )
    raw_compiled_micro = rendering.format_micro_plain(micro_blocks)
    raw_compiled_conc = grouped_conc_text

    if not master_lock:
        st.session_state["final_micro_edit"] = raw_compiled_micro
        st.session_state["final_conc_edit"] = raw_compiled_conc

    final_micro = st.text_area("Microscopy", key="final_micro_edit", height=300, disabled=not master_lock)
    final_conc = st.text_area("Conclusion", key="final_conc_edit", height=150, disabled=not master_lock)

    st.divider()

    # Single code path regardless of Master Lock: final_html is always
    # built from the text areas' current content. When the lock is off
    # that content is auto-synced from the blocks every rerun; when it's
    # on, it's whatever was typed — including any **bold** the blocks
    # already put there, since raw_compiled_micro/conc carried it over.
    final_html = rendering.assemble_report_html(
        case_id, clinical_info, preset["name"],
        rendering.text_to_html(final_micro),
        rendering.text_to_html(final_conc),
    )
    st.markdown(final_html, unsafe_allow_html=True)

    c_save, c_copy = st.columns(2)
    with c_save:
        if st.button("💾 Save Case to Database", use_container_width=True, type="primary"):
            if case_id:
                structured_input = {
                    block["key"]: {
                        field["key"]: st.session_state[f"field_{block['block_id']}_{field['key']}"]
                        for field in block["fields"]
                    }
                    for block in blocks
                }
                if db.save_case(case_id, preset["id"], clinical_info, structured_input, final_html):
                    st.success("✅ Case saved successfully!")
                else:
                    st.error("❌ Error saving case.")
            else:
                st.warning("⚠️ Please enter a Case ID before saving.")

    with c_copy:
        safe_html = final_html.replace("`", "'").replace("\n", "")
        components_html = f"""
        <button onclick="copyRichText()" style="padding: 10px 20px; background-color: #4CAF50; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; width: 100%;">📋 Copy to Diamic</button>
        <script>
        function copyRichText() {{
            const tempDiv = document.createElement("div"); tempDiv.innerHTML = `{safe_html}`;
            tempDiv.style.position = "absolute"; tempDiv.style.left = "-9999px"; document.body.appendChild(tempDiv);
            const range = document.createRange(); range.selectNodeContents(tempDiv);
            const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
            try {{ document.execCommand("copy"); const btn = document.querySelector("button"); btn.innerHTML = "✅ Copied!"; btn.style.backgroundColor = "#2E7D32"; }} catch (err) {{ alert("Error"); }}
            selection.removeAllRanges(); document.body.removeChild(tempDiv);
        }}
        </script>
        """
        st.components.v1.html(components_html, height=40)