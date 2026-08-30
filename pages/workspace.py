import streamlit as st
import database as db
import rendering
import grouping
import quicktype
import consistency
import composition

CASE_SCOPED_PREFIXES = ("field_", "shared_", "wildcard_")
CASE_SCOPED_EXACT_KEYS = (
    "_wildcard_preset_id", "_loaded_case_number", "_case_block_instances",
    "_composition_field_restore",
)


def _preserve_fields_for_composition(blocks, form_generation):
    """Carry live field values through a composition-triggered rerun.

    Composition controls render above the fields. Their explicit rerun can
    otherwise happen before Streamlit re-instantiates those widgets, which
    leaves the browser showing an old value while server-side rendering falls
    back to the Field default. This is deliberately not a generation bump:
    instance_no keeps the widget identity stable through a reorder.
    """
    st.session_state["_composition_field_restore"] = {
        f"field_{block['block_id']}_{block['instance_no']}_{field['key']}_{form_generation}":
        st.session_state[f"field_{block['block_id']}_{block['instance_no']}_{field['key']}_{form_generation}"]
        for block in blocks
        for field in block["fields"]
        if f"field_{block['block_id']}_{block['instance_no']}_{field['key']}_{form_generation}" in st.session_state
    }


def resolve_case_blocks(preset_blocks, block_instances):
    """Attach immutable instance identity and saved display order to Blocks.

    Stage 0 only accepts the instances derived from a Preset: therefore an
    instance maps to its preset row by ``(block_id, instance_no)`` while the
    two values are still initially equal to ``sort_order``. Keeping the
    current position in ``block_instances`` separate is the plumbing that
    lets a later reorder change report order without remounting widgets.
    """
    defaults = {
        (block["block_id"], block["sort_order"]): block
        for block in preset_blocks
    }
    resolved_blocks = []
    for instance in block_instances:
        block = defaults.get((instance["block_id"], instance["instance_no"]))
        if not block:
            block = db.get_block_by_id(instance["block_id"])
        if not block:
            raise ValueError(
                f"Block instance {instance['block_id']}#{instance['instance_no']} "
                "is not available in this preset."
            )
        resolved_blocks.append({**block, "instance_no": instance["instance_no"]})
    return resolved_blocks


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
        # Plain text_input, manually parsed — not st.number_input.
        # number_input has no reliable way to represent "cleared back to
        # blank" once a field has held a real value: confirmed against an
        # actual widget interaction (type a value, clear it, submit), not
        # just from documentation, that it reverts to a floor value
        # instead of staying None — and this held true whether or not
        # min_value was set. A text box has no such ambiguity: "" is
        # unambiguously blank, nothing widget-level to fight. Trade-off,
        # accepted by the person: loses the +/- stepper buttons.
        kwargs = {"key": widget_key, "disabled": disabled, "placeholder": "ex: 20"}
        if is_fresh:
            kwargs["value"] = field["value"] or ""
        raw = st.text_input(field["label"], **kwargs)
        stripped = raw.strip().replace(",", ".")  # tolerate "20,5" as well as "20.5"
        if not stripped:
            return None
        try:
            parsed = float(stripped)
        except ValueError:
            parsed = None
        if parsed is not None and parsed >= 0:
            return parsed
        # Invalid text, or a negative number (obviously wrong for a
        # physical size/volume — this is the guard min_value used to
        # provide, restored here since it's otherwise gone along with
        # number_input). Visible, not silent: shown right under the
        # field, and treated as blank rather than passed through as a
        # garbled string that could otherwise leak into the composed
        # report text unnoticed.
        if raw.strip():
            st.caption(f"⚠️ « {raw} » non reconnu pour « {field['label']} » — traité comme vide.")
        return None
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


def _handle_quick_type_submit():
    """
    on_change callback for the Quick Type text_input further down —
    fires the instant its value commits (Enter or blur), which happens
    BEFORE the script's main body reruns top-to-bottom. So
    _do_quick_type_apply is already set by the time the top-of-script
    processing block (mirroring case-reopen's own two-phase shape) picks
    it up in that SAME rerun — no st.rerun() call needed here, unlike the
    reopen form's submit handler: Streamlit already reruns the script for
    a widget value change on its own, a callback doesn't need to
    additionally request one.

    Deliberately NOT wrapped in st.form(), unlike the reopen box below —
    st.form there exists specifically to fix a timing race between a
    text_input's OWN blur-commit and a SEPARATE submit button's click
    landing before that commit is visible. There's no separate button
    here to race against: this callback IS the field's own commit event.

    Reads its own generation number fresh from session_state rather than
    closing over a form_gen local — this callback runs as its own
    invocation, before this rerun's normal top-to-bottom script execution
    (and its local variables) exist at all. Safe to do: nothing bumps
    _form_generation between the widget being drawn and this callback
    firing, so the value read here is guaranteed to match the key the
    widget was actually rendered with.
    """
    gen = st.session_state.get("_form_generation", 0)
    raw = st.session_state.get(f"qt_input_{gen}", "").strip()
    if not raw:
        return
    qt_preset, qt_overrides, qt_error = quicktype.parse_quick_type(raw)
    if qt_error:
        # Left as-is in the input box on failure — unlike a successful
        # apply, which naturally clears it via the generation bump below
        # — so a typo can be fixed in place instead of retyped from
        # scratch.
        st.session_state["_quicktype_error"] = f"⚠️ {qt_error}"
    else:
        st.session_state["_pending_quicktype_preset_id"] = qt_preset["id"]
        st.session_state["_pending_quicktype_overrides"] = qt_overrides
        st.session_state["_do_quick_type_apply"] = True


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

            preset_blocks = db.get_preset_blocks(preset["id"])
            block_instances = case["structured_input"].get(
                "block_instances", composition.derive_block_instances(preset_blocks)
            )
            try:
                case_blocks = resolve_case_blocks(preset_blocks, block_instances)
            except (KeyError, TypeError, ValueError) as error:
                st.session_state["_reopen_error"] = (
                    f"⚠️ Case '{case_number_to_load}' has an unavailable block instance: {error}"
                )
                case_blocks = []
            else:
                st.session_state["_case_block_instances"] = block_instances

            saved_blocks = case["structured_input"].get("blocks", {})
            for block in case_blocks:
                # Keyed by (Block key, instance_no), not just Block key — a
                # Preset can reuse the same Block twice (e.g. two Thyroid
                # Cytology nodules), and bare block["key"] would collide,
                # silently restoring the same values to both instances.
                saved_values = saved_blocks.get(f"{block['key']}#{block['instance_no']}", {})
                for field in block["fields"]:
                    if field["key"] in saved_values:
                        st.session_state[f"field_{block['block_id']}_{block['instance_no']}_{field['key']}_{gen}"] = saved_values[field["key"]]

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

# --- Quick Type apply: same two-phase pattern as case reopen just above
# (a flag + payload set by the Quick Type form's submit handler further
# down, actually applied here on the NEXT rerun, before any widget below
# reads the state it seeds). Preserves case_id and skips clinical_info
# exactly like a manual preset switch, since a Quick Type code IS a
# preset selection (plus field pre-fills) -- same reasoning
# _do_preset_switch_reset already uses, not a new mechanism. Deliberately
# its own flag rather than reusing _do_preset_switch_reset -- a different
# one-shot purpose, and reusing an existing reset flag for a second
# purpose is exactly the _form_generation-scoped-key bug pattern already
# hit before.
if st.session_state.pop("_do_quick_type_apply", False):
    _qt_preset_id = st.session_state.pop("_pending_quicktype_preset_id", None)
    _qt_overrides = st.session_state.pop("_pending_quicktype_overrides", {})
    _qt_old_gen = st.session_state.get("_form_generation", 0)
    _qt_preserved_case_id = st.session_state.get(f"case_id_{_qt_old_gen}", "")
    _clear_case_scoped_state()
    _qt_new_gen = st.session_state["_form_generation"]
    st.session_state[f"case_id_{_qt_new_gen}"] = _qt_preserved_case_id

    _qt_preset = db.get_preset_by_id(_qt_preset_id) if _qt_preset_id else None
    if not _qt_preset:
        st.session_state["_quicktype_error"] = "⚠️ Quick Type code resolved to a preset that no longer exists."
    else:
        _qt_preset_label = f"{_qt_preset['name']} ({_qt_preset['short_code']})"
        st.session_state["preset_select"] = _qt_preset_label
        # Set alongside preset_select, same as the case-reopen block above --
        # prevents the preset-switch-change watcher further down from seeing
        # this as a fresh change and scheduling a second, unwanted reset.
        st.session_state["_last_selected_label"] = _qt_preset_label

        _qt_summary_parts = [_qt_preset["short_code"]]
        _qt_preset_blocks = db.get_preset_blocks(_qt_preset["id"])
        _qt_blocks = resolve_case_blocks(
            _qt_preset_blocks, composition.derive_block_instances(_qt_preset_blocks)
        )
        for _qt_block in _qt_blocks:
            _qt_block_overrides = _qt_overrides.get(_qt_block["sort_order"], {})
            for _qt_field in _qt_block["fields"]:
                if _qt_field["key"] in _qt_block_overrides:
                    _qt_raw_value = _qt_block_overrides[_qt_field["key"]]
                    st.session_state[
                        f"field_{_qt_block['block_id']}_{_qt_block['instance_no']}_{_qt_field['key']}_{_qt_new_gen}"
                    ] = _qt_raw_value
                    _qt_summary_parts.append(f"{_qt_field['label']}={_qt_raw_value}")
        st.session_state["_quicktype_success"] = "✅ " + " ; ".join(_qt_summary_parts)

# Must run before any field widget below is instantiated. See
# _preserve_fields_for_composition() for why composition's explicit rerun
# needs this one-shot restoration even though widget identities are stable.
for _field_key, _field_value in st.session_state.pop("_composition_field_restore", {}).items():
    st.session_state[_field_key] = _field_value

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
    ("_quicktype_success", st.success),
    ("_quicktype_error", st.error),
):
    if _msg_key in st.session_state:
        _renderer(st.session_state.pop(_msg_key))

st.title("🔬 Workspace")

c1, c2, c3 = st.columns([1, 2, 1])
with c1: case_id = st.text_input("📁 Case ID", key=f"case_id_{form_gen}")
with c2:
    presets = db.get_all_presets()
    preset_labels = ["-- Select --"] + [f"{p['name']} ({p['short_code']})" for p in presets]
    selected_label = st.selectbox("📋 Select Preset", preset_labels, key="preset_select")
with c3:
    st.text_input(
        "⚡ Quick Type",
        placeholder="ex: dai37",
        key=f"qt_input_{form_gen}",
        on_change=_handle_quick_type_submit,
    )
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
    preset_blocks = db.get_preset_blocks(preset["id"])
    if "_case_block_instances" not in st.session_state:
        st.session_state["_case_block_instances"] = composition.derive_block_instances(preset_blocks)
    block_instances = st.session_state["_case_block_instances"]
    blocks = resolve_case_blocks(preset_blocks, block_instances)

    with st.expander("🧩 Compose specimens"):
        st.caption("Réordonnez les spécimens ou retirez ceux qui ne font pas partie de ce cas.")
        for index, block in enumerate(blocks):
            name_col, up_col, down_col, remove_col = st.columns([6, 1, 1, 2])
            with name_col:
                st.markdown(f"{index + 1}. {block['name']}")
            with up_col:
                move_up = st.button("▲", key=f"compose_up_{index}", disabled=index == 0)
            with down_col:
                move_down = st.button("▼", key=f"compose_down_{index}", disabled=index == len(blocks) - 1)
            with remove_col:
                remove = st.button("Retirer", key=f"compose_remove_{index}", disabled=len(blocks) == 1)

            if move_up or move_down:
                _preserve_fields_for_composition(blocks, form_gen)
                neighbor = index - 1 if move_up else index + 1
                for note in st.session_state.get("wildcard_notes", []):
                    if note.get("target_idx") == index:
                        note["target_idx"] = neighbor
                    elif note.get("target_idx") == neighbor:
                        note["target_idx"] = index
                st.session_state["_case_block_instances"] = composition.move_instance(
                    block_instances, index, -1 if move_up else 1
                )
                st.rerun()

            if remove:
                _preserve_fields_for_composition(blocks, form_gen)
                removed = block_instances[index]
                prefix = f"field_{removed['block_id']}_{removed['instance_no']}_"
                for key in list(st.session_state):
                    if key.startswith(prefix):
                        del st.session_state[key]
                st.session_state["wildcard_notes"] = [
                    {**note, "target_idx": note["target_idx"] - (note["target_idx"] > index)}
                    for note in st.session_state.get("wildcard_notes", [])
                    if note.get("target_idx") != index
                ]
                st.session_state["_case_block_instances"] = composition.remove_instance(block_instances, index)
                st.rerun()

        add_options = db.get_all_blocks()
        add_names = {block["id"]: block["name"] for block in add_options}
        selected_addition_id = st.selectbox(
            "Ajouter un spécimen", list(add_names), format_func=add_names.get, key="compose_add_block"
        )
        if st.button("Ajouter", key="compose_add"):
            _preserve_fields_for_composition(blocks, form_gen)
            st.session_state["_case_block_instances"] = composition.add_instance(
                block_instances, selected_addition_id
            )
            st.rerun()

    # Peeked early: the actual master_lock toggle widget is declared later
    # (in "2. Final Report"), but field edits made while it's on have no
    # effect on the final report until it's turned off — so fields get
    # visually disabled here, before that widget itself even runs this
    # pass. Reading the same session_state key it uses is safe since the
    # value from the previous interaction is already there by rerun time.
    master_lock_active = st.session_state.get(f"master_lock_{form_gen}", False)
    total_specimens = len(block_instances)
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
            # block_id alone isn't a unique instance identifier — a Preset
            # can reuse the same Block twice (e.g. two Thyroid Cytology
            # nodules in one case), so immutable instance_no is folded in
            # too. Display order lives only in block_instances and must
            # never be part of a widget key.
            widget_key = f"field_{block['block_id']}_{block['instance_no']}_{field['key']}_{form_gen}"
            with col:
                overrides[field["key"]] = render_field_widget(field, widget_key, master_lock_active)
        block_ctx_overrides[(block["block_id"], block["instance_no"])] = overrides

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
            _, only_title_txt, _ = rendering.render_context_fragments(
                blocks[0], block_ctx_overrides.get((blocks[0]["block_id"], blocks[0]["instance_no"]), {})
            )
            if only_title_txt:
                auto_title = f"{auto_title} {only_title_txt}"
        st.session_state[f"final_title_edit_{form_gen}"] = auto_title

    if disabled_context:
        auto_context, _, _ = rendering.render_context_fragments(
            blocks[0], block_ctx_overrides.get((blocks[0]["block_id"], blocks[0]["instance_no"]), {})
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
                    st.session_state[
                        f"field_{b['block_id']}_{b['instance_no']}_{field_key}_{form_gen}"
                    ] = st.session_state[shared_key]

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
    all_consistency_warnings = []

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
        overrides = dict(block_ctx_overrides.get((block["block_id"], block["instance_no"]), {}))

        for col, field in zip(cols, medical_fields):
            # Generation-suffixed, same reasoning as case_id/clin_info: these
            # widgets are continuously rendered across a same-preset reset
            # (Save button) and never structurally disappear/remount the way
            # they do when leaving "-- Select --" — so a fixed key relies on
            # the frontend re-syncing a cleared value, which isn't reliable.
            widget_key = f"field_{block['block_id']}_{block['instance_no']}_{field['key']}_{form_gen}"
            with col:
                overrides[field["key"]] = render_field_widget(field, widget_key, master_lock_active)

        # Checked here, once this block's overrides are fully resolved --
        # identical regardless of whether each value came from this loop's
        # own widgets, a Preset default, or a Quick Type code, since all
        # three are already folded into `overrides` by this point. See
        # consistency.py / PROGRESS.md for the full design reasoning.
        all_consistency_warnings.extend(consistency.check_block(block, overrides))

        micro_txt, conc_txt = rendering.render_block(block, overrides, total_specimens=total_specimens)
        # With 2+ specimens, a block's own composed context (if it set
        # context_template) becomes its numbered specimen header instead
        # of the plain Block name — e.g. "1. Nodule lobaire gauche de
        # 20 mm EUTIRADS 4" instead of "1. Cytologie thyroïdienne".
        # Falls back to block["name"] when there's nothing composed,
        # exactly today's behavior. With exactly 1 specimen,
        # format_micro_plain suppresses the header entirely regardless of
        # what's passed here, so no extra branch on total_specimens is
        # needed — confirmed against CR_Sample.docx's single-nodule case,
        # which has no header at all despite context_template being set.
        header_context_txt, _, _ = rendering.render_context_fragments(block, overrides)
        micro_blocks.append((header_context_txt or block["name"], micro_txt))
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
        # Ordered display composition for this Case. Stage 0 always seeds
        # it from the Preset, but later stages can reorder this list without
        # changing the immutable instance_no used by widgets/saved values.
        "block_instances": block_instances,
        "blocks": {
            # Keyed by (Block key, instance_no) — see the matching comment
            # in the reopen-restoration loop above for why bare
            # block["key"] silently loses data when a Preset reuses the
            # same Block twice.
            f"{block['key']}#{block['instance_no']}": {
                field["key"]: st.session_state[f"field_{block['block_id']}_{block['instance_no']}_{field['key']}_{form_gen}"]
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

    # Field-consistency warning: consolidated across every block in the
    # case (not just one), shown once each even if the identical message
    # fired from more than one block instance. Warn-and-confirm, same
    # pattern as the duplicate-case-number guard above — never a hard
    # block, since a rare-but-real presentation shouldn't be something
    # this tool refuses to let him document accurately.
    consistency_confirmed = True
    unique_consistency_warnings = list(dict.fromkeys(all_consistency_warnings))
    if unique_consistency_warnings:
        for msg in unique_consistency_warnings:
            st.warning(f"⚠️ {msg}")
        consistency_confirmed = st.checkbox(
            "Je comprends — poursuivre malgré l'incohérence signalée",
            key=f"consistency_confirm_{form_gen}",
        )

    c_pending, c_validated, c_copy = st.columns(3)

    with c_pending:
        if st.button("💾 Save as Pending", use_container_width=True, disabled=not (overwrite_confirmed and consistency_confirmed)):
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
        if st.button("✅ Save as Validated", use_container_width=True, type="primary", disabled=not (overwrite_confirmed and consistency_confirmed)):
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
