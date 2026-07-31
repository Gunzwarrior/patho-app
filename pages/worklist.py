import streamlit as st
import database as db

st.title("📋 Worklist")

c1, c2 = st.columns([1, 2])
with c1:
    status_filter = st.selectbox("Status", ["All", "Pending", "Validated"])
with c2:
    search_term = st.text_input("Search", placeholder="Case number or clinical info...")

status_map = {"All": None, "Pending": "pending", "Validated": "validated"}
cases = db.get_all_cases(status=status_map[status_filter], search_term=search_term.strip() or None)

st.caption(f"{len(cases)} case(s)")
st.divider()

if not cases:
    st.info("No cases match the current filter.")

for case in cases:
    with st.container(border=True):
        col1, col2, col3, col4 = st.columns([2, 2, 3, 1])
        with col1:
            st.markdown(f"**{case['case_number']}**")
            st.caption(case.get("preset_name") or "—")
        with col2:
            if case["status"] == "pending":
                st.markdown("🟡 Pending")
                if case.get("pending_reason"):
                    st.caption(case["pending_reason"])
            else:
                st.markdown("✅ Validated")
        with col3:
            clinical = (case.get("clinical_info") or "").strip()
            st.caption(clinical[:80] + ("…" if len(clinical) > 80 else "") if clinical else "—")
            st.caption(case.get("updated_at") or case.get("created_at") or "")
        with col4:
            # query_params is the only reliable way to carry data across a
            # Streamlit page navigation — session_state is confirmed
            # unreliable across st.switch_page in some cases, but query
            # params survive since they're baked into the destination URL
            # itself, not passed through session state.
            st.page_link(
                "pages/workspace.py", label="Reopen", icon="🔓",
                query_params={"reopen": case["case_number"]},
            )