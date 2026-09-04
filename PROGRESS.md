# PROGRESS.md — compact current handoff

Read this file first when resuming. It is intentionally short: use
**CLAUDE.md** for working rules, **TESTING.md** for test/DB-isolation rules,
and **HISTORY.md** only when a task needs the detailed rationale or build
record from earlier rounds.

## Current status

**Editor UI Stage 2 is complete at baseline commit `d620fdc`** (following the
implementation commit `03f85af`). The final browser review passed, `venv/bin/pytest -q`
passed with 129 tests, and the operational `pathology.db` checksum remained
unchanged across the suite. The working tree was clean immediately after the
commit.

**Stage 3 — Safe direct editing is complete and browser-reviewed.** Direct
writes require an explicit initial manual
snapshot acknowledgement, then use the narrow Block/Field/Snippet/Preset
allowlists only. Each save has a candidate-state render on its own transaction
connection, strict Jinja/snippet/default validation, optimistic concurrency,
and one atomic Content_Revision/Content_Change audit record. Recent revisions
have explicit conflict-safe reversion. The isolated suite passes with 151
tests; `pathology.db` remained unchanged. Do not begin Stage 4 operational
review tooling, Stage 5 model packages, or any Stage 6 structural editing.

**Stage 2 checkpoint 1 — backend safety: verified.** Additive/idempotent
migration, validation history/backfill, immutable validated-case backend
rules and audited return-to-pending transition, per-connection foreign keys,
fail-closed pytest DB redirection, deterministic case-aware snapshot
export/restore, and fingerprint/revision persistence are covered by the
isolated suite. `pytest -q` passed with 113 tests at this checkpoint.

**Stage 2 checkpoint 2 — Workspace safety: verified.** Workspace now uses
Preset IDs rather than display labels, opens validated cases as a saved,
read-only artifact with the explicit audited return action only, and checks a
pending case's relevant-content fingerprint on every rendering rerun. A
changed draft needs acknowledgement for its current fingerprint before either
save action is enabled. The combined suite passes with 116 tests. Final
static and Streamlit boot checks remain before review.

**Stage 2 review corrections — verified.** The adversarial review findings
were fixed without expanding scope: both default DB names now fail closed
under pytest; migration repairs earlier Stage 2 draft fingerprints/history
once without masking later content changes; fingerprints exclude non-rendering
Preset metadata; snapshot relationships are validated exactly; saved-Case
protection compares candidate fingerprints and is rechecked under a locked
apply; and Case history no longer duplicates validation lines. Regression
coverage now includes audit rollback, all composed fingerprint shapes,
same-session acknowledgement invalidation, malformed/case-altering restores,
restore concurrency, and stable relationship IDs.

**Stage 2 browser-review corrections — verified.** The frozen validated view
now has an explicit New Case reset; validated case-number collisions show
only the immutable-record error rather than an inapplicable pending-overwrite
warning; and Workspace's deprecated full-width button arguments were updated
without changing layout behavior. Follow-up browser findings are also fixed:
a second same-session content change can be acknowledged again after stale
consent is cleared, and Preset display labels remain frozen for the current
case generation so a concurrent rename cannot remount the selector and wipe
the form; the new label appears on the next case/reset.

**Stage 2 final verification — reviewed and committed.** `pytest -q`
passes with 129 tests; changed Python files compile; `git diff --check`
passes; and Streamlit booted successfully with an HTTP 200 homepage check.
Thomas completed the browser review, including validated-case navigation,
duplicate validated IDs, continuous fingerprint acknowledgement, and stable
Preset selection across a live rename. No Editor content-write control was
included in Stage 2.

**Stage 3 checkpoint 1 — backend safety: verified.** `content_editing.py`
uses strict table/column allowlists, full-state hashes, savepoint candidate
validation through connection-scoped rendering helpers, default and pending
case renders, discrete checkbox/select branches, and transaction rollback on
either validation or audit failure. It also records/requires the one-time
manual snapshot marker and implements conflict-safe revision revert.

**Stage 3 checkpoint 2 — Editor controls: verified by AppTest.** Editor is
read-only until the initial snapshot download/acknowledgement step. Once
enabled it exposes only the approved forms and new-Snippet creation, preserves
stable-ID selection, shows impact/default previews, and offers explicit safe
revert controls. No structural configuration, deletion control, package
import, or operational-review feature was added.

**Stage 3 adversarial-review corrections — verified.** Editable Jinja now
uses one sandboxed environment in validation and normal rendering, with no
built-in globals and only literal `snippet()` calls. Candidate validation
rejects blank Snippet creation, unsafe/cleared defaults, table-Block edits,
and bad nondefault addendum branches. Field-addendum Snippet dependencies now
drive both pending fingerprints and impact counts. The Editor provides
rollback-only before/candidate Preset previews, generated revision identity,
reversible creation-revert chains, and a reset-safe new-Snippet form. Direct
editing can repair an invalid current template as long as the candidate state
is valid.

**Stage 3 browser-review corrections — verified.** The Editor now uses a
persistent section selector, so ordinary Streamlit reruns no longer send Block,
Snippet, or revision actions back to Presets. Loaded form snapshots also remain
stable until selection or save, allowing the server-side compare-and-swap check
to reject a genuinely stale form. A stale save stays on its current section,
shows an explicit error, and reloads the current database values. AppTest covers
section/entity persistence, stale two-session saves, Snippet creation, and
confirmation/reversion navigation. Thomas completed the browser re-review:
normal edits and previews, persistent navigation, stale-form refusal, Snippet
create/revert/revert restoration, validation failures, scope boundaries, and
Workspace regressions all behaved as intended.

**Stage 3 final verification — browser-approved.** `venv/bin/pytest -q` passes
with 151 tests; changed Python files compile; `git diff --check` passes; and an
isolated Streamlit boot returned HTTP 200. Tests and the boot used temporary
databases and did not modify the operational `pathology.db`.

Editor UI **Stage 1 is complete**:
the unsafe Snippet writer is replaced by a read-only navigator for Presets,
Blocks, Fields, and Snippets. It shows relationships, conservative
pending-case impact (including saved per-case composition), and renders a
Preset's resolved defaults through the real Workspace report pipeline. No
Editor write path, schema migration, or content change is enabled.

Tier 1 (foundation) is complete. Tier 2 content is now considered **stable
enough to expose for self-editing**, with the usual safeguard that a real
template/content change still needs clinical review before an operational
baseline is deliberately updated. Tier 3 (Editor UI) may now be implemented
only in the frozen stages defined in `EDITOR_UI_PROPOSAL.md`.

## Last verified state

- Per-case Block composition is complete through its Stage 3 regression:
  per-case add/remove/reorder, immutable instance identity, save/reopen,
  Quick Type reset, and 4-specimen output are all covered.
- Thomas checked the important browser flows: remove → save → reopen; add →
  save → reopen; and Quick Type after composition.
- `pytest -q`: **101 passed**.
- All golden fixtures regenerate byte-identically.
- The real `pathology.db` checksum was unchanged across the final test run.
- `py_compile`, `git diff --check`, and the Streamlit app boot check passed.
- Stage 1 verification: `pytest -q` reports **105 passed**, including the
  read-only Editor's database relationships, composed-case impact, default
  rendering, and AppTest smoke coverage; `py_compile`, `git diff --check`,
  and a Streamlit boot + HTTP 200 check passed.

Most recent implementation commits:

```
03f85af Add operational safety foundation
12212cc Add read-only editor navigator
efe8d56 Freeze editor UI design
```

Always verify this against a fresh `git log --oneline -20` and `git status`
at the start of a session; this document describes the checked state above,
not any later local work.

## Editor UI implementation boundary

`EDITOR_UI_PROPOSAL.md` is the frozen design baseline, including its
validated-case protection, pending-case acknowledgement, recovery, test
isolation, and staging requirements. Stages 1 and 2 are complete. Start at
Stage 3 only, and leave the repository tested and self-consistent before any
proposal to begin Stage 4.

Not blockers for starting implementation: extending Quick Type beyond
`dai`, extending field-consistency beyond the Appendix pilot, and future
case-type content such as breast.

## Where to find detail

- **Operational rules and durable architecture:** `CLAUDE.md`
- **Test commands, DB isolation, and fixture ritual:** `TESTING.md`
- **Settled design rationale and completed historical build records:**
  `HISTORY.md`
- **Editor UI (Tier 3) design proposal:** `EDITOR_UI_PROPOSAL.md`
