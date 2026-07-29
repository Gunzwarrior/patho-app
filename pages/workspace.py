import streamlit as st
import database as db
import rendering
import grouping

CASE_SCOPED_PREFIXES = ("field_", "shared_", "wildcard_")
CASE_SCOPED_EXACT_KEYS = ("_wildcard_preset_id", "final_micro_edit", "final_conc_edit", "master_lock")


def _clear_case_scoped_state():
    """
    Clears everything scoped to "the case currently on screen" and bumps
    the form generation counter (see below). Must be called at the very
    top of a fresh rerun, before any widget below is instantiated —
    Streamlit refuses to clear a widget's session_state value in the same
    run where that widget already rendered.
    """
    st.session_state["_form_generation"] = st.session_state.get("_form_generation", 0) + 1
    for key in list(st.session_state.keys()):
        if key.startswith(CASE_SCOPED_PREFIXES) or key in CASE_SCOPED_EXACT_KEYS:
            del st.session_state[key]


# --- Plain reset: returning to "-- Select --", or a fresh case after save.
if st.session_state.pop("_do_workspace_reset", False):
    _clear_case_scoped_state()

# --- Reopen a saved case: same clearing, then layer the case's saved
# values back on top of the freshly-cleared state, before any widget
# below reads them.
if st.session_state.pop("_do_case_reopen", False):
    case_number_to_load = st.session_state.pop("_reopen_case_number", None)
    case = db.get_case_by_number(case_number_to_load) if case_number_to_load else None

    if not case:
        st.session_state["_reopen_error"] = f"⚠️ No case found with number '{case_number_to_load}'."
    else:
        preset = db.get_preset_by_id(case["preset_id"])
        if not preset:
            st.session_state["_reopen_error"] = (
                f"⚠️ Case '{case_number_to_load}' references a preset that no longer exists."
            )
        else:
            _clear_case_scoped_state()
            gen = st.session_state["_form_generation"]
            st.session_state[f"case_id_{gen}"] = case["case_number"]
            st.session_state[f"clin_info_{gen}"] = case["clinical_info"] or ""

            preset_label = f"{preset['name']} ({preset['short_code']})"
            st.session_state["preset_select"] = preset_label
            st.session_state["_last_selected_label"] = preset_label

            saved_blocks = case["structured_input"].get("blocks", {})
            for block in db.get_preset_blocks(preset["id"]):
                saved_values = saved_blocks.get(block["key"], {})
                for field in block["fields"]:
                    if field["key"] in saved_values:
                        st.session_state[f"field_{block['block_id']}_{field['key']}_{gen}"] = saved_values[field["key"]]

            st.session_state["wildcard_notes"] = case["structured_input"].get("wildcard_notes", [])
            st.session_state["_wildcard_preset_id"] = preset["id"]

            reason_note = f" — {case['pending_reason']}" if case.get("pending_reason") else ""
            status_label = "en attente" if case["status"] == "pending" else "validé"
            st.session_state["_reopen_success"] = (
                f"📂 Case '{case['case_number']}' reopened ({status_label}{reason_note})."
            )

form_gen = st.session_state.get("_form_generation", 0)

# Compact pending-cases panel — lives in the sidebar so it's visible
# alongside the form without navigating away. Clicking an item reuses the
# exact same reopen mechanism as the manual reopen form above (same
# session_state flags, same rerun) — no page switch involved, so this
# sidesteps the cross-page session_state reliability concerns that a
# separate Worklist page's "click to reopen" would need to handle.
with st.sidebar:
    st.markdown("### 📋 Pending Cases")
    pending_cases = db.get_pending_cases()
    if pending_cases:
        with st.container(height=300):
            for pc in pending_cases:
                label = pc["case_number"]
                if pc["pending_reason"]:
                    label += f" — {pc['pending_reason']}"
                if st.button(label, key=f"pending_quicklink_{pc['case_number']}_{form_gen}", use_container_width=True):
                    st.session_state["_reopen_case_number"] = pc["case_number"]
                    st.session_state["_do_case_reopen"] = True
                    st.rerun()
    else:
        st.caption("No pending cases.")

# One-shot messages: shown once here, before anything else renders, then
# cleared. st.toast() does NOT survive a rerun called right after it
# (confirmed open Streamlit issue), so these use session_state instead.
for _msg_key, _renderer in (
    ("_save_confirmation", st.success),
    ("_reopen_success", st.success),
    ("_reopen_error", st.error),
):
    if _msg_key in st.session_state:
        _renderer(st.session_state.pop(_msg_key))

st.title("🔬 Workspace")

c1, c2, c3 = st.columns([1, 2, 2])
with c1: case_id = st.text_input("📁 Case ID", key=f"case_id_{form_gen}")
with c2: clinical_info = st.text_input("🩺 Renseignements cliniques", key=f"clin_info_{form_gen}")
with c3:
    presets = db.get_all_presets()
    preset_labels = ["-- Select --"] + [f"{p['name']} ({p['short_code']})" for p in presets]
    selected_label = st.selectbox("📋 Select Preset", preset_labels, key="preset_select")

# Detect a fresh transition INTO "-- Select --" (not just already sitting
# there — that would rerun forever) and schedule the reset for next run.
if selected_label == "-- Select --":
    if st.session_state.get("_last_selected_label") != "-- Select --":
        st.session_state["_last_selected_label"] = "-- Select --"
        st.session_state["_do_workspace_reset"] = True
        st.rerun()
else:
    st.session_state["_last_selected_label"] = selected_label

# --- Minimal reopen trigger. Temporary — the Worklist page (next slice)
# will replace this with a proper case list; this just proves the
# underlying mechanism works before building that UI around it.
#
# Wrapped in st.form(): a plain text_input doesn't commit its typed value
# to the server until it loses focus (Enter or clicking away) — clicking
# a button right next to it without blurring first can submit against the
# PREVIOUS value. st.form() batches the input and its submit button so the
# value is always current at submit time, regardless of blur timing.
with st.expander("🔓 Reopen a saved case"):
    with st.form(f"reopen_form_{form_gen}", clear_on_submit=True):
        reopen_input = st.text_input("Case number")
        reopen_submitted = st.form_submit_button("Reopen")
        if reopen_submitted:
            if reopen_input.strip():
                st.session_state["_reopen_case_number"] = reopen_input.strip()
                st.session_state["_do_case_reopen"] = True
                st.rerun()
            else:
                st.warning("⚠️ Enter a case number first.")

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
            shared_key = f"shared_{field_key}_{form_gen}"

            def _apply_shared(field_key=field_key, entries=entries, shared_key=shared_key):
                for b, _ in entries:
                    st.session_state[f"field_{b['block_id']}_{field_key}_{form_gen}"] = st.session_state[shared_key]

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
            # Generation-suffixed, same reasoning as case_id/clin_info: these
            # widgets are continuously rendered across a same-preset reset
            # (Save button) and never structurally disappear/remount the way
            # they do when leaving "-- Select --" — so a fixed key relies on
            # the frontend re-syncing a cleared value, which isn't reliable.
            widget_key = f"field_{block['block_id']}_{field['key']}_{form_gen}"
            # Only pass an explicit default on genuine first creation of this
            # key. If reopen already pre-seeded session_state for this key,
            # letting the widget also pass its own value= is what Streamlit
            # flags as ambiguous ("created with a default value but also had
            # its value set via the Session State API") — omitting it here
            # lets the pre-seeded value win cleanly, with no such warning.
            is_fresh = widget_key not in st.session_state

            with col:
                if field["type"] == "number":
                    kwargs = {"min_value": 0, "key": widget_key}
                    if is_fresh:
                        kwargs["value"] = int(field["value"])
                    val = st.number_input(field["label"], **kwargs)
                elif field["type"] == "select":
                    options = field["options"] or []
                    kwargs = {"key": widget_key}
                    if is_fresh:
                        kwargs["index"] = options.index(field["value"]) if field["value"] in options else 0
                    val = st.selectbox(field["label"], options, **kwargs)
                elif field["type"] == "checkbox":
                    kwargs = {"key": widget_key}
                    if is_fresh:
                        kwargs["value"] = str(field["value"]) in ("1", "True", "true")
                    val = st.checkbox(field["label"], **kwargs)
                else:
                    kwargs = {"key": widget_key}
                    if is_fresh:
                        kwargs["value"] = field["value"] or ""
                    val = st.text_input(field["label"], **kwargs)

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
                    "blocks": {
                        block["key"]: {
                            field["key"]: st.session_state[f"field_{block['block_id']}_{field['key']}_{form_gen}"]
                            for field in block["fields"]
                        }
                        for block in blocks
                    },
                    "wildcard_notes": st.session_state.get("wildcard_notes", []),
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