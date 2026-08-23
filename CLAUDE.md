# CLAUDE.md — context for picking this project back up

This file is for an AI assistant (any model, any conversation) resuming
work on PathoPilot after a context loss. Read this fully before touching
code. It's organized stable-reference-first; **PROGRESS.md** has the
fast-changing "where are we right now" — but verify that against the real
`git log`, since neither of us has live access to the actual repo state
except what the person tells us or pastes in.

## Operational note: the sandbox doesn't persist across long gaps

Confirmed at least once: a real-world gap of ~2 weeks between conversation
turns fully reset the sandbox environment — every project file had to be
reconstructed from the conversation's own history before anything could be
run or tested. If you're resuming after any real gap, check whether your
working files actually exist before assuming they do; if not, budget real
effort to reconstruct them accurately (and verify the reconstruction with
a regression test against known-correct output) before starting new work.

## Who this is for, and how to work with them

One user: a French private-practice pathologist, technically capable
(reads/modifies code confidently, not a professional developer). He wants
to **stay the architecture decision-maker** — a past AI session let itself
drive priorities and architecture, and he hit a wall he couldn't diagnose.
Since then the working pattern has been: propose a design with reasoning,
implement, test thoroughly yourself before showing him anything, let him
verify in his own browser, and **he decides when to commit** — not you.
He explicitly asked to be flagged if he seems to have moved past a natural
commit point without committing, rather than have Claude push for it.

He is not a professional software engineer, but he is careful and asks
good "why" questions — treat his pushback as a signal to actually
reconsider, not just re-explain.

## Core vocabulary — do not blur these

- **Field**: an atomic, reusable user-facing input (number/decimal/select/
  checkbox/text). Its *value* interpolates into a template via
  `{{field_key}}`. `number` is integer-only (fragment counts); `decimal`
  supports fractional values (sizes, volumes) — both the type-coercion
  layer and the actual Streamlit widget need to distinguish them (passing
  an int `value=` to `st.number_input` silently locks it to integer-only
  spinner behavior — a real near-miss, caught before shipping).
- **Block**: one complete micro+conclusion template for one diagnostic
  *shape* at one site. Critically: **granularity matters**. If two
  presentations of the same organ share a sentence structure and only
  differ in a value slotted in (mild/moderate/severe, positive/negative),
  that's Field-driven variation within *one* Block. If they differ in
  actual paragraph *structure* — different concepts being described
  entirely (e.g. a cancer description vs. a normal/inflammatory
  description) — that's a *separate* Block, sharing `site_label`/
  `conclusion_group` with its siblings if relevant, never crammed into one
  template with a diagnosis-type mega-switch. This exact mistake
  (over-broad Blocks) was caught and corrected once already — don't
  reintroduce it.
- **Preset**: a saved, ordered list of Blocks for a recurring case shape,
  with a short, memorable `short_code` matching the person's real aText
  shortcut vocabulary (e.g. `dai`, `etc0`-`etc5`) — preserve his real
  vocabulary, don't invent "clearer" replacements without being asked.
  `Preset_Blocks.field_overrides` lets a Preset pre-fill specific field
  values — this is how "fast variant" presets work (e.g. `etc2` loads
  thyroid cytology pre-filled to Bethesda II). Use this whenever a case
  type has a few common, distinct presentations worth one-click access to.
- **Snippet**: an exact, short phrase reused *verbatim across otherwise-
  unrelated* Blocks (the textbook example: "Absence de signe de
  malignité.", confirmed appearing across 7+ unrelated block types).
  Referenced from a template via `{{ snippet('shortcut_key') }}` — a real
  Jinja2-callable, not just a stored phrase (see `rendering.snippet_lookup`).
  Most of the person's original aText library turned out to be either
  Field-driven variation or distinct Blocks, *not* Snippets — don't assume
  a large snippet library is coming; it's a narrow category.
- **Case**: one actual patient specimen being reported. `status` is
  `'pending'` or `'validated'`. **Validated cases are frozen forever** —
  `rendered_html` never regenerates even if templates change later.
  **Pending cases are live drafts** — reopening one re-renders from
  *current* templates on purpose, so a template fix benefits an
  in-progress case. This distinction was deliberately chosen; don't
  "fix" it into uniform behavior.

## Architecture decisions worth knowing the reasoning for

- **Every non-table Block has a real `macro_template`, separate from
  `micro_template` — this is universal, not a per-block-type opt-in.**
  Went through two wrong framings before landing here: first assumed a
  fold-everything-into-one-string approach was universal, based only on
  a gastric sample; then, when real Gallbladder/Appendix samples showed
  whole-organ specimens need genuine "Examen macroscopique"/"Examen
  microscopique" bold headers, assumed that was *their* special case and
  biopsy-style Blocks (Gastric Trio) should keep the fold pattern
  permanently. Wrong again — the person caught it: `fragment_text` is
  itself a real macro statement, just previously folded in unlabeled.
  `rendering.render_block` takes a `total_specimens` argument and
  decides at render time, per block: 1 specimen in the whole case →
  headers, 2 blank lines around each, header hugs its own content; 2+
  specimens → no headers, one blank line between macro and micro, under
  the block's own numbered specimen-name header (itself absent entirely
  when there's only one specimen — see numbering, below).
  `macro_template=NULL` stays supported for a block with genuinely no
  macro content to state, but every current block has one set. The one
  real exception: `is_table` blocks (future prostate biopsies) — a
  different, row-based rendering path this mechanism doesn't touch.
- **Numbering (both micro block headers and conclusion lines) only
  appears when a case has 2+ specimens.** A single-specimen case gets no
  header at all — not just unnumbered, entirely absent, since the report
  title already identifies the specimen. Implemented independently in
  `rendering.format_micro_plain` and `grouping.render_conclusion_plain`,
  each computing `len(...) == 1` on its own input — confirmed via a real
  single-gallbladder sample.
- **Grouping engine** (`grouping.py`): two adjacent blocks merge into one
  numbered conclusion line if their conclusion text is identical once each
  block's own `site_label` is blanked to a sentinel. This one mechanism
  covers both "same diagnosis, different site" (Antrum+Fundus →
  "antro-fundique") *and* literal-duplicate detection (blocks with no
  `site_label` at all) — don't build a second mechanism for the latter.
- **`conclusion_group`**: contiguous blocks sharing this value form a
  *section* — blank line between different sections, case-level addenda
  placed at the end of their own section (not the whole conclusion).
  Merging requires matching signature **and** matching `conclusion_group`
  — this is a safety property, not just an organizational one: it
  prevents two different clinical categories from ever accidentally
  merging just because their text happened to coincide.
- **`Fields.conclusion_addendum_template`**: for facts captured per-block
  but that are really case-level (e.g. overall H. pylori status). Removed
  from each block's own conclusion, rendered once instead. If all blocks
  using the field disagree on its value, the addendum is silently
  dropped and the user is warned — never guess a synthesis rule (e.g.
  "positive if either site is positive") without being asked.
- **Widget key generation-suffixing**: any widget that's *continuously
  rendered* across a same-preset reset (Case ID, clinical info, Field
  widgets, Master Lock, the manual-edit text areas) needs its key suffixed
  with `_form_generation`, bumped on every reset. This bug pattern was
  found **three separate times** (Case ID, then Fields, then Master Lock)
  before the fix became habitual — check new persistent widgets against
  this pattern proactively, don't wait for it to surface.
- **Reset must trigger on ANY preset selection change, not just
  transitions to/from `-- Select --`.** A fourth instance of the same
  underlying staleness family: switching directly between two Presets
  that share the same underlying Block (e.g. `etc0` → `etc5`) doesn't
  change the field widget's key either, since the key is based on
  `block_id` not `preset_id`. Fixed by tracking the previously-selected
  label and resetting on *any* change, not just specific transitions.
  That first fix routed through `_do_workspace_reset` (the same full
  reset used for "New Case"), which turned out wrong: `_clear_case_
  scoped_state()` bumps `_form_generation`, orphaning the generation-
  scoped Case ID / Renseignements cliniques widget keys even though
  nothing explicitly deletes them — those two fields silently blanked on
  every preset switch, since they're entered before the preset in the
  tab flow and aren't specific to any one preset. Now uses a dedicated
  `_do_preset_switch_reset` flag that captures both under the old
  generation before clearing and re-seeds them under the new one.
- **`_do_workspace_reset` / `_do_case_reopen` / `_do_preset_switch_reset`
  flag + `st.rerun()` pattern**: Streamlit refuses to clear a widget's
  `session_state` value in the same run where that widget already
  rendered. The fix is always: set a flag, call `st.rerun()`, do the
  actual clearing/restoring at the very top of the *next* run, before
  any widget instantiates. Give a *new* one-shot reset need its own new
  flag in this family rather than reusing an existing one for a
  slightly different purpose — reusing `_do_workspace_reset` for the
  preset-switch case is exactly what caused the Case ID bug above.
- **Cross-page reopen uses `st.page_link(..., query_params={...})`, not
  `session_state` + `st.switch_page()`.** Confirmed via Streamlit's own
  GitHub issues that `session_state` is unreliable across `st.switch_page`;
  query params survive because they're baked into the destination URL.
- **One-shot confirmation messages use `session_state`, not `st.toast()`.**
  Confirmed `st.toast()` does not survive an `st.rerun()` called right
  after it (open Streamlit issue) — store the message, pop and display it
  at the top of the next run instead.
- **The reopen text box (and any future input+submit-button pair) uses
  `st.form()`.** A plain `st.text_input` doesn't commit its value until
  blur; a button clicked immediately after typing can read the *previous*
  value. `st.form()` batches them so the value is always current at submit.
- **Duplicate-case-number guard**: warns the moment an existing case
  number is typed (not just at save time), disables both save buttons
  until an explicit confirmation checkbox is checked — but only if the
  case wasn't reached via a legitimate reopen. This pattern (warn early +
  require explicit confirmation before a destructive action) is the
  template for any future "are you sure" moment — prefer it over a native
  browser dialog or `st.dialog`, both less proven in this app so far. It's
  also the most likely template for the field-consistency validation
  question raised in PROGRESS.md (still undecided as of the last session).
- **Jinja2 conditional whitespace**: any separator (space or newline)
  adjacent to a conditional block must go *inside* the conditional, not
  outside it — `"{% if x %}text{% endif %}\n\n"` renders the `\n\n`
  unconditionally even when `x` is false, leaving a stray blank line.
  This exact bug was found in real Gallbladder content — check any
  existing template using this pattern before assuming it's safe.

## Deliberately deferred — don't silently re-decide these

- **Protection against losing unsaved work** (closing the tab, clicking a
  sidebar item mid-edit): considered in depth, then explicitly deprioritized
  by the person — typing a report is fast (~2 min lost, worst case), and he
  wants real-world frequency data before building anything. Don't
  resurrect this speculatively; wait for him to raise it, or for evidence
  it's actually costing him time.
- **Copy-to-Diamic modernization**: the button rests on two separately-
  deprecated technologies (`document.execCommand('copy')` at the browser
  level, `st.components.v1.html` at the Streamlit level — the latter's
  stated removal date has already passed as of this writing, though it
  still works). Known fragile, explicitly not fixed yet — a real rewrite
  (`navigator.clipboard.write()` + `st.iframe`) is warranted eventually.
- **Scroll-to-top / autofocus after Save**: no native Streamlit API for
  either (confirmed via search — both are long-standing open feature
  requests). Only achievable via a JS hack reaching into Streamlit's
  undocumented internal DOM — a third instance of exactly the kind of
  fragility already flagged for Copy-to-Diamic.
- **Bundling Copy into "Save as Validated"**: browser clipboard access
  needs a direct synchronous user gesture; a script firing as a
  consequence of an earlier click (via Streamlit's rerun cycle) doesn't
  reliably qualify. The only workaround is a fragile hidden-button JS
  trick — explicitly rejected in favor of two adjacent buttons.
- **The Editor UI (Tier 3)**: deliberately not started — Tier 2's case-type
  templates are still actively being corrected against real-world usage
  (see PROGRESS.md), and building Editor UI against templates that keep
  changing would mean redoing Editor work too. Wait until Tier 2 settles.
- **Snippet library assumption**: don't assume the person's whole aText
  library will become Snippets. Most of it turned out to be Field-driven
  variation or distinct Blocks. Categorize new content carefully using the
  vocabulary section above before assuming "Snippet."

## Working conventions

**Git**: imperative mood, lowercase after the verb, subject under ~50
chars. Itemized body for any multi-part commit — one bullet per distinct
change, written for a future reader (including a future AI) who needs to
know *what's actually in this commit*, not just a vague gist. Atomic
commit test: "would I want to revert one piece of this without the rest?"
If no, it's one commit, even across multiple files. Never commit
speculative/untested work. **The person decides commit timing** — present
the diff and test results, don't push for a commit.

**Checkpointing vs. committing — different actions, don't conflate them.**
A checkpoint is: working tree left in a self-consistent, tested state,
plus a note in PROGRESS.md's mid-task section (start one when a task
starts, clear it when the task is genuinely done) describing exactly what
was done and why. Cheap, and should happen at every verified sub-step,
regardless of session or budget limits — it's what lets the *next*
session (any tool) pick up a task that died mid-way without reconstructing
intent from a bare diff. A commit is gated on the verification level the
change actually needs, independent of whether a checkpoint just happened:
pure logic/backend changes with solid automated coverage can be committed
without waiting on the person specifically; anything rendering/UI-facing
where correctness means matching his judgment or a real sample should
wait for him to actually look at it — automated tests can be green
throughout while the real thing is still wrong (the Examen macroscopique/
microscopique header structure is the concrete example: every test passed
across two different wrong designs before the person's own sample caught
it). This is a solo local repo with no shared remote — an early commit
that turns out wrong costs nothing to amend or fix up, so that risk
shouldn't discourage checkpointing.

**Mixed-tool workflow.** The person trials multiple AI tools against this
same repo — a free-tier chat without repo access, Cline against API or
OpenRouter credits, eventually Claude Code — to compare costs before
settling on one. Consequences, for any session regardless of which tool:
- Whichever tool makes a change is in the best position to record it
  accurately in PROGRESS.md, right when it happens. Reconstructing intent
  from someone else's diff after the fact is real, avoidable work —
  confirmed directly reconstructing a pasted-in Cline fix, which took
  line-by-line comparison against the local file before it could be
  trusted enough to document.
- A tool without repo access can't checkpoint via git — compensate by
  producing files at each verified sub-step, not just at the end of the
  whole task.
- Don't assume the person has copied your last output into his repo the
  moment you produce it — he may be working in parallel elsewhere, or
  applying changes by hand. Confirm state (`git log`/`git diff`, or ask
  directly) rather than assuming your last output is what's actually live.

**Testing discipline**: this project has no human-in-the-loop for real
browser verification in every session (the person has gone on 2-week
stretches without testing). Compensate by testing thoroughly *before*
presenting anything:
- `python3 -m py_compile` every changed file.
- Direct function calls exercising realistic value combinations (not just
  "happy path") — e.g. all 8 combinations of Gallbladder's 3 boolean/select
  fields were tested individually before being called done.
- `streamlit.testing.v1.AppTest` for anything involving widget interaction,
  `session_state`, or multi-step reruns — it simulates real script reruns
  and widget state, catching bugs that direct function calls can't (e.g.
  the reopen-needs-two-clicks bug, the Field-reset-after-Save bug, the
  etc0→etc5 preset-switch bug).
- A full regression pass across *every* existing case type after any
  engine-level change, not just the new content.
- Full app boot check (`streamlit run` + `curl` every page route) before
  presenting.
- Clean up test data from `pathology.db` and delete throwaway test scripts
  after each round — don't leave residue.
- Be explicit about the boundary of what's actually been verified. `AppTest`
  confirms server-side/session_state correctness; it does **not** run a
  real browser, so it cannot catch frontend-only staleness bugs (this
  distinction mattered directly at least twice). Cross-page browser
  navigation similarly can't be fully verified without the person's own
  browser — say so plainly rather than imply full confidence.

**When something is technically uncertain** (an exact Streamlit API
capability, whether a mechanism is reliable): search and verify before
building, don't guess from training-data memory — this project has been
bitten by wrong assumptions about `st.toast()`, `st.switch_page()`, and
`st.page_link()`'s query param support. Each was checked before relying
on it, and each check changed the actual design.

**When a design decision was made from limited evidence, expect to
revisit it.** The macro/micro folding rule was built from one sample
(gastric), assumed universal, then corrected once more real samples
arrived. Treat single-example generalizations as provisional, and say so
explicitly rather than presenting them as settled.

## The person's real-world workflow (for judging what's actually useful)

Batch processing many cases per day. Two rough patterns: read a whole
stack of cases then type all reports in one sitting, or interleave
(read → write → validate → next). Priority order: read new cases → read
returned additional-technique results → update/validate pending cases →
type brand-new reports. A `#` placeholder in his old aText workflow
deliberately exploited Diamic's own validation to force him to never
accidentally submit unedited pending content — a real, intentional safety
mechanism, not sloppiness (noted as a "later, if we reach production
polish" item, not solved yet). Diamic wipes the clipboard when opening a
new case, so copy-then-open-case ordering matters slightly, but he's
already tolerant of the minor friction this causes. He tracks case numbers
in a separate Word document purely for his own reference — case numbers
are never pasted into Diamic, so the app's rendered report should not
include one either.