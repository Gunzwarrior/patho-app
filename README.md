# PathoPilot

A personal pathology report-writing tool, built to replace a fragmented
workflow of Word + a spreadsheet tracker + aText (a text expander). Built
for one user (a French private-practice pathologist), runs entirely on a
local home server — no cloud, no multi-tenant concerns, no auth system.

## Stack

Python 3 / Streamlit / SQLite. No ORM — raw `sqlite3` throughout.

## Quickstart

```bash
python3 init_db.py       # destructive: drops and rebuilds the whole DB
                          # from schema (init_db.py) + content (seed_data.py)
streamlit run app.py
```

Runs as a Streamlit multi-page app: Workspace (daily report-writing),
Worklist (browse/search/reopen saved cases), Editor (safe, limited content
editing and snapshot export), Manager (raw table views).

## Operational content review

This is an occasional, deliberate clinical-review workflow. It is **not** the
pytest golden-fixture workflow and it does not run when an Editor change is
saved. It never opens `pathology.db`: it takes one exported content snapshot,
loads it into a temporary database, and renders every Preset at its resolved
defaults through the same rendering path used by Workspace and Editor
previews.

1. In the Editor, use **Download current content snapshot** and save the JSON
   file in a review folder (for example, `operational-reviews/snapshots/`),
   never as or over `pathology.db`.
2. From the project directory, create a candidate review artifact:

   ```bash
   venv/bin/python operational_review.py generate \
     --snapshot /safe/path/content-snapshot.json \
     --candidate /safe/path/review-candidate.json
   ```

   The command prints the snapshot hash and the candidate-artifact hash. Open
   the pretty-printed candidate JSON in an editor and review each report's
   `title`, `clinical_info`, `micro_plain`, `conclusion_plain`, `conflicts`,
   and `html`. The tool does not claim that this output is clinically correct;
   that review remains the pathologist's decision.
3. If an accepted artifact already exists, compare the new candidate against
   it:

   ```bash
   venv/bin/python operational_review.py compare \
     --candidate /safe/path/review-candidate.json \
     --accepted /safe/path/review-accepted.json
   ```

   It reports added, removed, changed, and unchanged Presets, prints focused
   report diffs, and prints the exact candidate hash that was compared.
4. Only after review, explicitly accept that exact candidate:

   ```bash
   venv/bin/python operational_review.py accept \
     --candidate /safe/path/review-candidate.json \
     --accepted /safe/path/review-accepted.json \
     --expected-candidate-hash <hash-printed-by-generate-or-compare>
   ```

   Acceptance atomically replaces the accepted artifact and refuses if the
   candidate changed after the displayed hash was calculated. Candidate,
   accepted-artifact, and snapshot paths must be distinct and must never be
   `pathology.db`.

## Repo map

| File | Job |
|---|---|
| `init_db.py` | Schema only (CREATE TABLE). Destructive rebuild. |
| `seed_data.py` | All content — Fields/Blocks/Presets/Snippets, one function per case type. |
| `database.py` | All SQL queries. |
| `rendering.py` | Per-block Jinja2 rendering, text/HTML formatting, the Snippet lookup mechanism. |
| `grouping.py` | The grouping engine — merges/sections conclusions across blocks. |
| `app.py` | Slim multi-page entry point. |
| `pages/workspace.py` | Daily report-building UI — the main screen. |
| `pages/worklist.py` | Browse/search/reopen all saved cases. |
| `pages/editor.py` | Read-only navigation, limited safe content editing, revisions, and snapshot export. |
| `operational_review.py` | Explicit snapshot-fed clinical review, comparison, and acceptance CLI. |
| `pages/manager.py` | Read-only raw table views, for debugging. |

## For AI assistants picking this project back up

Read **`CLAUDE.md`** first (architecture, vocabulary, conventions, hard-won
gotchas), then **`PROGRESS.md`** (current state — but verify it against the
actual `git log`, it can go stale).
