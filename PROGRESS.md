# PROGRESS.md — compact current handoff

Read this file first when resuming. It is intentionally short: use
**CLAUDE.md** for working rules, **TESTING.md** for test/DB-isolation rules,
and **HISTORY.md** only when a task needs the detailed rationale or build
record from earlier rounds.

## Current status

**No task is currently in progress.** The next task is an **Editor UI design
discussion**; implementation has not started.

Tier 1 (foundation) is complete. Tier 2 content is now considered **stable
enough to expose for self-editing**, with the usual safeguard that a real
template/content change still needs clinical review before its fixtures are
updated. Tier 3 (Editor UI) may now be planned.

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

Most recent implementation commits:

```
0997142 Add composed case blocks
8270a53 Add case block composition controls
384f43c Add case block composition plumbing
```

Always verify this against a fresh `git log --oneline -20` and `git status`
at the start of a session; this document describes the checked state above,
not any later local work.

## Editor UI planning boundary

The first Editor UI discussion should decide which editing capabilities are
in scope and the safety model for changing live templates/data. It should
use the existing test suite and golden-fixture review ritual rather than
inventing a parallel workflow.

Not blockers for starting that discussion: extending Quick Type beyond
`dai`, extending field-consistency beyond the Appendix pilot, and future
case-type content such as breast.

## Where to find detail

- **Operational rules and durable architecture:** `CLAUDE.md`
- **Test commands, DB isolation, and fixture ritual:** `TESTING.md`
- **Settled design rationale and completed historical build records:**
  `HISTORY.md`

