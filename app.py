import streamlit as st
import sqlite3
import pandas as pd
import json

st.set_page_config(page_title="PathoPilot", layout="wide", page_icon="🔬")

def get_db_connection(): return sqlite3.connect('pathology.db')

def load_table(table_name):
    conn = get_db_connection()
    df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
    conn.close()
    return df

def get_template_details(template_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT type, default_micro, default_conclusion FROM Templates WHERE name = ?", (template_name,))
    result = cursor.fetchone()
    conn.close()
    return result

with st.sidebar:
    st.title("🔬 PathoPilot")
    st.markdown("---")
    app_mode = st.radio("Navigation", ["Workspace (Daily Ops)", "Manager (Settings)"])

if app_mode == "Manager (Settings)":
    st.title("⚙️ System Manager")
    tab1, tab2, tab3, tab4 = st.tabs(["Templates", "Master Templates", "Snippets", "Cases"])
    with tab1: st.dataframe(load_table("Templates"), use_container_width=True, hide_index=True)
    with tab2: st.dataframe(load_table("Master_Templates"), use_container_width=True, hide_index=True)
    with tab3: st.dataframe(load_table("Snippets"), use_container_width=True, hide_index=True)
    with tab4: st.dataframe(load_table("Cases"), use_container_width=True, hide_index=True)

elif app_mode == "Workspace (Daily Ops)":
    st.title("🔬 Workspace")
    
    # 1. TOP BAR (Added Clinical Info)
    c1, c2, c3 = st.columns([1, 2, 2])
    with c1: case_id = st.text_input("📁 Case ID", key="case_id")
    with c2: clinical_info = st.text_input("🩺 Renseignements cliniques", key="clin_info")
    with c3:
        conn = get_db_connection()
        master_templates = [row[0] for row in conn.cursor().execute("SELECT name FROM Master_Templates").fetchall()]
        conn.close()
        selected_master = st.selectbox("📋 Select Protocol", ["-- Select --"] + master_templates)

    st.markdown("---")

    if selected_master != "-- Select --":
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT template_sequence FROM Master_Templates WHERE name = ?", (selected_master,))
        template_sequence = json.loads(cursor.fetchone()[0])
        conn.close()
        
        compiled_micro = []
        compiled_conc = []

        st.subheader("1. Medical Variables")
        with st.container():
            for i, block_name in enumerate(template_sequence):
                block_data = get_template_details(block_name)
                
                if block_data:
                    block_type, def_micro, def_conc = block_data
                    
                    # Layout: Label | Fragments | Variables
                    c_label, c_frag, c1, c2 = st.columns([1.5, 1, 2, 2])
                    c_label.markdown(f"**{i+1}. {block_name}**")
                    fragments = c_frag.number_input("Fragments", min_value=1, value=2 if block_name=="Duodenum" else 3 if block_name=="Antrum" else 1, key=f"frag_{i}")
                    
                    frag_text = f"Un fragment biopsique inclus en totalité." if fragments == 1 else f"{fragments} fragments biopsiques inclus en totalité."
                    
                    if block_type == "Smart":
                        if block_name == "Duodenum":
                            is_normal = c1.checkbox("Normal?", value=True, key=f"norm_{i}")
                            micro_txt = f"{frag_text}\n\n{def_micro}" if is_normal else f"{frag_text}\n\nAnomalie détectée."
                            conc_txt = def_conc if is_normal else "Duodénite."
                        else:
                            inflam = c1.selectbox("Inflammation", ["légère", "modérée", "sévère"], index=1, key=f"inf_{i}", label_visibility="collapsed")
                            hp = c2.checkbox("H. Pylori +", value=True, key=f"hp_{i}")
                            
                            # Format the French text with the variables
                            base_micro = def_micro.replace("{inflam}", inflam)
                            micro_txt = f"{frag_text}\n\n{base_micro}"
                            
                            conc_txt = f"Gastrite chronique interstitielle {block_name.lower()} {inflam}, active, sans métaplasie intestinale ni atrophie glandulaire."
                            
                            if hp: 
                                micro_txt += "\nPrésence d'éléments ayant la morphologie d'Helicobacter pylori en HES.\n\nEtude immunohistochimique :\n- HP : positif"
                                conc_txt += "\nPrésence d'une infection à hélicobacter pylori."
                    
                    # HTML formatting for the block title
                    compiled_micro.append(f"<b>{i+1}. {block_name}</b>\n{micro_txt}")
                    compiled_conc.append(f"<b>{i+1}.</b> {conc_txt}")

        st.divider()

        st.subheader("2. Final Report (Review & Edit)")
        master_lock = st.toggle("🔒 Enable Manual Edit Mode", key="master_lock")
        
        if not master_lock:
            st.session_state['final_micro_edit'] = "\n\n".join(compiled_micro)
            st.session_state['final_conc_edit'] = "\n\n".join(compiled_conc)
            st.caption("🟢 *Auto-generation active.*")

        final_micro = st.text_area("Microscopy", key="final_micro_edit", height=400, disabled=not master_lock)
        final_conc = st.text_area("Conclusion", key="final_conc_edit", height=200, disabled=not master_lock)

        st.divider()

        # HTML Generation matching your screenshot exactly
        html_micro = final_micro.replace('\n', '<br>')
        html_conc = final_conc.replace('\n', '<br>')
        title = "BIOPSIES GASTRODUODENALES" if selected_master == "Gastric Trio" else selected_master.upper()
        
        final_html = f"""
        <div style="font-family: 'Times New Roman', Times, serif; font-size: 11pt; padding: 15px; border: 1px solid #ddd; background-color: #fff; color: #000;">
            N° {case_id}<br>
            <i>Renseignements cliniques : {clinical_info}</i><br><br><br>
            <div style="text-align: center;"><b>{title}</b></div><br><br>
            {html_micro}<br><br><br>
            <b>CONCLUSION</b><br><br>
            {html_conc}
        </div>
        """
        
        st.markdown(final_html, unsafe_allow_html=True)
        
        safe_html = final_html.replace('`', "'").replace('\n', '')
        components_html = f"""
        <button onclick="copyRichText()" style="padding: 10px 20px; background-color: #4CAF50; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; width: 100%;">
            📋 Copy to Diamic
        </button>
        <script>
        function copyRichText() {{
            const tempDiv = document.createElement("div");
            tempDiv.innerHTML = `{safe_html}`;
            tempDiv.style.position = "absolute";
            tempDiv.style.left = "-9999px";
            document.body.appendChild(tempDiv);
            const range = document.createRange();
            range.selectNodeContents(tempDiv);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            try {{
                document.execCommand("copy");
                const btn = document.querySelector("button");
                btn.innerHTML = "✅ Copied Successfully!";
                btn.style.backgroundColor = "#2E7D32";
                setTimeout(() => {{ btn.innerHTML = "📋 Copy to Diamic"; btn.style.backgroundColor = "#4CAF50"; }}, 2000);
            }} catch (err) {{ alert("Error"); }}
            selection.removeAllRanges();
            document.body.removeChild(tempDiv);
        }}
        </script>
        """
        st.components.v1.html(components_html, height=60)
