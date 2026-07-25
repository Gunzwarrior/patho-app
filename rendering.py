import re
from jinja2 import Template

# Lightweight Markdown-style bold convention for Master Lock manual edits.
# Plain text areas have no concept of bold, and guessing which lines
# "should" be bold from structure (numbered headers vs. bulleted labels vs.
# whatever the next case type needs) is a losing game. Instead, the user
# marks bold explicitly with **like this**, same as Markdown — works for
# any report structure without special-casing any of them.
BOLD_MARKUP_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def text_to_html(text):
    """Converts Master Lock manual-edit plain text into HTML: **bold** markup
    becomes <b>, newlines become <br>."""
    html = BOLD_MARKUP_RE.sub(r"<b>\1</b>", text)
    return html.replace("\n", "<br>")


def format_fragment_text(count):
    """Grammatically correct French description for a biopsy fragment count."""
    count = int(count)
    if count == 1:
        return "Un fragment biopsique inclus en totalité."
    return f"{count} fragments biopsiques inclus en totalité."


def coerce_field_value(field_type, raw_value):
    """Converts a stored/override string value into the right Python type
    for use inside a Jinja2 template context (checkboxes need real bools,
    numbers need real ints, otherwise Jinja truthiness/formatting breaks)."""
    if raw_value is None:
        return None
    if field_type == "checkbox":
        return str(raw_value) in ("1", "True", "true")
    if field_type == "number":
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return raw_value
    return raw_value


def build_context(block, field_values_override=None):
    """
    block: a block dict from database.get_preset_blocks(), including 'fields'.
    field_values_override: optional {field_key: value} from live UI widgets,
    which takes priority over the block's already-resolved defaults.

    Returns the dict passed into the Jinja2 template as render context.
    """
    context = {}
    for field in block["fields"]:
        value = field["value"]
        if field_values_override and field["key"] in field_values_override:
            value = field_values_override[field["key"]]
        context[field["key"]] = coerce_field_value(field["type"], value)

    if "fragments" in context and context["fragments"] is not None:
        context["fragment_text"] = format_fragment_text(context["fragments"])

    if block.get("site_label") is not None:
        context["site_label"] = block["site_label"]

    return context


def render_block(block, field_values_override=None):
    """Renders one block instance's microscopy and conclusion text."""
    context = build_context(block, field_values_override)
    micro_txt = Template(block["micro_template"]).render(**context)
    conc_txt = Template(block["conclusion_template"]).render(**context)
    return micro_txt.strip(), conc_txt.strip()


def format_micro_plain(micro_blocks):
    """
    Plain text for the microscopy section, with **bold** already at the
    block-header spots — matches the sample reports: header line bold,
    body text plain. This is the single source of truth: used for the
    auto-render preview AND as the exact pre-fill when switching into
    Master Lock, so existing bold never needs retyping.
    """
    parts = []
    for i, (name, text) in enumerate(micro_blocks):
        parts.append(f"**{i+1}. {name}**\n{text}")
    return "\n\n".join(parts)


def assemble_report_html(case_id, clinical_info, preset_title, micro_html, conc_html):
    """
    Final report shell. Takes already-converted HTML for microscopy/
    conclusion — call text_to_html() on the plain text first. Bold comes
    entirely from **markers** in that plain text now; nothing here forces
    extra bolding, so auto-render and manual edits stay visually identical
    for identical content.
    """
    return f"""
    <div style="font-family: 'Times New Roman', Times, serif; font-size: 11pt; padding: 15px; background-color: #fff; color: #000;">
        N° {case_id}<br>
        <b><i>Renseignements cliniques :</i></b> <i>{clinical_info}</i><br><br><br>
        <div style="text-align: center;"><b>{preset_title.upper()}</b></div><br><br>
        <u>MICROSCOPY:</u><br>{micro_html}<br><br><br>
        <b>CONCLUSION</b><br><br>
        {conc_html}
    </div>
    """


def compile_final_html(case_id, clinical_info, preset_title, micro_blocks, conclusion_plain_text):
    """Convenience wrapper for the auto-render path. conclusion_plain_text
    must already be the output of grouping.render_conclusion_plain()."""
    micro_html = text_to_html(format_micro_plain(micro_blocks))
    conc_html = text_to_html(conclusion_plain_text)
    return assemble_report_html(case_id, clinical_info, preset_title, micro_html, conc_html)