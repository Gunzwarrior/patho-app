import streamlit as st
import pandas as pd
import database as db

st.title("✏️ Editor")

st.subheader("Snippets", anchor=False)
st.caption("Reusable text fragments, referenced by shortcut from Block templates, or used standalone as a text expander.")

with st.form("add_snippet_form", clear_on_submit=True):
    c1, c2, c3 = st.columns([1, 2, 1])
    with c1:
        shortcut = st.text_input("Shortcut", placeholder="hp+")
    with c2:
        expansion = st.text_input("Expansion", placeholder="Présence d'éléments ayant la morphologie d'Helicobacter pylori.")
    with c3:
        category = st.text_input("Category", placeholder="digestif")

    submitted = st.form_submit_button("➕ Add Snippet", type="primary")

    if submitted:
        if not shortcut.strip() or not expansion.strip():
            st.warning("⚠️ Shortcut and Expansion are required.")
        else:
            success, error = db.add_snippet(shortcut.strip(), expansion.strip(), category.strip() or None)
            if success:
                st.success(f"✅ Added snippet '{shortcut.strip()}'.")
            else:
                st.error(f"❌ {error}")

st.divider()

snippets = db.get_all_snippets()
if snippets:
    st.dataframe(pd.DataFrame(snippets), use_container_width=True, hide_index=True)
else:
    st.caption("No snippets yet — add one above.")