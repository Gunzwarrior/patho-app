# PROGRESS.md — current state (verify against real `git log` first)

**This document can go stale the moment a commit happens without it being
updated.** Neither Claude nor this file has live access to the actual repo
— before relying on anything below, ask the person to paste
`git log --oneline -20` (or however far back is useful) and reconcile.
Where this doc says "committed," that's based on the conversation's own
memory of agreeing on a commit message, not a verified fact.

## Where we are right now (read this first)

The person tested all four case types built in the previous session for
the first time in a real browser, after returning from two weeks away.
Real findings surfaced — captured precisely below. **Only one of them has
actually been fixed and verified this session; the rest are open.** This
session ran out of usable length mid-way through fixing the list — resume
by working through the open items below, roughly in the order given.

### Fixed and verified this session

- **Preset-switch reset bug**: switching directly between two Presets that
  share the same underlying Block (e.g. `etc0` → `etc5`, both pointing at
  Thyroid Cytology) didn't reset field values — the old reset logic only
  triggered on transitions to/from `-- Select --`, never on a plain
  preset-to-preset switch, and the widget key (based on `block_id`, not
  `preset_id`) doesn't change between two presets sharing a Block either.
  Fixed in `pages/workspace.py`: now resets on **any** change to the
  selected preset. Verified via `AppTest` (exact `etc0`→`etc5` scenario,
  plus a regression check that switching between genuinely different
  Blocks still works). **Not yet confirmed in the person's real browser.**
  This is the only file that changed this session — everything else
  matches what was already on his machine from before the break.

### Confirmed via a fresh real-report sample, not yet implemented

Re-reading `CR_Sample.docx` this session settled several open questions
precisely:

- **No numbering at all when a case has exactly one specimen** — neither
  on the micro block header nor the conclusion line (a real single-
  gallbladder-specimen sample shows `Cholécystite chronique lithiasique.`
  with no "1." prefix at all). Numbering only appears with 2+ specimens,
  exactly as currently built for those cases.
- **Two distinct macro/micro conventions, not one**: biopsy-fragment-style
  specimens (Gastric Trio) correctly fold macro info into the same
  numbered entry with no section header — the original design was right
  for these. But whole-organ/resection specimens (Gallbladder, Appendix)
  need genuine **"Examen macroscopique" / "Examen microscopique" bold
  section headers**, blank-line separated — confirmed via a real single-
  gallbladder sample. This wasn't built for Gallbladder/Appendix yet.
  Thyroid Cytology's one-line liquid volume/color statement was *assumed*
  to fit the "fold" pattern (not the dedicated-header pattern) by analogy
  — **not explicitly confirmed**, worth double-checking with the person.
- **Remove "MICROSCOPY:" label** from the report shell entirely — doesn't
  appear in any real sample, for either convention.
- **Remove the case number (`N° {case_id}`)** from the rendered report
  entirely — the person only ever tracked it in a separate Word doc for
  himself, never pastes it into Diamic, so the app rendering it is pure
  clutter that doesn't belong in the actual output.

### Bugs found, not yet fixed

- **Blank lines in Gallbladder/Appendix microscopy text.** Root cause
  identified precisely: `"{% if cholesterolosis %}...{% endif %}\n\n"` —
  the `\n\n` sits *outside* the conditional, so it renders unconditionally
  even when the sentence inside doesn't. When `cholesterolosis` is false,
  this leaves a visible blank line where the sentence would have been.
  Same pattern likely present in Appendix's template too — needs a check
  across all four case types' templates, not just the one instance found.
  The general fix pattern: put any separator (space or newline) *inside*
  the conditional block, right where the optional content begins, never
  adjacent-but-outside it.
- **Compactness, more broadly**: the person wants microscopy descriptions
  written as continuous prose (sentences separated by spaces), not
  blank-line-separated — this matches the real samples, which show e.g.
  "La muqueuse est faite de franges... Le chorion abrite..." as one
  continuous paragraph, not blank-line-broken. Gallbladder and Appendix's
  current templates use `\n\n` between conditional sentences in several
  places and need reworking to match, not just the specific empty-
  conditional bug above.

### Content/design requests, not yet implemented

- **Swap Cholesterolosis/Lithiasis field order** in Gallbladder — the
  person's exact words: *"one is almost always considered, the other
  rarely so it should be last."* Likely means Lithiasis first (the more
  routinely salient finding for a cholecystectomy), Cholesterolosis last
  — but this is an inference, not confirmed; check with him before
  assuming which way round.
- **A separate, editable "Title" field**, pre-filled per Preset but not
  identical to the Preset's internal `name` (used for the dropdown/
  browsing, not the report). Examples given: Gastric Trio's title should
  default to "Biopsies gastroduodénales" (not "Gastric Trio"); Thyroid
  Cytology's title defaults to "Cytologie thyroïdienne" but the person
  routinely *extends* it by hand per case with site context, e.g.
  "Cytologie thyroïdienne lobaire gauche" — so it must stay freely
  editable, not locked to the preset default. Appendix and Gallbladder's
  current titles are already fine as-is (match their preset names).
  Implementation sketch (not yet built): add `Presets.default_title`
  (nullable TEXT, distinct from `name`), a new Title widget in Workspace
  pre-filled from it, wired into `assemble_report_html` in place of
  `preset["name"]`.

### Genuinely new architectural question — needs a real decision, not just a fix

- **Field-consistency validation.** The person's example: selecting
  "fausses membranes" (false membranes) on Appendix shouldn't be
  combinable with mild severity levels (`endo`, `suppuree`) or `intervalle`
  — clinically inconsistent. His own framing offered two options: prevent
  the invalid combination outright (dynamically restrict which
  `appendicite_type` options are selectable based on `false_membranes`),
  or at minimum show an inconsistency warning. Neither exists in the
  current architecture — Field rendering has no concept of one field's
  options depending on another field's value, and there's no place to
  declare "these values together are questionable." The warn-and-require-
  confirmation pattern already built for the duplicate-case guard is the
  most likely reusable template for the "warning" option, but this is a
  real design decision, not a quick patch — likely to recur for other
  organs' field combinations later, so whatever gets built should be
  general, not Appendix-specific. **Discuss before building.**

## Overall shape

The plan has two tiers:
- **Tier 1 — foundation**: schema, reopen mechanism, real pending/validated
  status, Worklist, the two-button Save split. **Complete.**
- **Tier 2 — breadth**: build out real case types (content), using the
  now-stable engine. **In progress** — 4 case types built, currently being
  refined against real-world testing feedback (see above).
- **Tier 3 (informal) — self-service**: the Editor UI. **Not started** —
  deliberately deferred until the Tier 2 refinement above settles, since
  building the Editor against templates that are still actively changing
  would mean re-doing Editor work too.

## Immediate next steps, if resuming without other instructions

1. Ask for `git log` to establish real state, and whether the person has
   tested the preset-switch fix in his own browser yet.
2. Work through the "Confirmed, not yet implemented" and "Bugs found, not
   yet fixed" sections above — they're independent of each other and
   don't need a specific sequence, but all four case types' templates
   likely need touching for the compactness/blank-line fix, so batch that
   work rather than doing it four separate times.
3. Raise the field-consistency architectural question explicitly and get
   a real decision before building anything for it.
4. Don't start the Editor (Tier 3) until Tier 2's content is stable —
   see reasoning above.