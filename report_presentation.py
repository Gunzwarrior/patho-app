"""Restricted, display-only HTML boundary for rendered reports.

Canonical report HTML and saved artifacts intentionally remain untouched.  This
module only produces the string handed to Streamlit's unsafe-HTML renderer.
"""

from html import escape
from html.parser import HTMLParser
import re


ALLOWED_TAGS = {
    "b", "blockquote", "br", "div", "em", "h1", "h2", "h3", "h4",
    "h5", "h6", "hr", "i", "li", "ol", "p", "span", "strong", "sub",
    "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}
VOID_TAGS = {"br", "hr"}
DROP_WITH_CONTENT = {"applet", "audio", "canvas", "embed", "iframe", "math",
                     "noscript", "object", "script", "style", "svg", "video"}
STYLE_RULES = {
    "background-color": re.compile(r"^(?:#[0-9a-fA-F]{3,8}|transparent|white|black)$"),
    "color": re.compile(r"^(?:#[0-9a-fA-F]{3,8}|white|black)$"),
    "font-family": re.compile(r"^[A-Za-z0-9 '\",-]+$"),
    "font-size": re.compile(r"^(?:\d+(?:\.\d+)?(?:pt|px|em|rem|%)|small|medium|large)$"),
    "font-style": re.compile(r"^(?:normal|italic)$"),
    "font-weight": re.compile(r"^(?:normal|bold|[1-9]00)$"),
    "margin": re.compile(r"^[0-9 .%-]+(?:px|pt|em|rem|%)?$"),
    "margin-left": re.compile(r"^[0-9.]+(?:px|pt|em|rem|%)$"),
    "padding": re.compile(r"^[0-9 .%-]+(?:px|pt|em|rem|%)?$"),
    "text-align": re.compile(r"^(?:left|right|center|justify)$"),
    "text-decoration": re.compile(r"^(?:none|underline|line-through)$"),
    "white-space": re.compile(r"^(?:normal|pre|pre-wrap)$"),
}


def _safe_style(value):
    """Keep a deliberately small formatting vocabulary; URLs never survive."""
    if not isinstance(value, str):
        return ""
    declarations = []
    for declaration in value.split(";"):
        if ":" not in declaration:
            continue
        name, raw_value = (part.strip() for part in declaration.split(":", 1))
        name = name.lower()
        if re.search(
            r"url\s*\(|expression\s*\(|@import|javascript\s*:|behavior\s*:|-moz-binding",
            declaration, re.IGNORECASE,
        ):
            continue
        validator = STYLE_RULES.get(name)
        if validator and validator.fullmatch(raw_value):
            declarations.append(f"{name}: {raw_value}")
    return "; ".join(declarations)


class _RestrictedReportParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.drop_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.drop_depth:
            if tag in DROP_WITH_CONTENT:
                self.drop_depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            self.drop_depth = 1
            return
        if tag not in ALLOWED_TAGS:
            return
        safe_attrs = []
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on") or name in {
                "action", "background", "formaction", "href", "poster", "src", "srcdoc", "xlink:href",
            }:
                continue
            if name == "style":
                value = _safe_style(value)
                if value:
                    safe_attrs.append((name, value))
            elif name in {"colspan", "rowspan"} and value and re.fullmatch(r"[1-9][0-9]{0,2}", value):
                safe_attrs.append((name, value))
        rendered = "".join(f' {name}="{escape(value, quote=True)}"' for name, value in safe_attrs)
        self.output.append(f"<{tag}{rendered}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.drop_depth:
            if tag in DROP_WITH_CONTENT:
                self.drop_depth -= 1
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.output.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.drop_depth:
            self.output.append(escape(data))

    def handle_entityref(self, name):
        if not self.drop_depth:
            self.output.append(f"&amp;{escape(name)};")

    def handle_charref(self, name):
        if not self.drop_depth:
            self.output.append(f"&amp;#{escape(name)};")


def restricted_report_html(value):
    """Return inert report presentation HTML while preserving report formatting."""
    parser = _RestrictedReportParser()
    parser.feed(value or "")
    parser.close()
    return "".join(parser.output)
