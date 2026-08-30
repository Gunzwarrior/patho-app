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

    # block_sort_order must be contiguous -- never interleaved -- across
    # the sequence. The parser's block-cursor logic (parse_tokens, below)
    # assumes all of one block's tokens appear together before the next
    # block's start; this wasn't checked when the column was first added
    # (Checkpoint 1) because nothing consumed it yet. Surfaced while
    # designing the parser (Checkpoint 2) -- folded in here since it's a
    # property of the same config this function already validates, not a
    # new concern.
    seen_blocks = []
    for token in tokens:
        bso = token.get("block_sort_order", 0)
        if not seen_blocks or seen_blocks[-1] != bso:
            if bso in seen_blocks:
                raise ValueError(
                    f"block_sort_order {bso} reappears non-contiguously in the token "
                    f"sequence (at field '{token['field_key']}') -- a block's tokens must "
                    f"all be contiguous, never interleaved with another block's."
                )
            seen_blocks.append(bso)


def _block_sequence(tokens):
    """Ordered list of distinct block_sort_order values, in first-appearance
    order -- e.g. [0] for every current single-block preset, [0, 1] for a
    future two-block one. Relies on validate_quick_type_config's
    contiguity check having already passed for this token list."""
    seen = []
    for t in tokens:
        bso = t.get("block_sort_order", 0)
        if not seen or seen[-1] != bso:
            seen.append(bso)
    return seen


def find_preset_by_prefix(raw_code, presets):
    """
    Longest-registered-short_code-prefix match: finds the longest
    Presets.short_code that is a prefix of raw_code. presets: list of
    preset dicts with at least 'id'/'short_code' (e.g. what
    database.get_all_presets() returns).

    Returns (preset, remainder) -- remainder is whatever's left of
    raw_code after the matched short_code (possibly "" for a bare code
    like "dai"). Returns (None, None) if no registered short_code
    prefixes raw_code at all.

    Deliberately takes the LONGEST match when more than one short_code
    prefixes the input, with no ambiguity error -- a short_code that's a
    prefix of another (e.g. hypothetical "etc" alongside "etc2") is a
    config-authoring problem to avoid at the source (flagged for the
    future Editor UI, not solved here), not something this function
    tries to detect or warn about at parse time.
    """
    matches = [p for p in presets if raw_code.startswith(p["short_code"])]
    if not matches:
        return None, None
    best = max(matches, key=lambda p: len(p["short_code"]))
    return best, raw_code[len(best["short_code"]):]


def parse_tokens(remainder, tokens):
    """
    remainder: the modifier string after the preset code (e.g. "37" from
    "dai37", or "" for a bare preset code with no modifiers typed).
    tokens: this preset's Quick_Type_Tokens, already ordered by
    sort_order (e.g. database.get_quick_type_tokens(preset_id)) --
    assumed already valid (validate_quick_type_config passed at seed
    time); this function doesn't re-validate the config itself, only the
    input string against it.

    Returns (field_overrides_by_block, None) on success --
    field_overrides_by_block is {block_sort_order: {field_key: raw_value}},
    containing only fields actually touched by the typed string (same
    "override" meaning as Preset_Blocks.field_overrides elsewhere --
    untouched fields keep their existing default, nothing here decides
    that). raw_value is left as a plain string (e.g. "37" for a
    measurement) -- coercion to the field's real type happens downstream
    via rendering.coerce_field_value, exactly like every other override
    channel already does; no new coercion logic needed here.

    Returns (None, error_message) on ANY failure -- unrecognized
    character, out-of-table lookup key, leftover unparsed characters, or
    '!' with nothing to skip to. Nothing is ever partially applied: a
    None first element means "apply nothing," full stop.

    '!' advances to the next block's tokens without consuming any of the
    current block's remaining ones. Not required between blocks --
    exhausting a block's tokens naturally moves to the next one on the
    following character. In a single-block preset (every current preset),
    '!' has nothing to advance to and is a parse error.
    """
    if not tokens:
        if remainder:
            return None, f"'{remainder}' left over -- this preset has no Quick Type modifiers configured."
        return {}, None

    blocks_seq = _block_sequence(tokens)
    tok_idx = 0
    block_pos = 0
    overrides = {}
    i = 0

    while i < len(remainder):
        # Automatic rollover is represented by tok_idx advancing into the
        # next block's contiguous token run. Keep block_pos aligned with
        # that cursor before handling '!' so a skip after rollover skips
        # the block currently awaiting input, rather than the one just
        # consumed.
        if tok_idx < len(tokens):
            block_pos = blocks_seq.index(tokens[tok_idx].get("block_sort_order", 0))
        else:
            block_pos = len(blocks_seq) - 1

        char = remainder[i]

        if char == "!":
            if block_pos >= len(blocks_seq) - 1:
                return None, "'!' has no next block to skip to in this preset."
            block_pos += 1
            next_block = blocks_seq[block_pos]
            while tok_idx < len(tokens) and tokens[tok_idx]["block_sort_order"] != next_block:
                tok_idx += 1
            i += 1
            continue

        if tok_idx >= len(tokens):
            return None, f"'{remainder[i:]}' left over -- no more modifiers configured for this preset."

        token = tokens[tok_idx]

        if token["token_kind"] == "lookup":
            table = token["lookup_table"] or {}
            if char not in table:
                return None, (
                    f"'{char}' is not a valid value for '{token['field_key']}' "
                    f"(expected one of {sorted(table.keys())})."
                )
            overrides.setdefault(token["block_sort_order"], {})[token["field_key"]] = table[char]
            i += 1
            tok_idx += 1

        elif token["token_kind"] == "measurement":
            j = i
            cap = len(remainder) if not token.get("digit_width") else min(len(remainder), i + token["digit_width"])
            while j < cap and remainder[j].isdigit():
                j += 1
            if j == i:
                return None, f"expected digits for '{token['field_key']}' at position {i}, got '{char}'."
            overrides.setdefault(token["block_sort_order"], {})[token["field_key"]] = remainder[i:j]
            i = j
            tok_idx += 1

        else:
            # Unreachable if validate_quick_type_config already passed on
            # this config -- guarded anyway rather than assumed.
            return None, f"unknown token_kind '{token['token_kind']}' for '{token['field_key']}'."

    return overrides, None


def parse_quick_type(raw_code):
    """
    Convenience wrapper: the actual entry point workspace.py will call.
    Fetches presets and the matched preset's tokens from the database
    itself (same pattern grouping.py already uses for
    get_conclusion_group_label) rather than requiring the caller to fetch
    and inject them -- find_preset_by_prefix/parse_tokens above stay pure
    and independently testable for anyone who wants to test parsing logic
    without a database.

    Returns (preset, field_overrides_by_block, error) -- error is None on
    success. On failure, preset and field_overrides_by_block are both
    None and error is a specific, user-displayable message.
    """
    import database as db

    raw_code = raw_code.strip()
    if not raw_code:
        return None, None, "empty Quick Type code."

    preset, remainder = find_preset_by_prefix(raw_code, db.get_all_presets())
    if preset is None:
        return None, None, f"no preset short_code matches '{raw_code}'."

    tokens = db.get_quick_type_tokens(preset["id"])
    overrides, error = parse_tokens(remainder, tokens)
    if error:
        return None, None, f"{preset['short_code']}: {error}"
    return preset, overrides, None
