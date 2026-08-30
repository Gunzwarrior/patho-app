# TESTING.md — persistent test suite: philosophy, structure, checkpoints

## Why this exists

Every round of work on this project so far has been verified with a
throwaway script — direct function calls or an `AppTest` flow, written
fresh, deleted after ("clean up test data... delete throwaway test
scripts after each round," per CLAUDE.md). That's worked because a
tested-by-someone-who-knows-this-codebase round has always sat between
a template change and a real case seeing it.

Two things are changing that:
- **Editor UI (Tier 3)** is specifically about letting Thomas edit
  templates directly, with no round-trip through a session where
  something gets tested first.
- **A mixed-model workflow** means a change might come from a tool or
  model with no track record on this specific codebase — one that
  hasn't lived through the widget-key bug three times, or the macro/
  micro header logic being wrong twice before it was right.

A persistent, re-runnable suite is the shared safety net that doesn't
depend on which tool or which model made a given change, or how
thorough that model's own ad-hoc testing happened to be.

## This is not TDD

No test-first requirement. Write the suite (or add to it) the same way
every other round's verification has happened — after something works,
before it's presented — the only difference is it stays instead of
getting deleted. No coverage targets, no mocking framework: this app's
actual architecture (a real SQLite DB, deterministic Jinja2 templates,
`AppTest` for Streamlit instead of a browser) needs almost none of the
ceremony that made RSpec/TDD feel heavy. A test here is a Python
function with a handful of `assert` lines — nothing more elaborate is
required, though `pytest` supports grouping tests into classes
(`class TestFoo:`) if that scratches the same itch `describe` blocks
did in RSpec — entirely optional, used lightly in what's built so far.

## How to run it

```
cd ~/patho-app && source venv/bin/activate
pip install pytest --break-system-packages   # one-time
pytest              # whole suite
pytest -q           # compact output
pytest -v tests/test_consistency.py   # one file, verbose
```

76 tests currently exist, running in well under a second.

## Structure

```
patho-app/
  tests/
    conftest.py              # DB-isolation fixtures — read this first
    test_rendering.py        # unit tests, rendering.py pure functions
    test_quicktype.py        # unit tests, quicktype.py (partial)
    test_consistency.py      # unit tests, consistency.py
    test_golden_output.py    # golden-fixture regression tests (partial)
    golden_helpers.py        # shared render-a-preset-at-defaults helper
    golden_fixtures/         # frozen known-good plain-text output
    regenerate_golden.py     # deliberate, human-reviewed fixture updates
```

## DB isolation — the one rule everything else depends on

**Tests must never read or write the real `pathology.db`.** That file
will eventually hold real patient specimen data (the `Cases` table) — a
test suite that touches it, even by accident, isn't a safety net, it's
a hazard.

`conftest.py` has two fixtures:
- **`db`** (session-scoped): builds a real schema + real `seed_data.py`
  content once, at a temp path, and points `database.DB_NAME` there for
  the rest of the test process. Use this for anything that only
  reads — which is almost everything: rendering, grouping, quicktype,
  consistency, golden-output.
- **`mutable_db`** (function-scoped): a private *copy* of that same
  seeded DB, for exactly one test, auto-discarded after. Use this only
  for a test that actually writes (`database.save_case`, or anything
  that `INSERT`/`UPDATE`s) — so one test's writes can never leak into
  another test's expected result.

One implementation detail worth knowing before touching `conftest.py`:
`database.py` and `init_db.py` each have their **own** `DB_NAME`
constant — same default value, not the same variable.
`init_db.setup_database()` now takes an optional `db_name` override
(added alongside this suite) specifically so building the test DB
doesn't depend on monkeypatching both globals in lockstep — `python3
init_db.py` with no argument is completely unaffected.

Confirmed directly, not assumed: a full test run's `pathology.db`
checksum was compared before and after — unchanged.

## Golden-output fixtures

The core of this plan, and the part worth understanding properly before
extending it.

Each fixture pair (`<short_code>_default_micro.txt`,
`<short_code>_default_conclusion.txt`) freezes the **plain text, with
`**bold**` markers, before HTML conversion** — the
`format_micro_plain()` / `grouping.render_conclusion_plain()` output,
not `assemble_report_html()`'s wrapped HTML. That layer is where the
actual clinical wording lives, and it's far more readable to diff than
HTML would be. HTML wrapping itself (the clinical-info line appearing
only when non-empty, the title, the header structure) is simple enough
to cover with a few direct assertions rather than a full snapshot —
that's Checkpoint 5's job, not done yet.

**Why golden files instead of just calling the render functions and
eyeballing it**: because "does this still say what it said before" is
exactly the failure mode that matters most here — a template edit (by
Thomas through a future Editor UI, or by an unfamiliar model, or by a
familiar one on an off day) can produce output that's perfectly valid
Jinja2 and perfectly plausible-looking prose while being subtly,
silently wrong for a case type nobody's actively looking at. A frozen
fixture catches that; a person reading the new output in isolation
often won't, because nothing about it looks wrong on its own.

**Update ritual — this matters more than the mechanism**:
1. Confirm the *new* output is actually correct — against a real docx
   sample if one exists for that case type, or explicit human judgment
   if not. Never regenerate a fixture just because a test started
   failing.
2. `python3 tests/regenerate_golden.py <short_code>` — prints a diff of
   what changed before overwriting.
3. Read the `git diff` on the fixture file itself before committing.
   For a text-rendering engine, that diff *is* the human-readable
   record of exactly how a real report's output changed — arguably
   more informative than the code diff that caused it.

Four fixture pairs exist today: `dai`, `gt`, and `vb` at their stored
default values, plus Appendix with `false_membranes=True` and
`appendicite_type=phlegmoneuse`. All are generated from the app's actual
rendering pipeline, not typed by hand. Thyroid fixtures are deliberately
deferred until the planned `etc0`–`etc5` Quick Type consolidation has
settled; freezing those soon-to-change presets now would create churn,
not protection.

## What's deliberately not automated

The full app-boot check used earlier this project (`streamlit run` +
`curl`, confirming the whole app starts clean as a real server, not
just via `AppTest`) stays a **manual pre-flight step**, not wired into
`pytest`. Spinning up a real server process from inside a test run is
exactly the kind of environment-dependent, port-availability-dependent
fragility this project has already been burned by elsewhere (see
CLAUDE.md, "Deliberately deferred") — not worth automating for what it
would add on top of `AppTest`, which already exercises the real
session-state/widget logic without needing a live server.

## Checkpoints

**Checkpoint 0 — scaffolding: DONE.** `conftest.py`'s two fixtures,
`init_db.setup_database()`'s new optional `db_name` parameter, and a
starter file per category below (not yet complete — see each file's own
module docstring for what's explicitly left as a gap). Verified: 39
tests pass in <0.1s, real `pathology.db` checksum unchanged before and
after a full run, the regeneration script runs cleanly and correctly
reports "unchanged" when nothing changed.

**Checkpoint 1 — `test_rendering.py`: DONE.** Covers
`format_decimal_display` (the exact historical "8.0 cm" bug, frozen as
an explicit regression case), `format_fragment_text`, `text_to_html`,
and `coerce_field_value`, plus DB-backed `build_context()` defaults and
live overrides (including decimal display, fragment grammar, and site
label) and `render_block()`'s single-specimen, multi-specimen, and
no-macro paths.

**Checkpoint 2 — `test_quicktype.py`: DONE.** Covers every
`validate_quick_type_config()` rejection path, lookup and measurement
consumption, `digit_width` capping, multi-block automatic rollover and
`!` skip semantics, plus the DB-backed `parse_quick_type()` entry point.
The rollover/skip regression test exposed and fixed a real parser bug:
after automatic rollover, `!` had been skipping to the block already
awaiting input rather than past it.

**Checkpoint 3 — `test_grouping.py`: DONE.** Covers `_merge_section`'s
contiguous-only merging and case-wide numbering,
`_partition_into_sections`'s group boundaries and no-group fallback,
and `compute_conclusion_addenda`'s agreement and named
conflict-drops-silently behavior. The latter explicitly preserves the
"never guess a synthesis rule" safety property.

**Checkpoint 4 — `test_consistency.py`: done** as a worked example —
formalizes the ad-hoc script this session's field-consistency
validation feature was actually verified with. Good reference for the
*shape* other checkpoints should take.

**Checkpoint 5 — `test_golden_output.py`: in progress.** Default pairs
are frozen for `dai`, `gt`, and `vb`, plus the deliberately chosen
field-consistency-compatible Appendix variation
(`false_membranes=True` + `appendicite_type=phlegmoneuse`). The
regeneration tool now supports named variations and synthetic scenarios
as well as defaults. The synthetic Gallbladder+Appendix case freezes
the multi-specimen numbering/header structure, including the absence of
single-specimen “Examen…” labels. Thyroid fixtures (`etc0`–`etc5`,
`etc_bi`) are deliberately deferred until the planned Quick
Type/preset consolidation settles; then freeze the resulting real
preset shapes rather than short-lived intermediary ones.

**Checkpoint 6 — `test_workspace_ui.py`: DONE.** `AppTest` coverage
locks the historically fragile direct `etc0`→`etc5` preset switch
(generation bump, preserved Case ID, and reset field default), Quick
Type’s atomic success/failure behavior, a real isolated pending save
plus same-preset generation reset, duplicate-case Save gating, and the
consistency-warning confirmation gate. Write paths use `mutable_db`; a
full run confirms the real `pathology.db` remains untouched.

**Checkpoint 7 — wire it into the documented workflow: not started.**
Update CLAUDE.md's "Testing discipline" so `pytest` is the first,
default step before presenting any change that fits something the
suite already covers — the ad-hoc-script-then-delete habit stays fine
for genuinely one-off exploratory checks that don't belong in the
permanent suite, but stops being the *only* option. Point CLAUDE.md at
this file. Update PROGRESS.md to track completion the same way
field-consistency validation's checkpoints were tracked.

## How this fits the mixed-model workflow

`pytest` is the same command regardless of which tool or model is
running it — a much lower bar than expecting an unfamiliar model to
correctly improvise the right verification code from scratch each time,
which is exactly where a weaker or less-familiar model is most likely
to under-test, or test the wrong thing. And when something fails, the
output — especially a golden-fixture text diff — is readable enough
that Thomas, or a more-trusted model reviewing the change, can judge
"here's exactly what changed" without needing to trust the model that
made the change in the first place.

Practical suggestion for spending model-comparison budget (OpenRouter,
Codex, etc.) on this plan specifically: start an unfamiliar model on
Checkpoints 1-3 (small, pure-function-only, zero risk of touching real
data even if something goes wrong) before trusting it with Checkpoint 6
(session-state, the most historically fragile area, the one place a
subtle mistake is most likely to reproduce a bug this project already
paid to learn). Same instinct as piloting Quick Type on `dai` alone
before extending it — applied to trusting a model instead of a feature.
