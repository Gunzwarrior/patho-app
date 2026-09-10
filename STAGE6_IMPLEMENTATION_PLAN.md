# Stage 6 implementation plan — Autonomous Content Studio

**Approved working architecture; planning documentation only. No Stage 6
implementation has begun.**

Authority: `EDITOR_UI_PROPOSAL.md` Stage 6, the Stage 5 completion record in
`STAGE5_IMPLEMENTATION_PLAN.md`, and the approved planning pass captured here.
Repository reviewed at `5b12125`.

## 1. Outcome and architecture

The smallest robust Stage 6 is a guided UI over a generalised Stage 5
candidate/review/Apply service, not a second writer. It needs two important
additive schema extensions: archival state on base content and a separate
Preset display order that does not disturb saved instance identity.

Introduce one small domain layer, tentatively `content_studio.py`, which
translates guided-form intent into internal content operations. It must not
write directly. Its output goes through a generalised
`content_changes.review_candidate()` and the existing immutable review, stale
guard, atomic Apply, revision audit, and reviewed inverse flow.

Keep these module boundaries:

- `change_packages.py` remains the restricted external AI v1 parser.
- `content_studio.py` owns human-facing intents such as duplicate, archive,
  relationship replacement, and safe deletion.
- `content_changes.py` owns source-independent materialisation, complete
  validation, impact review, Apply, and inverse.
- `pages/editor.py` owns form and session behaviour only.
- `content_editing.py` remains for legacy Stage 3 compatibility while the
  Editor UI is migrated away from its one-row direct-save path.

Every Stage 6 form prepares a frozen review, then requires confirmation and a
separate Apply. Once review mode begins, the draft form is hidden or disabled
until the user chooses **Edit draft**. This avoids displaying an old review
beside newly changed widgets.

## 2. Lifecycle semantics

Add `is_archived INTEGER NOT NULL DEFAULT 0` to:

- `Fields`;
- `Blocks`;
- `Presets`;
- `Snippets`.

Stable keys and SQLite IDs remain immutable.

An archived entity:

- is hidden from new relationship pickers;
- is unavailable for new Workspace and Quick Type selection;
- remains visible in Content Studio under an explicit archived filter;
- remains resolvable by saved pending Cases;
- retains its relationships and exact rendering data; and
- can be restored through another reviewed candidate.

Archive uses an upward dependency closure so the active graph remains usable:

- Archiving a Field also archives active Blocks that use it and their active
  Presets.
- Archiving a Snippet also archives active Fields/addenda and Blocks that call
  it, then affected Presets.
- Archiving a Block also archives active Presets that contain it.
- Archiving a Preset affects no reusable content beneath it.

The complete closure is shown before Apply. Archive status itself is not
rendering content and does not change a pending Case fingerprint when the
underlying report remains identical.

Restoration works in the opposite direction: restoring a Preset restores or
requires restoration of all content needed by its active graph. The candidate
preview makes the resulting availability explicit.

Permanent deletion never cascade-deletes another base entity implicitly. It
may perform clearly listed mechanical cleanup of relationships/configuration
owned by the target. If remaining templates or owners are invalid, the
candidate is refused and the UI identifies the prerequisite edit.

If any pending Case depends on a base target, permanent deletion is
unavailable and the UI prepares archive instead. Relationship unlinking may
still proceed when the final candidate and every pending Case render
successfully, followed by the normal fingerprint acknowledgement.

Validated Cases do not block deletion, but deleting content can make a later
**Return to pending** impossible. That consequence is shown during review. The
transition backend preflights reconstruction and refuses unvalidation if the
necessary content has been permanently deleted.

Unsaved browser-only drafts cannot be inventoried and remain outside the
deletion blocker. Persisted pending Cases are the authoritative dependency
set; open browser sessions retain Workspace's continuous rendering and
fingerprint checks.

## 3. Dependency and cleanup rules

| Target | Dependencies and cleanup that must be analysed |
|---|---|
| Field | `Block_Fields`, templates using its variable, Preset overrides, Quick Type tokens, consistency rules, and pending compositions |
| Snippet | Block templates, Field addenda, and transitively affected Blocks, Presets, and pending Cases |
| Block | `Block_Fields`, `Preset_Blocks`, table rows, consistency rules, group labels, Quick Type block targets, and pending compositions |
| Preset | `Preset_Blocks`, table rows, Quick Type tokens, and pending and validated Cases |
| Group label | Referenced Block-key set, affected merged conclusions, and pending fingerprints |
| `Block_Fields` | Template context, defaults, widget ordering, context-section placement, and pending saved values |
| `Preset_Blocks` | Default composition, immutable instance identity, overrides, Quick Type targets, and legacy pending composition |

Deletion cleanup is deliberately mechanical rather than clinical:

- deleting a Preset removes its owned `Preset_Blocks`, table rows, and Quick
  Type tokens and performs the validated-Case detachment described in §6;
- deleting a Block removes its `Block_Fields`, `Preset_Blocks`, table rows,
  consistency rules, affected group-label rows, and Quick Type targets for its
  Preset instance;
- deleting a Field removes its `Block_Fields`, matching Quick Type tokens and
  consistency rules, and prunes its keys from Preset override objects;
- deleting a Snippet never rewrites clinical templates automatically and is
  refused while a template or addendum still calls it.

The complete candidate validator decides whether the remaining graph is
usable. Cleanup cannot silently delete another Field, Block, Preset, or
Snippet just to make deletion succeed.

## 4. Preset ordering versus immutable instance identity

`Preset_Blocks.sort_order` currently serves two incompatible purposes:

1. initial display position; and
2. immutable saved `instance_no`, also referenced by Quick Type and Preset
   overrides.

Changing it to reorder a Preset would corrupt the meaning of saved pending
compositions. Add:

```text
Preset_Blocks.display_order INTEGER
```

Migration backfills `display_order = sort_order`.

Thereafter:

- `sort_order` is called `instance_no` conceptually in UI/domain code and
  never changes;
- `display_order` controls new-case and default display order;
- UI reorder changes only `display_order`;
- new links receive a currently safe `instance_no < 1000`;
- an identity is not reused while a pending Case still contains the same
  `(block_id, instance_no)`; and
- display positions are unique and normalised per Preset.

This preserves Stage 7's existing `Quick_Type_Tokens.block_sort_order`
semantics.

For saved Cases with explicit `block_instances`, their saved list remains the
authoritative order. A legacy pending Case without that list inherits the
current Preset display order and therefore receives the normal content-change
preview and acknowledgement after a reorder.

## 5. Defaults and inheritance

Forms expose inheritance explicitly rather than encoding it as a blank text
box:

- Field default: a typed control.
- `Block_Fields.default_override`: **Inherit Field default** or **Override**,
  with a typed value.
- `Preset_Blocks.field_overrides`: per Field, **Inherit Block/Field value** or
  **Override**.
- For text/decimal Preset overrides, explicit `null` remains distinguishable
  from a missing/inherited member.
- `label_override` uses **Use Field label** versus a nonblank override.
- `context_section` is an explicit toggle.

Field, Block, and Preset override values all pass the same final Field-type
validator. The current `Block_Fields` storage meaning remains intact: SQL
`NULL` means inherit, so it does not gain a distinct explicit-null override.

`default_adicap` may be exposed as ordinary Preset metadata, with a clear note
that Workspace currently has no consumer for it.

## 6. Snapshot, Stage 5, and validated-Preset compatibility

### Snapshot and Stage 5 compatibility

Archive state and `display_order` affect operational configuration and must
enter the canonical snapshot and content hash.

- Emit `pathopilot-content-snapshot-v2`.
- Accept legacy v1 snapshots by normalising missing `is_archived` to `0` and
  missing `display_order` to `sort_order`.
- Keep `pathopilot-content-change-package-v1` unchanged.
- Allow its base hash to bind the new snapshot representation.
- Document archived rows as read-only targets for external v1 packages.
- Reject AI-package updates or links involving archived content.

This avoids silently changing the strict v1 snapshot shape while preserving
the existing restricted Stage 5 package protocol. The operational-review
artifact format need not change; a newly generated artifact simply records a
v2 source snapshot hash. Old packages naturally become stale after migration
rather than being rebased.

### Validated Preset detachment

`Cases.preset_id` currently prevents permanent deletion of any Preset ever
used by a validated Case. Use an additive migration:

- `Cases.preset_short_code_snapshot`;
- `Cases.preset_name_snapshot`;
- corresponding columns on `Case_Validation_History`; and
- a small `Case_Content_Reference_Changes` audit table recording only revision
  ID, internal Case ID, reference kind, and before/after Preset ID/key—not
  clinical text or case number.

Saving or validating a Case fills the frozen identity. Migration backfills it
while the Preset exists.

Permanent Preset deletion may then, atomically:

1. verify that there is no pending dependency;
2. retain the frozen Preset identity;
3. set `preset_id = NULL` only for validated Cases;
4. record the detachments in the same content revision; and
5. delete the Preset and owned configuration.

Worklist display uses the live Preset name when present and the frozen name
otherwise. A reviewed inverse recreates the original Preset ID before
reattaching unchanged validated references. Case-reference changes are bound
by the local review guard and exact before/after checks but remain outside AI
exports and content snapshot hashes.

## 7. Checkpoint summary

| Checkpoint | Nature | Boundary |
|---|---|---|
| 1. Persistence and compatibility | Architecturally critical | Natural commit/conversation boundary |
| 2. Generalised candidate service | Architecturally critical | Natural commit/conversation boundary |
| 3. Lifecycle and runtime resolution | Architecturally critical | Natural commit/conversation boundary |
| 4. Content Studio shell, Fields, Snippets, labels | Critical UI foundation; much form work mechanical | Natural boundary |
| 5. Block Studio | Architecturally critical | Natural boundary |
| 6. Preset Studio | Architecturally critical | Natural boundary |
| 7. Consolidation and full acceptance | Mostly mechanical integration, safety-critical verification | Final Stage 6 boundary |

## 8. Checkpoint 1 — persistence and compatibility foundation

### Goal

Introduce lifecycle and ordering storage without changing current rendering or
visible behaviour.

### Likely files and components

- `init_db.py`
- `database.py`
- `content_snapshot.py`
- `change_packages.py`
- `operational_review.py`
- `tests/test_stage2_safety.py`
- `tests/test_operational_review.py`
- `tests/test_stage5_packages.py`
- new `tests/test_stage6_schema.py`

### Data-model and schema changes

- `is_archived` on the four base content tables.
- `Preset_Blocks.display_order`.
- Frozen Preset identity columns on `Cases` and
  `Case_Validation_History`.
- `Case_Content_Reference_Changes`.
- Named, idempotent migration marker or markers.

All changes are additive; no table rebuild.

### UI behaviour

None beyond preserving existing UI behaviour exactly.

### Invariants and safety constraints

- Existing content migrates active.
- Existing `Preset_Blocks` retain identical order and identity.
- Existing Cases and validation history retain their live Preset identity.
- Migration is idempotent and never regenerates reports.
- Snapshot v1 normalisation is read-only and deterministic.
- A current v2 snapshot round-trips byte-deterministically.
- Old package hashes naturally become stale after migration rather than being
  rebased.

### Test strategy

- Migration from a pre-Stage 6 schema with pending and validated Cases.
- Repeated migration.
- ID and report preservation.
- v1 snapshot restore into the Stage 6 schema.
- v2 export/restore.
- Stage 4 artifact generation from both snapshot versions.
- Stage 5 export/privacy tripwires remain intact.
- Golden output remains unchanged.

### Dependencies

None.

### Manual browser checks

- All current Presets retain their order.
- Pending and validated Cases reopen as before.
- AI-context and recovery downloads still work.

### Boundary

Yes. This should be a tested commit and conversation boundary before
candidate-service changes.

## 9. Checkpoint 2 — generalised candidate, audit, and inverse service

### Goal

Extend the Stage 5 engine to support every internal operation Stage 6 needs
while preserving the narrower AI contract.

### Likely files and components

- `content_changes.py`
- new `content_studio.py`
- `content_editing.py`
- `content_snapshot.py`
- `editor_preview.py`
- `quicktype.py`
- `consistency.py`
- `tests/test_stage5_packages.py`
- `tests/test_stage5_transactions.py`
- new `tests/test_stage6_candidates.py`

### Data-model and schema changes

None beyond Checkpoint 1.

### UI behaviour

No Content Studio UI yet. Existing AI-package and inverse screens continue to
work.

### Service behaviour

Internal operations cover:

- create, update, archive, restore, and delete base rows;
- create, update, and unlink relationship rows;
- relationship reorder and override changes;
- group-label create, update, and delete;
- mechanical cleanup of Quick Type tokens, consistency rules, table rows, and
  group labels; and
- validated Preset-reference detach and reattach.

The external package parser continues to emit only its current
create/update/link subset.

### Invariants and safety constraints

- One candidate copy, one final-graph validation, one immutable review.
- Deterministic materialisation: create endpoints, update rows, unlink/remove
  dependents, create/update relationships, then validate the finished graph.
- Exact physical before/after images for every content/configuration row.
- Exact allowed Case-reference changes captured separately.
- No Case clinical data enters review provenance or audit.
- Active Quick Type and consistency configurations are validated even though
  their authoring UIs remain later work.
- Inverses restore original IDs, display orders, relationships, archive flags,
  and validated references.
- Legacy Stage 3 and Stage 5 revisions remain readable.
- A create inverse that would remove pending-dependent content remains
  refused; the user may archive it separately.

### Test strategy

- Every operation kind on every supported table.
- Shuffled intents produce one deterministic candidate.
- Stale content, audit ABA, pending-set changes, and identity replacement.
- Fault injection through every materialisation and audit phase.
- Revert-of-revert.
- Relationship reorder and override restoration.
- Stage 5 package behaviour unchanged.
- No-write review proof and operational-DB tripwires.

### Dependencies

Checkpoint 1.

### Manual browser checks

Only regression of AI-package review/Apply/inverse and Recent revisions.

### Boundary

Yes. This is the most important backend checkpoint.

## 10. Checkpoint 3 — lifecycle, dependency analysis, and archived resolution

### Goal

Implement active/archive/delete decisions and make archived dependencies
safely usable by saved pending Cases.

### Likely files and components

- `content_studio.py`
- `content_changes.py`
- `database.py`
- `editor_preview.py`
- `pages/workspace.py`
- `pages/worklist.py`
- `quicktype.py`
- new `tests/test_stage6_lifecycle.py`
- existing Stage 2 and Workspace tests

### Data-model and schema changes

None beyond Checkpoint 1.

### UI behaviour

Backend-facing lifecycle summaries become available: direct dependencies,
archive closure, pending blockers, validated detachments, mechanical deletion
cleanup, and refusal reasons.

Workspace and Quick Type show only active Presets for new work. Reopening a
saved pending Case may inject its archived Preset into that case generation
without making it generally selectable.

### Invariants and safety constraints

- Active Presets resolve only active dependencies.
- Saved pending Cases resolve archived Presets, Blocks, Fields, and Snippets by
  preserved IDs and keys.
- Archive retains all physical data and relationships.
- Permanent deletion requires zero pending dependencies before candidate
  preparation.
- Every pending Case still renders after any unlink/configuration change.
- Validated reports are never re-rendered or rewritten.
- Return-to-pending performs a reconstruction preflight and fails without
  changing status if dependencies were deleted.
- Archive-only changes do not alter relevant-content fingerprints.
- Actual wording, default, link, and order changes do.
- Apply never acknowledges or updates an ordinary pending Case.

### Test strategy

- Archive each entity type with direct, transitive, duplicate, ad hoc, and
  legacy pending compositions.
- New-case exclusion versus pending-case resolution.
- Archive and restore closure.
- Deletion eligibility races between review and Apply.
- Validated Preset detachment, Worklist fallback label, and inverse
  reattachment.
- Refused unvalidation after permanent dependency deletion.
- Same-session pending acknowledgement invalidation after actual content
  changes.
- No acknowledgement for availability-only archival.
- Explicit coverage of the unsaved-browser-draft boundary.

### Dependencies

Checkpoints 1–2.

### Manual browser checks

- Archive a Preset with a pending Case: absent for new cases, reopenable from
  Worklist/sidebar.
- Archive an ad hoc Block used by a pending Case.
- Restore archived content.
- View a validated Case whose Preset was permanently deleted.
- Confirm Return-to-pending refusal is clear when reconstruction is
  impossible.

### Boundary

Yes. Complete this before exposing destructive controls.

## 11. Checkpoint 4 — Content Studio shell, Fields, Snippets, and group labels

### Goal

Establish the reusable guided-review UI and deliver the simpler entity
workflows.

### Likely files and components

- `pages/editor.py`
- `content_studio.py`
- `database.py`
- review-display helpers extracted from `pages/editor.py`
- new `tests/test_stage6_ui.py`
- `tests/test_editor.py`
- `tests/test_stage5_ui.py`

### Data-model and schema changes

None.

### UI behaviour

- Content Studio section with Active/Archived/All filters.
- Stable-ID entity selectors with archived badges.
- Create/edit Field forms with typed default widgets.
- Existing Field type/options shown but locked.
- Create/edit Snippet forms.
- Guided group-label editor using a Block multi-select and canonical sorted
  key set.
- Archive, Restore, and deletion-eligibility panels.
- Draft → Prepare review → frozen review → confirm → Apply.
- Exact changes, reports, pending impact, availability impact, and warnings
  use the shared review renderer.
- Existing one-row **Save immediately** UI paths are removed or rerouted
  through this flow; no second editing experience remains.

### Invariants and safety constraints

- Stable keys cannot be renamed.
- New select Fields require nonblank unique options.
- New Fields have a usable standalone default.
- Snippet deletion is refused while any template or addendum calls it.
- Group-label deletion is a rendering change, not a lifecycle archive.
- Changing selection or draft generation clears review and confirmation.
- Stale tabs reload current values rather than overwriting them.

### Test strategy

- All create, edit, archive, restore, and delete paths.
- Typed defaults and explicit null/checkbox/number/decimal/select behaviour.
- Snippet reference impact through Blocks and addenda.
- Group-label merged-report before/after output.
- AppTest form reset, section persistence, stale review, Apply failure, and
  success.
- Existing AI-package state remains independent.

### Dependencies

Checkpoints 1–3.

### Manual browser checks

- Create each Field type and use an accented French label.
- Edit/archive/restore a Snippet used transitively through an addendum.
- Create/change/delete a group label and inspect a real merged Gastric report.
- Two-tab stale-form behaviour.

### Boundary

Yes. This is a natural UI-foundation commit and conversation boundary.

## 12. Checkpoint 5 — Block Studio and `Block_Fields`

### Goal

Make a complete non-table Block and its Field wiring maintainable in one
atomic guided workflow.

### Likely files and components

- `pages/editor.py`
- `content_studio.py`
- `content_changes.py`
- `database.py`
- `editor_preview.py`
- new `tests/test_stage6_blocks.py`
- existing rendering, grouping, Stage 3, and golden tests

### Data-model and schema changes

None.

### UI behaviour

One Block form controls:

- immutable key;
- editable name;
- `site_label`;
- `conclusion_group`, selected from existing values or explicitly created;
- all six template columns;
- ordered Field bindings;
- Field add/remove;
- `label_override`;
- inherited versus overridden default; and
- `context_section`.

Actions and boundaries:

- Create starts empty and requires complete valid templates.
- Duplicate requires a new key and name and copies base data and
  `Block_Fields`.
- Existing consistency rules are copied exactly with a duplicated Block but
  remain read-only.
- Edit can coordinate template and relationship changes in one candidate.
- Up/Down ordering changes `Block_Fields.sort_order`.
- Archive/delete uses Checkpoint 3 lifecycle analysis.
- Table Blocks remain visibly read-only.

### Invariants and safety constraints

- Non-table macro/micro/conclusion requirements remain enforced.
- Field variables exactly match final bindings.
- Context/title variables come only from context-section Fields.
- `site_label` and `conclusion_group` are validated in complete reports, never
  by a one-Block preview alone.
- Field position is unique per Block.
- Removing a Field and its template use can be one atomic candidate.
- Duplicate creates no Preset relationship.
- Permanent Block deletion cleans relationships/configuration but never
  silently deletes Presets or Fields.
- Any surviving invalid Preset blocks the candidate.

### Test strategy

- Complete create and duplicate parity.
- The same Field shared across Blocks.
- Reorder without changing values.
- Every inheritance state.
- Context-section movement and title/context effects.
- Site-label merging and conclusion-group section/addendum effects.
- Pending duplicate and ad hoc instances.
- Delete cleanup of `Preset_Blocks`, rules, group labels, and Quick Type
  targets.
- Candidate refusal when a surviving template or Preset would break.
- Golden fixtures stay unchanged unless a deliberately reviewed fixture
  change belongs to a real content edit.

### Dependencies

Checkpoints 1–4.

### Manual browser checks

- Create an orphan Block and inspect its standalone preview.
- Duplicate Appendix or Gallbladder and compare output.
- Reorder Fields and verify tab order.
- Change `site_label`/`conclusion_group` in a disposable database and inspect
  merging.
- Archive a Block used by a pending composed Case and reopen it.

### Boundary

Yes. This is the second major architectural checkpoint.

## 13. Checkpoint 6 — Preset Studio and `Preset_Blocks`

### Goal

Complete ordinary Stage 6 authoring with safe Preset composition, ordering,
and overrides.

### Likely files and components

- `pages/editor.py`
- `content_studio.py`
- `content_changes.py`
- `database.py`
- `composition.py`
- `quicktype.py`
- `editor_preview.py`
- `pages/workspace.py`
- new `tests/test_stage6_presets.py`
- existing composition, Quick Type, Workspace, and golden tests

### Data-model and schema changes

None beyond `display_order` from Checkpoint 1.

### UI behaviour

One Preset form controls:

- immutable `short_code`;
- name, category, default title, and `default_adicap`;
- ordered non-table Block instances;
- add/remove Block instance;
- Up/Down reorder through `display_order`; and
- per-instance, per-Field inheritance/override controls.

Actions and boundaries:

- Create requires at least one active non-table Block.
- Duplicate requires a new code/name and copies metadata, composition, display
  order, and overrides.
- Quick Type tokens are deliberately not copied; review warns that the
  duplicate has only its bare shortcut until Stage 7 configuration is added.
- Edit handles metadata and composition atomically.
- Archive/restore/delete uses the shared lifecycle service.
- Table-bearing Presets are read-only/advanced unless exact unchanged
  duplication is separately approved.

### Invariants and safety constraints

- `sort_order`/instance number never changes during reorder.
- Display order is unique and contiguous.
- Duplicate instances of the same Block remain distinct.
- Overrides are valid for the exact linked Field set.
- Removing a Block prunes only that instance's overrides/configuration.
- Explicit pending composition retains its saved order across a Preset
  reorder.
- Legacy pending composition without `block_instances` follows the new default
  and requires acknowledgement.
- Permanent Preset deletion detaches only validated Cases and cannot proceed
  with pending Cases.
- Quick Type configuration remains valid after any mechanical cleanup.

### Test strategy

- Create, duplicate, edit, archive, restore, and delete.
- Duplicate Block instances with different overrides.
- Reorder while proving immutable instance numbers and Quick Type targets.
- Explicit pending composition unaffected by display reorder.
- Legacy pending acknowledgement.
- Override inherit/null/zero/false/empty-string distinctions.
- Prefix-overlap warnings for new Preset codes.
- Validated detachment and reviewed inverse.
- AppTest for stable selector behaviour after rename/archive and two-tab
  conflicts.

### Dependencies

Checkpoints 1–5.

### Manual browser checks

- Duplicate `etc2` into a disposable code and verify the copied default
  report.
- Reorder a two-specimen Preset and inspect numbering/grouping.
- Use duplicate instances with distinct overrides.
- Reopen a pending Case created before reorder.
- Archive/delete a Preset with pending versus validated dependencies.

### Boundary

Yes. Ordinary Stage 6 functionality is complete here.

## 14. Checkpoint 7 — consolidation, regression, and acceptance

### Goal

Remove duplicate UI paths, harden cross-feature behaviour, and verify the
whole Stage 6 outcome.

### Likely files and components

- all Stage 6 modules and tests;
- `pages/editor.py`;
- `pages/workspace.py`; and
- `PROGRESS.md`, `TESTING.md`, `EDITOR_UI_PROPOSAL.md`, and stale `CLAUDE.md`
  passages, but only after implementation results are known.

### Data-model and schema changes

None.

### UI behaviour

- One coherent Content Studio experience.
- Neutral review wording reusable by manual changes, AI packages, and
  inverses.
- Clear distinctions among Edit, Duplicate, Archive, Restore, Unlink, and
  Permanently delete.
- Destructive review explicitly lists automatic cleanup and loss of future
  unvalidation capability.
- Recent revisions identifies `content_studio` origin and prepares reviewed
  inverses.

### Invariants and safety constraints

- No hidden direct writer remains reachable from Editor UI.
- The initial recovery-snapshot gate protects every Apply.
- Stage 5 AI packages cannot manipulate lifecycle state.
- Stage 7 tables are only mechanically preserved/cleaned, not authored.
- Operational DB, accepted review artifacts, and golden fixtures are never
  modified by automated verification.
- All five routes boot against an isolated database.

### Test strategy

- Full isolated suite.
- Focused Stage 2–6 transaction/UI suite.
- Compilation and whitespace checks.
- Migration from a realistic pre-Stage 6 database copy.
- Snapshot v1/v2 recovery drills.
- Two-connection review/Apply races.
- Full rollback after Case-reference detachment and after every destructive
  phase.
- Operational database raw checksum before and after.
- No golden regeneration merely to make tests pass.

### Dependencies

All ordinary checkpoints.

### Manual browser checks

Final matrix:

- every entity create/edit/archive/restore/delete;
- Block and Preset duplication;
- relationship add/remove/reorder;
- all inheritance states;
- site/group/group-label effects;
- pending before/after acknowledgement;
- archived pending reopen;
- frozen validated behaviour;
- two tabs;
- AI-package regression;
- inverse of an archive, relationship edit, and permanent deletion; and
- recovery snapshot download and restore in an isolated copy.

### Boundary

Yes. This is the final Stage 6 acceptance and commit boundary. Documentation
is updated with actual results and browser findings only after those results
exist.

## 15. Advanced and exceptional work

These items do not inflate or block ordinary Stage 6.

### Field type or option migration

This is not part of the ordinary checkpoints. Creating a new Field—including a
select with options—is ordinary. Changing an existing Field's type or options
is advanced because values may exist in:

- global defaults;
- Block overrides;
- Preset overrides;
- saved pending structured input;
- Quick Type lookup tables;
- consistency rules; and
- templates relying on numeric display aliases.

A later bounded checkpoint needs an explicit old-to-new value mapping,
complete pending-case conversion preview, override/rule/token migration,
atomic rollback, and a decision about whether pending structured inputs are
rewritten or preserved through a replacement Field. Until that design is
reviewed, type and options stay immutable.

### Table Blocks and `Preset_Block_Rows`

Existing table Blocks remain readable and renderable but are not editable,
duplicable, or structurally authored through ordinary Stage 6 forms. Table row
authoring needs a dedicated model for columns, row identity, ordering, and
validation. Presets containing table Blocks refuse structural editing rather
than partially exposing unsupported configuration.

### Other intentional exclusions

- Stable-key renaming for Fields, Blocks, Presets, or Snippets.
- Automatic modification of clinical templates during deletion.
- Automatic deletion of dependent base entities.
- Automatic operational-golden acceptance or `seed_data.py` rewrite.
- Multi-user collaboration beyond current compare-and-swap protection.
- Hostile-template execution quotas or exhaustive Cartesian rendering.
- Inventory or protection of unsaved browser-only drafts.

## 16. Stage 7 exclusions

Stage 6 may preserve, validate, copy where intrinsically required, and safely
clean existing Quick Type or consistency configuration, but it does not expose
authoring for it. The following remain Stage 7:

- guided `Quick_Type_Tokens` create/edit/delete/reorder;
- Quick Type lookup, measurement, digit-width, ambiguity, and collision UI;
- consistency-rule authoring;
- variant-Preset migration such as `etc0`–`etc5` to a base `etc` grammar;
- paste/upload of `(case ID, Quick Type)` rows;
- atomic bulk pending-Case creation; and
- bulk decoded-value/report review.

No Stage 6 operation automatically creates or validates Cases.
