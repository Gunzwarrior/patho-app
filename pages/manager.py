import streamlit as st
import database as db

st.title("⚙️ System Manager")
tab1, tab2, tab3, tab4, tab5 = st.tabs(["Fields", "Blocks", "Presets", "Snippets", "Cases"])
with tab1: st.dataframe(db.load_table_as_df("Fields"), use_container_width=True, hide_index=True)
with tab2: st.dataframe(db.load_table_as_df("Blocks"), use_container_width=True, hide_index=True)
with tab3: st.dataframe(db.load_table_as_df("Presets"), use_container_width=True, hide_index=True)
with tab4: st.dataframe(db.load_table_as_df("Snippets"), use_container_width=True, hide_index=True)
with tab5: st.dataframe(db.load_table_as_df("Cases"), use_container_width=True, hide_index=True)
st.caption(
    "Read-only for now — a proper Fields/Blocks/Presets/Snippets editor "
    "(add, edit, reorder without touching code) is a later step."
)