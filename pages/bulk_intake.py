"""CP4 operational bulk preview page.  It intentionally has no Apply control."""

import hashlib

import streamlit as st

import bulk_intake
from report_presentation import restricted_report_html


st.title("Bulk intake preview")
st.caption("Decode and review new pending-case candidates. This checkpoint never creates Cases.")

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
    st.session_state.pop("_bulk_review", None)
    st.session_state.pop("_bulk_review_input_key", None)
    st.info("Input settings changed; the prior preview was discarded.")

if st.button("Prepare decoded preview", type="primary", key="bulk_prepare"):
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
        st.info("CP4 is preview-only: no Cases, batch audit, validation, deletion, inverse, or Apply action exists here.")
