"""CP5 operational bulk intake: preview, acknowledge, and atomically create."""

import hashlib

import streamlit as st

import bulk_intake
from report_presentation import restricted_report_html


st.title("Bulk intake preview")
st.caption("Decode, review, and create new pending Cases atomically.")

# A successful Apply clears this page's source controls on the following rerun.
# Keep the generation distinct from review state so an old confirmation cannot
# be reused for a newly entered batch.
reset_generation = st.session_state.get("_bulk_apply_reset_generation", 0)
if st.session_state.get("_bulk_apply_rendered_generation") != reset_generation:
    for state_key in (
        "bulk_input_mode", "bulk_paste_source", "bulk_upload_source", "bulk_delimiter", "bulk_header",
        "bulk_apply_confirm", "bulk_warning_acknowledged",
    ):
        st.session_state.pop(state_key, None)
    st.session_state["_bulk_apply_rendered_generation"] = reset_generation
success_message = st.session_state.pop("_bulk_apply_success_message", None)
if success_message:
    st.success(success_message)


def _discard_review(*, message=None):
    """Drop a frozen review and every consent that could authorize it."""
    for state_key in (
        "_bulk_review", "_bulk_review_input_key", "bulk_apply_confirm", "bulk_warning_acknowledged",
    ):
        st.session_state.pop(state_key, None)
    if message:
        st.info(message)

source_mode = st.radio("Input", ("Paste", "Upload"), horizontal=True, key="bulk_input_mode")
if source_mode == "Paste":
    source = st.text_area("UTF-8 CSV/TSV", key="bulk_paste_source", height=180,
                          placeholder="Case ID,Quick Type\nCASE-001,dai37")
else:
    upload = st.file_uploader("UTF-8 CSV/TSV file", type=["csv", "tsv", "txt"], key="bulk_upload_source")
    source = upload.getvalue() if upload is not None else b""

control_one, control_two = st.columns(2)
with control_one:
    delimiter_label = st.selectbox("Delimiter", ("Comma (,)", "Tab"), key="bulk_delimiter")
with control_two:
    first_row_is_header = st.checkbox("First row is header", key="bulk_header")
delimiter = "," if delimiter_label == "Comma (,)" else "\t"

# Never retain raw Quick Type in the review.  The current text/upload is
# browser-session input only; any material change discards the frozen review.
source_bytes = source.encode("utf-8") if isinstance(source, str) else source
input_key = hashlib.sha256(source_bytes).hexdigest(), delimiter, first_row_is_header, source_mode
if st.session_state.get("_bulk_review_input_key") not in (None, input_key):
    _discard_review(message="Input settings changed; the prior preview was discarded.")

if st.button("Prepare decoded preview", type="primary", key="bulk_prepare"):
    # A new issuer/digest is a new review even when the input happens to be
    # identical. Consent must never survive replacement by a fresh review.
    _discard_review()
    review = bulk_intake.prepare_bulk_review(source, delimiter, first_row_is_header)
    st.session_state["_bulk_review"] = review
    st.session_state["_bulk_review_input_key"] = input_key

review = st.session_state.get("_bulk_review")
if review is not None:
    if not review.applicable:
        for error in review.errors:
            st.error(error)
    else:
        stale = bulk_intake.review_staleness(review)
        if stale:
            # A review that became stale is not permitted to regain old
            # confirmation if content later happens to return to the same state.
            for state_key in ("bulk_apply_confirm", "bulk_warning_acknowledged"):
                st.session_state.pop(state_key, None)
            st.warning(stale)
        else:
            st.success(f"Decoded {len(review.rows)} rows against one frozen content state.")
        st.caption(
            f"Source digest: {review.normalized_source_sha256} · content revision: "
            f"{review.content_revision_id} · interpretation digest: {review.interpretation_sha256}"
        )
        st.dataframe([
            {"Row": row.row_number, "Case ID": row.case_number, "Preset": row.preset_code,
             "Warnings": len(row.warnings), "Conflicts": len(row.conflicts)}
            for row in review.rows
        ], hide_index=True, width="stretch")
        for row in review.rows:
            with st.expander(f"Row {row.row_number}: {row.case_number}"):
                st.json(row.structured_input, expanded=False)
                if row.warnings:
                    st.subheader("Consistency warnings", anchor=False)
                    for warning in row.warnings:
                        st.warning(warning)
                if row.conflicts:
                    st.subheader("Conclusion conflicts", anchor=False)
                    for conflict in row.conflicts:
                        st.warning(conflict)
                st.subheader("Restricted report", anchor=False)
                st.markdown(restricted_report_html(row.rendered_html), unsafe_allow_html=True)
        if stale:
            st.info("Prepare a new preview before this batch can be applied.")
        else:
            if any(row.warnings for row in review.rows):
                warnings_acknowledged = st.checkbox(
                    "I acknowledge the consistency warnings for this entire batch.",
                    key="bulk_warning_acknowledged",
                )
                warning_acknowledgement = (
                    bulk_intake.acknowledge_batch_warnings(review)
                    if warnings_acknowledged else None
                )
            else:
                warning_acknowledgement = None
            confirmed = st.checkbox(
                "I confirm creation of these new pending Cases.", key="bulk_apply_confirm",
            )
            if st.button("Apply reviewed batch", type="primary", key="bulk_apply"):
                result = bulk_intake.apply_bulk_review(
                    review, source, delimiter, first_row_is_header, confirmed=confirmed,
                    warning_acknowledgement=warning_acknowledgement,
                )
                if result:
                    st.session_state.pop("_bulk_review", None)
                    st.session_state.pop("_bulk_review_input_key", None)
                    st.session_state["_bulk_apply_reset_generation"] = reset_generation + 1
                    st.session_state["_bulk_apply_success_message"] = (
                        f"Created {result.row_count} pending Case(s) atomically."
                    )
                    st.rerun()
                else:
                    st.error(result.error or "The batch could not be applied.")
        st.info("Bulk intake only creates new pending Cases. It has no bulk validation, overwrite, deletion, inverse, or retry path.")
