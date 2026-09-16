# PROGRESS.md — compact current handoff

Read this file first when resuming. It is intentionally short: use
**CLAUDE.md** for working rules, **TESTING.md** for test/DB-isolation rules,
and **HISTORY.md** only when a task needs the detailed rationale or build
record from earlier rounds.

## Current status

**PR1 — Permanent pending-Case deletion: complete and manually accepted
(2026-09-16).** A single transactional deletion operation resolves the Case
inside its write transaction, refuses missing/currently validated Cases,
cleans Case-owned history/reference rows, and removes batch provenance only
after its final referencing Case is gone. Worklist exposes deliberate,
pending-only deletion with immediate disappearance after success. Isolated
PR1 + Stage 2 safety coverage passed 26 tests; Stage 7 bulk-provenance
regression coverage passed 23 tests; `py_compile` and `git diff --check`
passed. No operational database was opened or modified. PR2 has not begun.

**Stage 7 final acceptance is complete. The focused Stage 7 suite passed 96
tests; the final full suite passed 597 tests in 351.87s; browser acceptance,
`py_compile`, and `git diff --check` passed. Independent Sol review found no
functional or safety violation; stale completion documentation was the sole
remaining blocker.**
The authoritative architecture and seven-checkpoint contract is
`STAGE7_IMPLEMENTATION_PLAN.md`, based on the completed Stage 6 repository at
`a23e917cc674481d6ab1e33f50ab0da13d1f33ab`. It preserves separate content-
configuration and operational Case planes: Quick Type/consistency authoring
reuses Content Studio candidate → frozen review → Apply → inverse, while bulk
intake has a separate no-write decoded preview and atomic pending-only Case
Apply built on the shared Case persistence primitive. Checkpoint 6's
`etc0`–`etc5` consolidation is conditional and does not block bulk intake.

**Stage 6 checkpoint 7 — accepted and complete.** Editor has one reachable human authoring surface:
Content Studio. The remaining unused one-row direct-save/preview helpers were
removed, so manual Fields, Snippets, Blocks, Presets, relationships, lifecycle
actions, AI packages, and reviewed inverses all retain the frozen review →
confirmation → Apply boundary. The common review renderer now uses neutral
review language; its recovery-snapshot gate and the backend gate still protect
every Apply. Table Blocks/table-bearing Presets remain read-only; Stage 7
Quick Type and same-Block consistency-rule authoring now use that surface.

CP7 adds source-level regressions proving that the Editor embeds no legacy
direct writer, alongside the existing UI/transaction coverage for lifecycle,
inheritance, pending acknowledgement, archived pending resolution, validated
Preset detachment, stale/two-tab refusal, recovery gating, AI packages, and
reviewed inverses. Final-review remediation adds signed physical-row draft
assertions for Field/Snippet/group-label edits; validates the required
detach → images → attach order for inverse and inverse-of-inverse; preserves
detached validated artifacts through snapshot recovery without live rendering;
removes the legacy one-click revision revert; and compares complete before and
candidate validated-Case graphs so the signed warning counts every new loss of
Return-to-pending reconstruction (including Block deletion and reviewed
inverse-of-inverse), rather than special-casing Preset detachments.
The shared local guard now also digests the complete validated-Case set and
full reconstruction input rows, so late Case arrival or changes after review
make both ordinary destructive Apply and reviewed inverse Apply stale without
exposing Case data in the review payload or audit.
`TESTING.md` records the supported isolated snapshot-restore drill. Final
independent acceptance passed the full isolated suite: **493 passed in
302.06s**. It independently reproduced and correctly rejected a late validated-Case
review/Apply race; completed the browser matrix; verified validated deletion →
inverse → inverse-of-inverse, stale-before-Prepare protection, recovery restore
with canonical hash equality, and boot of the isolated restored database.
Compilation and `git diff --check` passed; the reviewer found no remaining
implementation or data-integrity defect. No operational database, golden
fixture, seed content, or accepted review artifact was modified.

**Stage 6 checkpoint 3 — complete and approved for commit.** The
candidate service now expands every base lifecycle action through the approved
read-only Content Studio planner: archive follows the upward active-dependency
closure, restoration brings back archived prerequisites, and deletion refuses
persisted pending dependencies while explicitly cleaning owned configuration
and detaching every validated Case reference for a Preset deletion. The final
graph accepts only usable active Presets/Quick Type/table/consistency paths;
archived data and relationships remain physically intact for saved drafts.

Normal Workspace and Quick Type selection now expose active Presets/Blocks
only. Reopening a pending Case injects its archived Preset just for that case
generation and resolves archived Blocks, Fields and Snippets by their saved
identities; a new-case reset removes it again. `display_order` now controls
new/default resolution while saved explicit instance lists retain their order.
Archive-only changes leave pending fingerprints unchanged; actual legacy
default-order changes surface ordinary pending impact. Validated → pending
preflights full reconstruction inside its transaction and refuses without a
status change when permanent deletion made it impossible. Worklist continues
to use frozen Preset labels after detachment. Reopening any validated Case now
takes the frozen-artifact path before live Preset lookup, so a permanently
deleted Preset cannot hide its validated report; its attempted return to
pending reaches the existing reconstruction preflight and remains refused.

CP3 review remediation makes saved pending composition—not Preset archive
state—the archived-resolution authority, validates every active Block's
Field/Snippet availability even when orphaned, and fingerprints explicit
Preset-Block link presence so unlink/relink requires draft acknowledgement.
Lifecycle summaries now distinguish direct dependents, archive closure,
restore prerequisites, deletion cleanup, validated detachments, blockers, and
refusal reasons; validated detachments appear only when an executable Preset
deletion plan actually includes them; shared candidate expansion still
enforces them. The explicit deletion-eligibility
race is guarded at Apply by the existing pending-set local guard. Legacy
fingerprint migration now upgrades only no-instance compositions whose old
fingerprint matches current old-format content. Every explicit composition is
left requiring acknowledgement because its historic link presence cannot be
proved; a database that ran the superseded v1 baseline is explicitly failed
closed for those drafts.

`tests/test_stage6_lifecycle.py` adds focused lifecycle summary, explicit-link
impact, conservative fingerprint-baseline, and deletion-race tests; Workspace
AppTests cover archived Preset and active-Preset/archived-ad-hoc reopening;
candidate tests cover orphan active Block dependency rejection. Focused Stage
2 safety/lifecycle coverage passed 29 tests; the final isolated suite passed
414 tests in 259.52 s. Changed Python files compile and `git diff --check`
passes.
No operational database, seed content, golden fixtures, or accepted review
artifact was opened or changed. An isolated temporary-database Streamlit boot
returned HTTP 200 for all five routes, then its server/database were removed.
**Stage 6 checkpoint 4 — complete, independently reviewed, and browser
verified.** The Editor now opens on Content Studio, where Fields,
Snippets, and conclusion group labels use the shared content candidate service
only: create/edit/lifecycle actions prepare a frozen review, require explicit
confirmation, and Apply atomically. The old immediate-save Field/Snippet UI
paths are gone; Presets and Blocks remain intentionally read-only until their
own checkpoints. Content Studio provides Active/Archived/All filters,
stable-ID selectors with archive badges, typed Field defaults (including a
type picker outside the form so a new select Field can establish its options
before choosing a default), immutable keys/types/options, canonical group-key
sets, and archive/restore/deletion-eligibility panels.

`tests/test_stage6_ui.py` covers the no-write frozen-review boundary, the
separate confirmed Apply action, typed-default rebuild, decimal review, and
archived filter/badge. Existing Editor AppTests were migrated to assert the
new single authoring path. The focused CP4/Editor/lifecycle/Stage 5 UI and
candidate selections passed after remediation; the final isolated suite passed
**428 tests**. Changed Python files compile and `git diff --check` passes.
Completed browser verification covered typed Fields, addendum-linked Snippet
lifecycle, archived group labels, report review, and stale-review behaviour.
No operational database, seed content, golden fixtures, or accepted-review
artifact was opened or changed.

**Stage 6 checkpoint 5 — complete, independently reviewed, remediated, and
approved for commit.** Supported non-table Blocks now use the frozen Content
Studio review → confirmation → Apply workflow for create, edit, duplicate,
lifecycle, and coordinated `Block_Fields` changes. Stable keys and table
Blocks remain read-only; duplication copies only Block-owned configuration,
not Preset relationships. Complete physical Block/binding identities, every
desired Field endpoint, and copied consistency rules are bound into
non-mutating candidate assertions, closing stale-draft, TOCTOU, and endpoint
ABA paths while preserving typed override storage and post-review Apply
guards.

`tests/test_stage6_blocks.py` covers Block authoring, duplication, binding
add/remove/reorder, override semantics, template coordination, lifecycle,
pending impact, and adversarial stale-source/identity races. Manual browser
verification and final independent review passed; the final isolated suite
passed **459 tests**. Changed Python files compile and diff checks pass.

**Stage 6 checkpoint 6 — Preset Studio and `Preset_Blocks` — accepted for
commit.** Preset create/edit/duplicate, lifecycle, composition, ordering, and
per-instance Field overrides use the same frozen review → confirmation → Apply
path and source/endpoint identity protections as the preceding Content Studio
checkpoints. `short_code` and instance `sort_order` remain immutable;
`display_order` alone changes default presentation. Duplicate Block instances,
including their individual overrides and inherit/null/zero/false/empty-string
semantics, remain distinct. Draft-only instance endpoint baselines are retained
per instance, and complete Field metadata/physical identity participates in
stale protection. Frozen reviews include prefix-overlap and duplicate-without-
Quick-Type warnings.

Final independent acceptance closed all CP6 blockers: the full isolated suite
passed **476 tests** and focused independent verification passed **76 tests**.
The endpoint ABA/rebasing regression was independently re-tested and fixed;
compilation and `git diff --check` passed. CP7 has not begun.

**Stage 6 checkpoint 2 remediation — complete locally and awaiting approval.**
The independent review's blocking findings are addressed without extending
into Checkpoint 3: generalized inverses now refuse pending-dependent removals
and show pending impact; final-graph validation checks usable Presets, Quick
Type ownership/types, table-row ownership, relationship order, and effective
defaults; and Case-reference changes are limited to complete validated-Case
detachments accompanying that Preset's reviewed deletion. Audit replay binds
the reference IDs and frozen keys to the exact deleted/recreated Preset image.
The generalized multi-column no-op check now refuses only when every supplied
value is unchanged. Seventeen isolated candidate tests, including adversarial
coverage for these findings, pass; this remediation is uncommitted.

**Stage 6 checkpoint 2 — complete, manually regression-reviewed, and ready
to commit.** `content_studio.py`
now translates guided-form actions into internal stable-key operations without
writing directly. `content_changes.review_candidate(..., internal=True)`
uses the existing candidate copy, local stale guard and immutable review, but
now materialises base content, all six configuration tables, archive/restore,
relationship reorder/override changes, explicit cleanup, and narrowly scoped
validated-Case Preset detachments. It records exact physical content/config
row images in `Content_Changes`, and Case reference changes only in
`Case_Content_Reference_Changes`, with no clinical text. Generalised inverses
restore original IDs, configuration/order rows, archive flags and detached
validated references; legacy Stage 3/5 audit paths remain intact. The external
AI v1 parser remains unchanged and still takes its original narrow path.

`tests/test_stage6_candidates.py` currently has seven isolated tests for
no-write review, stale/rollback guards, archive/revert/revert, deterministic
intent ordering, all configuration tables, display-order restoration, and
validated Preset deletion/detachment/inverse reattachment. Focused results:
7 Stage 6 candidate tests; 32 Stage 2/4/schema compatibility tests; 12
Stage 5 package-contract regression tests; and 9 Stage 5 transaction/inverse
regressions passed. `py_compile` and `git diff --check` pass. No operational
database, seed content, goldens or accepted artifacts was opened or changed.

**Stage 6 checkpoint 1 — complete and serves as the foundation for
checkpoint 2.** The foundation was implemented in `9445e8b`, manually
browser-checked, and independently reviewed by Sol High. That review found
the validation-history Preset-identity migration defect; its conservative,
separate idempotent repair migration was independently reviewed and verified.
The working tree has the approved additive persistence and
compatibility foundation only: `is_archived` on base content, independent
`Preset_Blocks.display_order`, frozen Preset code/name columns on Cases and
validation history, and the empty Case-content-reference audit table. The
named `stage6_persistence_compatibility_v1` migration backfills active state,
display order and frozen identities once without rebuilding tables or
regenerating reports. Snapshot export is now v2; v1 recovery files normalise
read-only to v2, and operational-review artifacts hash that normalised v2
source. The AI v1 package format itself remains unchanged; its contract marks
archived base rows read-only and it rejects archived update/link targets.

The bounded remediation corrects fresh validation-history backfill to resolve
each row through its own `preset_id`, and adds the named, idempotent
`stage6_validation_history_preset_identity_repair_v1` marker for databases
that already ran the original migration. The repair changes only a strictly pre-marker
history row with a resolvable, non-null `preset_id` different from its parent
Case's current `preset_id`, where both frozen fields exactly equal the Case's
frozen fields. Null/unavailable references and every ambiguous frozen identity
are preserved. The isolated Stage 6/Stage 2/Stage 4 migration selection,
compilation, and `git diff --check` pass (32 tests). No operational database,
seed content, golden, or accepted artifact was opened or changed.

New schema tests cover an actual pre-Stage-6 schema migration with pending and
validated Cases, repeat migration, IDs/reports/history, v1/v2 restore,
operational artifacts for both formats, archived package refusal, and legacy
Stage 5 audit-image readability. Focused Stage 2/4/schema tests passed 28;
all 133 Stage 5 package tests and all 53 Stage 5 transaction tests passed in
bounded shards; golden/pure rendering tests passed 89; Stage 3 Editor/backend
tests passed 26; Workspace AppTests passed 33; Stage 5 UI AppTests passed 14.
`py_compile`, `git diff --check`, and collection (380 tests) pass. No
operational database, seed content, goldens, or accepted artifact was changed.
The manual browser check and independent Sol High remediation review passed;
Checkpoint 1 is complete.

**Stage 5 is complete: checkpoint 1 is `220cd2e`, checkpoint 2 is
`5868ad9`, and checkpoint 3 is `5522a7b`.** The final checkpoint 1
authoring-contract refinement and checkpoint 2 safe diagnostics are committed
as `de8ccce` and `3b93dea`; the independently reviewed blocking remediation is
`717fda6`. Thomas authorized the checkpoint 1 commit and continuation with
checkpoint 2. Thomas browser-checked Appendix/Gallbladder, thyroid decimals,
duplication/grouping, manual lock, Editor previews and frozen validated reports.
Thomas also browser-confirmed the wildcard fix: correct duplicate targeting,
save/reopen persistence and removal. He reported the first composition action
after reopen closing the section. Thomas has now confirmed that the section
fix and the authorized live thyroid correction also work in his browser.
Thomas completed the checkpoint 3 browser review on 2026-09-08 and approved
the functionality and commit. External real-model acceptance completed on
2026-09-10. At that Stage 5 boundary, Stages 6–7 remained; current authority
for later work is recorded at the top of this file.
Thomas confirmed that PathoPilot must remain fully usable without a paid AI plan. Stage 5 is now an optional, token-economical AI context/change-
package workflow; Stage 6 is a committed autonomous Content Studio for guided
creation, relationship management, editing, archive, and safe deletion; Stage
7 is a Quick Type Studio plus atomic two-column `(case ID, Quick Type)` bulk
intake that creates pending Cases for review. Modifications continue to use the
existing pending-content acknowledgement. Removal archives content when a
pending draft depends on it and offers permanent deletion when no pending
dependency remains and the complete candidate is valid. See
`EDITOR_UI_PROPOSAL.md` §§1, 3, 4, 8–10 and
`STAGE7_IMPLEMENTATION_PLAN.md`. Each future stage or checkpoint requires its
approved bounded contract before coding.

**Stage 5 checkpoint 1 — verified on 2026-09-05.**
`change_packages.py` provides the compact content-only AI export, generated
v1 contract/example, bounded strict parser, canonical package hashing, and
separate fixed AI correction feedback. `content_changes.py` provides reusable
create/update/link candidate operations, complete final-graph validation,
one in-memory SQLite backup per review, exact physical change images,
complete Preset and affected-pending reports, and an immutable server-held
review. Its local guard binds revision identity, content row identities and
the complete pending set; no guard or Case data enters AI exports.

Snapshot exports now use one read transaction and the shared snapshot hash;
Stage 4 artifacts and the existing manual snapshot gate retain the same byte
and hash conventions. Connection-aware consistency checks and shared Workspace
assembly preserve macro/micro layout, grouping/addenda, decimal widget strings,
wildcard positions, instance-specific overrides, duplicate/ad hoc identities,
both locks and empty manual edits. Candidate validation still renders automatic
underlying content under locks and checks every pending Case. Invalid base
reports remain local before-errors when the candidate repairs them. Orphan
content and individual discrete branches are validated, with warnings retained.

**Concrete compatibility correction:** the established thyroid Fields
`nodule_site` and `nodule_eutirads` contain an empty select option, and
`etc_bi` stores decimal overrides as strings. Existing values remain valid
under the production resolver; nonblank new select options and native typed
package values are enforced at the package boundary. Nothing rewrites those
legacy values or seed content. Report display sanitization stays in checkpoint
3 as specified by the plan's delivery sequence; no package report UI is exposed.

**Verification:** the focused package/Workspace run passed **137 tests**;
the final full isolated `venv/bin/pytest -q` passed **274 tests** (61.12 s),
including Stage 2/3/4, Editor and golden regressions. Changed Python files
compile; `git diff --check` passes. An isolated Streamlit server returned
HTTP 200 for the homepage and all four page routes; localhost socket access
required sandbox escalation. AppTest verifies saved-case/Workspace component
and HTML parity, not real-browser appearance. The seeded AI-context export is
**24,215 UTF-8 bytes** (including contract); its snapshot is **20,571 bytes**.
Operational `pathology.db` was never opened through SQLite; its file SHA-256
remained `50978f4ae0cf96b5feffd4c079174d1b1cda45de4232f7dd936d59c5d526be0c`.
No golden regeneration, accepted-review changes, or seed changes. Before the
checkpoint commit, the full suite passed again: **274 tests in 60.37 s**;
compilation, whitespace checks and the operational checksum also passed.
Browser appearance remains for Thomas to check; no new package UI exists yet.

**Checkpoint 2 — verified on 2026-09-06; committed.** Atomic Apply and reviewed inverse preparation
are implemented in `content_changes.py`, with three additive nullable revision
provenance columns. The backend binds issued immutable reviews to locked live
state, checks exact changed-row/audit images, restricts writes by transaction
phase, and preserves original IDs through inverses. Existing manual reverts
remain supported; the legacy endpoint refuses new reviewed revisions so it
cannot bypass inverse confirmation. The initial transaction suite passed
41 tests, and the existing package/Stage 2/Stage 3 selection passed 146 tests.
The expanded transaction suite passed 52 tests (129.90 s). The focused
package/Workspace/golden regression passed 164 tests (56.43 s), including
the four new browser-defect regressions. The final full isolated suite passed
**330 tests in 192.35 s**. All changed Python files compile; `git diff --check`
passes. An isolated Streamlit server returned HTTP 200 for `/`, `/workspace`,
`/worklist`, `/editor` and `/manager`. Tests and boot never opened operational
`pathology.db` through SQLite. During this round its SHA-256 remained
`9622dd14e544e266941f26e28342efc53dd88102aca5e7b4d09991bc6782efa4`;
this differs from checkpoint 1's earlier checksum after Thomas's browser use.
No operational content correction had been applied at that verification point.

**Browser defects reported on 2026-09-06:** Workspace wildcard selection now
uses `(block_id, instance_no)` with numbered specimen labels, preserving the
existing saved-note schema. AppTest verifies the second duplicate, reorder,
save/reopen and target removal. The thyroid macro now conditionally includes
`de … mL` only for a supplied volume: blank gives `Liquide clair.`, while zero
and comma-decimal input remain visible. Three AppTests verify each value through
save/reopen. This explicit requested wording correction also updates bootstrap
seed content; default golden reports are unchanged. The compact seeded export
is now **24,263 bytes**, snapshot **20,619 bytes** (+48 template bytes).
Thomas explicitly authorized the current-library correction on 2026-09-06.
It was applied through `content_editing.save_edit` as **revision 25**, with the
existing initial-snapshot gate, exact old-template match and optimistic row
hash. Readback confirmed only `Blocks.thyroid_cytology.macro_template` changed;
the connection authorizer denied Case/history and other unrelated writes.
The operational file hash changed intentionally from
`363470bbb63257f364978830a614c3f2177e75001fcdff321e77bcdac4296eb2` to
`1831ffe96373cd46174086f36f41623f5a75e06552672b59222a992414320dcb`.
No automatic content migration, reseeding or saved-report rewrite was used.
Existing pending drafts retain the normal content-change acknowledgement;
validated artifacts remain frozen.

**Follow-up composition fix — 2026-09-06:** the one-shot reopen notice disappeared
on the first interaction and shifted the unkeyed composition expander from
render-tree position 5 to 4, remounting it closed. All one-shot notices now
occupy a single persistent container, preserving following layout positions
without new session flags or changing the expander default. Four AppTests
(move up/down, remove, add; each with repeated reopen) failed on the original
code and passed after the fix (2.33 s). This verifies stable layout positions;
Thomas subsequently confirmed the browser behavior works. Final isolated
regression passed **334 tests in 193.58 s**. Changed Python files compile;
`git diff --check` and isolated app boot on all five routes pass. The operational
checksum stayed at the post-correction hash throughout these tests. Checkpoint
2 and these browser fixes are included in the current checkpoint commit.

**Independent checkpoint 1–2 review corrections — 2026-09-06:** three
confirmed review findings are resolved. Snippet dependencies now use shared
Jinja AST analysis, so every accepted literal call syntax participates in
Preset impact, pending fingerprints and inverse dependency refusal. Candidate,
standalone and Stage 3 validation now render resolved defaults as freshly
mounted Workspace widgets expose them; nullable text therefore cannot hide an
active branch behind SQL `NULL`. Workspace decimal initialization preserves
native numeric zero from package/Preset overrides. Focused defect and Stage
3/4 compatibility checks passed 26 tests; the package/transaction/Workspace
selection passed 201 tests; the final isolated suite passed **338 tests in
212.04 s**. Changed Python files compile and `git diff --check` passes. All
five routes returned HTTP 200 from an isolated Streamlit boot. All database
reproductions, tests and boot checks used temporary isolated files;
operational `pathology.db` was not opened.

**Stage 5 checkpoint 3 — implemented and verified on 2026-09-07; browser
review passed on 2026-09-08.** Editor now has one persistent **AI package**
section. It exposes the measured content-only AI-context download with separate
snapshot/download hashes, exact UTF-8 byte size, privacy/economy wording, and an
explicit distinction from the recovery snapshot. Upload, dry run, exact-review
confirmation, and Apply are separate actions. Session-local generation-bound
state detects byte changes including same-name replacement, clears stale consent,
never treats browser operations as proof of review, and retains the backend
recovery and stale-state gates.

Successful reviews show the complete normalized operation list, exact physical
before/after values, template diffs, warnings, selectable full Preset previews,
standalone content, and affected pending-Case current/candidate/saved reports.
Fixed allowlisted AI feedback is separate from local session-only error/report
details. Successful Apply reports the revision ID, clears loaded manual forms,
keeps the AI-package section selected, and points to Recent revisions. Reviewed
revision inverses there have separate Prepare, confirmation, and Apply actions;
legacy manual reverts retain their existing flow.

`report_presentation.py` is the single display-only restricted HTML boundary.
It preserves PathoPilot's report typography, emphasis, spacing, lists, and table
formatting while removing scripts/active resource elements, event attributes,
external-resource URL attributes, and unsafe CSS. Editor package/default previews
and every Workspace report display use it. Canonical rendering, validated/saved
artifacts, and database report HTML are unchanged.

Checkpoint 3 AppTests cover section/result persistence, upload removal and
same-name replacement, failed dry run, recovery gating, full readable reports,
safe feedback, pending local reports, two-session staleness, failed/successful
Apply, form clearing, inverse navigation, and restricted HTML in Editor and
Workspace. The focused run passed **56 tests in 34.68 s**. The final full
isolated suite passed **351 tests in 233.02 s**. All changed Python files compile;
`git diff --check` passes; and an isolated temporary-database Streamlit boot
returned HTTP 200 for `/`, `/workspace`, `/worklist`, `/editor`, and `/manager`.
The operational `pathology.db` was not opened; its file SHA-256 remained
`436259e755802b08be659fa2fac589f209b0b40603b14905261d217bfc1acb93`
across final verification. No golden fixture, accepted operational-review
artifact, or seed content was changed.

**Final regression and real-model acceptance — 2026-09-10.** The current
isolated test selection passed all **370 collected tests** (run in bounded
terminal shards); its focused Stage 5 package/transaction/Editor/Workspace
selection passed **243 tests**. Relevant Python files compiled and `git diff
--check` passed. A temporary seeded database boot returned HTTP 200 for `/`,
`/workspace`, `/worklist`, `/editor`, and `/manager`; it was removed after the
check. The operational database was not opened through SQLite and its raw
SHA-256 was unchanged before and after:
`5fa1abcc1a69d623108291e9e5839b85767a9ca99f89d4b2a2e316584d89477e`.
No golden fixtures, accepted operational-review artifacts, or seed content
were changed.

For independent real-model acceptance, the same fresh
`pathopilot-ai-context-v1` export and fully specified request were given
independently to Sonnet 5, ChatGPT High, and Gemini, without a reference answer
or repository access. Each returned a valid first package that passed dry run.
Their JSON differed slightly in bytes (about 1.0–1.4 kB) but produced the same
intended normalized/rendered result in PathoPilot. For the intentionally
incomplete Block request, all three declined to return a package and asked for
missing clinical information; none invented wording, used placeholders, or
returned an empty package. In the corrective-feedback loop, PathoPilot safely
rejected a deliberately malformed relationship key with `link_key` feedback;
Sonnet 5 received the original package, context export, and copied feedback,
then returned a corrected replacement that passed dry run on its first attempt.

**Next boundary:** Stage 5 is complete. Do not begin Stage 6 or Stage 7 without
the next bounded authorization. The deferred Stage 5 items remain deferred;
this acceptance did not add a v2 format, alter parser/validator semantics, or
change clinical content.

**Editor UI Stages 1–3 are complete at baseline commit `c19fe85`**
(`Complete safe direct editing`). Stage 3 was browser-reviewed; its isolated
suite passed with 151 tests and did not alter operational `pathology.db`.

**Stage 3 — Safe direct editing is complete and browser-reviewed.** Direct
writes require an explicit initial manual
snapshot acknowledgement, then use the narrow Block/Field/Snippet/Preset
allowlists only. Each save has a candidate-state render on its own transaction
connection, strict Jinja/snippet/default validation, optimistic concurrency,
and one atomic Content_Revision/Content_Change audit record. Recent revisions
have explicit conflict-safe reversion.

**Stage 4 — Operational content review is complete.** `operational_review.py` accepts only an explicitly supplied
`pathopilot-content-snapshot-v1` file, displays/records its canonical content
SHA-256, materializes it in a fresh temporary database, and renders every
Preset's resolved defaults through the connection-aware production preview
path. Its canonical JSON artifacts are separate from pytest goldens; compare
reports added/removed/changed/unchanged Presets, and acceptance is a separate
atomic action bound to the candidate hash Thomas compared. The focused suite
has 5 tests; the full isolated suite has 156 tests. Tests tripwire both the
default connector and a direct canary SQLite path, and neither tests nor the
tool open `pathology.db`. Thomas manually exercised snapshot generation,
candidate inspection, comparison, and explicit acceptance. Stages 5–7 are
approved roadmap outcomes but remain unimplemented.

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

Selected implementation commits:

```
220cd2e Add no-write package review
c19fe85 Complete safe direct editing
d620fdc Update documentation after Editor UI Stage 2 completion
03f85af Add operational safety foundation
```

Always verify this against a fresh `git log --oneline -20` and `git status`
at the start of a session; this document describes the checked state above,
not any later local work.

## Editor UI implementation boundary

`EDITOR_UI_PROPOSAL.md` preserves the frozen implemented safety baseline and
forward roadmap. Stages 1–7 are complete. Future work needs its own bounded
authorization; Stage 7 remains governed by the completed contract in
`STAGE7_IMPLEMENTATION_PLAN.md`.

### Stage 7 CP1 — configuration-authoring safety foundation

CP1 is implemented as a backend-only boundary. Complete Quick Type token and
same-Block consistency-rule drafts now plan through Content Studio's existing
frozen review, confirmation, atomic Apply, audit, and reviewed inverse path;
no Stage 7 form or Case/bulk writer exists. The additive named migration adds
the unique `(preset_id, sort_order)` Quick Type position index after a
read-only duplicate preflight. Connection-scoped Quick Type parsing is
available for candidate-state callers and does not reopen the operational
database. The remediation pass added minimal-diff composition, typed decimal
rule canonicalization, and generalized archived/table-owner enforcement;
focused CP1/candidate/schema/Quick Type/rule tests pass. CP2–CP6 remain
explicitly unimplemented.

Quick Type expansion is no longer an incidental non-blocker: it is the focus
of Stage 7, including authoring and bulk pending-case intake. Future case-type
content such as breast may be created through either the optional Stage 5 AI
workflow or the independent Stage 6 Content Studio.

### Stage 7 CP3 — consistency-rule authoring

CP3 is accepted. Content Studio now provides guided create/edit/delete for a
complete same-Block warn-and-confirm rule set on active non-table Blocks,
using typed exact value sets and stable source/generation-scoped draft widget
identity. It reuses the CP1 candidate → frozen review → confirmation → Apply
→ audit/reviewed-inverse path; no Case writer or second evaluator was added.
Frozen reviews show production-evaluator matching/nonmatching probes plus
default-Preset and reconstructable pending-Case warning deltas. Warning-only
changes are distinct from rendered clinical output and content fingerprints;
pending Cases remain untouched, show the new warning on reopen, and use the
ordinary Workspace confirmation on their next Save. Validated artifacts stay
frozen.

The accepted remediation set covers physical stale owner/endpoint protection
with an explicit read-only preserved-draft/reload flow (including the
review-time race), order-insensitive warning membership with multiplicity,
duplicate-Block copied-rule inverse/re-inverse, and warning-only report
classification.

### Stage 7 CP4 — decoded bulk preview, no Case writes

CP4 adds the Workspace-adjacent **Bulk Intake** page and its read-only
`bulk_intake.py` service. It accepts only explicitly delimited UTF-8 CSV/TSV
with an explicit header decision, applies the 1 MiB/250-row/two-cell contract,
and normalizes the complete source before opening a database connection.
One read transaction then parses every row against one content state, derives
Preset-Block instances, materializes the same structured Case input shape
Workspace saves, renders through `editor_preview`, and proves every row
round-trips through `render_saved_case` with identical HTML. The session-local
review binds normalized-source/content/revision/target-absence/interpretation
digests, exposes warnings and conflicts, and invalidates on input, content, or
target-ID change. CP4 is accepted after independent closure verification.
CP5 subsequently adds the separate pending-only Apply boundary; CP4's preview
remains the no-write review checkpoint.

### Stage 7 CP5 — atomic pending-Case creation

CP5 is accepted. An issued, session-local batch review now requires separate
confirmation and a review-bound signed acknowledgement for any consistency
warnings. Apply takes one `BEGIN IMMEDIATE` transaction, rechecks the exact
source digest, content snapshot/revision, active grammar, target absence,
warnings, reconstruction, and materialized interpretation, then creates one
local `Case_Batch_Imports` audit and all new Cases through the shared
connection-scoped Case persistence primitive. It is create-only pending with a
`NULL` pending reason; it cannot validate, overwrite, delete, inverse, or
partially retry. Any failure rolls back the audit and every Case.

The additive provenance link remains outside content snapshots, AI context,
and operational-review artifacts. Bulk values retain Workspace widget-wire
compatibility (including decimal text values), so Cases reopen, edit, and
validate through ordinary Workspace/Worklist flows. Review replacement or
staleness clears confirmation and warning consent fail-closed.

### Stage 7 CP6 — conditional `etc0`–`etc5` consolidation

CP6's reviewed migration has been applied and browser-verified: active `etc`
uses Bethesda II for its bare code and fixed `0/1/2/3/5` lookup suffixes;
`etc0`, `etc1`, `etc2`, `etc3`, and `etc5` are archived; `etc_bi` and existing
Cases remain unchanged. The one-off migration assistant and all associated
runtime planner/evidence code were removed after use. The permanent product
surface is therefore only the resulting content plus normal Quick Type,
Content Studio, and reviewed-inverse machinery. Focused compatibility tests
cover exact routing/report equivalence, archived pending reopening/fingerprint
behavior, unchanged legacy Cases, generic rollback/stale review, and generic
inverse/re-inverse/blocker behavior. CP7 then removed the unused bulk-service
compatibility aliases, leaving one canonical preview/Apply workflow, and added
source regressions for that boundary and for the absence of UI SQL mutation
paths. No new schema or migration-specific runtime surface was added. Final
acceptance passed the focused Stage 7 suite (**96 passed**), the full isolated
suite (**597 passed in 351.87s**), browser acceptance, `py_compile`, and
`git diff --check`. Independent Sol review found no functional/safety
violation; stale completion documentation was the sole remaining blocker.

## Where to find detail

- **Operational rules and durable architecture:** `CLAUDE.md`
- **Test commands, DB isolation, and fixture ritual:** `TESTING.md`
- **Settled design rationale and completed historical build records:**
  `HISTORY.md`
- **Editor UI (Tier 3) design proposal:** `EDITOR_UI_PROPOSAL.md`
