def format_fragment_text(count):
    """Returns the grammatically correct French description for biopsy counts."""
    if count == 1:
        return "Un fragment biopsique inclus en totalité."
    return f"{count} fragments biopsiques inclus en totalité."

def generate_block_text(block_name, block_type, def_micro, def_conc, fragments, is_normal, inflam, global_hp):
    """
    Computes the microscopy text and conclusion text for an individual block 
    based on user selected clinical variables.
    """
    frag_text = format_fragment_text(fragments)
    
    if block_type != "Smart":
        return f"<b>{block_name}</b>\n{frag_text}\n\n{def_micro}", def_conc

    if block_name == "Duodenum":
        micro_txt = f"{frag_text}\n\n{def_micro}" if is_normal else f"{frag_text}\n\nAnomalie détectée."
        conc_txt = def_conc if is_normal else "Duodénite."
        return micro_txt, conc_txt
        
    # Default processing for Antrum and Fundus
    base_micro = def_micro.replace("{inflam}", inflam)
    micro_txt = f"{frag_text}\n\n{base_micro}"
    conc_txt = f"Gastrite chronique interstitielle {block_name.lower()} {inflam}, active, sans métaplasie intestinale ni atrophie glandulaire."
    
    if global_hp: 
        micro_txt += "\nPrésence d'éléments ayant la morphologie d'Helicobacter pylori en HES."
        if block_name == "Antrum":
            micro_txt += "\n\nEtude immunohistochimique :\n- HP : positif"
        conc_txt += "\nPrésence d'une infection à hélicobacter pylori."
        
    return micro_txt, conc_txt

def compile_final_html(case_id, clinical_info, protocol_title, micro_blocks, conc_blocks):
    """Combines all blocks into the final precise HTML layout ready for clipboard extraction."""
    # 1. Build Microscopy section safely by handling the newline replacement out of the f-string string interpolation
    formatted_micro_list = []
    for i, (name, text) in enumerate(micro_blocks):
        safe_text = text.replace('\n', '<br>')
        formatted_micro_list.append(f"<b>{i+1}. {name}</b><br>{safe_text}")
    html_micro = "<br><br>".join(formatted_micro_list)
    
    # 2. Build Conclusion section safely
    formatted_conc_list = []
    for i, (_, text) in enumerate(conc_blocks):
        safe_text = text.replace('\n', '<br>')
        formatted_conc_list.append(f"<b>{i+1}.</b> {safe_text}")
    html_conc = "<br>".join(formatted_conc_list)
    
    display_title = "BIOPSIES GASTRODUODENALES" if protocol_title == "Gastric Trio" else protocol_title.upper()
    
    return f"""
    <div style="font-family: 'Times New Roman', Times, serif; font-size: 11pt; padding: 15px; background-color: #fff; color: #000;">
        N° {case_id}<br>
        <b><i>Renseignements cliniques :</i></b> <i>{clinical_info}</i><br><br><br>
        <div style="text-align: center;"><b>{display_title}</b></div><br><br>
        <u>MICROSCOPY:</u><br>{html_micro}<br><br><br>
        <b>CONCLUSION</b><br><br>
        <b>{html_conc}</b>
    </div>
    """