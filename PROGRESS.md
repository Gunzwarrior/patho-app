# PROGRESS.md — compact current handoff

Read this file first when resuming. It is intentionally short: use
**CLAUDE.md** for working rules, **TESTING.md** for test/DB-isolation rules,
and **HISTORY.md** only when a task needs the detailed rationale or build
record from earlier rounds.

## Current status

**No task is currently in progress.** Editor UI **Stage 1 is complete**:
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
