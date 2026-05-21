import streamlit as st
import json
import database as db
import report_engine as engine

st.set_page_config(page_title="PathoPilot", layout="wide", page_icon="🔬")

with st.sidebar:
    st.title("🔬 PathoPilot")
    st.markdown("---")
    app_mode = st.radio("Navigation", ["Workspace (Daily Ops)", "Manager (Settings)"])

# ==========================================
# MODE A: THE MANAGER (SETTINGS)
# ==========================================
if app_mode == "Manager (Settings)":
    st.title("⚙️ System Manager")
    tab1, tab2, tab3, tab4 = st.tabs(["Templates", "Master Templates", "Snippets", "Cases"])
    with tab1: st.dataframe(db.load_table_as_df("Templates"), use_container_width=True, hide_index=True)
    with tab2: st.dataframe(db.load_table_as_df("Master_Templates"), use_container_width=True, hide_index=True)
    with tab3: st.dataframe(db.load_table_as_df("Snippets"), use_container_width=True, hide_index=True)
    with tab4: st.dataframe(db.load_table_as_df("Cases"), use_container_width=True, hide_index=True)

# ==========================================
# MODE B: THE WORKSPACE (DAILY OPS)
# ==========================================
elif app_mode == "Workspace (Daily Ops)":
    st.title("🔬 Workspace")
    
    # 1. TOP BAR
    c1, c2, c3 = st.columns([1, 2, 2])
    with c1: case_id = st.text_input("📁 Case ID", key="case_id")
    with c2: clinical_info = st.text_input("🩺 Renseignements cliniques", key="clin_info")
    with c3:
        master_templates = db.get_all_master_template_names()
        selected_master = st.selectbox("📋 Select Protocol", ["-- Select --"] + master_templates)

    st.markdown("---")

    if selected_master != "-- Select --":
        # Fetch template sequence from database module
        raw_sequence = db.get_master_template_sequence(selected_master)
        template_sequence = json.loads(raw_sequence[0])
        
        micro_blocks = []
        conc_blocks = []

        # Global Modifiers
        global_hp = False
        if selected_master == "Gastric Trio":
            st.markdown("### ⚙️ Global Modifiers")
            global_hp = st.checkbox("🦠 Global H. Pylori (+) - Applies to Antrum & Fundus", value=True)
            st.divider()

        st.subheader("1. Medical Variables")
        with st.container():
            for i, block_name in enumerate(template_sequence):
                block_data = db.get_template_details(block_name)
                
                if block_data:
                    block_type, def_micro, def_conc = block_data
                    
                    c_label, c_frag, c1 = st.columns([1.5, 1, 4])
                    c_label.markdown(f"**{i+1}. {block_name}**")
                    fragments = c_frag.number_input("Fragments", min_value=1, value=2 if block_name=="Duodenum" else 3 if block_name=="Antrum" else 1, key=f"frag_{i}")
                    
                    # Track simple element selections
                    is_normal = True
                    inflam = "modérée"
                    
                    if block_type == "Smart":
                        if block_name == "Duodenum":
                            is_normal = c1.checkbox("Normal?", value=True, key=f"norm_{i}")
                        else:
                            inflam = c1.selectbox("Inflammation", ["légère", "modérée", "sévère"], index=1, key=f"inf_{i}", label_visibility="collapsed")
                    
                    # Delegate text production to report engine module
                    micro_txt, conc_txt = engine.generate_block_text(block_name, block_type, def_micro, def_conc, fragments, is_normal, inflam, global_hp)
                    
                    micro_blocks.append((block_name, micro_txt))
                    conc_blocks.append((block_name, conc_txt))

        st.divider()

        st.subheader("2. Final Report (Review & Edit)")
        master_lock = st.toggle("🔒 Enable Manual Edit Mode", key="master_lock")
        
        # Format strings raw array conversions for input boxes
        raw_compiled_micro = "\n\n".join([f"{i+1}. {name}:\n{text}" for i, (name, text) in enumerate(micro_blocks)])
        raw_compiled_conc = "\n".join([f"{i+1}. {text}" for i, (_, text) in enumerate(conc_blocks)])

        if not master_lock:
            st.session_state['final_micro_edit'] = raw_compiled_micro
            st.session_state['final_conc_edit'] = raw_compiled_conc

        final_micro = st.text_area("Microscopy", key="final_micro_edit", height=300, disabled=not master_lock)
        final_conc = st.text_area("Conclusion", key="final_conc_edit", height=150, disabled=not master_lock)

        st.divider()

        # Build clean markup via logic engine
        final_html = engine.compile_final_html(case_id, clinical_info, selected_master, micro_blocks, conc_blocks)
        st.markdown(final_html, unsafe_allow_html=True)
        
        # --- ACTION BUTTONS ---
        c_save, c_copy = st.columns(2)
        with c_save:
            if st.button("💾 Save Case to Database", use_container_width=True, type="primary"):
                if case_id:
                    if db.save_case(case_id, selected_master, final_html):
                        st.success("✅ Case saved successfully!")
                    else: st.error("❌ Error saving case.")
                else: st.warning("⚠️ Please enter a Case ID before saving.")

        with c_copy:
            safe_html = final_html.replace('`', "'").replace('\n', '')
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