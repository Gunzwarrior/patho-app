# PROGRESS.md — compact current handoff

Read this file first when resuming. It is intentionally short: use
**CLAUDE.md** for working rules, **TESTING.md** for test/DB-isolation rules,
and **HISTORY.md** only when a task needs the detailed rationale or build
record from earlier rounds.

## Current status

**Stage 2 is in progress (uncommitted).** Baseline verification at commit
`12212cc`: `venv/bin/pytest -q` passed (105 tests) and the working tree was
clean. The work is limited to the operational-safety foundation; Editor
content-write controls remain disabled.

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

**Stage 2 final verification — ready for review, uncommitted.** `pytest -q`
passes with 129 tests; changed Python files compile; `git diff --check`
passes; and Streamlit booted successfully with an HTTP 200 homepage check.
No Editor content-write control was added.

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
0997142 Add composed case blocks
8270a53 Add case block composition controls
384f43c Add case block composition plumbing
```

Always verify this against a fresh `git log --oneline -20` and `git status`
at the start of a session; this document describes the checked state above,
not any later local work.

## Editor UI implementation boundary

`EDITOR_UI_PROPOSAL.md` is the frozen design baseline, including its
validated-case protection, pending-case acknowledgement, recovery, test
isolation, and staging requirements. Start at its read-only Stage 1. Do not
enable content writes until Stage 2 is fully implemented and verified; each
stage must leave the repository tested and self-consistent before the next.

Not blockers for starting implementation: extending Quick Type beyond
`dai`, extending field-consistency beyond the Appendix pilot, and future
case-type content such as breast.

## Where to find detail

- **Operational rules and durable architecture:** `CLAUDE.md`
- **Test commands, DB isolation, and fixture ritual:** `TESTING.md`
- **Settled design rationale and completed historical build records:**
  `HISTORY.md`
- **Editor UI (Tier 3) design proposal:** `EDITOR_UI_PROPOSAL.md`
