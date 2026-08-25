import streamlit as st
import database as db
import rendering
import grouping

CASE_SCOPED_PREFIXES = ("field_", "shared_", "wildcard_")
CASE_SCOPED_EXACT_KEYS = ("_wildcard_preset_id", "_loaded_case_number")


def render_field_widget(field, widget_key, disabled):
    """
    Renders the correct Streamlit widget for one resolved field dict (from
    database.get_preset_blocks), keyed for reset-safe persistence. Shared
    between the Clinical Context section (context_section-flagged fields)
    and Medical Variables (everything else) -- same widget-type dispatch
    either way, only the surrounding layout/loop differs.

    Only passes an explicit default value= on genuine first creation of
    widget_key -- if session_state was already pre-seeded (e.g. by a case
    reopen), letting the widget's own value= also fire is what Streamlit
    flags as ambiguous; omitting it here lets the pre-seeded value win
    cleanly, same reasoning as the original inline version of this logic.
    """
    is_fresh = widget_key not in st.session_state
    if field["type"] == "number":
        kwargs = {"min_value": 0, "key": widget_key, "disabled": disabled}
        if is_fresh:
            kwargs["value"] = int(field["value"])
        return st.number_input(field["label"], **kwargs)
    elif field["type"] == "decimal":
        kwargs = {"min_value": 0.0, "step": 0.1, "format": "%.1f", "key": widget_key, "disabled": disabled}
        if is_fresh:
            kwargs["value"] = float(field["value"])
        return st.number_input(field["label"], **kwargs)
    elif field["type"] == "select":
        options = field["options"] or []
        kwargs = {"key": widget_key, "disabled": disabled}
        if is_fresh:
            kwargs["index"] = options.index(field["value"]) if field["value"] in options else 0
        return st.selectbox(field["label"], options, **kwargs)
    elif field["type"] == "checkbox":
        kwargs = {"key": widget_key, "disabled": disabled}
        if is_fresh:
            kwargs["value"] = str(field["value"]) in ("1", "True", "true")
        return st.checkbox(field["label"], **kwargs)
    else:
        kwargs = {"key": widget_key, "disabled": disabled}
        if is_fresh:
            kwargs["value"] = field["value"] or ""
        return st.text_input(field["label"], **kwargs)


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


# --- Cross-page reopen trigger: the Worklist page links here with
# ?reopen=CASE_NUMBER via st.page_link's query_params kwarg — the only
# reliable way to carry data across a Streamlit page navigation (plain
# session_state is confirmed unreliable across st.switch_page in some
# cases; query params survive because they're baked into the destination
# URL itself). Cleared immediately so it can't re-trigger on subsequent
# reruns of this same page load.
if "reopen" in st.query_params:
    st.session_state["_reopen_case_number"] = st.query_params["reopen"]
    st.session_state["_do_case_reopen"] = True
    del st.query_params["reopen"]

# --- Plain reset: returning to "-- Select --", or a fresh case after save.
if st.session_state.pop("_do_workspace_reset", False):
    _clear_case_scoped_state()

# --- Preset-switch reset: same Block-scoped clearing as a full reset, but
# Case ID / Renseignements cliniques are entered *before* the preset in
# the tab flow (Case ID -> Renseignements cliniques -> Preset) and aren't
# specific to any one preset's blocks — switching presets mid-entry
# shouldn't wipe them out from under the person. Captured under the OLD
# generation's keys before clearing, then re-seeded under the NEW
# generation's keys afterward, same pattern as case-reopen restoration.
if st.session_state.pop("_do_preset_switch_reset", False):
    _old_gen = st.session_state.get("_form_generation", 0)
    _preserved_case_id = st.session_state.get(f"case_id_{_old_gen}", "")
    _clear_case_scoped_state()
    _new_gen = st.session_state["_form_generation"]
    st.session_state[f"case_id_{_new_gen}"] = _preserved_case_id
    # Clinical info is deliberately NOT preserved across a preset switch
    # (reversed from the original fix this pattern was built for) — it
    # may now be auto-composed from fields specific to the OLD preset
    # (e.g. Thyroid's nodule site/size/EUTIRADS), which carry no meaning
    # for an unrelated new preset. See PROGRESS.md.


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
            st.session_state["_loaded_case_number"] = case["case_number"]

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

            # Restore Master Lock only if it was actually on when saved — if
            # it was off, the auto-render path already fully reconstructs
            # the report from blocks/notes alone, so there's nothing extra
            # to restore and the text areas auto-sync normally as usual.
            if case["structured_input"].get("master_lock"):
                st.session_state[f"master_lock_{gen}"] = True
                st.session_state[f"final_micro_edit_{gen}"] = case["structured_input"].get("final_micro_edit", "")
                st.session_state[f"final_conc_edit_{gen}"] = case["structured_input"].get("final_conc_edit", "")

            # Same restore-only-if-manual pattern for Context/Title. When it
            # was auto (off), the field values restored above already let
            # the auto-compose logic below reconstruct the correct
            # context/title on its own — clin_info_{gen} itself is already
            # restored unconditionally a few lines up.
            if case["structured_input"].get("context_title_lock"):
                st.session_state[f"context_title_lock_{gen}"] = True
                st.session_state[f"final_title_edit_{gen}"] = case["structured_input"].get("final_title_edit", "")

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

c1, c2 = st.columns([1, 2])
with c1: case_id = st.text_input("📁 Case ID", key=f"case_id_{form_gen}")
with c2:
    presets = db.get_all_presets()
    preset_labels = ["-- Select --"] + [f"{p['name']} ({p['short_code']})" for p in presets]
    selected_label = st.selectbox("📋 Select Preset", preset_labels, key="preset_select")
# Renseignements cliniques and Title moved below, into the Clinical
# Context section — both now depend on which preset (and which fields)
# are selected, so they can no longer render before that choice is made.

# Duplicate-case guard: warn the moment an existing case number is typed,
# not just at save time — catches a typo/collision before any time is
# spent writing a report, rather than after. A match is only a real
# conflict if this case wasn't the one legitimately loaded via reopen —
# resaving the case you just reopened is supposed to overwrite it.
existing_case = db.get_case_by_number(case_id) if case_id.strip() else None
is_legit_resave = existing_case and case_id == st.session_state.get("_loaded_case_number")
duplicate_conflict = existing_case is not None and not is_legit_resave

overwrite_confirmed = True
if duplicate_conflict:
    reason_note = f", {existing_case['pending_reason']}" if existing_case.get("pending_reason") else ""
    last_touched = existing_case.get("updated_at") or existing_case.get("created_at")
    st.warning(
        f"⚠️ Case '{case_id}' already exists ({existing_case['status']}{reason_note}, "
        f"last touched {last_touched}). Saving now will **overwrite it**. "
        "To edit the existing case instead, reopen it via the sidebar or the reopen box below."
    )
    overwrite_confirmed = st.checkbox(
        "I understand — overwrite the existing case anyway", key=f"overwrite_confirm_{form_gen}"
    )

# Detect ANY change in preset selection (not just transitions to/from
# "-- Select --") and schedule a reset for next run. This matters even
# between two real presets: if they share the same underlying Block (e.g.
# etc0 -> etc5, both pointing at the Thyroid Cytology block), the field
# widget keys don't change either — without this, the stale value from
# the previous preset would silently persist.
_previous_label = st.session_state.get("_last_selected_label")
if selected_label != _previous_label:
    st.session_state["_last_selected_label"] = selected_label
    if _previous_label is not None:  # skip the harmless no-op reset on cold start
        st.session_state["_do_preset_switch_reset"] = True
        st.rerun()

# --- Minimal reopen trigger. Temporary — the Worklist page now covers the
# browsable case, but this stays as a fast direct-entry alternative.
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

    # Peeked early: the actual master_lock toggle widget is declared later
    # (in "2. Final Report"), but field edits made while it's on have no
    # effect on the final report until it's turned off — so fields get
    # visually disabled here, before that widget itself even runs this
    # pass. Reading the same session_state key it uses is safe since the
    # value from the previous interaction is already there by rerun time.
    master_lock_active = st.session_state.get(f"master_lock_{form_gen}", False)
    total_specimens = len(blocks)
    has_context_composition = any(block.get("context_template") for block in blocks)

    # --- Clinical Context + Title. Fields flagged context_section render
    # here (e.g. Thyroid Cytology's site/size/EUTIRADS), ahead of Medical
    # Variables further down, matching the intended tab flow. A preset
    # whose blocks set none of these (Gastric Trio, Gallbladder, Appendix)
    # simply has nothing render in the fields loop below — Renseignements
    # cliniques then behaves exactly as it always has, a plain free-typed
    # box, since disabled_context is unconditionally False in that case.
    st.subheader("📝 Contexte clinique", anchor=False)

    block_ctx_overrides = {}
    for block in blocks:
        context_fields = [f for f in block["fields"] if f.get("context_section")]
        if not context_fields:
            continue
        ctx_cols = st.columns(len(context_fields))
        overrides = {}
        for col, field in zip(ctx_cols, context_fields):
            widget_key = f"field_{block['block_id']}_{field['key']}_{form_gen}"
            with col:
                overrides[field["key"]] = render_field_widget(field, widget_key, master_lock_active)
        block_ctx_overrides[block["block_id"]] = overrides

    context_title_lock = st.toggle(
        "🔒 Modifier le contexte et le titre manuellement", key=f"context_title_lock_{form_gen}"
    )
    # disabled == "currently auto-synced, don't let the user fight it."
    # Title always has a sensible auto-value (falls back to just
    # default_title with no fragment when there's nothing to compose), so
    # it's always sync-eligible while unlocked. Context is only
    # auto-synced for the single-specimen case where a block actually
    # composes something — with 2+ specimens the composed text goes to
    # each specimen's own header instead (a later checkpoint), so the top
    # box here stays free text unconditionally, matching a real
    # multi-specimen sample (CR_Sample.docx) where it's just "goitre."
    disabled_title = not context_title_lock
    disabled_context = has_context_composition and total_specimens == 1 and not context_title_lock

    if disabled_title:
        auto_title = preset.get("default_title") or preset["name"]
        if total_specimens == 1:
            _, only_title_txt = rendering.render_context_fragments(
                blocks[0], block_ctx_overrides.get(blocks[0]["block_id"], {})
            )
            if only_title_txt:
                auto_title = f"{auto_title} {only_title_txt}"
        st.session_state[f"final_title_edit_{form_gen}"] = auto_title

    if disabled_context:
        auto_context, _ = rendering.render_context_fragments(
            blocks[0], block_ctx_overrides.get(blocks[0]["block_id"], {})
        )
        st.session_state[f"clin_info_{form_gen}"] = auto_context

    ctx_c1, ctx_c2 = st.columns([2, 1])
    with ctx_c1:
        clinical_info = st.text_input(
            "🩺 Renseignements cliniques", key=f"clin_info_{form_gen}", disabled=disabled_context
        )
    with ctx_c2:
        title = st.text_input(
            "📄 Titre", key=f"final_title_edit_{form_gen}", disabled=disabled_title
        )

    st.divider()

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
                disabled=master_lock_active,
            )
        st.divider()

    st.subheader("1. Medical Variables", anchor=False)
    micro_blocks, conclusion_entries = [], []

    for i, block in enumerate(blocks):
        st.markdown(f"**{i+1}. {block['name']}**")
        # context_section fields already rendered above, in Clinical
        # Context — showing them again here would be both redundant and a
        # second, conflicting widget instance for the same widget_key.
        medical_fields = [f for f in block["fields"] if not f.get("context_section")]
        cols = st.columns(max(len(medical_fields), 1))
        # Start from whatever was entered in Clinical Context above, so a
        # future conclusion_template/micro_template referencing e.g.
        # {{nodule_site}} renders the value actually entered — not that
        # field's resolved default — even though this loop never displays
        # that widget itself.
        overrides = dict(block_ctx_overrides.get(block["block_id"], {}))

        for col, field in zip(cols, medical_fields):
            # Generation-suffixed, same reasoning as case_id/clin_info: these
            # widgets are continuously rendered across a same-preset reset
            # (Save button) and never structurally disappear/remount the way
            # they do when leaving "-- Select --" — so a fixed key relies on
            # the frontend re-syncing a cleared value, which isn't reliable.
            widget_key = f"field_{block['block_id']}_{field['key']}_{form_gen}"
            with col:
                overrides[field["key"]] = render_field_widget(field, widget_key, master_lock_active)

        micro_txt, conc_txt = rendering.render_block(block, overrides, total_specimens=len(blocks))
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
            target_name = st.selectbox("Spécimen", block_names, key="wildcard_target", disabled=master_lock_active)
        with wc2:
            note_type = st.selectbox(
                "Type", ["Niveaux", "Immunohistochimie", "Coloration", "Autre"],
                key="wildcard_type", disabled=master_lock_active,
            )

        default_text = (
            "Les niveaux supplémentaires ne mettent pas en évidence de lésion additionnelle."
            if note_type == "Niveaux" else ""
        )
        note_text = st.text_area(
            "Texte (**gras** possible)", value=default_text, key="wildcard_text", disabled=master_lock_active
        )

        if st.button("➕ Ajouter", key="wildcard_add", disabled=master_lock_active):
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
                    if st.button("🗑️", key=f"wildcard_del_{note_idx}", disabled=master_lock_active):
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
    master_lock = st.toggle("🔒 Enable Manual Edit Mode", key=f"master_lock_{form_gen}")

    grouped_conc_text, conflicts = grouping.render_conclusion_plain(conclusion_entries)
    if conflicts:
        st.warning(
            f"⚠️ {', '.join(conflicts)} differs between specimens — not auto-added to the "
            "conclusion. Add a summary line yourself via Manual Edit Mode below."
        )
    raw_compiled_micro = rendering.format_micro_plain(micro_blocks)
    raw_compiled_conc = grouped_conc_text

    if not master_lock:
        st.session_state[f"final_micro_edit_{form_gen}"] = raw_compiled_micro
        st.session_state[f"final_conc_edit_{form_gen}"] = raw_compiled_conc

    final_micro = st.text_area("Microscopy", key=f"final_micro_edit_{form_gen}", height=300, disabled=not master_lock)
    final_conc = st.text_area("Conclusion", key=f"final_conc_edit_{form_gen}", height=150, disabled=not master_lock)

    st.divider()

    # Single code path regardless of Master Lock: final_html is always
    # built from the text areas' current content. When the lock is off
    # that content is auto-synced from the blocks every rerun; when it's
    # on, it's whatever was typed — including any **bold** the blocks
    # already put there, since raw_compiled_micro/conc carried it over.
    final_html = rendering.assemble_report_html(
        clinical_info, title,
        rendering.text_to_html(final_micro),
        rendering.text_to_html(final_conc),
    )
    st.markdown(final_html, unsafe_allow_html=True)

    st.divider()

    reason_options = ["IHC", "Niveaux", "Avis", "Colo", "Autre"]
    pending_reason_choice = st.selectbox(
        "Raison (si sauvegardé en attente)", reason_options, key=f"pending_reason_select_{form_gen}"
    )
    pending_reason_value = pending_reason_choice
    if pending_reason_choice == "Autre":
        custom_reason = st.text_input(
            "Préciser", key=f"pending_reason_custom_{form_gen}", placeholder="Préciser la raison"
        )
        pending_reason_value = custom_reason.strip() or "Autre"

    st.caption(
        "Pour revenir sur une validation par erreur : rouvrez le cas (barre latérale ou "
        "ci-dessus) puis cliquez « Save as Pending »."
    )

    structured_input = {
        "blocks": {
            block["key"]: {
                field["key"]: st.session_state[f"field_{block['block_id']}_{field['key']}_{form_gen}"]
                for field in block["fields"]
            }
            for block in blocks
        },
        "wildcard_notes": st.session_state.get("wildcard_notes", []),
        "master_lock": master_lock,
        # Always saved, regardless of master_lock — final_micro/final_conc
        # already hold the correct final text either way (that's how
        # final_html gets built unconditionally). Kept as a fallback
        # historical record: if a Block's template ever changes later,
        # structured_input's field values alone might not reconstruct the
        # exact original text on reopen, but this always will.
        "final_micro_edit": final_micro,
        "final_conc_edit": final_conc,
        # Same reasoning, same pattern, for Context/Title.
        "context_title_lock": context_title_lock,
        "final_title_edit": title,
    }

    c_pending, c_validated, c_copy = st.columns(3)

    with c_pending:
        if st.button("💾 Save as Pending", use_container_width=True, disabled=not overwrite_confirmed):
            if case_id:
                if db.save_case(case_id, preset["id"], clinical_info, structured_input, final_html,
                                 status="pending", pending_reason=pending_reason_value):
                    st.session_state["_save_confirmation"] = (
                        f"✅ Case '{case_id}' saved as pending ({pending_reason_value}) — "
                        "workspace reset for the next case."
                    )
                    st.session_state["_do_workspace_reset"] = True
                    st.rerun()
                else:
                    st.error("❌ Error saving case.")
            else:
                st.warning("⚠️ Please enter a Case ID before saving.")

    with c_validated:
        if st.button("✅ Save as Validated", use_container_width=True, type="primary", disabled=not overwrite_confirmed):
            if case_id:
                if db.save_case(case_id, preset["id"], clinical_info, structured_input, final_html,
                                 status="validated", pending_reason=None):
                    st.session_state["_save_confirmation"] = (
                        f"✅ Case '{case_id}' saved as validated — workspace reset for the next case."
                    )
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