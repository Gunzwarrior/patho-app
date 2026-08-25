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

Everything in "Fixed and verified this round" below is confirmed committed
— the person's own `git log --oneline -20` (pasted into a later session,
reconciled at the time) shows `1601015`, `32f0e3f`, and `5988fe4` matching
those entries. **One discrepancy to flag, not yet reconciled**: that same
log's HEAD is `0b498c4 "Switch cholesterolosis and lithiasis fields"` —
a commit not described anywhere in this file. Likely a small manual
tweak done outside a documented AI session; confirm what it actually
contains (`git show 0b498c4`) before assuming this file is complete.

## Currently mid-task

**Task: Title field + clinical-context/title/conclusion consistency
linking, for Thyroid Cytology first.** Full design discussion happened
in a Claude.ai chat (not yet reflected anywhere else) — real motivation:
repeated real-world reporting mistakes where the same fact (laterality,
receptor status) was typed independently in clinical info / title /
conclusion and drifted out of sync. Design reasoning, if resuming without
the original conversation:

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

**Checkpoint 1 gate — resolved.** Person confirmed real Cases exist in
the live db were not at risk / backup wasn't needed; ran the updated
`init_db.py` himself against his real repo, tested cases, confirmed
working. Checkpoints 1-6 below are done in the sandbox. **Several
decisions surfaced along the way that weren't in the original plan**
(noted inline below) — all already implemented and tested, not just
proposed.

**Review status: pending, deferred by the person's own choice.** He was
at work and asked Claude to keep going through Checkpoints 5-6 on
automated sandbox verification alone (`AppTest`, byte-matching against
`CR_Sample.docx`, a real save→DB→reopen round trip), explicitly planning
to review each checkpoint himself when home rather than block progress
on that. Treat every checkpoint below as "sandbox-verified" — a real bar,
but not the same as "the person looked at it" — until he actually
confirms in his own browser. If picking this up without him having done
that review yet, say so plainly rather than assuming it happened.

**⚠️ Incident this round, now fixed — worth knowing if resuming:**
Early in this round (before `seed_data.py`/`workspace.py` were available
as real files to work from), Claude hand-retyped `grouping.py`/
`rendering.py`/`database.py`/`init_db.py` from text shown earlier in the
conversation instead of the actual files, and that retyping silently
dropped most of their original docstrings/comments. The Checkpoint 1
(`init_db.py`) and Checkpoint 2 (`rendering.py`) deliverables handed to
the person were built on that flawed base, and **he applied both to his
real repo before the loss was caught.** Caught and fixed later in this
same round: both files were rebuilt from the true originals with only
the intended diffs layered on top (verified line-by-line — every removed
line now accounted for by an intended edit, zero unexplained loss), and
corrected versions were redelivered. `seed_data.py` was never affected
(Claude worked from the real file for it throughout). If picking this up
fresh: confirm the person actually applied the *corrected* redelivery,
not the original flawed one — `grep` for `format_decimal_display`'s full
docstring in his `rendering.py` as a quick check (should be ~10 lines,
not one bare line).

**Checkpoints:**
1. ✅ **Schema.** `Blocks.context_template`, `Blocks.title_fragment_template`,
   `Presets.default_title` — all nullable TEXT. Verified byte-identical
   rendering across all 8 presets before/after.
2. ✅ **Thyroid Cytology content.** Three new Fields: `nodule_site` (select
   — "lobaire droit"/"lobaire gauche"/"isthmique", a real site not a
   strict laterality, confirmed by the sample's second nodule being
   "isthmique"), `nodule_size_mm` (decimal), `nodule_eutirads` (select
   2-5). `context_template`/`title_fragment_template` set. `default_title`
   set on all 8 Presets rows. **Bonus fix, not originally planned**: the
   decimal-display bug (`coerce_field_value` renders "8.0" not "8") turned
   out to be a *pre-existing* bug already live in production — Gallbladder's
   `specimen_size_cm`, Appendix's `appendix_size_cm`, and Thyroid's
   `liquid_volume_ml` were all already affected. Fixed generally
   (`format_decimal_display()` + a `{key}_display` companion in
   `build_context()` for every decimal field) and applied to all three
   existing spots, not just the new one — shipped as its own atomic commit,
   separate from the Thyroid content commit, so it can be reverted
   independently if needed.
3. ✅ **Rendering support.** `rendering.render_context_fragments(block,
   overrides)` — renders both new templates through the existing
   `build_context()`. Verified exact string match against both real
   nodules in `CR_Sample.docx` ("Nodule lobaire gauche de 20 mm EUTIRADS
   4" / "Nodule isthmique de 15 mm EUTIRADS 3").
4. ✅ **Workspace — single-specimen path.** New "📝 Contexte clinique"
   section, rendered right after preset selection, before Global
   Modifiers/Medical Variables. One shared lock toggle governs both the
   Renseignements cliniques box and a new Titre box (not two separate
   toggles) — auto-synced while unlocked, frozen/hand-editable while
   locked, structurally identical to Master Lock. Verified via `AppTest`:
   default field values auto-compose correctly; live field edits propagate;
   locking + hand-editing survives a field change made while locked;
   unlocking resyncs to *current* field state (not stale); non-composing
   presets (Gallbladder, Gastric Trio) are completely unaffected — Titre
   still works via `default_title`, Renseignements cliniques stays a plain
   always-editable box exactly as before.

   **Decision not in the original plan, needed once this was actually laid
   out**: the page needs to know which fields belong in the new Clinical
   Context area vs. existing Medical Variables further down. Rejected
   inferring this from which fields `context_template` happens to
   reference (same reasoning as rejecting free-text parsing earlier —
   explicit columns over inference). Added `Block_Fields.context_section`
   (boolean, default 0) instead. `workspace.py`'s old inline per-field-type
   widget dispatch (number/decimal/select/checkbox/text) was extracted into
   a shared `render_field_widget()` helper, now used by both the new
   Context loop and the existing Medical Variables loop — avoided
   duplicating that dispatch logic in two places.

   **Second decision, also not in the original plan**: preset-switch
   preservation for clinical info (fixed in commit `1601015`) had to
   reverse. Since clinical info can now be auto-composed from
   preset-specific fields, letting it survive a switch to an unrelated
   preset would carry stale wording forward (e.g. thyroid nodule text into
   a fresh Gallbladder case). Case ID's preservation is unchanged — only
   clinical info's reversed. `structured_input`/case-reopen also extended
   for `final_title_edit`/`context_title_lock`, same restore-only-if-manual
   pattern already used for Master Lock.

5. ✅ **New 2-block Thyroid Cytology preset.** `etc_bi` — two `Preset_Blocks`
   rows both pointing at `thyroid_cytology`, different `sort_order`,
   `field_overrides` matching `CR_Sample.docx`'s two real nodules (lobaire
   gauche/20mm/EUTIRADS4/etc2 vs isthmique/15mm/EUTIRADS3/etc1 — verified
   these pattern codes are the right ones by matching `thyroid_conc`'s
   per-pattern Bethesda text against the sample's two conclusions). Not
   just a throwaway test fixture — pre-filled like `etc0`-`etc5`, it's a
   real fast-access preset for genuine 2-nodule cases too.

   **Significant bug found and fixed, not anticipated in the original
   plan**: `etc_bi` is the *first* preset ever to reuse the same Block
   twice, and doing so immediately crashed the page —
   `StreamlitDuplicateElementKey` on `field_6_nodule_site_1`. Root cause:
   every per-field widget key, `structured_input`'s saved-blocks dict, and
   the reopen-restoration lookup were all keyed off `block['block_id']`/
   `block["key"]` alone — fine when every Block in a Preset is distinct,
   silently wrong the moment one Block is reused, since two instances
   share the same `block_id`/key. Beyond the crash, this would have
   silently **lost data on save**: `structured_input["blocks"]` was a
   dict keyed by bare `block["key"]`, so the second nodule's field values
   would silently overwrite the first's, both on save and on reopen
   restoration — no error, just a case that quietly reopens with both
   nodules holding the same values. Fixed by keying everything off
   `(block_id, sort_order)` instead (`sort_order` is already part of
   `Preset_Blocks`' own primary key for exactly this reason — two
   instances of one Block always differ in `sort_order`). Touched 6 spots
   in `workspace.py`: both field-widget-key call sites, `block_ctx_overrides`
   (4 places), the reopen-restoration lookup, and the `structured_input`
   save dict. **Verified with a real save → DB round-trip → reopen test**,
   not just widget-level assertions: gave the two nodules different
   `nodule_site` values, saved, confirmed both persisted independently in
   `Cases.structured_input` under distinct keys (`thyroid_cytology#0`/
   `thyroid_cytology#1`), reopened, confirmed both widgets restored to
   their own correct value.

6. ✅ **Workspace — multi-specimen path.** `micro_blocks` now gets built
   with the rendered `context_txt` as the header when a block set one,
   falling back to `block["name"]` otherwise — one line in `workspace.py`,
   no change needed inside `rendering.format_micro_plain` itself (it
   already suppresses the header entirely for a single specimen,
   regardless of what's passed as the name, confirmed against the
   sample's single-nodule case having no header at all despite
   `context_template` being set).

   **Extra scope added here, beyond the original plan**: the sample's
   multi-specimen conclusion also prefixes each *unmerged* entry with its
   own site ("1. Nodule lobaire gauche : Matériel satisfaisant...") —
   something Checkpoint 6's original description assumed would come "for
   free" via an ordinary `{{field}}` reference inside `conclusion_template`,
   but that was never actually wired into `thyroid_conc`. Implemented
   generically in `grouping.py` instead: `_merge_section` now also returns
   a `site_fragment` per unmerged entry (reusing `title_fragment_template`
   — the same short fragment that feeds the Title box — rather than a new
   column), and `render_conclusion_plain` prepends it after the number
   when the block set one and the case isn't single-specimen. `None` for
   any block that doesn't set `title_fragment_template` (Gastric Trio,
   Gallbladder, Appendix) — confirmed via regression those are completely
   unaffected. **One known, deliberately-accepted gap**: the sample says
   "**Nodule** lobaire gauche :", the current output says "lobaire
   gauche :" (no "Nodule"). Reusing `title_fragment_template` verbatim
   can't produce both — Title wants the bare site, the conclusion prefix
   wants "Nodule" prepended — and a third schema column just to recover
   one word felt disproportionate without discussing it first. Easy
   follow-up either direction (edit the template text, or split into a
   dedicated column) once the person weighs in.

   Test: full `AppTest` run through `etc_bi`'s actual Save button,
   confirmed structural match against `CR_Sample.docx` case 2 (headers,
   conclusion numbering, top clinical-info box staying free/"goitre"-
   style, title staying static). Full regression re-run across all 8
   original presets — `grouping.py` changed, so every case type's
   conclusion rendering was re-verified byte-for-byte unchanged.

7. **Regression + wrap-up.** Mostly done, but **only as far as a sandbox
   can verify** — everything above has real automated coverage (`AppTest`
   for every UI path touched, byte-matching against `CR_Sample.docx`, a
   genuine save→DB→reopen round trip, full regression across all 8
   presets after every change), but none of it is a substitute for the
   person actually clicking through it in his own browser, especially
   given this round touched shared `workspace.py` layout code. He's
   deferring that review to when he's home (see note at the top of this
   section) — treat this checkpoint as open until he's actually looked,
   not closed just because the sandbox is clean.

**Explicitly deferred, not part of this round**: a lightweight
warn-only cross-check between free-text clinical info and a structured
field's value (e.g. laterality word mismatch) — useful, but only once
the above exists, and never as text-extraction/autofill. Also still
separate and unaddressed: the `fausses_membranes`/`appendicite_type`
field-*combination* validation question raised earlier in this file —
a related but distinct problem (invalid combinations of independently-
plausible values, not the same fact typed in multiple places). And now
also open: whether to add "Nodule" to the conclusion prefix (see
Checkpoint 6 above).

## Fixed and verified this round

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

## Content/design requests, not yet implemented

- **Title field**: superseded by the fuller design now underway — see
  "Currently mid-task" above. `Presets.default_title` is still the right
  starting point, but it's now bundled with `context_template`/
  `title_fragment_template` rather than built standalone, since the
  simple version turned out to be a subset of the same schema change.

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
  problem from the context/title/conclusion linking work now underway
  (see "Currently mid-task") — that one is "the same single fact
  shouldn't be typed twice," this one is "two different, individually
  valid field values shouldn't be combinable." Solving one doesn't solve
  the other. Still open.

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

1. Ask for `git log` to establish real state, and confirm what's actually
   committed vs. what's staged — including reconciling the undocumented
   `0b498c4` commit flagged above.
2. If the checkpoint plan under "Currently mid-task" hasn't started yet:
   get the real-Cases-backup question answered first, then start at
   Checkpoint 1.
3. If it's mid-way: pick up at the next unchecked checkpoint — each one
   should have left the working tree self-consistent and tested, per
   CLAUDE.md's checkpointing convention.
4. The `fausses_membranes`/field-combination validation question is still
   open and unrelated to the above — raise separately when it comes up.
5. Don't start the Editor (Tier 3) until Tier 2's content is stable.