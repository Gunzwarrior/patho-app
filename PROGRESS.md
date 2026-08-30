# PROGRESS.md — current state (verify against real `git log` first)

**This document can go stale the moment a commit happens without it being
updated.** Neither Claude nor this file has live access to the actual repo
— before relying on anything below, ask the person to paste
`git log --oneline -20` (or however far back is useful) and reconcile.
Where this doc says "committed," that's based on the conversation's own
memory of agreeing on a commit message, not a verified fact.

This round also mixed tools for the first time: some of what's below was
built in a Claude.ai chat, some in Cline against the Claude API directly,
some the person did by hand. Noted per item, since it's new territory for
how this file gets kept honest — see near the bottom for the open
question that raises.

## Where we are right now (read this first)

**Reconciled against a real `git log --oneline -20`, pasted by Thomas
this session:**
```
d4a1a96 (HEAD -> main) Add field-consistency validation, pilot on Appendix
460034b (origin/main) Replace Quick Type's form+button with a bare Enter-driven text_input
8cea2a7 Wire Quick Type into the Workspace UI
d71a2c7 Add Quick Type parser (dai end-to-end)
25c690a Add Quick Type schema and config validator, pilot on dai
8195b52 Switch decimal fields to a parsed text_input, not number_input
06d5605 Allow blank context fields; separate conclusion label from title fragment
9405865 Wire multi-specimen context composition into headers and conclusion
ada771e Add 2-nodule Thyroid Cytology preset (etc_bi)
86d603b Fix widget/save key collision when a Preset reuses one Block twice
51fb9d1 Add Clinical Context section with Title/Context composition
```
That's the whole history (11 commits) — `git log -20` returned no more.

- **Clinical Context/Title/Conclusion composition IS committed** —
  `51fb9d1`, `86d603b`, `9405865`, `06d5605` account for it. The
  previous version of this paragraph said "not yet committed" and cited
  three different hashes (`1601015`, `32f0e3f`, `5988fe4`) that don't
  appear anywhere in this log — that was stale/wrong, not a real
  discrepancy; corrected here.
- **Field-consistency validation is committed**: `d4a1a96`.
- **`0b498c4` is resolved, not a mystery**: confirmed directly by
  Thomas — a tiny manual fix he made himself outside a documented
  session. It doesn't appear in the current log at all (likely folded
  into history differently since, or the log this file's earlier claim
  was based on was itself already stale) — this is a solo local repo
  with no shared remote, so history moving around costs nothing per
  CLAUDE.md's own convention. Nothing further to chase here.
- **`origin/main` is one commit behind local `main`** (`460034b` vs.
  `d4a1a96`) — `d4a1a96` hasn't been pushed yet. Not urgent on a solo
  repo, just noted for accuracy.

**Quick Type and field-consistency validation are both fully done and
confirmed working in the person's real browser** — see "Fixed and
verified this round" for both build records, "Quick Type — settled
design" for the grammar/reasoning, and "Field-consistency validation —
design question (RESOLVED)" for that design's reasoning.

**A persistent automated test suite is now mid-task** — full plan and
per-checkpoint status live in the new **TESTING.md**, tracked briefly in
"Currently mid-task" below. Scaffolding is built and verified (39 tests
passing, real `pathology.db` confirmed untouched); most content
checkpoints are still open and are meant to be picked up piecemeal,
possibly by different sessions or different models — see TESTING.md's
"How this fits the mixed-model workflow."

## Currently mid-task

**Persistent test suite — plan written, scaffolding built and verified,
content checkpoints mostly still open.** Full plan, philosophy, and
per-checkpoint detail now live in **TESTING.md**, not duplicated here —
this section just tracks status.

Decided this session: a persistent `pytest` suite is a prerequisite/
companion to Editor UI, since Editor UI removes the Claude-tested
round-trip that today's ad-hoc, thrown-away testing depends on as its
safety net, and since a mixed-model workflow means a change might come
from a tool with no track record on this codebase. TESTING.md's own
"How this fits the mixed-model workflow" section has the reasoning on
which checkpoints are safe to hand an unfamiliar model first.

**Checkpoint status** (see TESTING.md for what each one covers and why):
- [x] 0. Scaffolding — `tests/conftest.py`'s `db`/`mutable_db`
      fixtures, `init_db.setup_database()`'s new optional `db_name`
      parameter (additive, `python3 init_db.py` unaffected). Verified:
      39 tests pass in <0.1s; real `pathology.db`'s checksum confirmed
      unchanged before/after a full run, not just assumed.
- [x] 1. `test_rendering.py` — mostly done (pure functions covered;
      `build_context`/`render_block` themselves not yet).
- [ ] 2. `test_quicktype.py` — partial (validator + pure parsing
      functions covered; `parse_tokens`'s token-consumption paths and
      the DB-backed `parse_quick_type` entry point are not).
- [ ] 3. `test_grouping.py` — not started.
- [x] 4. `test_consistency.py` — done, formalizes this session's
      ad-hoc verification script.
- [ ] 5. `test_golden_output.py` — one fixture pair done (`dai` at
      defaults, confirmed byte-exact against the real rendering
      pipeline), eight presets plus a few deliberate variations still
      open. Open question for Thomas: does he still have the original
      docx samples other case types were verified against, to cross-
      check new fixtures before freezing them?
- [ ] 6. `test_workspace_ui.py` — not started. Save for last / most-
      trusted model: this is where the real historical fragility lives
      (the widget-key bug alone took three occurrences to become a
      named convention).
- [ ] 7. Wire into the documented workflow — **partially done**:
      CLAUDE.md's "Testing discipline" and its Editor UI note both now
      point at TESTING.md and list `pytest` as the first step. Not yet
      done: nothing to add here until checkpoints 2-6 exist to be
      pointed at as "the suite."

When a task starts that might outlast a session, note the plan and
checkpoints here as it goes, not just at the end. Clear this section
once the task is genuinely done — not yet, most content checkpoints are
still open and are meant to be picked up piecemeal, possibly by
different sessions or different models.

## Fixed and verified this round

**Field-consistency validation — fully built and confirmed by the
person in his own browser, not just sandbox-verified.** Design
discussion and the "why not dynamic option-restriction" reasoning are
in "Field-consistency validation — design question (RESOLVED)" below,
not repeated here — this is the build record. **One real-browser bug
along the way**: his first test showed no warning at all — turned out
to be an unsaved edit to `workspace.py` in his editor (never hit
Ctrl+S), not a code defect; worth remembering as a first thing to check
next time a change "does nothing" despite `init_db.py` and the sandbox
both looking right.

- **Schema** (`init_db.py`): new `Field_Consistency_Rules` table —
  `block_id`, a symmetric `field_a_key`/`field_a_values` +
  `field_b_key`/`field_b_values` pair (JSON value lists, no
  directionality between the two sides), and a `message`. Scoped to one
  Block for v1 — a cross-block/case-level version isn't designed and
  shouldn't be assumed needed until a real case for it shows up.
- **`consistency.py`** (new module, parallel to `grouping.py`/
  `quicktype.py`): `check_block(block, overrides)` resolves the block's
  current values via `rendering.build_context()` and returns every fired
  rule's message (`[]` = consistent, the common case);
  `validate_consistency_rules()` is the seed-time config sanity check
  (unknown field key, a rule comparing a field to itself, an empty value
  list), same role `quicktype.validate_quick_type_config()` plays for
  Quick Type. `database.get_consistency_rules(block_id)` added alongside.
- **`seed_data.py` pilot rule** — Appendix only (same "pilot on one case
  type" approach Quick Type used on `dai`): `false_membranes=True`
  flagged against `appendicite_type` in `{endo, suppuree, intervalle}`.
  Message wording confirmed fine as a first pass by the person;
  explicitly **not a priority** to revisit further right now.
- **`workspace.py` wiring**: checked once per block, right where
  `render_block()` already runs, so the check sees a block's fully
  resolved values regardless of whether they came from a widget, a
  Preset default, or Quick Type — confirmed directly by sandbox testing
  all three paths independently, including a Quick-Type-set field
  combined with a widget-set field firing correctly together. One
  consolidated warning banner + a single "proceed anyway" checkbox
  before the Save row, reusing the duplicate-case-number guard's exact
  pattern; both Save buttons gated on `overwrite_confirmed and
  consistency_confirmed`. Confirmed in his own browser: banner shows in
  the right spot, fires exactly when it should, and the checkbox lets
  him save anyway rather than hard-blocking.

**"Quick Type" — fully built and confirmed by the person in his own
browser, not just sandbox-verified.** Fast-typed shortcut codes (e.g.
`dai37`) that resolve to a preset plus field overrides in one move. Full
grammar/token/ambiguity reasoning lives in "Quick Type — settled design"
below, not repeated here — this is the build record.

- **Schema + validator** (`init_db.py`, new `quicktype.py`): new
  `Quick_Type_Tokens` table, `validate_quick_type_config()` enforcing the
  digit-adjacency rule that makes concatenated (no-delimiter) tokens
  unambiguous, plus a `block_sort_order`-contiguity check added once the
  parser actually depended on it. Piloted on `dai` only — extending to
  Gallbladder/Thyroid is explicit follow-up work, not done yet.
- **Parser** (`quicktype.py`): `find_preset_by_prefix()` (longest-match
  against `Presets.short_code`), `parse_tokens()` (lookup/measurement
  consumption, `!` for early block-skip, automatic rollover into the next
  block with no `!` needed otherwise), `parse_quick_type()` convenience
  wrapper. A synthetic 2-block fixture confirmed block-transition logic
  works even though no real preset spans 2 blocks yet.
- **UI** (`workspace.py`): a `text_input` next to the Preset dropdown,
  its own one-shot apply flag (`_do_quick_type_apply`, not reused from
  `_do_preset_switch_reset`), atomic apply-or-reject with a specific
  error on failure. **Revised once from real-browser feedback**: the
  first version wrapped this in `st.form` with a submit button, mirroring
  the existing reopen box — the button turned out to be vestigial, since
  Enter alone was always how it got used. Replaced with a bare
  `on_change` callback (no button at all): simpler (one rerun instead of
  two) and better-behaved on failure (the mistyped code stays in the box
  for in-place correction, instead of a form's `clear_on_submit` wiping
  it unconditionally). Worth remembering for future text-entry-plus-
  action UI in this app — check whether a form is actually needed
  (a *separate* button to race against a text_input's blur-commit) before
  reaching for one by default.


done, confirmed by the person in his own browser (not just
sandbox-verified).** Multi-session piece of work, largest change this
project has had. Full design reasoning below for anyone extending this
pattern to a future case type (e.g. breast).

- **Schema**: `Blocks.context_template` / `title_fragment_template` /
  `conclusion_label_template` (all nullable — a Block that sets none of
  them is completely unaffected), `Presets.default_title`,
  `Block_Fields.context_section`. All additive, no existing behavior
  changed by the columns' existence alone.
- **Mechanism**: a Field's value can now render into up to four places
  — its own Block's micro/conclusion text (already existed), a
  case/specimen-level "Clinical Context" sentence, a short Title
  fragment, and (for 2+ specimens) a short conclusion-prefix label.
  Deliberately **three separate templates**, not one reused: Title wants
  the bare site ("Cytologie thyroïdienne lobaire gauche"), the
  conclusion prefix wants "Nodule" prepended ("Nodule lobaire gauche :
  Matériel..."), and the context sentence wants the full composed
  sentence — the person read a reused bare fragment in the conclusion as
  "sloppy," confirming these genuinely need different wording, not one
  fragment reused three ways. `rendering.render_context_fragments()`
  renders all three through the same `build_context()`.
- **Where composed text goes depends on total_specimens** — the same
  conditional this codebase already used twice (macro/micro headers,
  conclusion numbering), now a third and fourth time: 1 specimen →
  auto-composed Clinical Context box + Title; 2+ specimens → composed
  text becomes that specimen's own numbered header instead, top Context
  box stays 100% free text, Title stays static. All fields can be left
  genuinely blank (selects default to `""`, the decimal defaults to
  `NULL`) rather than a guessed value — a silently-guessed default
  (originally "lobaire droit") was itself exactly the kind of unnoticed
  wrong-value risk this feature exists to prevent. Every template is
  wrapped in `{% if %}` guards so any subset of blank fields degrades
  gracefully (no double spaces, no literal "None", empty renders as
  nothing rather than a half-sentence).
- **UI**: new "📝 Contexte clinique" section, right after preset
  selection, before Global Modifiers/Medical Variables. One shared lock
  toggle governs both the Context box and Title (not two toggles) —
  auto-synced while unlocked, frozen/hand-editable while locked,
  structurally identical to Master Lock. `workspace.py`'s old inline
  per-field-type widget dispatch was extracted into a shared
  `render_field_widget()` helper.
- **Decimal fields are a plain parsed `text_input`, not
  `st.number_input`** — found the hard way: `number_input` has no
  reliable way to represent "cleared back to blank" once a field has
  held a real value (confirmed against a real widget interaction, not
  just docs — it reverts to a floor value, min_value or not). A first
  attempt just removed `min_value`; didn't fix it and gave up the
  negative-number guard for nothing, so it was discarded rather than
  committed. The `text_input` replacement parses manually (tolerates
  `,` as a decimal separator, rejects negative/non-numeric input with a
  visible caption rather than passing a garbled string through
  silently) and restores the negative-number guard on top of fixing the
  actual bug. Applies to every decimal field, not just the optional
  ones (shared dispatch) — loses the +/- stepper buttons everywhere,
  trade-off the person explicitly accepted.
- **Real bug found and fixed along the way, not anticipated in the
  original plan**: the new `etc_bi` two-nodule preset was the *first*
  preset ever to reuse the same Block twice, and doing so crashed the
  page (`StreamlitDuplicateElementKey`) — every widget key,
  `structured_input`'s saved-blocks dict, and the reopen-restoration
  lookup were keyed off `block_id`/`block["key"]` alone, silently wrong
  once two instances share one. Beyond the crash, this would have
  **silently lost data on save** (second instance's values overwriting
  the first's). Fixed by keying everything off `(block_id, sort_order)`
  instead. Verified with a real save → DB → reopen round trip, not just
  widget assertions.
- **Preset-switch preservation reversed for clinical info specifically**
  (Case ID's is unchanged) — clinical info can now be auto-composed from
  preset-specific fields, so carrying it across an unrelated preset
  switch would carry stale wording forward.
- Thyroid Cytology got 3 new Fields (`nodule_site` — a real site
  selector including "isthmique", not a strict laterality, confirmed
  against `CR_Sample.docx`'s second nodule; `nodule_size_mm`;
  `nodule_eutirads`) and a new 2-nodule fast-access preset (`etc_bi`,
  also doubles as a real preset for genuine 2-nodule cases).
- **Bonus fix, found while building this**: the "8.0 cm" decimal-display
  bug (`coerce_field_value` renders a Python float without stripping a
  trailing `.0`) was already live in production on Gallbladder's
  `specimen_size_cm`, Appendix's `appendix_size_cm`, and Thyroid's
  `liquid_volume_ml` — not just the new fields. Fixed generally
  (`format_decimal_display()` + a `{key}_display` companion in
  `build_context()`) and applied to all three existing spots too,
  shipped as its own atomic commit separate from the Thyroid content.
- **⚠️ Documentation-loss incident, now fixed**: early in this round,
  before `seed_data.py`/`workspace.py` were available as real files,
  Claude hand-retyped `grouping.py`/`rendering.py`/`database.py`/
  `init_db.py` from text shown earlier in the conversation instead of
  the actual files, silently dropping most of their docstrings/comments.
  Two flawed deliverables (`init_db.py`, `rendering.py`) were applied to
  the real repo before this was caught — caught during an unrelated
  sandbox reset, fixed by rebuilding both from the true originals with
  only the intended diffs layered on top (verified line-by-line, zero
  unexplained loss), corrected versions redelivered and reapplied.
  `seed_data.py` was never affected. **Process fix, not a workflow
  change on the person's end**: always pull real files from disk via
  `cp`/`view`, never retype from memory of earlier context, even when a
  file was already shown inline once.
- Verified overall: `AppTest` covering every UI path touched (blank
  fields, lock/unlock cycles, multi-specimen, non-composing presets),
  byte-exact match against both real nodules in `CR_Sample.docx`, a
  genuine save→DB→reopen round trip, full regression across all 9
  presets after every change, and — for everything UI-facing — the
  person's own confirmation in his real browser, not just sandbox
  testing.

- **Gallbladder cholesterolosis blank-line bug** (Claude.ai chat). Root
  cause: the separator after `{% if cholesterolosis %}...{% endif %}`
  sat outside the conditional, so it rendered unconditionally even when
  the sentence inside didn't. Fixed in `seed_data.py` by moving the
  separator inside/adjacent-only-when-true. Audited all four case types
  for the same pattern — no other instance existed (every other
  conditional in these templates has no empty branch to begin with).
  Verified via direct function calls: all 64 combinations of
  Gallbladder's `cholesterolosis` × `lithiasis` × `inflammation_type` ×
  `specimen_state`.

- Rejected parsing/extracting structured facts out of free-text clinical
  info (an NLP-guessing approach) — doesn't fit this engine's existing
  "never guess a synthesis rule, fail loud not silently" philosophy
  (see `Fields.conclusion_addendum_template`'s conflict-handling, and
  `snippet_lookup`'s missing-shortcut behavior). Facts should be entered
  once, as real Fields, and *rendered* into multiple places — never
  independently typed twice and reconciled after the fact.
- Landed on: two new template columns on `Blocks`, parallel to the
  existing `macro_template`/`micro_template`/`conclusion_template`
  family — `context_template` (a full composed sentence, e.g. "nodule
  lobaire gauche de 20 mm EUTIRADS 4") and `title_fragment_template` (a
  short fragment, e.g. "lobaire gauche"). Both nullable/opt-in, same as
  `macro_template` — a block that doesn't set them behaves exactly as
  today. No new Field-level flag, no new "case-level field" concept, no
  new Block *type* (a "clinical-context block" that renders nothing in
  the conclusion was considered and rejected — breaks the Block
  vocabulary in CLAUDE.md, which is explicit that a Block is one
  complete diagnostic shape, not a bag of unrelated fields).
- Where the rendered text goes is conditional on `total_specimens` — the
  same conditional this codebase already has twice (macro/micro headers,
  conclusion numbering), now a third time:
  - **1 specimen**: `context_template`'s output auto-composes the top
    "Renseignements cliniques" box; `title_fragment_template`'s output
    appends to the preset's new `Presets.default_title`. Both need a
    Master-Lock-style lock toggle (auto-synced while unlocked, frozen and
    hand-editable while locked) so genuinely one-off additions ("Bilan
    pré IRA-thérapie") don't require abandoning the auto-composition.
  - **2+ specimens**: `context_template`'s output replaces that
    specimen's own numbered header (`format_micro_plain`'s existing "N.
    Name" — literally just pass the composed text instead of
    `block["name"]` from `workspace.py`'s assembly loop; nothing inside
    `rendering.format_micro_plain` itself needs to change). Title stays
    at the static default (one laterality can't represent two different
    nodules) and the top clinical-info box stays 100% free text, exactly
    like today — confirmed against a real two-nodule sample
    (`CR_Sample.docx`, case 2: top box just says "goitre," each nodule's
    header carries its own composed sentence, conclusion carries a short
    per-nodule prefix via an ordinary `{{field}}` reference already
    supported today, no new mechanism needed there).
  - Blocks with neither template set (Gastric Trio, Gallbladder,
    Appendix) are completely unaffected — this is additive, not a
    forced pattern on every Block.

- **Gallbladder + Appendix compactness** (Claude.ai chat). Descriptive
  microscopy prose was blank-line-separated between sentences; now reads
  as continuous prose (single spaces), matching the real `CR_Sample.docx`
  samples. Verified via direct function calls: all 64 Gallbladder + all
  12 Appendix combinations.
- **`{{ snippet('absence_malignite') }}` placement** (Claude.ai chat, on
  request). Sits on its own line directly under the microscopy
  description — a hard newline, not a blank-line paragraph break.
- **Preset-switch reset was wiping Case ID / Renseignements cliniques**
  (Cline + Claude API, refining the prior session's preset-switch fix).
  The prior fix correctly cleared stale Block field values on any preset
  change, but routed through the same `_do_workspace_reset` used for a
  full reset — and `_clear_case_scoped_state()` bumps `_form_generation`,
  which orphans the generation-scoped `case_id_{gen}` / `clin_info_{gen}`
  widget keys even though nothing explicitly deletes them. Fixed with a
  dedicated `_do_preset_switch_reset` flag that captures both values
  under the old generation before clearing, then re-seeds them under the
  new generation — same pattern already used for case-reopen restoration.
  Verified via `AppTest`: the exact `etc0`→`etc5` scenario with Case ID
  and Renseignements cliniques typed in first now preserves both, and a
  regression check confirms the original stale-field-value fix still
  holds through the new path.
- **"MICROSCOPY:" label and case number removed from the rendered
  report** (person's own manual edit in `rendering.py`). Neither appears
  in any real sample. Re-verified in this chat: compiles clean, full
  shell render confirmed correct with both lines gone.
- **Unused `case_id` parameter cleaned up** (person's own manual edit,
  after the "MICROSCOPY:"/case-number removal left it unused in
  `assemble_report_html`). Not reproduced in this chat's copy of the
  files — the person did this directly in his own repo, and it's
  behavior-neutral, so there's nothing to verify beyond what he's already
  confirmed working.

### "Examen macroscopique" / "Examen microscopique" headers — architecture change, now fully applied and verified

The first attempt (hardcoding the headers into `seed_data.py`'s template
text) was wrong — confirmed against `CR_Sample.docx`, which the person
provided with two real cases (single-specimen Gallbladder, two-specimen
Gallbladder+Sleeve). The two-specimen case has **no** "Examen..." labels
anywhere; each specimen just gets its existing numbered "N. Name" header
followed by macro text, one blank line, then micro text. Headers only
belong to the single-specimen case, which also drops the specimen-name
header entirely (the title already covers it) — a template can't know
its own specimen count, so this can't live in template text at all.

**Schema change**: new nullable column `Blocks.macro_template`. This is
**universal** across every non-table block, not a per-block-type opt-in
— an earlier version of this same round first framed it as "Gastric Trio
opts out forever," which the person caught and corrected: fragment_text
is a genuine macro statement (specimen count received), just previously
folded unlabeled into `micro_template` — splitting it out doesn't change
today's 3-block Gastric Trio output at all (`render_block`'s
multi-specimen path recombines `macro_txt + "\n\n" + micro_txt`,
confirmed byte-identical to the old folded template), but it means a
future single-specimen preset of *any* kind (e.g. an antrum-alone
biopsy) correctly gets the same header treatment as
Gallbladder/Appendix/Thyroid, rather than needing special-casing later.
`macro_template=NULL` stays supported for a block with genuinely no
macro content to state, but isn't expected to be the normal case — every
current block (all 4 case types) has one set. The one real exception,
not yet built: `is_table` blocks (prostate biopsies) — a fundamentally
different, row-based rendering path outside this mechanism entirely.

**Rendering change**: `rendering.render_block` takes a new
`total_specimens` argument (`workspace.py` passes `len(blocks)`). When
`macro_template` is set, it renders macro and micro separately, then
combines them: 1 specimen → headers, 2 blank lines around each, header
hugs its own content; 2+ specimens → no headers, one blank line between
macro and micro, wrapped in the existing numbered specimen header.
`format_micro_plain` drops the specimen-name header entirely (not just
its number) when there's exactly one specimen — an earlier pass in this
round only stripped the "1." prefix, which was half right.

**Shell fixes, same evidence**: title-to-content gap is 2 blank lines,
not 1 (`assemble_report_html` only had `<br><br>`). "Renseignements
cliniques :" is now omitted entirely when `clinical_info` is empty,
rather than rendering as a label with nothing after it.

**Testing**: verified against the real sample paragraph-by-paragraph
(extracted from the docx XML directly, not eyeballed from a render) —
matches exactly for the single-specimen case. Verified through the
actual `init_db.py` → `seed_data.seed_all()` flow (not a hand-built test
schema): all 64 Gallbladder + 12 Appendix + 5 Thyroid + 3 Gastric Trio
(duodenum/antrum/fundus individually) single-specimen combinations
produce exactly one of each header with no stray blank lines; the
existing 3-specimen Gastric Trio preset's output is byte-identical to
its pre-split output; a synthetic two-specimen case (Gallbladder +
Appendix together, since Sleeve isn't a real block yet) produces zero
"Examen..." labels and correct "1./2." specimen headers instead,
matching the Sleeve sample's structure. **Since then, the person ran the
updated `init_db.py` himself against his real `pathology.db` and
confirmed everything works as intended in his actual browser.**

## Overall shape

The plan has two tiers:
- **Tier 1 — foundation**: schema, reopen mechanism, real pending/validated
  status, Worklist, the two-button Save split. **Complete.**
- **Tier 2 — breadth**: build out real case types (content), using the
  now-stable engine. **In progress** — 4 case types built, now matching
  real report samples structurally (macro/micro headers, numbering,
  compactness) after this round's work. Content-level items below still
  open.
- **Tier 3 (informal) — self-service**: the Editor UI. **Not started** —
  deliberately deferred until Tier 2's content settles further, since
  building the Editor against templates that are still actively changing
  would mean re-doing Editor work too. **A persistent automated test
  suite was decided on this session as a prerequisite/companion, not yet
  designed** — see "Immediate next steps": Editor UI's whole point is
  self-service template editing without a Claude-tested round-trip
  first, which is exactly the safety net today's ad-hoc, thrown-away
  testing depends on.

## Quick Type — settled design (Claude.ai chat)

Grammar, tokens, and parsing rules below are **settled** — build status
against this design is tracked in "Currently mid-task" above, not here.
This section is reference material for the reasoning, kept separate so
"what was decided" and "what's actually built so far" don't get
conflated as checkpoints land.

- **Not a new paradigm** — a generalization of the existing `etc0`-`etc5`
  pattern (preset + one pre-set modifier), extended to several
  *independent* modifier axes via string parsing instead of enumerating
  every combination as its own preset.
- **A dedicated field, not shared with the Preset dropdown.**
  `st.selectbox` is closed-vocabulary by design (that's what makes it
  discoverable); making it also accept freeform text means one widget
  doing two contradictory jobs — the same shape of mistake as an
  over-broad Block. A separate `st.text_input` in its own `st.form` (same
  blur-timing reasoning as the existing reopen box) sits next to the
  dropdown instead.
- **Preset-code-first, not measurement-first** (reverses the earlier
  `7DAI3`/`8fvicl` framing above, which put a raw size before the preset
  code — superseded). Preset-first makes Quick Type a strict superset of
  the dropdown: typing just `dai` is a faster way to do exactly what
  selecting "Appendice (dai)" does, no separate mental model needed.
- **Finding where the preset code ends**: longest-registered-short_code
  prefix match against `Presets.short_code` — not a delimiter. A
  short_code that's a prefix of another (`etc` existing alongside `etc2`,
  say) would resolve deterministically but not obviously — deliberately
  **not** guarded against yet (see below), since Quick Type config
  authoring only happens by hand in `seed_data.py` right now; belongs
  with the future Editor UI's own validation, not bolted onto Quick Type
  first.
- **Two token kinds only, v1** — `lookup` (single character, keyed
  against a small per-token table; covers both real select-field values
  and presence flags like `l` for lithiasis — no separate "flag" kind
  needed) and `measurement` (a run of digit characters). A third kind
  (multi-character lookup, for something like a fixed 2-letter code) was
  considered and deliberately deferred: it has no natural
  self-terminating rule the way the other two do, and nothing in the
  real shorthand needs it yet.
- **The actual ambiguity rule, precisely**: a `measurement` token
  consumes digits greedily until a non-digit or end-of-string. This is
  only safe if it's the *last* token, or the token right after it can't
  itself start with a digit. **Bounding a measurement's digit-width does
  NOT resolve this** — it was tried as a first pass and correctly
  rejected: capping at 2 digits doesn't tell the parser whether "37"
  after a 2-cm-capped size token is one 2-digit size followed by a new
  token, or a 1-digit size followed by another digit-keyed token. The
  real fix is checking, per pair of adjacent tokens, whether their
  possible first-characters actually overlap with digits — a
  config-time check (`quicktype.validate_quick_type_config`), not
  something the parser guesses about per input. `digit_width` still
  exists as an optional field on each token, but only as a sanity guard
  against a typo producing a wrong-but-plausible large number, not as the
  mechanism that makes adjacency safe.
- **`!` is reserved**: advances to the next block's tokens without
  consuming any of the current block's remaining ones — "I'm done
  modifying this block, move to the next." **Not required between
  blocks** — fully consuming a block's configured tokens rolls into the
  next block automatically; `!` only matters for stopping *early*. In a
  single-block preset (everything today), `!` has nothing to advance to
  and must be a parse error, not a silent no-op. Reserved everywhere — no
  lookup table may use `!` as a key (checked by the validator).
- **A future "skip a middle token" reserved character (`*`) was raised
  and deliberately deferred** — cheap to add later (a single reserved
  char, same validator treatment as `!`), but nothing in real usage has
  needed it yet. Not built, not designed further than "the mechanism
  doesn't foreclose it."
- **Multi-block scaling, without building multi-block now**: every
  `Quick_Type_Tokens` row carries `block_sort_order` (mirrors the
  `(block_id, sort_order)` idiom `workspace.py` already uses for
  reused-block presets), always `0` today. The parser treats a preset's
  whole token sequence as one flat, block-agnostic list — the same
  adjacency rule has to hold across a block boundary as within one block,
  since auto-rollover crosses a boundary without needing `!`. When
  multi-block Quick Type is eventually wanted, it's more
  `Quick_Type_Tokens` rows targeting a different `block_sort_order`, not
  a parser redesign or a grammar change.
- **Fail-loud, atomic apply-or-reject — no separate preview step.**
  Considered a "preview before commit" screen and rejected it in favor of
  something stronger: the whole typed string either parses cleanly
  against that preset's config, in which case it applies immediately and
  shows a specific confirmation (resolved field *labels* and values, not
  raw keys — e.g. `dai → Appendice : type=périappendicite, taille=7 cm`),
  or nothing gets touched and a specific error names exactly what failed
  to parse. A preview can still be misread at typing speed; atomic
  apply-or-reject with a precise error is the stronger guarantee, and
  matches the "never guess, fail loud" posture already used elsewhere in
  this app (`snippet_lookup`'s missing-shortcut marker,
  `conclusion_addendum`'s conflict-skip).
- Batch processing (a case-ID + code table, generating many reports at
  once) and a voice-input front end both still fall out for free once the
  core parser is a clean `string -> (preset, field_overrides)` function —
  later extensions, not part of the initial build.

## Field-consistency validation — design question (RESOLVED)

Kept verbatim below for the record of what was actually considered —
see "Currently mid-task" above for the decision and the concrete
implementation plan/checkpoints.

- **Field-consistency validation.** Example: selecting "fausses
  membranes" on Appendix shouldn't be combinable with mild severity
  levels (`endo`, `suppuree`) or `intervalle` — clinically inconsistent.
  Two options on the table: prevent the invalid combination outright
  (dynamically restrict `appendicite_type` options based on
  `false_membranes`), or at minimum show an inconsistency warning.
  Neither exists in the current architecture. The warn-and-confirm
  pattern already built for the duplicate-case guard is the likely
  reusable template for the "warning" option, but this is a real design
  decision, not a quick patch — likely to recur for other organs' field
  combinations. **Discuss before building.** Note: this is a *different*
  problem from the context/title/conclusion linking work finished this
  round (see "Fixed and verified this round") — that one was "the same
  single fact shouldn't be typed twice," this one is "two different,
  individually valid field values shouldn't be combinable." Solving one
  didn't solve the other.

  **Decision**: warn-and-confirm on resolved field values, checked
  generically at the same point across all three entry paths (manual
  widget, Preset default, Quick Type) — not dynamic option-restriction.
  Rejected option-restriction because it can only ever cover the manual
  widget (Quick Type and Preset overrides bypass it entirely), and
  because dynamically shrinking a `st.selectbox`'s options risks a
  crash/silent-jump if the current value falls outside the new list —
  the same widget-state bug family that's already bitten this project
  three times.

## Open question: keeping this file honest across tools

The person is trialing a mixed workflow — this Claude.ai chat when free,
Cline + Claude API or OpenRouter credits when the free tier is hit — to
compare costs against a Pro-plan Claude Code subscription before
committing to one. Two things surfaced this round worth carrying forward:
reconstructing a fix pasted in from another tool (the preset-switch
preservation fix) took comparing it line-by-line against the local file
to figure out precisely what changed and why — doable for one fix, but
the effort scales with how much gets batched before syncing; and this
round also included manual edits the person made directly in his own
repo (case number/"MICROSCOPY:" removal, the `case_id` param cleanup)
that got described in prose rather than reproduced in this chat's files.
Both point the same direction: whoever makes a change — tool or person —
is in the best position to record it accurately, right when it happens,
rather than deferring reconstruction to whichever session syncs next.

## Immediate next steps, if resuming without other instructions

1. **Persistent test suite: pick up the next open checkpoint in
   TESTING.md.** Scaffolding (checkpoint 0) is done and verified;
   checkpoints 1 and 4 are mostly/fully done as worked examples;
   checkpoints 2, 3, 5, 6 are open, separable, and — per TESTING.md's
   own "How this fits the mixed-model workflow" — deliberately suited to
   being picked up by different sessions or different models rather
   than needing to be done in one sitting by one of them. Checkpoint 6
   (`AppTest`-based UI flows) is the one place real historical fragility
   lives — save it for last, or for the model with the most track record
   on this codebase.
2. Ask for a fresh `git log` at the start of any new session regardless
   — this session's reconciliation (see "Where we are right now") is
   only current as of `d4a1a96`. The `0b498c4` question from earlier
   rounds is fully resolved, not something to re-raise.
3. Once the test suite's core checkpoints exist (or alongside finishing
   them): Editor UI (Tier 3) is the next real feature — don't start it
   until Tier 2's content is stable, per "Overall shape." Extending
   field-consistency validation beyond the Appendix pilot, extending
   Quick Type configs to Gallbladder/Thyroid, and extending the
   context/title/conclusion pattern to a future breast preset are all
   candidate *later* work on top of that, no forced order between them,
   none currently active.