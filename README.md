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
Worklist (browse/search/reopen saved cases), Editor (add Snippets — Fields/
Blocks/Presets editing not built yet), Manager (raw table views).

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
| `pages/editor.py` | Add/list Snippets (Fields/Blocks/Presets editing not built yet). |
| `pages/manager.py` | Read-only raw table views, for debugging. |

## For AI assistants picking this project back up

Read **`CLAUDE.md`** first (architecture, vocabulary, conventions, hard-won
gotchas), then **`PROGRESS.md`** (current state — but verify it against the
actual `git log`, it can go stale).