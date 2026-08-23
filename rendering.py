import re
from jinja2 import Template
import database as db

# Lightweight Markdown-style bold convention for Master Lock manual edits.
# Plain text areas have no concept of bold, and guessing which lines
# "should" be bold from structure (numbered headers vs. bulleted labels vs.
# whatever the next case type needs) is a losing game. Instead, the user
# marks bold explicitly with **like this**, same as Markdown — works for
# any report structure without special-casing any of them.
BOLD_MARKUP_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def snippet_lookup(shortcut):
    """
    Jinja2-callable, used inside Block templates as {{ snippet('shortcut') }}
    so an exact, reused-verbatim phrase (e.g. "Absence de signe de
    malignité.") lives in the Snippets table and gets edited once, not
    copy-pasted into every Block template that needs it.

    Fails loud, not silently: a missing shortcut renders as a visible
    bracketed marker in the output rather than an empty string, since a
    silently-vanishing phrase in a real report is far worse than an
    obviously-wrong one that gets caught immediately.
    """
    result = db.get_snippet_by_shortcut(shortcut)
    if result is None:
        return f"[SNIPPET NOT FOUND: {shortcut}]"
    return result["expansion"]


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
    if field_type == "decimal":
        try:
            return float(raw_value)
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


def render_block(block, field_values_override=None, total_specimens=1):
    """
    Renders one block instance's microscopy and conclusion text.

    total_specimens: how many specimens (Blocks) are in the whole case,
    not just this one. Only matters when block["macro_template"] is set:
    whether "Examen macroscopique"/"Examen microscopique" bold headers
    appear, and with what spacing, depends on this — a template has no
    way to know it about itself, confirmed by trying to bake the headers
    into the template text directly and getting it wrong for the
    multi-specimen case. So it's decided here instead, every time, from
    data the caller already has (len(blocks) in workspace.py's assembly
    loop).

    This is meant to be universal, not a per-block-type opt-in: every
    non-table block gets a real macro_template (even Gastric Trio's is
    just "{{fragment_text}}" now, split out from what used to be folded
    unlabeled into micro_template) — so a single-specimen preset of any
    kind gets the same header treatment, not just Gallbladder/Appendix/
    Thyroid. macro_template=None stays supported for a block with
    genuinely no separate macro content to state, but isn't expected to
    be the normal case. The one real exception is is_table blocks (e.g.
    future prostate biopsies) — a fundamentally different, row-based
    rendering path this function doesn't touch.
    """
    context = build_context(block, field_values_override)
    conc_txt = Template(block["conclusion_template"]).render(snippet=snippet_lookup, **context).strip()

    macro_template = block.get("macro_template")
    if macro_template:
        macro_txt = Template(macro_template).render(snippet=snippet_lookup, **context).strip()
        micro_only_txt = Template(block["micro_template"]).render(snippet=snippet_lookup, **context).strip()
        if total_specimens == 1:
            # 2 blank lines around each header, header hugs its own
            # content with no gap — confirmed paragraph-by-paragraph
            # against a real single-specimen sample.
            micro_txt = (
                f"**Examen macroscopique**\n{macro_txt}"
                f"\n\n\n**Examen microscopique**\n{micro_only_txt}"
            )
        else:
            # No headers at all when part of a multi-specimen case — just
            # macro and micro, one blank line apart, under this block's
            # own "N. Name" specimen header (added later in
            # format_micro_plain). Confirmed against a real two-specimen
            # sample: no "Examen..." labels appear anywhere in it.
            micro_txt = f"{macro_txt}\n\n{micro_only_txt}"
    else:
        micro_txt = Template(block["micro_template"]).render(snippet=snippet_lookup, **context).strip()

    return micro_txt, conc_txt


def format_micro_plain(micro_blocks):
    """
    Plain text for the microscopy section, with **bold** already at the
    block-header spots — matches the sample reports: header line bold,
    body text plain. This is the single source of truth: used for the
    auto-render preview AND as the exact pre-fill when switching into
    Master Lock, so existing bold never needs retyping.

    No specimen-name header at all — not even un-numbered — when there's
    exactly one specimen in the case. Confirmed against a real sample:
    the title alone covers it (whether or not that one block also gets
    its own "Examen macroscopique/microscopique" headers from
    render_block is irrelevant here — either way, nothing wraps it).
    Numbered "N. Name" headers return as soon as there are 2+.
    """
    single_specimen = len(micro_blocks) == 1
    if single_specimen:
        return micro_blocks[0][1]
    parts = [f"**{i+1}. {name}**\n{text}" for i, (name, text) in enumerate(micro_blocks)]
    return "\n\n".join(parts)


def assemble_report_html(clinical_info, preset_title, micro_html, conc_html):
    """
    Final report shell. Takes already-converted HTML for microscopy/
    conclusion — call text_to_html() on the plain text first. Bold comes
    entirely from **markers** in that plain text now; nothing here forces
    extra bolding, so auto-render and manual edits stay visually identical
    for identical content.

    The "Renseignements cliniques :" line is omitted entirely when empty
    — not rendered as a label with nothing after it — confirmed against
    a real sample where the field wasn't filled in for that case.
    """
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
    """Convenience wrapper for the auto-render path. conclusion_plain_text
    must already be the output of grouping.render_conclusion_plain()."""
    micro_html = text_to_html(format_micro_plain(micro_blocks))
    conc_html = text_to_html(conclusion_plain_text)
    return assemble_report_html(clinical_info, preset_title, micro_html, conc_html)