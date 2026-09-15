import streamlit as st
import database

st.set_page_config(page_title="PathoPilot", layout="wide", page_icon="🔬")
# Idempotent and additive: operational deployments receive safety columns
# without a destructive init_db rebuild.
database.migrate_schema()

with st.sidebar:
    st.title("🔬 PathoPilot")
    st.markdown("---")

workspace_page = st.Page("pages/workspace.py", title="Workspace (Daily Ops)", icon="🔬", default=True)
worklist_page = st.Page("pages/worklist.py", title="Worklist", icon="📋")
bulk_intake_page = st.Page("pages/bulk_intake.py", title="Bulk Intake", icon="📥")
editor_page = st.Page("pages/editor.py", title="Editor", icon="✏️")
manager_page = st.Page("pages/manager.py", title="Manager (Settings)", icon="⚙️")

pg = st.navigation([workspace_page, worklist_page, bulk_intake_page, editor_page, manager_page])
pg.run()
