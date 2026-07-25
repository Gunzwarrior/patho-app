import streamlit as st

st.set_page_config(page_title="PathoPilot", layout="wide", page_icon="🔬")

with st.sidebar:
    st.title("🔬 PathoPilot")
    st.markdown("---")

workspace_page = st.Page("pages/workspace.py", title="Workspace (Daily Ops)", icon="🔬", default=True)
manager_page = st.Page("pages/manager.py", title="Manager (Settings)", icon="⚙️")

pg = st.navigation([workspace_page, manager_page])
pg.run()