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

**⚠️ Before Checkpoint 1 — real risk, needs the person's confirmation
first, not something Claude can check from the sandbox:** `init_db.py`
fully drops and recreates every table, including `Cases`, with no
migration path (its own docstring already says this is dev-only,
destructive, "not for a database holding real case history"). If the
person's live `pathology.db` now holds real validated Cases from actual
use, running the updated `init_db.py` against it will destroy them.
Confirm real-case status and back up (export `Cases` first, or write an
`ALTER TABLE`-based migration instead of the destructive rebuild for this
round specifically) before Checkpoint 1 touches the real repo — sandbox
testing itself is safe either way, this only matters at the point the
person applies the change to his own machine.

**Checkpoints:**
1. **Schema.** Add `Blocks.context_template` (TEXT, nullable),
   `Blocks.title_fragment_template` (TEXT, nullable),
   `Presets.default_title` (TEXT, nullable) to `init_db.py`. Pure
   structure, no content. Test: rebuild against a throwaway db, confirm
   the 4 existing case types render byte-identical to before (new columns
   NULL on every existing row).
2. **Thyroid Cytology content.** Three new Fields on `thyroid_cytology`:
   `nodule_site` (select: "lobaire droit" / "lobaire gauche" /
   "isthmique" — a real site, not a strict laterality; confirmed by the
   sample's second nodule being "isthmique", not a side), `nodule_size`
   (decimal), `nodule_eutirads` (select: "2"/"3"/"4"/"5"). Write
   `context_template` = `"Nodule {{nodule_site}} de {{nodule_size_display}}
   mm EUTIRADS {{nodule_eutirads}}"`, `title_fragment_template` =
   `"{{nodule_site}}"`. Set `default_title` on all 4 existing presets'
   underlying Presets rows (the originally-requested simple defaults:
   "Vésicule biliaire", "Appendice", "Biopsies gastroduodénales",
   "Cytologie thyroïdienne" — same column, same pass). **Gotcha caught in
   planning, not yet hit in practice**: `coerce_field_value` renders
   decimal fields as Python floats, so a naive `{{nodule_size}}` would
   print "20.0" not "20", breaking the byte-match against the sample.
   Fix: `build_context()` should also expose a `{key}_display` companion
   for decimal fields (int-clean formatting) — same pattern already used
   for `fragment_text` being derived alongside `fragments`. Test: direct
   function calls over a spread of field combos, confirm decimal display
   is clean.
3. **Rendering support.** New function(s) in `rendering.py` to render
   `context_template`/`title_fragment_template` through the existing
   `build_context()`, same idiom as every other template. Test: exact
   string match against the sample's "nodule lobaire gauche de 20 mm
   EUTIRADS 4."
4. **Workspace — single-specimen path.** Title widget (editable,
   `default_title` + fragment when `total_specimens==1`,
   generation-suffixed persistence like Case ID). Clinical Context lock
   toggle, structurally copied from Master Lock. Test: `AppTest` —
   single Nodule, fill fields, confirm auto-composed context/title match
   the sample; lock, hand-edit, confirm fields no longer clobber it;
   unlock, confirm resync. Full regression across the 4 existing case
   types (this touches shared `workspace.py` code even though it
   shouldn't change their behavior).
5. **New 2-block Thyroid Cytology preset (test fixture).** New Preset —
   two `Preset_Blocks` rows both pointing at `thyroid_cytology`, different
   `sort_order`, `field_overrides` matching the sample's two nodules
   (lobaire gauche/20mm/EUTIRADS4 vs isthmique/15mm/EUTIRADS3). Purely
   additive seed data.
6. **Workspace — multi-specimen path.** One-line change where
   `micro_blocks` gets built: header becomes the rendered `context_txt`
   when present, `block["name"]` otherwise — no change needed inside
   `rendering.format_micro_plain` itself. Title/top-context auto-compose
   stay inactive when `total_specimens > 1`. Test: run the new bi-nodular
   preset through the full pipeline, confirm structural match against
   `CR_Sample.docx` case 2. Also confirm (not just assume) that
   `grouping.py` needs no change: `thyroid_cytology.site_label` is
   already `NULL`, so the two nodules' genuinely-different conclusion
   text won't spuriously merge — this is existing behavior, not new.
7. **Regression + wrap-up.** Full boot check, regression across all 5
   presets, clean up throwaway db/test scripts, update this file, present
   diff for commit (likely 2 atomic commits: schema+Title generically,
   then Thyroid content+bi-nodular preset+specimen-header logic — apply
   the usual "would I revert one without the other" test).

**Explicitly deferred, not part of this round**: a lightweight
warn-only cross-check between free-text clinical info and a structured
field's value (e.g. laterality word mismatch) — useful, but only once
the above exists, and never as text-extraction/autofill. Also still
separate and unaddressed: the `fausses_membranes`/`appendicite_type`
field-*combination* validation question raised earlier in this file —
a related but distinct problem (invalid combinations of independently-
plausible values, not the same fact typed in multiple places).

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