"""
PathoPilot — Quick Type: fast-typed shortcut codes that resolve to a
Preset plus a set of field overrides in one move (e.g. "dai37" -> the
Appendice preset with appendicite_type=periappendicite,
appendix_size_cm=7).

This file currently holds only validate_quick_type_config() (Checkpoint
1). The parser (string -> preset + field overrides, or a specific error)
is Checkpoint 2, not yet built.

Design settled in conversation, not yet in CLAUDE.md -- summarized here
so the reasoning travels with the code:

- Two token kinds only, v1: 'lookup' (single character, keyed against a
  small per-token table -- covers both real select-field lookups and
  boolean presence flags, e.g. "l" for lithiasis) and 'measurement' (a
  run of digit characters, coerced to the target field's numeric type).
  A third kind (multi-character lookup) was considered and deliberately
  deferred -- it has no natural self-terminating rule the way the other
  two do, and nothing in the person's real shorthand needs it yet.
- '!' is reserved: it advances to the next block's tokens without
  consuming any of the current block's remaining tokens ("skip the rest
  of this block's modifiers"). It is NOT required between blocks --
  fully consuming a block's tokens rolls into the next block
  automatically. '!' only matters for stopping early. In a single-block
  preset, '!' has nothing to advance to and must be a parse error, not a
  silent no-op.
- Ambiguity rule for 'measurement' tokens: a measurement token consumes
  greedily until a non-digit character or the end of the string. This is
  only safe if the token immediately following it (if any) cannot itself
  start with a digit -- otherwise there's no way to know where the
  measurement stops and the next token starts. This is a property of the
  PRESET'S CONFIG, checked once here at build time, not something the
  parser has to guess about per-input at runtime.
- block_sort_order exists on every token now (always 0 for today's
  single-block presets) purely so multi-block Quick Type is additive
  later -- more Quick_Type_Tokens rows targeting a different
  block_sort_order -- rather than a schema change. The validator already
  treats a preset's tokens as one flat, block-agnostic sequence, since
  the ambiguity rule has to hold across a block boundary exactly the same
  as within one block (auto-rollover means a boundary can be crossed
  without '!' too).
"""

DIGITS = set("0123456789")
RESERVED_CHARS = {"!"}  # '!' = skip to next block. Never valid as a real
                         # token value -- would be unparseable otherwise.


def _first_chars(token):
    """
    The set of characters that could legally be the FIRST character
    consumed by this token. Used to check whether a 'measurement' token
    can safely precede it without ambiguity.

    - 'lookup': exactly its own key set (each key is required to be a
      single character -- enforced below, not assumed here).
    - 'measurement': any digit -- it always starts by consuming a digit,
      that's what makes it a measurement token.
    """
    if token["token_kind"] == "lookup":
        return set(token["lookup_table"].keys())
    elif token["token_kind"] == "measurement":
        return set(DIGITS)
    else:
        raise ValueError(f"Unknown token_kind '{token['token_kind']}' for field '{token['field_key']}'.")


def validate_quick_type_config(tokens):
    """
    tokens: ordered list of token dicts for ONE preset's Quick Type
    config, already flattened across however many blocks it spans (sorted
    by sort_order) -- e.g. what a seed function is about to insert into
    Quick_Type_Tokens, or what the future Editor UI is about to save.
    Each dict needs at least: field_key, token_kind ('lookup' |
    'measurement'), lookup_table (required for 'lookup', a dict of
    single-char keys; ignored for 'measurement').

    Raises ValueError with a specific, actionable message on the first
    violation found. Returns None (no return value) on success -- this is
    a gate, called at the point a config is authored (seed_data.py today,
    the Editor UI eventually), so an invalid config can never reach a
    real typed code. Never called at parse time -- by the time a person
    is typing a code, the config it's checked against is already known
    good.

    Checks:
    1. Every 'lookup' token's keys are exactly 1 character each, and none
       of them collide with a RESERVED_CHARS value ('!' today).
    2. Every 'measurement' token is either the last token in the
       sequence, or the token immediately after it has no possible first
       character in common with digits 0-9 -- otherwise the parser has no
       way to know, at read time, where the measurement's digits stop.
    """
    if not tokens:
        return  # a preset with no Quick Type config is just not quick-typeable past its bare short_code -- not an error.

    for i, token in enumerate(tokens):
        field_key = token["field_key"]

        if token["token_kind"] == "lookup":
            keys = set(token["lookup_table"].keys())
            if not keys:
                raise ValueError(f"Lookup token for '{field_key}' has an empty lookup_table.")
            oversized = {k for k in keys if len(k) != 1}
            if oversized:
                raise ValueError(
                    f"Lookup token for '{field_key}' has key(s) {sorted(oversized)} longer than 1 "
                    f"character -- multi-character lookup isn't supported yet (deferred, see module docstring)."
                )
            collisions = keys & RESERVED_CHARS
            if collisions:
                raise ValueError(
                    f"Lookup token for '{field_key}' uses reserved character(s) {sorted(collisions)} "
                    f"as a key -- {sorted(RESERVED_CHARS)} are reserved for block-skip control, not field values."
                )

        elif token["token_kind"] == "measurement":
            is_last = (i == len(tokens) - 1)
            if not is_last:
                next_token = tokens[i + 1]
                overlap = _first_chars(next_token) & DIGITS
                if overlap:
                    raise ValueError(
                        f"Measurement token for '{field_key}' is immediately followed by a token for "
                        f"'{next_token['field_key']}' whose recognized characters overlap with digits "
                        f"({sorted(overlap)}) -- ambiguous where the measurement's digits stop. "
                        f"Reorder so the measurement is last in the sequence, or put a non-digit-keyed "
                        f"token between them."
                    )
        else:
            raise ValueError(f"Unknown token_kind '{token['token_kind']}' for field '{field_key}' (expected 'lookup' or 'measurement').")