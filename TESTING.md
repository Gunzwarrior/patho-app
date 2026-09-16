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

Final independent Stage 6 acceptance: **493 passed in 302.06s** in the full
isolated suite. The reviewer also completed the browser matrix, reproduced and
rejected the late validated-Case review/Apply race, verified deletion → inverse
→ inverse-of-inverse and stale-before-Prepare protection, restored a snapshot
with canonical hash equality, and booted the isolated restored database.

## Structure

```
patho-app/
  tests/
    conftest.py              # DB-isolation fixtures — read this first
    test_rendering.py        # unit tests, rendering.py pure functions
    test_quicktype.py        # unit tests, quicktype.py (partial)
    test_consistency.py      # unit tests, consistency.py
    test_golden_output.py    # golden-fixture regression tests (partial)
    test_workspace_ui.py     # AppTest coverage for Workspace state and safety gates
    test_stage2_safety.py    # migration, lifecycle, fingerprint, and snapshot safety
    test_stage3_editing.py   # direct-edit transactions, candidate validation, and reversion
    test_operational_review.py # explicit-snapshot Stage 4 artifact review
    test_stage5_packages.py  # strict AI contract and no-write candidate review
    test_stage5_transactions.py # atomic Apply, stale guards and audited inverses
    test_stage5_ui.py        # package Editor flow and restricted report HTML
    test_stage6_schema.py    # additive lifecycle/order storage and v1/v2 compatibility
    test_stage6_candidates.py # generalized internal candidate/audit/inverse coverage
    test_stage6_lifecycle.py # lifecycle closure, archived resolution and deletion safety
    test_stage6_presets.py   # Preset Studio composition, overrides, identity, and stale guards
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
that remains a focused-assertion need when a change touches the wrapper,
rather than a reason to expand the golden fixtures into HTML snapshots.

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

Eleven fixture pairs exist today: stored defaults for `dai`, `gt`, `vb`,
`etc0`, `etc1`, `etc2`, `etc3`, `etc5`, and `etc_bi`; the deliberate
Appendix phlegmoneuse/false-membranes variation; and a synthetic
Gallbladder+Appendix multi-specimen case. All are generated from the
app's actual rendering pipeline, not typed by hand.

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

**Checkpoint 5 — `test_golden_output.py`: DONE.** Default pairs are
frozen for all nine current clinical presets (`dai`, `gt`, `vb`, `etc0`,
`etc1`, `etc2`, `etc3`, `etc5`, `etc_bi`), plus the deliberately chosen
field-consistency-compatible Appendix variation
(`false_membranes=True` + `appendicite_type=phlegmoneuse`) and a
synthetic Gallbladder+Appendix two-specimen case. The regeneration tool
supports defaults, named variations, and synthetic scenarios. `etc_bi`
was confirmed with its clinically useful Bethesda II + II default, and
the golden helper mirrors Workspace’s composed multi-specimen headers.

**Checkpoint 6 — `test_workspace_ui.py`: DONE.** `AppTest` coverage
locks the historically fragile direct `etc0`→`etc5` preset switch
(generation bump, preserved Case ID, and reset field default), Quick
Type’s atomic success/failure behavior, a real isolated pending save
plus same-preset generation reset, duplicate-case Save gating, and the
consistency-warning confirmation gate. Write paths use `mutable_db`; a
full run confirms the real `pathology.db` remains untouched.

**Checkpoint 7 — wire it into the documented workflow: DONE.**
`CLAUDE.md`'s Testing discipline makes `pytest` the first/default step,
points directly here for checkpoint scope and DB isolation, and retains
the appropriate follow-on checks (`py_compile`, direct realistic cases,
AppTest, and a manual full boot check). `PROGRESS.md` records each
checkpoint as it lands. One-off exploratory checks still remain
throwaway scripts when they do not belong in the permanent suite.

**Stage 3 — safe direct editing: DONE (browser review passed).**
`test_stage3_editing.py` covers the one-time snapshot prerequisite,
allowlists, required-column/default coercion invariants, strict Jinja and
snippet checks, same-connection candidate rendering, atomic audit rollback,
stale hashes, safe/refused reverts, pending fingerprint invalidation, shared
addenda/grouping/macro paths, and non-default checkbox/select branches.
`test_editor.py` covers locked Editor state and the forms exposed after the
initial snapshot acknowledgement, rollback-only candidate previews,
new-Snippet form reset, persistent section/entity selection, confirmation and
revert navigation, and stale two-session form rejection with visible reload.
The backend suite additionally covers sandbox escape
rejection, cleared/negative defaults, blank creation, Field-addendum Snippet
fingerprints/impact, nondefault addendum branches, table-Block refusal,
repairing invalid current content, identifiable revision details, and
create/revert/revert restoration. All write tests use `mutable_db`. Thomas's
browser review also passed the normal edit/preview flows, persistent section
navigation, two-tab stale-write refusal, validation failures, Snippet
create/revert/revert restoration, scope checks, and Workspace regressions.

**Stage 4 — operational content review: COMPLETE.**
`test_operational_review.py` covers missing/malformed snapshot
refusal, canonical snapshot hashing, byte-identical candidate generation,
every-Preset rendering through the connection-aware Editor preview path,
focused changed-report diffs, added/removed Presets, render-failure atomicity,
explicit hash-bound acceptance, and a canary operational-DB tripwire. The
tripwire blocks `database.get_db_connection()`, direct `sqlite3.connect()` to
the canary, and any initializer call without an explicit temporary path.
Candidate and accepted-artifact writes use temporary directories/files only.

**Stage 5 checkpoint 1 — no-write contract/review: COMPLETE (2026-09-05).**
`tests/test_stage5_packages.py` contains 113 tests. They cover exact JSON
envelopes/operations, UTF-8, duplicate keys, overflow/non-finite/bool-as-int,
size/depth/count limits, fixed capped feedback, native Field/override types,
normalization/no-ops, new graph ownership and positions, reserved names/context
bindings, static/orphan content, nullable defaults, duplicate Blocks,
Quick Type prefix warnings, and complete default/discrete/pending validation.
Coverage includes invalid-base repair, grouping/consistency warnings,
unchanged visible output with changed fingerprints, saved/live/candidate
separation, both locks/empty text, malformed inputs and incomplete reports.

Isolation tests enforce content-only export queries with SQLite authorization,
patient/audit canaries, explicit connection use, one in-memory backup, closed
connections on success/failure, source byte preservation and refusal of
triggered Case writes. Two-connection WAL tests exercise coherent exports and
backup under concurrent commits. Guard tests cover audited ABA, content row
identity replacement, pending arrival/edit/deletion/status transitions, and
pending-only versus content-stale retries. Apply/inverse transaction coverage
is recorded separately under Stage 5 checkpoint 2 below.

Five additional Workspace AppTests compare actual reopened title, clinical
context, micro, conclusion and rendered HTML against the saved-case helper,
including comma/blank decimal text, reordered duplicate and ad hoc instances,
wildcards, independent locks and empty manual edits. HTML comparison accounts
only for Streamlit's own outer whitespace cleanup. Existing Workspace reset,
composition, saved-content acknowledgement, validated immutability and manual
Editor/revert tests remain in the full suite.

Verified commands/results:

- Baseline `venv/bin/pytest -q`: **156 passed**.
- Focused `venv/bin/pytest -q tests/test_stage5_packages.py tests/test_workspace_ui.py`:
  **137 passed**.
- Final `venv/bin/pytest -q`: **274 passed in 61.12 s**, including Stage 3/4,
  Editor and unchanged golden fixtures.
- `py_compile` for all changed Python files and `git diff --check`: passed.
- Isolated Streamlit boot: HTTP 200 for `/`, `/workspace`, `/worklist`,
  `/editor`, `/manager`; temporary seeded DB, server stopped and temporary
  files removed afterward. Localhost sockets required sandbox escalation.
- Operational DB file SHA-256 before/after:
  `50978f4ae0cf96b5feffd4c079174d1b1cda45de4232f7dd936d59c5d526be0c`.
  No SQLite connection to that DB, golden regeneration, seed edit, or accepted
  operational artifact change occurred.

For the named `seed_data.seed_all` fixture, canonical AI-context export size
is **24,215 bytes**, including the generated v1 contract/example; snapshot-only
size is **20,571 bytes**, SHA-256
`e27caa6bd8dd6ff72c6f98cf8ff1cf0dd83f16b8bdd90d63885f706997e611df`.
A test records the complete export size. These are UTF-8 bytes, not a token
estimate or a free-plan capacity guarantee.

Compatibility is deliberate: legacy thyroid select options may include
`""`, and historical Preset decimal overrides may be strings. Tests preserve
both while new packages require native typed values and newly created select
options must be nonblank. Report display sanitization and import/apply UI stay
in checkpoint 3. Numeric-range/combinatorial exhaustive analysis, hostile
template resource quotas and real-browser visual review are not claimed.

## Stage 5 checkpoint 2 — atomic Apply and reviewed inverse

`tests/test_stage5_transactions.py` adds 52 synthetic DB tests. Its focused run
passed **52 tests in 129.90 s**. The final full isolated run passed
**330 tests in 192.35 s**, with compilation, whitespace and isolated five-route
Streamlit boot checks passing. The operational DB checksum remained unchanged
during that verification. The subsequently authorized library correction is
recorded in PROGRESS.md as revision 25; tests never open the operational DB.

Coverage includes the additive/idempotent nullable provenance migration;
initial-snapshot gating; one locked content/audit commit; exact physical audit
images and preserved IDs; internal operations without an AI envelope; complete
inverse review and original-ID restoration, including revert-of-revert and
legacy audit rows. The existing manual-revert endpoint refuses reviewed
revisions, preventing it from bypassing inverse review.

Stale tests exercise content changes, audited ABA, identity replacement, pending
arrival/edit/clinical-text/saved-HTML changes, deletion, validation/unvalidation,
changed audit data, duplicate Apply and replaced/fabricated review objects.
Real independent SQLite connections verify lock contention and exclusion of a
concurrent content writer during Apply. Failed writes after each materialization
phase, validation/hash/image checks, audit inserts and commit roll back content,
Cases/history and audit together. SQLite authorizers deny helper/trigger writes
outside the allowed phase and nested commits. A separate exact-row comparison
rejects mutations absent from the audit list, including during dry-run preparation.

Inverse tests cover later edits, relationships and template dependencies;
pending Preset/ad hoc/Field/Snippet/relationship dependencies even where fallback
rendering would succeed; validated-Preset foreign-key refusal; malformed audit
images and duplicate targets; reused original IDs; unrelated later work; and
rollback after inverse validation failure. Update-only inverses review pending
impact without acknowledging or rewriting saved cases. A stale-fingerprint
save after package Apply is refused by the existing Case backend.

The raw upload-size boundary is checked once: normalized defaults may enlarge
an accepted envelope beyond 1 MiB without making its later Apply invalid.
Tests use isolated databases; golden fixtures and accepted review artifacts are unchanged.
No import/Apply UI or HTML-presentation change is claimed; those remain in
checkpoint 3. Browser appearance still requires Thomas's review.

## Stage 5 checkpoint 3 — Editor integration and restricted presentation

`tests/test_stage5_ui.py` contains 14 checkpoint-specific tests. Together with the
existing Editor and Workspace AppTests, the focused checkpoint run passed
**56 tests in 34.68 s**. Coverage includes the persistent AI-package section and
measured private export wording; explicit upload/dry-run/review/confirm/Apply;
ordinary review persistence; upload removal and same-filename byte replacement;
failed dry run and failed/stale Apply confirmation reset; recovery gating;
complete readable operation, change, Preset, standalone, and pending-Case
presentation; fixed AI-safe feedback; successful revision/form reset; two
independent AppTest sessions; and separately prepared/confirmed/applied inverses.

The new display-only `report_presentation.restricted_report_html` boundary is
tested with active elements, event attributes, external resource URLs, unsafe
CSS, and allowed report formatting. AppTests verify its use in both Editor and
Workspace and verify that the canonical candidate HTML and saved validated
artifact remain byte-for-byte unchanged in memory/database. The five pre-existing
saved-case/Workspace parity tests now compare against this presentation string,
while report parts and canonical HTML retain their original assertions.

Final checkpoint results:

- Focused `venv/bin/pytest -q tests/test_stage5_ui.py tests/test_editor.py tests/test_workspace_ui.py`:
  **56 passed in 34.68 s**.
- Full isolated `venv/bin/pytest -q`: **351 passed in 233.02 s**.
- `py_compile` for every changed Python file and `git diff --check`: passed.
- Isolated Streamlit boot with a synthetic temporary database: HTTP 200 for
  `/`, `/workspace`, `/worklist`, `/editor`, and `/manager`; the server and
  temporary database were removed afterward.
- Operational `pathology.db` was not opened and its file SHA-256 remained
  `436259e755802b08be659fa2fac589f209b0b40603b14905261d217bfc1acb93`
  across final verification. Golden fixtures, accepted operational-review
  artifacts, and seed content were not changed.

AppTest establishes server/session behavior and emitted restricted HTML, not
real-browser layout, file-download behavior, or frontend remount behavior.
Thomas completed the checkpoint 3 browser review on 2026-09-08; it passed, and
he approved the functionality and this commit.

## Stage 6 checkpoint 1 — persistence and compatibility foundation

`test_stage6_schema.py` proves additive, idempotent migration from a
pre-Stage-6 schema with pending and validated Cases; identity/report/history
preservation; archive/display-order/frozen-Preset backfill; v1 read-only
normalisation; byte-deterministic v2 round-trip; Stage 4 artifact generation
from both versions; archived v1-package refusal; and legacy Stage 5 audit-image
readability. Focused Stage 2/4/schema coverage passed 28 tests; all 133 Stage
5 package tests and all 53 Stage 5 transaction tests passed in bounded shards;
unchanged golden/pure rendering coverage passed 89; Stage 3 Editor/backend
tests passed 26; Workspace AppTests passed 33; and Stage 5 UI AppTests passed
14. The checkpoint was manually browser-checked.

Independent Sol High review found that the original migration backfilled
validation-history Preset identity from the parent Case's current Preset. The
conservative remediation corrects fresh backfill through each history row's
own `preset_id` and adds the separate idempotent
`stage6_validation_history_preset_identity_repair_v1` marker. Its repair
criterion requires strict pre-marker timing, a resolvable non-null historical
Preset distinct from the parent Case Preset, and both history identity fields
matching the Case identity; null, unavailable, and otherwise ambiguous rows
are preserved. The remediation was independently reviewed and verified. Its
final focused Stage 6/Stage 2/Stage 4 migration selection passed 32 tests;
the complete suite now collects 380 tests. Checkpoint 1 is complete and ready
to serve as the foundation for Checkpoint 2.

## Stage 6 checkpoint 2 — generalized candidate, audit, and inverse

`test_stage6_candidates.py` adds isolated coverage for no-write candidate
review, stale and audit rollback, archive/revert/revert, deterministic intent
ordering, all configuration tables, Preset display-order restoration, and
validated Preset detachment/deletion with inverse reattachment. The focused
candidate suite passed 7 tests; Stage 6 schema plus Stage 2/4 compatibility
coverage passed 32; targeted Stage 5 package and transaction/inverse
regressions passed 21. Changed files compiled and `git diff --check` passed.
The existing AI-package review/Apply/inverse and Recent revisions manual
regression review also passed. No operational database, seed content, golden,
or accepted review artifact was changed.

Checkpoint 2 remediation extends this file to 17 candidate tests. Its
adversarial cases cover pending-dependent inverse refusal and pending impact,
semantic cleanup breakage (Preset, Quick Type, table-row and default paths),
Case-reference transition/audit-identity refusal, and inverse rollback after
materialisation failure. Focused Stage 6 candidates, Stage 2/4/schema, and
Stage 5 package/transaction compatibility selections pass.

## Stage 6 checkpoint 3 — lifecycle and archived resolution

`test_stage6_lifecycle.py` proves upward archive closure, reverse restore
prerequisites, persisted-pending deletion blockers, active new-work exclusion,
archived pending-case reconstruction (including Snippets), validated Preset
detachment/Worklist fallback/inverse, refused reconstruction on return to
pending after permanent deletion, and the `display_order` split between legacy
default composition and explicit saved composition. Workspace AppTests prove
that an archived Preset appears only while reopening its own pending Case and
is removed by the next fresh-case reset; an active-Preset pending Case still
resolves an archived ad-hoc/cross-Preset Block; and a validated Case detached
by permanent Preset deletion still opens as its frozen report while its
attempted return to pending is refused without changing the artifact. Candidate
tests also require every active standalone Block to have active Field/Snippet
dependencies. Lifecycle tests freeze explicit-composition link/unlink impact,
executable-plan summary detachments, and the pending-Case deletion-eligibility
Apply race. Fingerprint-migration tests permit automatic upgrade only for
provably safe legacy no-instance drafts; they preserve already-stale drafts,
leave ambiguous explicit drafts unacknowledged, fail closed if the superseded
v1 baseline ran, and never touch validated Cases/history.

Focused Stage 2 safety/lifecycle coverage passed **29 tests in 5.94 s**. The
full isolated suite passed **414 tests in 259.52 s**. Changed Python files compiled
and `git diff --check` passed. Tests use only temporary databases; no seed
content, golden fixture, operational database, or accepted review artifact was
changed. An isolated temporary-database Streamlit boot returned HTTP 200 for
`/`, `/workspace`, `/worklist`, `/editor`, and `/manager`, then was removed.

## Stage 6 checkpoint 5 — Block Studio and Block Fields

`test_stage6_blocks.py` covers frozen-review Create/Edit/Duplicate workflows,
immutable keys, non-table-only authoring, exact `Block_Fields` inheritance and
override storage, Field add/remove/reorder, template-variable coordination,
pending-Case impact, lifecycle, duplication without Preset relationships, and
stale review/Apply refusal. Adversarial regressions bind the complete physical
Block/binding image, every desired Field endpoint, and copied consistency
rules, including natural-key-identical delete/recreate ABA races before review
and Apply.

Focused CP5/UI/candidate/lifecycle/editor selections and manual browser checks
passed. Final independent review closed all blockers, independently verified
Create/Edit/Duplicate/Apply ABA protection, and approved the checkpoint. The
full isolated suite passed **459 tests**; relevant Python files compiled and
diff checks passed.

## Stage 6 checkpoint 6 — Preset Studio and `Preset_Blocks`

`test_stage6_presets.py` covers frozen-review Preset create/edit/duplicate and
lifecycle flows, immutable short-code and Block-instance identity, duplicate
Block instances and per-instance overrides, pending composition ordering,
Quick Type preservation/cleanup, table-bearing refusal, selector/two-tab
staleness, and review warnings. Adversarial cases bind complete source and
Field endpoint images, including physical-ID ABA replacement, retained
draft-only instance baselines across add/remove operations, and concurrent
Field label/default changes.

Final independent acceptance passed the full isolated suite (**476 passed**)
and focused verification (**76 passed**). Independent retesting confirmed the
endpoint ABA/rebasing failure is fixed. Compilation and `git diff --check`
passed. Tests use isolated databases; CP7 was not started.

## Stage 6 checkpoint 7 — consolidation and acceptance preparation

CP7 removes the now-dead Editor one-row direct-save/preview helpers rather
than leaving a dormant second writer, and makes the shared review renderer's
summary/hash language neutral for Content Studio, imported AI packages, and
reviewed inverses. Final-review remediation adds full physical-source
assertions for Field, Snippet, and group-label drafts; direct-writer regression
coverage now also rejects `revert_revision()` from Editor; reference changes
run detach → content images → reattach for inverse and inverse-of-inverse; and
snapshot restore preserves detached validated artifacts without attempting
live reconstruction. The permanent-deletion warning about a future
Return-to-pending refusal is now graph-level: it compares every validated
Case's strict reconstruction result before and after the candidate, and warns
only for new losses. This covers Block deletion and inverse-of-inverse as well
as Preset detachment. Its signed result is protected by the shared local guard,
which now digests the complete validated-Case set and full rows supplied to
strict reconstruction. A Case arriving or changing after review therefore
makes ordinary and inverse Apply stale; the digest remains session-local and
does not place Case data in review provenance or audit.

Current focused results: **95 passed** across generalized candidates, lifecycle,
and Stage 5 transactions, including **10 passed** in the dedicated validated-
Case warning/race selection; the two focused Stage 5 local-guard/retry tests
also pass. A broader Stage 2 safety, Stage 6 schema/UI, and Editor selection
passed **60 tests**. The adversarial coverage includes late validated-Case arrival,
changes to `structured_input`, `clinical_info`, `preset_id`, identity and
status, a destructive reviewed inverse, unchanged-state Apply, and true→false,
false→true, and false→false warning semantics. No golden fixtures were
regenerated; tests used temporary databases only. Changed Python files compile
and `git diff --check` passes.

Final independent acceptance supersedes the earlier constrained-environment
timeouts: the full isolated suite passed **493 tests in 302.06s**; the browser
matrix completed; the late validated-Case review/Apply race was rejected as
stale; validated deletion → inverse → inverse-of-inverse and stale-before-
Prepare protections passed; recovery restore had canonical hash equality; and
the isolated restored database booted successfully. The final reviewer found
no implementation or data-integrity defect.

### CP7 recovery-snapshot drill (isolated database only)

The Editor download is a canonical content snapshot, restored by the existing
`content_snapshot.restore_content_snapshot()` service; it is not an AI package.
Do not point this drill at `pathology.db`. With a downloaded snapshot at
`/absolute/path/pathopilot-content-snapshot.json`, create a disposable target,
restore it, and compare canonical hashes:

```bash
venv/bin/python init_db.py --db /tmp/pathopilot-recovery-drill.db --rebuild
venv/bin/python -c 'import json, sys, content_snapshot; snapshot = json.load(open(sys.argv[1], encoding="utf-8")); ok, error = content_snapshot.restore_content_snapshot(snapshot, db_name=sys.argv[2]); print("restore:", "OK" if ok else error); raise SystemExit(0 if ok else 1)' /absolute/path/pathopilot-content-snapshot.json /tmp/pathopilot-recovery-drill.db
venv/bin/python -c 'import json, sys, content_snapshot; snapshot = content_snapshot.normalize_content_snapshot(json.load(open(sys.argv[1], encoding="utf-8"))); restored = content_snapshot.export_content_snapshot(sys.argv[2]); print("snapshot hashes match:", content_snapshot.content_snapshot_hash(snapshot) == content_snapshot.content_snapshot_hash(restored)); raise SystemExit(0 if content_snapshot.content_snapshot_hash(snapshot) == content_snapshot.content_snapshot_hash(restored) else 1)' /absolute/path/pathopilot-content-snapshot.json /tmp/pathopilot-recovery-drill.db
PATHOPILOT_DB_NAME=/tmp/pathopilot-recovery-drill.db venv/bin/streamlit run app.py
```

Inspect the five routes and representative reports against that isolated copy,
then remove only `/tmp/pathopilot-recovery-drill.db` when finished. Restore
validates the candidate on a temporary SQLite backup, preserves saved Cases,
and commits one content-only transaction with a `snapshot_restore` revision.
Pending Cases must still reconstruct with an identical content fingerprint;
validated frozen Cases, including intentionally detached Presets, are preserved
exactly without live reconstruction.

### Final contract hardening, regression, and real-model acceptance

The final checkpoint 1 authoring-contract refinement is committed as `de8ccce`;
checkpoint 2 safe, copyable diagnostics are committed as `3b93dea`. An
independent Sol review identified a blocking authoring-rule clarification,
remediated and committed as `717fda6` (`Clarify v1 package authoring rules`).
No deferred non-blocking review suggestions were implemented.

On 2026-09-10, the current isolated test selection passed all **370 collected
tests**, run in bounded terminal shards; the focused Stage 5
package/transaction/Editor/Workspace selection passed **243 tests**. Relevant
Python files compiled and `git diff --check` passed. An isolated temporary
seeded database returned HTTP 200 for `/`, `/workspace`, `/worklist`,
`/editor`, and `/manager`, then the server and database were removed. The
operational database was not opened through SQLite; its raw file SHA-256 was
unchanged before and after at
`5fa1abcc1a69d623108291e9e5839b85767a9ca99f89d4b2a2e316584d89477e`.
Golden fixtures, accepted operational-review artifacts, and seed content were
not changed.

Independent external-model acceptance used the same fresh
`pathopilot-ai-context-v1` export and no reference answer or repository access.
For a fully specified request, Sonnet 5, ChatGPT High, and Gemini each returned
a valid package on its first response, and each package passed PathoPilot dry
run. The files were not byte-identical and measured approximately 1.0–1.4 kB,
but all produced the same intended normalized/rendered result. For an
intentionally incomplete Block request, all three declined to generate a
package and explained missing clinical information to varying degrees; none
invented clinical wording, emitted placeholders, or returned an empty package.
For the corrective-feedback loop, PathoPilot rejected a deliberately malformed
relationship key with safe actionable `link_key` feedback. Given the original
package, export, and copied feedback, Sonnet 5 produced a corrected replacement
on its first attempt; it passed dry run.

The independent checkpoint 1–2 review added four regressions. They cover Jinja
literal-concatenation Snippet calls across pending fingerprints, impact and
inverse refusal; nullable text defaults taking the same branch in candidate
validation as a fresh Workspace widget; and native decimal zero surviving a
package Apply and fresh Workspace selection. The focused package/transaction/
Workspace run passed 201 tests. Stage 3/4 plus the defect cases passed 26 tests,
and the final isolated suite passed **338 tests in 212.04 s**. Changed Python
files compiled, `git diff --check` passed, and an isolated Streamlit boot
returned HTTP 200 for all five routes.

## Browser defect regressions — 2026-09-06

Four new Workspace AppTests cover selecting the second identical specimen for a
wildcard note, reordering it, saving/reopening and removing its target; and
thyroid liquid volume blank/zero/comma-decimal through save/reopen. A blank
volume renders `Liquide clair.` with no unit or inferred explanation. The
seed-template correction changes the documented AI export size to 24,263 bytes
(snapshot 20,619); existing default golden outputs remain unchanged. The
focused Workspace/package/golden run passed **164 tests in 56.43 s**.

Thomas confirmed the previous browser checks for Appendix/Gallbladder, decimal
entry, duplicate/grouped reports, manual lock, Editor previews and frozen
validated reports. He subsequently confirmed correct wildcard duplicate
targeting, save/reopen and deletion. The authorized thyroid correction was
applied to the current library as revision 25 through the audited Editor save;
reseeding the operational DB is never the update mechanism.

Four additional AppTests reproduce the first-action-after-reopen composition
reset: the disappearing notice shifted the unkeyed expander's render-tree
position. They failed before the fix and passed afterward (2.33 s), covering
move up/down, removal and addition over repeated reopen cycles. Notices now
use one persistent container. AppTest verifies stable layout positions, not
browser-held open state. Thomas subsequently browser-confirmed that the
section stays open and the live thyroid correction works.
The final full isolated suite passed **334 tests in 193.58 s**; compilation,
whitespace checks and isolated five-route app boot passed. The operational DB
checksum stayed unchanged at its post-correction value during verification.

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

## Stage 7 CP1 focused verification — 2026-09-14

The final remediation-focused runs passed **21** CP1 configuration tests,
**15** consistency tests, **22** Quick Type tests, **28** Stage 6 Block tests,
and **30** Stage 6 candidate tests. Coverage includes the named unique
token-position migration (including duplicate refusal and repeat migration),
candidate-connection parsing isolation, minimal token/rule composition and
physical identity, owner/endpoint/configuration ABA, stale review, typed
decimal rule canonicalization, archived/table-owner refusal, exact proven
Block-duplicate rule copying, and materialization/audit rollback.
`python3 -m py_compile database.py quicktype.py consistency.py
content_studio.py content_changes.py` and `git diff --check` passed. The full
isolated suite remains the manual command `venv/bin/python -m pytest -q`.

## Stage 7 CP3 acceptance — 2026-09-15

CP3 consistency-rule authoring is accepted. Independent closure verified the
complete isolated suite at **548 passed in 348.25s**, `git diff --check`, and
`tests/test_stage7_consistency_ui.py` at **9/9 passed**. That focused file
covers typed rule CRUD; candidate-only matching/nonmatching probes; default
and pending warning-only deltas with stable fingerprints; order-insensitive
warning membership; stale owner/endpoint and review-time race handling;
no-direct-writer AppTest behavior; and copied-rule Block duplication through
Apply, inverse Apply, and inverse-of-inverse Apply. CP3 focused/configuration/
lifecycle verification remains safe to run with:

```bash
venv/bin/python -m pytest -q \
  tests/test_stage7_configuration.py tests/test_stage7_quicktype_ui.py \
  tests/test_stage7_consistency_ui.py tests/test_consistency.py \
  tests/test_stage6_blocks.py tests/test_stage6_lifecycle.py
```

CP4 is not included in this acceptance boundary.

## Stage 7 CP4 acceptance — 2026-09-15

The current CP4 preview tests passed **18/18**. They cover equivalent BOM/CRLF CSV
and TSV normalization, quoting/outer trimming, invalid UTF-8/NUL/malformed
quotes/columns/blank cells/row limits, duplicate and existing Case-ID refusal,
one-transaction materialization, canonical immutable issued-review storage
(including direct/nested and `dict.__ior__` mutation attempts), authoritative
`render_saved_case` HTML equality, review staleness bindings, raw-Quick-Type
privacy in repr/structured input, and SQL/state plus SQLite-authorizer proof
that preparing a review writes zero Case/content rows. It also covers complete
active Quick Type endpoint-graph refusal for unavailable Block instances,
Fields, and archived Fields; duplicate fired warning-message multiplicity; and
the 140 KiB cell/exact 1 MiB/250-row input boundaries. The isolated Streamlit
page booted and prepared a decoded preview without exceptions; its only action
is **Prepare decoded preview**.

The bounded CP4 browser remediation also verifies that the invalid-Quick-Type
and existing-ID paths each render one complete safe Streamlit error, rather
than iterating over characters of an incorrectly shaped error value.

Focused remediation regressions passed: `tests/test_stage7_bulk_preview.py`
(**18 passed**), Quick Type/consistency/Workspace (**94 passed**), and Stage 7
configuration (**21 passed**). `python -m py_compile` and `git diff --check`
also passed. Independent Sol High closure verified the complete isolated suite:
**566 passed in 352.15s**. No operational database, schema change, seed-content
change, golden update, or CP5 work was performed. CP4 is accepted.

## Stage 7 CP5 acceptance — 2026-09-16

CP5 atomic pending-Case creation is accepted. Final independent closure
verified the complete isolated suite at **588 passed in 349.53s**, including
the final browser stale-consent check. Independent Sol High review closed both
CP5 blockers with no new blocker; `py_compile` and `git diff --check` passed.

Focused CP5 coverage includes additive migration from a genuine pre-CP5 Case
schema (historic `NULL` linkage and repeat migration); transaction neutrality
and caller rollback for the shared Case primitive; reviewed-target races across
two connections and `BEGIN IMMEDIATE` lock serialization; rollback for audit,
rebuild, render, reconstruction, serialization, final-comparison, and Case
insertion faults; review-specific confirmation and signed warning
acknowledgement; Case reopen/widget hydration for text, number, decimal,
select, and checkbox representations; and proof that batch provenance remains
outside content snapshots, AI context, and operational-review artifacts.

The final browser matrix covered warning-free Apply → Worklist → Workspace
reopen/edit/individual validation, warning acknowledgement, and stale/replaced
review consent. CP6 and CP7 were not started.

## Stage 7 CP6 acceptance and cleanup — 2026-09-16

CP6 was successfully applied and browser-verified. The isolated full suite
before cleanup passed **594 in 357.56s**. Browser sanity checks also passed.
The temporary bounded migration
assistant was then removed from the Editor and Content Studio runtime; no
replacement state, flag, or migration-specific UI remains.

Focused cleanup verification covers the applied-content routing contract and
report equality against each legacy thyroid variant; unchanged `etc_bi`;
untouched pending and validated legacy Cases; archived pending reconstruction/
fingerprint stability; generic rollback/stale review; generic inverse/
re-inverse and pending-Case inverse blockers; and absence of CP6 runtime
surface. Its test-only fixture uses the permanent generic Content Studio
operation grammar to model the applied rows, rather than retaining a product
migration planner.

Focused cleanup checks passed **66 in 28.84s** across Stage 7 configuration,
CP6 compatibility, Quick Type parser, and Quick Type Studio UI tests.
`venv/bin/python -m py_compile` and `git diff --check` passed; the full suite
was not rerun after this runtime-only cleanup.

## Stage 7 CP7 consolidation — 2026-09-16

CP7 retained the frozen CP1–CP6 architecture: one reviewed Content Studio
authoring path for Quick Type and consistency rules; the transaction-neutral
shared Case persistence primitive; and one reviewed, atomic, pending-only bulk
workflow. The only production cleanup removed the three unused bulk-service
compatibility aliases (`prepare_bulk_preview`, `prepare_batch_review`, and
`apply_batch_review`); callers use `prepare_bulk_review` and
`apply_bulk_review` exclusively. No schema, migration, CP6 runtime helper, or
Stage 5 package-v1 behavior was added or changed.

`tests/test_stage7_consolidation.py` locks the canonical bulk boundary and
verifies the Editor and Bulk Intake UI modules contain no SQL mutation path.
The CP5 recovery regression also restores both v1 and v2 content snapshots
around an ordinary historic Case and a batch-linked Case, proving that Cases
and `Case_Batch_Imports` survive content-only recovery unchanged.
Final acceptance passed the focused Stage 7 suite (**96 passed**) and the full
isolated suite (**597 passed in 351.87s**). Browser acceptance, `py_compile`,
and `git diff --check` also passed. Independent Sol review found no functional
or safety violation; stale completion documentation was the sole remaining
blocker.
