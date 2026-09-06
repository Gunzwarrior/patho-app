"""Shared static analysis for the small Jinja surface used by content."""

from jinja2 import Environment, TemplateError, nodes


class SnippetCallError(ValueError):
    """A snippet reference is not one literal, positional function call."""

    def __init__(self, kind):
        self.kind = kind
        super().__init__(kind)


def snippet_calls(parsed):
    """Return literal Snippet keys from a parsed template, rejecting other uses."""
    calls = []
    for call in parsed.find_all(nodes.Call):
        if not isinstance(call.node, nodes.Name) or call.node.name != "snippet":
            raise SnippetCallError("call")
        if (len(call.args) != 1 or call.kwargs or call.dyn_args or call.dyn_kwargs
                or not isinstance(call.args[0], nodes.Const)
                or not isinstance(call.args[0].value, str)):
            raise SnippetCallError("arguments")
        calls.append(call.args[0].value)
    snippet_names = [name for name in parsed.find_all(nodes.Name) if name.name == "snippet"]
    if len(snippet_names) != len(calls):
        raise SnippetCallError("reference")
    return calls


def snippet_shortcuts(templates):
    """Parse references with Jinja itself so fingerprints match rendering syntax."""
    environment = Environment()
    result = set()
    for source in templates:
        if source:
            try:
                result.update(snippet_calls(environment.parse(source)))
            except TemplateError as error:
                raise ValueError("Invalid template syntax.") from error
    return sorted(result)
