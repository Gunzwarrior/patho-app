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

**Active work is "Quick Type"** — see "Currently mid-task" below.
Checkpoint 1 (schema + `quicktype.py` validator) is done and
sandbox-verified, piloted on `dai` only; not yet applied to the person's
real repo. Everything from here through "Fixed and verified this round"
is the record of the *previous* round's work (Clinical
Context/Title/Conclusion composition), kept for reference.

The Clinical Context/Title/Conclusion composition feature at the top of
"Fixed and verified this round" is confirmed **working** in the
person's real browser as of this update, but **not yet committed** — he
asked for this file to be updated first, committing right after. If
`git log` doesn't show it yet, that's expected, not a discrepancy.

Everything else in that section — starting from "Gallbladder
cholesterolosis blank-line bug" onward — is confirmed **committed**:
the person's own `git log --oneline -20` (pasted into an earlier
session, reconciled at the time) showed `1601015`, `32f0e3f`, and
`5988fe4` matching those entries. **One discrepancy from that same log,
still not reconciled**: its HEAD was `0b498c4 "Switch cholesterolosis
and lithiasis fields"` — a commit not described anywhere in this file.
Likely a small manual tweak done outside a documented AI session;
confirm what it actually contains (`git show 0b498c4`) before assuming
this file is complete.

## Currently mid-task

**"Quick Type" — building now, Checkpoint 1 of 4 done (Claude.ai chat).**
Full design settled this session; see "Quick Type — settled design" below
for the complete grammar/token/parsing reasoning, not repeated here.

- **Checkpoint 1 — schema + validator (done, sandbox-verified only, not
  yet in the person's real repo).** New `Quick_Type_Tokens` table
  (`init_db.py`): `preset_id`, `sort_order` (flattened, block-agnostic
  position), `block_sort_order` (inert, always 0 today — see design
  section for why it exists anyway), `field_key`, `token_kind` ('lookup'
  | 'measurement'), `lookup_table` (JSON), `digit_width` (sanity cap, not
  the disambiguation mechanism). New module `quicktype.py`:
  `validate_quick_type_config(tokens)` — flattens a preset's tokens and
  checks (1) every lookup key is exactly 1 char and not a reserved
  character, (2) every measurement token is either last or followed by a
  token whose possible first-characters don't overlap digits. Piloted on
  `dai` only (`seed_quick_type_tokens()` in `seed_data.py`, called from
  `seed_all()`): token 1 = `appendicite_type` lookup (`"1"`-`"6"` →
  endo/suppuree/periappendicite/phlegmoneuse/gangreneuse/intervalle,
  confirmed against the real Field's option order), token 2 =
  `appendix_size_cm` measurement (safe because it's last).
  **Verified**: `py_compile` on all touched/dependent files; a real
  `init_db.py` → `seed_data.seed_all()` run seeds `dai`'s tokens and
  passes validation with no error; the seeded rows read back correctly
  from `pathology.db`; the validator was also exercised directly against
  6 adversarial synthetic configs (not just the real one) — two
  genuinely-ambiguous adjacency cases both correctly raise (measurement→
  measurement, measurement→digit-keyed lookup), the real-world-motivated
  safe case correctly passes (measurement→letter-keyed lookup, the
  `8fvicl`-style shape), a reserved-character collision and an
  unsupported multi-char lookup key both correctly raise with specific
  messages, and an empty token list is correctly treated as "not an
  error, just not quick-typeable yet" rather than a failure.
  `database.get_all_presets()`/`get_preset_blocks()` reconfirmed working
  unmodified against the new schema (9 presets, `dai` still resolves its
  3 fields correctly) — the schema addition is purely additive as
  intended, no regression. This is backend-only, pure logic, with solid
  automated coverage — per the commit-gating convention below, **committable
  without waiting on a real-browser check**, whenever the person is ready.
- **Checkpoint 2 — parser, not started.** `parse_quick_type(raw_string) ->
  (preset, field_overrides_by_block, error_or_none)` in `quicktype.py`.
  Longest-registered-short_code-prefix match to find where the preset
  code ends; walks the flattened token list consuming lookup/measurement
  tokens plus `!` (skip to next block; a parse error if used in a
  single-block preset — see design section). Test plan: direct function
  calls against the real `dai` config — `dai`, `dai3`, `dai37` all
  succeed with the right resolved values; `dai9` (key out of range),
  `dai37x` (trailing unparseable chars), and `dai!` (nothing to skip to)
  all fail with specific messages, nothing silently guessed.
- **Checkpoint 3 — UI wiring, not started.** New `st.form` + `text_input`
  next to the Preset dropdown in `workspace.py`. On successful parse:
  needs its **own** new reset flag (`_do_quick_type_apply` or similar) —
  per the established convention below, reusing `_do_preset_switch_reset`
  for a different one-shot purpose is exactly the bug pattern already
  hit three times with `_form_generation`-scoped widgets. Capture
  (preset, field overrides) in `session_state`, `_clear_case_scoped_state()`,
  re-seed field widget keys under the new generation — same skeleton as
  case-reopen restoration, not a new mechanism. One-shot success message
  via the existing `_save_confirmation`-style pattern (`st.success`,
  popped at top of next run), formatted with resolved field *labels*, not
  raw keys. On failure: inline error, state left completely untouched —
  atomic apply-or-reject. Test plan: `AppTest` — valid code applies
  correctly and shows the right message; invalid code leaves prior state
  untouched and shows the right error.
- **Checkpoint 4 — regression + real browser, not started.** Full
  regression across all 9 existing presets (Quick Type must not affect
  any non-Quick-Type flow), full app boot check, then hand off — this
  checkpoint is UI-facing, so per the same commit-gating convention it
  waits for the person's own browser confirmation before being
  considered done, unlike Checkpoint 1.

`dai` is deliberately the only preset with a Quick Type config right now
— extending to Gallbladder/Thyroid is explicitly follow-up work, once
`dai` has been exercised end-to-end through the real parser and UI, not
before.

## Fixed and verified this round

**Clinical Context / Title / Conclusion composition feature — fully
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
  would mean re-doing Editor work too.

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

## Genuinely new architectural question — needs a real decision, not just a fix

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
  didn't solve the other. Still open.

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

1. Ask for `git log` to establish real state, and confirm what's
   actually committed. The Clinical Context/Title/Conclusion feature and
   its bug fixes should all be one or a few recent commits — reconcile
   against "Fixed and verified this round" if anything looks
   unfamiliar. The `0b498c4` commit flagged earlier in this file was
   never actually reconciled — still worth a `git show 0b498c4` if it
   comes up.
2. **Quick Type is mid-task** — Checkpoint 1 (schema + validator) is done
   and sandbox-verified; Checkpoint 2 (the actual parser) is next. See
   "Currently mid-task" and "Quick Type — settled design" above before
   resuming — don't re-derive the grammar/token/ambiguity reasoning from
   scratch, it's already settled. Other natural next things, none
   started: extending the context/title/conclusion pattern to a future
   breast preset, or the `fausses_membranes` field-combination validation
   question below.
3. Don't start the Editor (Tier 3) until Tier 2's content is stable.