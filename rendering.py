import re
from jinja2 import Template
import database as db

BOLD_MARKUP_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def snippet_lookup(shortcut):
    result = db.get_snippet_by_shortcut(shortcut)
    if result is None:
        return f"[SNIPPET NOT FOUND: {shortcut}]"
    return result["expansion"]


def text_to_html(text):
    html = BOLD_MARKUP_RE.sub(r"<b>\1</b>", text)
    return html.replace("\n", "<br>")


def format_fragment_text(count):
    count = int(count)
    if count == 1:
        return "Un fragment biopsique inclus en totalité."
    return f"{count} fragments biopsiques inclus en totalité."


def format_decimal_display(value):
    """Formats a decimal field's value for display without a spurious
    trailing '.0' on whole numbers (20.0 -> '20'), while a real
    fractional value still renders normally (15.5 -> '15.5').
    coerce_field_value always produces a real Python float for 'decimal'
    fields, and Jinja2 has no built-in way to conditionally drop a
    trailing '.0' — this is computed here and exposed as a
    '{key}_display' companion value in build_context(). Pre-existing bug,
    caught while building it for a new field: Gallbladder's
    specimen_size_cm and Thyroid Cytology's liquid_volume_ml already
    render "8.0 cm"/"5.0 mL" today via direct {{field}} reference — both
    updated to use the new _display companion alongside this fix."""
    if value is None:
        return None
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def coerce_field_value(field_type, raw_value):
    if raw_value is None:
        return None
    if field_type == "checkbox":
        return str(raw_value) in ("1", "True", "true")
    if field_type == "number":
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return raw_value
    if field_type == "decimal":
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return raw_value
    return raw_value


def build_context(block, field_values_override=None):
    context = {}
    for field in block["fields"]:
        value = field["value"]
        if field_values_override and field["key"] in field_values_override:
            value = field_values_override[field["key"]]
        context[field["key"]] = coerce_field_value(field["type"], value)

    if "fragments" in context and context["fragments"] is not None:
        context["fragment_text"] = format_fragment_text(context["fragments"])

    # Every decimal field automatically gets a clean-display companion —
    # general, not per-field-name like fragment_text above, since any
    # decimal field can end up directly interpolated into a template.
    for field in block["fields"]:
        if field["type"] == "decimal" and context.get(field["key"]) is not None:
            context[f"{field['key']}_display"] = format_decimal_display(context[field["key"]])

    if block.get("site_label") is not None:
        context["site_label"] = block["site_label"]

    return context


def render_block(block, field_values_override=None, total_specimens=1):
    context = build_context(block, field_values_override)
    conc_txt = Template(block["conclusion_template"]).render(snippet=snippet_lookup, **context).strip()

    macro_template = block.get("macro_template")
    if macro_template:
        macro_txt = Template(macro_template).render(snippet=snippet_lookup, **context).strip()
        micro_only_txt = Template(block["micro_template"]).render(snippet=snippet_lookup, **context).strip()
        if total_specimens == 1:
            micro_txt = (
                f"**Examen macroscopique**\n{macro_txt}"
                f"\n\n\n**Examen microscopique**\n{micro_only_txt}"
            )
        else:
            micro_txt = f"{macro_txt}\n\n{micro_only_txt}"
    else:
        micro_txt = Template(block["micro_template"]).render(snippet=snippet_lookup, **context).strip()

    return micro_txt, conc_txt


def format_micro_plain(micro_blocks):
    single_specimen = len(micro_blocks) == 1
    if single_specimen:
        return micro_blocks[0][1]
    parts = [f"**{i+1}. {name}**\n{text}" for i, (name, text) in enumerate(micro_blocks)]
    return "\n\n".join(parts)


def assemble_report_html(clinical_info, preset_title, micro_html, conc_html):
    clinical_line = (
        f"<b><i>Renseignements cliniques :</i></b> <i>{clinical_info}</i><br><br><br>\n        "
        if clinical_info and clinical_info.strip() else ""
    )
    return f"""
    <div style="font-family: 'Times New Roman', Times, serif; font-size: 11pt; padding: 15px; background-color: #fff; color: #000;">
        {clinical_line}<div style="text-align: center;"><b>{preset_title.upper()}</b></div><br><br>
        {micro_html}<br><br><br>
        <b>CONCLUSION</b><br><br>
        {conc_html}
    </div>
    """


def compile_final_html(clinical_info, preset_title, micro_blocks, conclusion_plain_text):
    micro_html = text_to_html(format_micro_plain(micro_blocks))
    conc_html = text_to_html(conclusion_plain_text)
    return assemble_report_html(clinical_info, preset_title, micro_html, conc_html)