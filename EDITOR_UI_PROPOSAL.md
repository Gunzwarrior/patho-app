# EDITOR_UI_PROPOSAL.md — Tier 3 Editor UI: post-review design

Status: **Stages 1–4 complete. Their safety design remains frozen; Thomas
approved a revised product roadmap for Stages 5–7 before Stage 5 began.**

This is the approved design baseline and forward roadmap. Stages 1–4 record
the implemented safety foundation. Stages 5–7 record the product outcomes
Thomas wants next; each still needs a bounded implementation proposal and
review before code is written.

Sol’s review identified real blockers in the previous write-capable stages.
This revision accepts those findings where they are concrete, records
Thomas’s decisions, and moves the required safety work ahead of ordinary
content editing. A second adversarial pass added five narrow amendments:
stable Preset selection identity, ID-preserving restore, continuous
pending-draft fingerprint checks, an exact fingerprint boundary, and
candidate-state validation. It is intentionally not a second product
redesign.

## 1. Decisions settled by Thomas

1. **Validated is frozen, but mistaken validation is correctable.** A
   validated case is not editable or silently re-rendered. Thomas may use an
   explicit, auditable “Return to pending” action if it was validated by
   mistake; the prior validated artifact is preserved.
2. **Pending content changes require per-case acknowledgement.** If a
   reopened pending case would use different relevant content than it had when
   last saved, Workspace shows that fact and requires acknowledgement for
   that case before it can be saved or validated. One global Editor
   acknowledgement is not enough.
3. **`site_label` and `conclusion_group` are deferred from direct editing.**
   They are report-structure configuration, not ordinary wording.
4. **Exports remain manual and on-demand.** Thomas may make corrections
   during normal work without exporting after every save. A one-time initial
   snapshot plus a tested restore path precede Editor writes; later exports
   are voluntary checkpoints and model inputs.
5. **Two kinds of golden output remain distinct.** The automated suite keeps
   reproducible test baselines. Reviewing the current operational content is
   a separate, explicit operation against an exported snapshot—not something
   that silently uses stale `seed_data.py` content.
6. **PathoPilot must remain fully usable without paid AI.** Manual self-service
   is the primary product requirement. AI assistance is an optional faster
   route for drafting a coordinated change from report examples and clinical
   discussion; it must never be required to understand JSON, create content,
   or maintain the application.
7. **Ordinary changes do not block on pending cases.** A wording, default, or
   configuration change may proceed after its normal impact preview. An
   affected pending case continues to use the existing per-case content-change
   warning and acknowledgement when reopened. If removing content would make a
   pending draft unreopenable, the normal removal action archives it instead.
   Permanent deletion is available when no pending case depends on the target
   and the complete candidate state remains valid.
8. **Quick Type is the preferred high-speed path.** One configurable base
   Preset plus memorable modifiers is preferable to proliferating Presets such
   as `etc2`, `etc3`, and `etc5`. Quick Type authoring and safe bulk creation
   of pending cases from `(case ID, Quick Type)` rows are explicit roadmap
   outcomes, not speculative extras.
9. **AI context must be economical and private.** The AI export contains only
   the compact content/configuration context and authoring instructions needed
   to make a proposal—never Cases, case IDs, patient information, or audit
   history. The model returns a small operation set bound to the exported
   snapshot hash; it does not echo the complete snapshot in its answer.

---

## 2. Why `site_label` and `conclusion_group` are not ordinary text

They are both stored as text, but the grouping engine treats them as
instructions about report structure.

- `site_label` is substituted into a conclusion and is also temporarily
  replaced with a sentinel when the grouping engine asks whether adjacent
  conclusions differ only by site. For example, Antrum and Fundus can merge
  into one “antro-fundique” conclusion. A change can alter the actual wording
  and whether two specimens merge.
- `conclusion_group` partitions adjacent conclusions into clinical sections.
  It adds blank-line boundaries, prevents merging across categories, and
  determines where case-level addenda are placed. A typo can make an addendum
  appear in the wrong section or alter a merged conclusion.

Neither effect appears in a one-Block preview. They should therefore remain
visible/read-only in the first editor. A later relationship/configuration
proposal may expose them with full affected-case previews and the same
pending-case acknowledgement policy.

The same caution applies to `Blocks.name`: it is a simple label in some
views but becomes a specimen header in multi-specimen reports. It is also
deferred from direct editing rather than being treated as harmless wording.

---

## 3. First direct-edit scope (implemented Stage 3 boundary)

### Goal

For a normal correction, Thomas can open an existing item, edit it, see a
real rendered preview, save it, and continue. The workflow must be safe
without demanding a Git export or fixture update for every sentence change.

### Direct UI edits

- Block templates: `macro_template`, `micro_template`,
  `conclusion_template`, `context_template`, `title_fragment_template`, and
  `conclusion_label_template`.
- Field display `label`, `default_value`, and
  `conclusion_addendum_template`.
- Snippet `expansion` and `category`, plus creation of a new Snippet.
- Preset `name`, `category`, and `default_title`.

Before `Preset.name` becomes editable, Workspace’s preset selector must store
the stable Preset ID (or stable `short_code`) as its widget value and use a
display formatter for `“Appendice (dai)”`. Display text must never be widget
identity: a rename in another tab must not invalidate an open selection or
trigger the destructive preset-switch reset.

The editor shows, but does not directly edit:

- Block `key`, `name`, `site_label`, `conclusion_group`, and `is_table`;
- Field `key`, `type`, and `options`;
- `Block_Fields`, `Preset_Blocks`, order, and field overrides;
- `Quick_Type_Tokens`, `Field_Consistency_Rules`,
  `Conclusion_Group_Labels`, and `Preset_Block_Rows`;
- `default_adicap`, which is currently stored metadata with no Workspace
  consumer.

This is a deliberate first boundary between everyday content correction and
shape/configuration changes that can invalidate pending cases.

### Model-assisted creation remains an optional companion workflow

A later stage adds a reviewed **content change package**. A model can draft a
new Field/Block/Preset and their bindings after receiving report examples,
Thomas's clinical requirements, and a compact export of what PathoPilot already
uses. The ordinary conversation remains about the expected report; Field keys,
relationship rows, and package syntax are plumbing for the model and the app,
not information Thomas must supply.

The Editor validates a dry run and shows the complete human-readable operation
list and production-rendered result before Thomas explicitly applies it. It is
the route for a coherent new Preset when that is faster than building it by
hand; it is never a silent model database writer and never the only way to
create content.

The AI-context download includes the exact package instructions plus a
canonical snapshot hash. A returned package contains that hash, a summary, and
the proposed operations, but not a duplicate of the full source snapshot. This
keeps model output small enough for constrained/free plans where practical.
PathoPilot rechecks the live snapshot hash and materialises the candidate from
its own live state. Free-plan limits still cannot be guaranteed, so the export
format should remain compact and allow a future relevant-content subset.

Initial packages are additive/change-only. They cannot delete data, alter
Field key/type/options, touch Cases, or change ordering/configuration fields
deferred above.

---

## 4. Case lifecycle safety — prerequisite, not Editor polish

The current code incorrectly lets a normal save overwrite a validated Case
and reconstructs a validated case from current content on reopen. This
contradicts the app’s intended clinical record. The Editor must not be built
on that behaviour.

### Required model

1. **Pending case.** Its structured values remain editable. It has a stored
   last-rendered report, relevant content fingerprint, and content revision
   reference from its most recent save.
2. **Validated case.** It opens in a read-only view of its saved validated
   report. Normal `save_case` operations refuse to update it, including
   duplicate-case-number attempts.
3. **Validation event.** Each validation appends an immutable
   `Case_Validation_History` record containing the rendered HTML, structured
   input, clinical information, preset reference, content fingerprint, and
   timestamp. This preserves the artifact even if the case is later returned
   to pending and then revalidated.
4. **Explicit unvalidation.** From the read-only validated view, “Return to
   pending” requires an explicit confirmation and is recorded with a reason.
   It changes status through a dedicated backend transition; it is not a
   normal report save. The previous validation event remains intact.

Existing validated Cases are backfilled into history by the migration before
the new transition is enabled.

### Pending-content acknowledgement

Every pending save records:

- the current content revision ID for audit traceability; and
- a deterministic fingerprint of the content actually relevant to that case,
  defined below.

When a pending case is opened, Workspace captures its current relevant
fingerprint. It recomputes that fingerprint on every rerun that can render
the report and immediately before every Save. If it differs from either the
saved fingerprint or the fingerprint previously acknowledged in this open
session, Workspace clears the acknowledgement and shows a persistent “content
changed since this draft was saved” notice. The previous saved output and
newly rendered output are available for comparison. Thomas may continue to
inspect/edit the case, but must check an acknowledgement for this specific
current fingerprint before Save as Pending or Save as Validated is enabled.
Saving stores the new fingerprint.

The Editor also reports the number of potentially affected pending cases.
That is information only; acknowledgement happens at the right clinical
moment—when each draft is reopened—not in one broad confirmation dialog.

### Exact relevant-content fingerprint

The fingerprint follows the case’s actual saved `block_instances`, in their
saved order—not merely the normal Blocks currently configured for its Preset.
It includes every rendering-relevant value used to produce that case:

- the selected Preset’s title/context data;
- every referenced Block’s rendering columns, `name`, `site_label`, and
  `conclusion_group`;
- each resolved Field definition, default/override, and addendum template;
- referenced Snippet expansions; and
- applicable `Conclusion_Group_Labels`.

This covers added, removed, duplicated, and reordered composed Blocks as well
as ordinary Preset cases. Unrelated content elsewhere in the DB does not
invalidate a pending case. The implementation must test each of those
composition scenarios.

### Modification, archive, and deletion semantics

- A validated Case always opens its complete frozen `rendered_html`; later
  content edits, archival, or deletion do not re-render its report.
- A pending Case remains an editable live draft. Relevant modifications are
  allowed, then detected by its fingerprint and handled by the existing
  before/new-output comparison and per-case acknowledgement.
- Archive is the safe fallback when removal would break a pending draft. An
  archived item disappears from new-case and ordinary authoring choices but
  remains resolvable for existing pending Cases.
- Permanent deletion is offered when no pending Case depends on the content
  and the proposed deletion—including relationship cleanup—produces a valid
  candidate state. Historical validated artifacts must not keep operational
  content alive merely because they retain metadata references; the Stage 6
  design must specify the safe migration/detachment mechanism.
- The UI must explain what will be modified, archived, detached, or deleted,
  list affected pending Cases, render the relevant before/after impact, and
  apply the complete approved change atomically with audit history.

---

## 5. Persistence, recovery, and destructive-operation safety

### The live DB and snapshots have different roles

The live SQLite content tables remain the operational source that Workspace
uses. `seed_data.py` remains a reproducible bootstrap for a disposable
development/test DB; it is not rewritten after ordinary Editor saves.

An on-demand content snapshot is a deterministic, stable-key JSON export of
content and relationships only—never Cases, patient data, or audit history.
It is useful as a Git-reviewable checkpoint, a portable backup, and the
precise model input for a future change package.

Thomas does **not** have to export after each correction. To remove the
danger Sol identified, the system first creates one initial snapshot before
any live Editor write is allowed. After that, the in-DB revision log provides
ordinary undo, while exports are voluntary checkpoints at a time Thomas
chooses (for example, after a productive revision session or before asking a
model to draft new content).

### Safety work that comes before direct writes

- Add a non-destructive, idempotent schema migration path. It preserves
  Cases and content while adding Editor/case-history columns and tables.
- Change the normal `init_db.py` command so it refuses to overwrite an
  existing operational database without an explicit rebuild/development
  mode. Isolated test setup remains able to create its specified temporary
  DB directly.
- Deliver `export_content_snapshot` and
  `restore_content_snapshot`. Restore uses a temporary, isolated DB to
  validate the snapshot before a content-only transaction applies it; it
  never rebuilds or deletes Cases. Restore itself is a content revision, so
  pending drafts can detect the changed content on reopen.
- Restore matches Fields/Blocks/Presets/Snippets and relationship rows by
  their stable keys and updates matching rows **in place**, preserving their
  SQLite IDs. It must refuse the whole restore if its target would remove or
  change any entity/relationship needed by a saved Case, including IDs held
  inside `structured_input.block_instances` JSON. A failed exact restore is
  safer than an apparently successful restore that makes a pending case
  unreopenable.
- Test export → restore round-trip with pending composed cases, including
  added, removed, duplicated, and reordered Block instances—not just a
  content-only empty-Cases fixture.

This is not an instruction to export continually. It is a one-time recovery
foundation plus prevention of the known destructive initializer path.

### Lightweight content revision history

```
Content_Revisions(
  id, created_at, origin, summary
)

Content_Changes(
  id, revision_id, table_name, entity_key,
  operation, before_json, after_json, before_hash, after_hash
)
```

Manual Save creates one revision; a package import or snapshot restore creates
one revision containing all of its changes. `origin` distinguishes them.
Manual summaries are optional; package/restore summaries are required.

The first UI offers a compact recent-revisions panel and “Revert this
revision,” not a large history subsystem. Revert is allowed only if every
target row still exactly matches the recorded `after_hash`; otherwise it
explains that later work exists and refuses to erase it.

---

## 6. Validation, previews, and concurrent edits

### Strict column invariants

Backend validation, not the Streamlit form, owns these rules:

- required Block `micro_template` and `conclusion_template` are nonblank;
- a non-table Block may not clear its normal `macro_template` in direct
  editing; optional text columns normalize intentional blank input to `NULL`;
- required names/labels/Snippet expansion are nonblank;
- Field defaults coerce successfully to their existing type, including
  decimal; direct editing cannot modify the type/options;
- all template syntax is parsed, unknown variables are rejected, and every
  `snippet('shortcut')` reference resolves;
- application connections enable `PRAGMA foreign_keys = ON`, and all
  content/package writes use explicit table/column allowlists.

### Meaningful preview and validation

A bare Block preview is useful for immediate writing feedback but insufficient
as a save check. Before a direct change commits, the backend validates:

1. a strict-undefined render of the changed item;
2. a full before/after render of every affected Preset at resolved defaults;
3. every affected stored pending-case context, where one exists.

The Editor presents the compact default preview and links to affected Preset
previews. This catches macro/micro/header, title, addendum, grouping, and
multi-specimen effects that a one-Block display misses. It does not claim to
automatically judge clinical correctness; Thomas reviews the output.

Validation and previews must render a materialized **candidate post-change
state**. They may use the same transaction/connection after proposed rows are
written to a savepoint, or an equivalent isolated in-memory/temporary
candidate snapshot. They must not call render helpers that quietly open a new
global DB connection and therefore see old Snippets or old content. This is
required for a package that creates a Snippet and a Block referring to it in
the same Apply transaction.

For discrete Fields, validation additionally includes at least one
representative render for every legal checkbox value and every select option
affected by the change. It is a targeted branch check, not a full Cartesian
case simulator.

### Optimistic concurrency

Every edit form carries the hash of the row state it loaded. Save uses a
compare-and-swap update: if another tab or import changed that row first, the
write is rejected and the UI shows the new value rather than overwriting it.

Package Apply rechecks its base snapshot hash, all row hashes, and all
validation **inside** its transaction. Revert uses the same protection. This
prevents stale browser tabs, stale package previews, and an older revision
from silently replacing later work.

---

## 7. Golden output and testing

### Automated tests

Tests remain isolated from `pathology.db`, but that protection becomes
fail-closed: test startup redirects `database.DB_NAME` to a temporary DB
automatically. Forgetting `mutable_db` may at worst alter the isolated
session DB, never the operational database. Tests that write still use their
own per-test `mutable_db` copies.

The engine/editor suite gains deliberately synthetic scenarios for template
validation, missing variables, macros, grouping, shared Fields, addenda,
pending fingerprints (including same-session invalidation and composed
instances), validation/unvalidation, transaction rollback, stale writes,
revert safety, ID-preserving snapshot round trip, and package dry run/apply
against a candidate state. These remain stable despite ordinary clinical
prose changes.

The future-stage suites must additionally prove dependency-aware archive and
deletion, validated-history independence, complete manual create/duplicate/
relationship workflows, Quick Type configuration validation and rollback,
variant-Preset migration, and atomic bulk intake. A malformed row, duplicate
case ID, stale content revision, failed render, or audit failure must leave a
bulk fixture with no newly created Cases. Tests and previews use synthetic
case IDs only and never place Case data in an AI-context artifact.

### Two output-baseline workflows

The phrase “golden files” previously blurred two jobs:

1. **Test baselines** are the reproducible fixtures used by `pytest`. They
   protect known rendering behaviour in a temporary seeded/synthetic DB.
   They never read the live database.
2. **Operational clinical review baselines** are optional, human-reviewed
   output comparisons for the content currently used in practice. They start
   from an exported snapshot, which is loaded into a temporary DB; the tool
   displays the snapshot hash and never opens `pathology.db` directly.

If Thomas wants to review/freeze a clinical content version, he exports a
snapshot, runs the operational review renderer against it, judges the diff
against a real example or clinical judgment, then deliberately updates that
review artifact and optionally commits it. No ordinary Editor Save runs this
process or requires fixture maintenance.

This answers the source-of-truth question: `seed_data.py` remains the
historical/reproducible bootstrap state for automated tests. An operational
baseline, when wanted, represents the exported operational content and says
which snapshot it came from.

---

## 8. UI flow

The implemented Stage 3 Editor replaced its unsafe direct Snippet-add form:

```
Presets
  └─ ordered Blocks
       └─ Fields used by that Block
Blocks (all, for shared Blocks)
Fields (all, for shared Fields)
Snippets
Recent revisions
Export content snapshot
Import change package (Stage 5)
```

Selecting an item opens one focused edit form. It displays where the item is
used, affected Presets, and affected pending-case count. It retains the
project’s English chrome/French clinical content split and follows the
generation-suffixed-key plus reset/rerun pattern for changing targets.

The initial preview uses resolved defaults. A richer choose-values preview is
not required: a blank Workspace case remains the efficient way to explore
unusual combinations.

The forward UI grows in two product-facing steps:

```
Stage 6 — Content Studio
  New / duplicate / edit / archive / delete Presets
  New / duplicate / edit / archive / delete Blocks
  New / edit / archive / delete Fields and Snippets
  Block–Field and Preset–Block relationships, order, defaults, and overrides
  Complete candidate preview and pending-case impact

Stage 7 — Quick Type and batch intake
  Quick Type rule editor per base Preset
  Parse/test examples before saving a rule set
  Paste or upload: case ID | Quick Type
  Validate and preview every row
  Create all rows as pending Cases for individual review
```

JSON remains an interchange/debugging detail. Neither Content Studio nor the
Quick Type editor requires Thomas to read or write it.

---

## 9. Staged implementation

Every stage must end in a tested, self-consistent state before the next one
starts. In particular, Stage 2’s validation, validation-history insertion,
and case-status transitions are atomic, and Stage 3 content writes remain
disabled until all Stage 2 safety requirements are implemented and verified.

1. **COMPLETE (`12212cc`) — Read-only navigator.** Replace the unsafe Snippet writer with browsing,
   use/impact display, and preview. This carries no migration or write risk.
2. **COMPLETE (`03f85af`) — Operational safety foundation.** Non-destructive migration; immutable
   validated-case view, history, and explicit unvalidation; pending content
   fingerprint/acknowledgement throughout an open session; stable-ID Preset
   selection before any Preset-name edit; `init_db.py` guard; per-connection
   foreign keys; fail-closed test DB isolation; ID-preserving, case-aware
   snapshot export/restore round trip.
3. **COMPLETE — Safe direct editing.** Transactional content revisions, strict
   candidate-state validation, affected-Preset/pending-context renders,
   compare-and-swap, safe revision revert, and direct edit forms for the
   limited §3 scope.
4. **COMPLETE — Operational content
   review.** `operational_review.py` accepts only an explicit
   `pathopilot-content-snapshot-v1` snapshot, hashes canonical snapshot
   content, restores it into a fresh temporary database, and renders every
   Preset at resolved defaults through the same connection-aware preview path.
   It emits deterministic complete-report JSON artifacts (title, clinical
   information, micro, conclusion, conflicts, and HTML), separate from
   pytest fixtures. Comparison reports added/removed/changed/unchanged
   Presets; acceptance is an explicit atomic command bound to the reviewed
   candidate artifact SHA-256. It never opens `pathology.db`.
5. **Optional reviewed AI change-package import.** Add a compact **Download
   context for AI** artifact containing the canonical content/configuration
   snapshot, its hash, and the exact authoring contract, but no Case or patient
   data. Thomas may continue to provide example reports and discuss what is
   clinically important; the model translates the agreed result into a small
   hash-bound summary and operation set without echoing the source snapshot.
   Import performs a no-write dry run, full candidate validation, stale-state
   protection, readable operation/impact output, complete rendered Preset
   previews, affected-pending-case before/after review, and one separately
   confirmed atomic Apply. Errors are concise and copyable back to an AI.
   Initial packages remain additive/change-only: package deletion and advanced
   configuration are not required here. No AI account or in-app AI connection
   is introduced, and every backend primitive should be reusable by Stage 6.
6. **Autonomous Content Studio.** Make every ordinary PathoPilot content task
   possible through guided forms without AI or JSON: create, duplicate, edit,
   archive, and safely delete Presets and non-table Blocks; create, edit,
   archive, and safely delete Fields and Snippets; manage `Block_Fields` and
   `Preset_Blocks`, ordering, defaults, overrides, `site_label`,
   `conclusion_group`, and group labels. A modification uses the established
   impact preview and lets each affected pending Case acknowledge the new
   fingerprint when reopened. A destructive action archives when a pending
   Case still needs the target and offers permanent deletion when no pending
   dependency remains and the whole candidate is valid. Field type/option
   migration, table-Block row authoring, and any other genuinely exceptional
   structure may use explicit advanced checkpoints within the Stage 6 design;
   they may not turn AI into a requirement for maintaining content the app
   already supports.
7. **Quick Type Studio and bulk pending-case creation.** Make Quick Type the
   core speed path rather than representing common modifier combinations as
   many Presets. Provide guided create/edit/delete/reorder controls for each
   base Preset's `Quick_Type_Tokens`: lookup mappings, measurement tokens,
   Block targets, order, and digit-width guards, with collision, ambiguity,
   Field/type, and complete example-parse validation before save. Include
   authoring for the applicable consistency rules and a deliberate migration
   path from variant Presets such as `etc0`–`etc5` to one base `etc` grammar.
   These operations use the same candidate/revision service from Stages 5–6,
   so they are available through guided forms and may also be proposed by an
   optional AI package without making AI necessary.
   Add a paste/upload intake with exactly two logical columns—case ID and
   Quick Type. Parse and render every row against one consistent content
   revision, report duplicate/existing IDs and all row errors before writing,
   show a compact decoded-value/report review, and create the batch atomically
   as **pending** Cases only. Each generated Case stores the normal structured
   input, rendered HTML, content fingerprint, and revision reference, then
   appears in the ordinary pending-case review workflow. A bad row means no
   partial batch, and bulk intake never validates cases automatically.

---

## 10. Explicit non-goals

- No automatic snapshot on every save, automatic Git commit, or automatic
  golden-fixture regeneration.
- No rewriting `seed_data.py` from the live DB.
- No paid AI plan, AI account, network connection, or JSON authoring required
  for ordinary use or content maintenance.
- No Cases, case IDs, patient data, or audit history in an AI-context export.
- No model writes without a visible validated dry run and explicit Apply.
- No normal editing of validated cases, and no duplicate-case overwrite of a
  validated record.
- No multi-user conflict system; compare-and-swap is protection against
  accidental stale tabs/imports, not a collaboration product.
- No silent hard deletion of content required by a pending Case; archive keeps
  the draft reopenable until permanent deletion becomes safe.
- No automatic validation of bulk-created Cases; the batch stops at pending
  so Thomas reviews each report through the normal workflow.
- No schema rebuild from Editor.
