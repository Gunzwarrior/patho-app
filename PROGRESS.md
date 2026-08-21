# PROGRESS.md — current state (verify against real `git log` first)

**This document can go stale the moment a commit happens without it being
updated.** Neither Claude nor this file has live access to the actual repo
— before relying on anything below, ask the person to paste
`git log --oneline -20` (or however far back is useful) and reconcile.
Where this doc says "committed," that's based on the conversation's own
memory of agreeing on a commit message, not a verified fact.

This round also mixed tools for the first time: some of what's below was
built in a Claude.ai chat, some in Cline against the Claude API directly.
Noted per item, since it's new territory for how this file gets kept
honest — see the bottom of this file for the open question that raises.

## Where we are right now (read this first)

Two rounds of fixes landed since the last real-browser testing pass
(itself described in the now-resolved sections below). **Nothing in this
version of the file has been committed yet** — this is what's staged and
tested, waiting on the person's go-ahead.

### Fixed and verified this round

- **Gallbladder cholesterolosis blank-line bug** (Claude.ai chat). Root
  cause was exactly as previously diagnosed: the separator after
  `{% if cholesterolosis %}...{% endif %}` sat outside the conditional, so
  it rendered unconditionally even when the sentence inside didn't. Fixed
  in `seed_data.py` by moving the separator inside/adjacent-only-when-true.
  Audited all four case types for the same pattern — Gastric Trio and
  Thyroid Cytology don't have it (their if/elif chains have no empty
  branch to begin with), Appendix doesn't either, so no other instance
  existed. Verified via direct function calls: all 64 combinations of
  Gallbladder's `cholesterolosis` × `lithiasis` × `inflammation_type` ×
  `specimen_state`.
- **Gallbladder + Appendix compactness** (Claude.ai chat). Descriptive
  microscopy prose was blank-line-separated between sentences; now reads
  as continuous prose (single spaces), matching the real `CR_Sample.docx`
  samples. The macro→micro paragraph break was deliberately left alone —
  that boundary is reserved for the "Examen macroscopique"/"Examen
  microscopique" header split still pending (see below), and already
  matches the fold pattern Gastric Trio uses. Verified via direct function
  calls: all 64 Gallbladder + all 12 Appendix combinations.
- **`{{ snippet('absence_malignite') }}` placement** (Claude.ai chat, on
  request). Sits on its own line directly under the microscopy
  description — a hard newline, not a blank-line paragraph break. Same
  64+12 combinations re-verified after this change.
- **Preset-switch reset was wiping Case ID / Renseignements cliniques**
  (Cline + Claude API, refining the prior session's preset-switch fix).
  The prior fix correctly cleared stale Block field values on any preset
  change, but routed through the same `_do_workspace_reset` used for a
  full reset — and `_clear_case_scoped_state()` bumps `_form_generation`,
  which orphans the generation-scoped `case_id_{gen}` / `clin_info_{gen}`
  widget keys even though nothing explicitly deletes them. Since Case ID
  and Renseignements cliniques are entered before the preset in the tab
  flow and aren't specific to any one preset, they'd silently blank out
  on any preset switch. Fixed with a dedicated `_do_preset_switch_reset`
  flag that captures both values under the old generation before
  clearing, then re-seeds them under the new generation — same pattern
  already used for case-reopen restoration. Verified via `AppTest` in
  this chat (not run when the Cline session made the change): the exact
  `etc0`→`etc5` scenario with Case ID and Renseignements cliniques typed
  in first now preserves both, and a regression check confirms the
  original stale-field-value fix (a manually-edited field reverting to
  the new preset's default) still holds after rerouting through the new
  flag.

## Overall shape

The plan has two tiers:
- **Tier 1 — foundation**: schema, reopen mechanism, real pending/validated
  status, Worklist, the two-button Save split. **Complete.**
- **Tier 2 — breadth**: build out real case types (content), using the
  now-stable engine. **In progress** — 4 case types built, currently being
  refined against real-world testing feedback (see below).
- **Tier 3 (informal) — self-service**: the Editor UI. **Not started** —
  deliberately deferred until the Tier 2 refinement below settles, since
  building the Editor against templates that are still actively changing
  would mean re-doing Editor work too.

## Confirmed via a fresh real-report sample, not yet implemented

- **No numbering at all when a case has exactly one specimen** — neither
  on the micro block header nor the conclusion line. Numbering only
  appears with 2+ specimens, exactly as currently built for those cases.
- **Two distinct macro/micro conventions, not one**: biopsy-fragment-style
  specimens (Gastric Trio) correctly fold macro info into the same
  numbered entry with no section header. Whole-organ/resection specimens
  (Gallbladder, Appendix) need genuine **"Examen macroscopique" / "Examen
  microscopique" bold section headers**, blank-line separated — confirmed
  via a real single-gallbladder sample, not yet built. Thyroid Cytology's
  one-line liquid volume/color statement was *assumed* to fit the "fold"
  pattern by analogy — still not explicitly confirmed, worth
  double-checking with the person.

## Content/design requests, not yet implemented

- **Swap Cholesterolosis/Lithiasis field order** in Gallbladder — likely
  Lithiasis first (more routinely salient for a cholecystectomy),
  Cholesterolosis last, but this is an inference — check with him before
  assuming which way round.
- **A separate, editable "Title" field**, pre-filled per Preset but not
  identical to the Preset's internal `name`. Examples: Gastric Trio's
  title should default to "Biopsies gastroduodénales"; Thyroid Cytology's
  defaults to "Cytologie thyroïdienne" but is routinely hand-extended per
  case (e.g. "Cytologie thyroïdienne lobaire gauche") — must stay freely
  editable, not locked to the preset default. Appendix and Gallbladder's
  current titles are already fine as-is. Implementation sketch: add
  `Presets.default_title` (nullable TEXT), a new Title widget in
  Workspace pre-filled from it, wired into `assemble_report_html` in
  place of `preset["name"]`.

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
  combinations. **Discuss before building.**

## New open question: keeping this file honest across tools

The person is trialing a mixed workflow — this Claude.ai chat when free,
Cline + Claude API or OpenRouter credits when the free tier is hit — to
compare costs against a Pro-plan Claude Code subscription before
committing to one. Reconstructing the preset-switch preservation fix
above (pasted in from a Cline session, not present in this file) took
comparing the pasted code line-by-line against the local file to figure
out precisely what changed and why — doable for one fix, but the effort
scales with how much gets batched before syncing. Worth deciding
explicitly, rather than each session ad-hoc: probably best if whichever
tool makes a fix also drafts its own PROGRESS.md entry for it in the same
session (it has full context of its own change), rather than deferring
that reconstruction to whichever session happens to sync next.

## Immediate next steps, if resuming without other instructions

1. Ask for `git log` to establish real state, and confirm what's actually
   committed vs. what's staged from this round.
2. Raise the field-consistency architectural question explicitly and get
   a real decision before building anything for it.
3. Work through the macro/micro header split for Gallbladder/Appendix,
   the Title field, and single-specimen numbering — independent of each
   other, no required order.
4. Don't start the Editor (Tier 3) until Tier 2's content is stable.