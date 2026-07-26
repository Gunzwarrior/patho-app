import streamlit as st
import database as db
import rendering
import grouping

# --- Reset everything scoped to "the case currently on screen" the moment
# we land back on "-- Select --". This must run at the very top of a fresh
# rerun, before any widget below is instantiated — Streamlit refuses to
# clear a widget's session_state value in the same run where that widget
# already rendered, so the actual clearing is deferred one rerun via this
# flag (set further down, where the "-- Select --" transition is detected).
if st.session_state.pop("_do_workspace_reset", False):
    # case_id/clin_info are unconditionally re-instantiated on every run
    # (unlike Field widgets, which sit inside the preset-selected block and
    # so structurally disappear/reappear on reset). Bumping this counter
    # and folding it into their keys below gives them a genuinely new
    # widget identity after a reset, rather than relying on the frontend
    # re-syncing a cleared value for the same persistent key.
    st.session_state["_form_generation"] = st.session_state.get("_form_generation", 0) + 1

    prefixes = ("field_", "shared_", "wildcard_")
    exact_keys = (
        "_wildcard_preset_id", "final_micro_edit", "final_conc_edit", "master_lock",
    )
    for key in list(st.session_state.keys()):
        if key.startswith(prefixes) or key in exact_keys:
            del st.session_state[key]

form_gen = st.session_state.get("_form_generation", 0)

# A confirmation set right before a reset-triggering rerun (e.g. after a
# successful save) — st.toast() does NOT survive a rerun called right
# after it (confirmed open Streamlit issue), so this is stored in
# session_state instead and shown once, here, before anything else renders.
if "_save_confirmation" in st.session_state:
    st.success(st.session_state.pop("_save_confirmation"))

st.title("🔬 Workspace")

c1, c2, c3 = st.columns([1, 2, 2])
with c1: case_id = st.text_input("📁 Case ID", key=f"case_id_{form_gen}")
with c2: clinical_info = st.text_input("🩺 Renseignements cliniques", key=f"clin_info_{form_gen}")
with c3:
    presets = db.get_all_presets()
    preset_labels = ["-- Select --"] + [f"{p['name']} ({p['short_code']})" for p in presets]
    selected_label = st.selectbox("📋 Select Preset", preset_labels)

# Detect a fresh transition INTO "-- Select --" (not just already sitting
# there — that would rerun forever) and schedule the reset for next run.
if selected_label == "-- Select --":
    if st.session_state.get("_last_selected_label") != "-- Select --":
        st.session_state["_last_selected_label"] = "-- Select --"
        st.session_state["_do_workspace_reset"] = True
        st.rerun()
else:
    st.session_state["_last_selected_label"] = selected_label

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

    # --- Wildcard notes: for unpredictable additions (niveaux, IHC,
    # colorations) that don't belong to any specific Block's own template.
    # One shared panel per case — not one per block — so the tab cost of
    # NOT needing it stays at a single stop, regardless of how many
    # specimens are in the case. Reset if the preset changes, so stale
    # notes don't linger referencing a different case's specimens.
    if st.session_state.get("_wildcard_preset_id") != preset["id"]:
        st.session_state["wildcard_notes"] = []
        st.session_state["_wildcard_preset_id"] = preset["id"]

    with st.expander("➕ Niveaux / IHC / Colorations (cas particuliers)"):
        st.caption("Pour tout ce qui est imprévisible — attache une note à n'importe quel spécimen de ce cas.")

        block_names = [b["name"] for b in blocks]
        wc1, wc2 = st.columns(2)
        with wc1:
            target_name = st.selectbox("Spécimen", block_names, key="wildcard_target")
        with wc2:
            note_type = st.selectbox(
                "Type", ["Niveaux", "Immunohistochimie", "Coloration", "Autre"], key="wildcard_type"
            )

        default_text = (
            "Les niveaux supplémentaires ne mettent pas en évidence de lésion additionnelle."
            if note_type == "Niveaux" else ""
        )
        note_text = st.text_area("Texte (**gras** possible)", value=default_text, key="wildcard_text")

        if st.button("➕ Ajouter", key="wildcard_add"):
            if note_text.strip():
                st.session_state.setdefault("wildcard_notes", []).append({
                    "target_idx": block_names.index(target_name),
                    "target_name": target_name,
                    "note_type": note_type,
                    "text": note_text.strip(),
                })
                st.rerun()
            else:
                st.warning("⚠️ Le texte ne peut pas être vide.")

        notes = st.session_state.get("wildcard_notes", [])
        if notes:
            st.markdown("**Notes ajoutées :**")
            for note_idx, note in enumerate(notes):
                nc1, nc2 = st.columns([6, 1])
                with nc1:
                    st.markdown(f"- **{note['target_name']}** ({note['note_type']}) : {note['text']}")
                with nc2:
                    if st.button("🗑️", key=f"wildcard_del_{note_idx}"):
                        st.session_state["wildcard_notes"].pop(note_idx)
                        st.rerun()

    # Apply wildcard notes to their target block's micro text before
    # formatting — plain continuation text, same as the rest of that
    # block's own body (only the block's header line is forced bold).
    for note in st.session_state.get("wildcard_notes", []):
        idx = note["target_idx"]
        if 0 <= idx < len(micro_blocks):
            name, text = micro_blocks[idx]
            micro_blocks[idx] = (name, text + "\n\n" + note["text"])

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
                    st.session_state["_save_confirmation"] = f"✅ Case '{case_id}' saved — workspace reset for the next case."
                    st.session_state["_do_workspace_reset"] = True
                    st.rerun()
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