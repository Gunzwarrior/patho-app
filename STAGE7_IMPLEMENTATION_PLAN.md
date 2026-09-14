# Stage 7 implementation plan — Quick Type Studio and bulk pending-case intake

**Approved architectural/checkpoint contract; planning documentation only.
Stage 7 implementation has not begun.**

Authority: `EDITOR_UI_PROPOSAL.md` Stage 7, the completed Stage 6 contract in
`STAGE6_IMPLEMENTATION_PLAN.md`, and the accepted Stage 7 planning pass. The
post-Stage-6 repository reviewed for this plan is
`a23e917cc674481d6ab1e33f50ab0da13d1f33ab`.

Later prompts may say “implement Stage 7 CPn according to
`STAGE7_IMPLEMENTATION_PLAN.md`.” This document is the controlling Stage 7
scope unless Thomas approves an amendment. It records intended behavior, not
progress; actual results belong in `PROGRESS.md` and `TESTING.md` as they land.

## 1. Outcome and data-plane separation

Stage 7 has two related outcomes but deliberately separate data planes.

### Configuration/content authoring

Quick Type tokens and consistency rules are reusable content configuration.
They retain the Stage 6 path:

```text
guided draft -> content_studio planner -> content_changes candidate
  -> frozen review -> confirmation -> atomic Apply
  -> Content_Revisions / Content_Changes -> reviewed inverse
```

There is no direct configuration writer in the UI. The recovery-snapshot gate,
canonical content hash, local Case guard, candidate-wide validation, exact
physical audit images, stale checks, and inverse machinery protect every
Apply.

### Operational Case/bulk intake

Bulk intake is a daily Case workflow, not Content Studio authoring:

```text
UTF-8 CSV/TSV -> bounded normalization
  -> one-connection Quick Type parse and Case construction
  -> decoded/rendered frozen review (no writes) -> confirmation
  -> one BEGIN IMMEDIATE transaction
  -> shared transaction-scoped Case persistence primitive
  -> pending Cases plus local batch audit
```

Cases remain outside content snapshots, AI context, `Content_Changes`, and
operational-review artifacts. Bulk gets its own immutable session-local review
because patient-bearing rows cannot enter content provenance. This does not
permit a second Case writer: ordinary one-Case save and batch Apply share the
same inner serialization, fingerprint, Preset-identity, and persistence
primitive. The inner primitive accepts a connection and never begins, commits,
or rolls back a transaction.

Bulk preview and Apply remain separate checkpoints. The preview must be
independently accepted before a multi-Case writer exists.

## 2. Module boundaries

- `quicktype.py`: pure and connection-scoped grammar validation, active-Preset
  resolution, parsing, and reachability analysis; never writes.
- `consistency.py`: pure rule validation/evaluation; never writes.
- `content_studio.py`: complete guided configuration drafts, physical
  source/endpoint baselines, and internal content intents; never writes.
- `content_changes.py`: candidate materialization, final-graph validation,
  frozen content review, Apply, audit, and reviewed inverse.
- `database.py`: connection-scoped reads and the shared inner Case persistence
  primitive.
- `editor_preview.py`: authoritative connection-scoped report construction and
  saved-pending-Case reconstruction.
- `pages/editor.py`: Quick Type and consistency form orchestration only.
- new `bulk_intake.py`: bounded input parsing, decoded Case preparation,
  immutable batch review, and Apply orchestration.
- new `pages/bulk_intake.py`: operational paste/upload/review UI only. It
  belongs beside Workspace and Worklist in `app.py`, not behind the Editor's
  content-recovery gate.

`change_packages.py` external v1 remains unchanged and read-only for Quick Type
and consistency tables. Initial Stage 7 has no AI-package v2.

## 3. Cross-cutting invariants

### Review and privacy

- UI modules perform no direct SQL writes.
- Configuration Apply requires an issued frozen review, separate confirmation,
  initial recovery snapshot, and existing content/local stale guards.
- Batch Apply requires an issued frozen batch review and separate confirmation.
  Changed input, interpretation, content state, or target Case namespace makes
  it stale.
- Apply rechecks under its write transaction; UI validation is not authority.
- Patient-bearing values and Case IDs never enter content audit/snapshots, AI
  context, operational artifacts, externally copyable errors, or object
  `repr`. Batch review data remains session-local.

### Quick Type

- Resolution remains case-sensitive longest-active-prefix matching.
- Archived Presets are unavailable for new input but remain resolvable by
  saved pending Cases.
- `block_sort_order` targets immutable `Preset_Blocks.sort_order` instance
  identity. `display_order` is presentation only and never retargets a token.
- Lookup keys are one character. `!` remains reserved to skip the rest of the
  current Block's tokens to the next configured Block.
- Measurement consumes digits greedily, capped by optional positive
  `digit_width`; width is a maximum typo guard, not a delimiter or exact width.
- More than one token may not target the same `(instance_no, Field)`.
- A configured modifier made unreachable by another active Preset is invalid.
  Deterministic bare-code prefix overlap that shadows no configured modifier
  is allowed with a clear warning.
- Parsing is all-or-nothing; a failed prefix is never partially applied.

### Consistency rules

- Initial rules remain unordered two-Field AND predicates within one Block.
- They warn and require confirmation; they neither prohibit nor rewrite
  clinically unusual values.
- Rule-only changes are workflow-warning changes, not artificial report
  content changes. They do not enter the relevant-content fingerprint merely
  to force acknowledgement.
- Candidate review nevertheless identifies default and saved pending contexts
  whose warning set changes.

### Cases

- Validated artifacts remain frozen and are never regenerated or updated.
- Existing pending Cases are not rewritten after Quick Type changes; they
  already contain materialized values.
- Bulk creates only new pending Cases. It never overwrites, validates,
  unvalidates, deletes, or supplies a bulk inverse.
- Bulk Cases store the same materialized structured input, HTML, content
  fingerprint/revision, and frozen Preset identity as ordinary pending saves.
- Original Quick Type is not stored in Case structured input. Materialized
  values are authoritative.
- Bulk `pending_reason` is `NULL`; provenance is not a clinical reason.

## 4. Migration and compatibility

Schema work is additive, named, idempotent, and safe on a database holding
Cases. No Stage 7 migration rebuilds tables, regenerates reports, changes Case
status, rewrites structured input, or infers clinical values.

CP1 adds only enforcement needed by CP2/CP3. CP5 adds batch provenance only
when its first writer arrives; bulk schema does not land in CP1. Configuration
rows already belong to content snapshot v2 and the generalized candidate
engine, so no snapshot-format bump is expected absent an unavoidable shape
change. Case/batch metadata remains excluded from content snapshots.

`seed_data.py` remains a reproducible bootstrap, not a live-content mirror.
No checkpoint automatically rewrites it, regenerates goldens, accepts an
operational artifact, exports a snapshot, commits, or deploys.

## 5. Checkpoint summary

| CP | Outcome | Boundary |
|---|---|---|
| 1 | Configuration-authoring safety foundation | Critical backend boundary |
| 2 | Guided Quick Type Studio | Complete product boundary |
| 3 | Consistency-rule authoring | Separate clinical-semantics boundary |
| 4 | Decoded bulk preview, no Case writes | Deliberate no-write boundary |
| 5 | Atomic pending-Case creation | Core Stage 7 delivery boundary |
| 6 | Conditional `etc0`–`etc5` consolidation | Optional, never blocks bulk |
| 7 | Consolidation and final acceptance | Final Stage 7 boundary |

Stage 7 can pause safely after CP2, CP3, CP4, or CP5. CP6 is not required for
bulk intake.

## 6. CP1 — configuration-authoring safety foundation

### Goal and outcome

Establish only the backend foundation CP2 and CP3 require. No new UI appears.
Tests and disposable callers can prepare, review, Apply, inverse, and
re-inverse a complete token configuration or same-Block rule set with full
source/endpoint protection.

### Scope and modules

- `init_db.py`, `database.py`, `quicktype.py`, `consistency.py`
- `content_studio.py`, `content_changes.py`, `content_snapshot.py`
- new `tests/test_stage7_configuration.py`
- focused existing schema/snapshot/candidate/Preset/Block/Quick Type/rule tests

Add connection-scoped Quick Type reads and parsing. Candidate evaluation uses
the supplied candidate connection for active Presets, tokens, instances,
Fields, and rendering; it cannot silently reopen the live database.

Add complete-set draft planners for one Preset's tokens and one Block's rules.
They emit minimal deterministic generalized operations plus signed no-write
assertions binding:

- owner physical row;
- complete current configuration rows, including surrogate IDs;
- exact Preset-Block instance endpoints for tokens;
- referenced Field rows/type/options and Block bindings; and
- required absence for new positions/natural keys.

Strengthen final-graph validation for duplicate token targets, reachability,
typed/canonical rule values, nonblank messages, and equivalent duplicate
rules. Provide only minimal backend review evidence: canonical before/after
configuration, validation/reachability findings, and exact database changes.
Generated/real Quick Type examples move to CP2; rule warning deltas move to
CP3. Do not build a speculative generic review-plugin framework.

### Schema

Add a unique index on `Quick_Type_Tokens(preset_id, sort_order)`, matching the
identity snapshot restore and generalized candidates already assume. Its named
migration must check duplicates read-only, refuse without partial changes,
create the index when safe, and be idempotent.

No new columns, rule-key migration, bulk tables, or snapshot version belong in
CP1. Symmetric rule uniqueness remains candidate validation rather than a
fragile SQL index over JSON operands.

### Invariants and existing behavior

- One complete configuration draft is the authoring unit.
- Token positions are unique. Guided edits normalize desired order, but
  unrelated changes do not rewrite valid legacy sparse positions.
- Lookup/reserved/value, measurement/type/width/adjacency, duplicate-target,
  reachability, Field endpoint, and rule invariants are backend enforced.
- Rule value sets are nonempty, unique, deterministic, Field-valid; Fields are
  distinct; message is nonblank; same or A/B-reversed predicates cannot
  duplicate one another.
- Archived endpoints and table Blocks cannot receive new configuration.
- All operations retain Stage 6 review, recovery gate, hash/local guard, exact
  audit, rollback, and inverse behavior.
- Token/rule changes alter snapshot hash but never edit/acknowledge Cases.
- Preset duplicate still omits tokens; Block duplicate still copies rules.
- Lifecycle cleanup must pass the strengthened final graph.
- Stage 5 package v1 and snapshot v1/v2 recovery stay compatible.

### Out of scope

No Streamlit forms, example/report chooser, pending warning-delta UI, bulk
parsing or semantics, Case schema/writes, `etc` migration, or AI changes.

### Automated tests

- valid and duplicate-position migrations, repeat migration, atomic refusal;
- candidate parsing sees candidate state rather than live state;
- full token/rule create/replace/delete and token reorder;
- no-write review, Apply, inverse, inverse-of-inverse;
- source set, owner, endpoint, instance, delete/recreate ABA, and two-tab races;
- all accepted grammar/rule invariants, including multi-Block adjacency,
  harmless versus unreachable prefix overlap, reversed rules, and sparse rows;
- fault rollback through materialization/audit;
- package v1, snapshot restore, lifecycle cleanup, duplicate/reorder regressions;
- operational-database isolation tripwires.

### Manual acceptance, dependency, boundary

On a disposable DB inspect and round-trip one `dai` token candidate and one
Appendix rule candidate, then reproduce a stale endpoint refusal. Depends only
on completed Stage 6. This is a critical natural review/commit boundary.

## 7. CP2 — guided Quick Type Studio

### Goal and outcome

Add a guided Quick Type area to Content Studio. A user can create, edit,
delete, and reorder one active Preset's tokens without JSON; test codes;
inspect decoded values/reports; then Prepare, confirm, and Apply a frozen
content review.

### Scope and modules

- `pages/editor.py`, `content_studio.py`, `content_changes.py`
- `quicktype.py`, `database.py`, `editor_preview.py`
- new `tests/test_stage7_quicktype_ui.py`

The form holds one complete generation-scoped token list. Each row exposes
target Block instance, target Field, kind, lookup mapping or optional maximum
digit width, and Up/Down/delete. Instances display in `display_order` but show
immutable instance number. Widget actions mutate only the draft and clear an
old review/confirmation.

Add a parse/test panel. Generated cases exercise bare code, every lookup map,
representative measurement widths, rollover, reserved character, excess
width, leftovers, and prefix routing. User-entered positive examples must
parse completely. Examples and interpretations are signed into the review.

### Schema and invariants

No schema beyond CP1. Only active non-table owners and active bound Fields are
selectable. Controls are Field-typed; width is labelled as a maximum. Review
shows before/after grammar, reachability errors or harmless warnings, decoded
instance/Field labels and values, and selectable production-path reports with
consistency warnings. Review mode hides/disables the draft until **Edit
draft**. Add/remove/reorder/test/Prepare never writes. Apply repeats all Stage
6/CP1 guards and audit checks.

### Existing behavior and out of scope

Workspace immediately uses applied grammar. Pending Cases keep materialized
values and fingerprints. Archived configuration is read-only until restore.
Preset duplication still does not copy tokens. Token-only changes may change
snapshot hash while report HTML stays identical. No consistency UI, automatic
token copy, `etc` wizard, multi-character/middle-skip/table grammar, bulk, or
AI change belongs here.

### Tests

- AppTest full CRUD/reorder and zero writes before Apply;
- typed mappings and measurement controls;
- duplicate instances and display reorder identity;
- generated/user positive and negative examples;
- collision/error/warning and decoded report display;
- mutation/target switch clears frozen review/confirmation;
- recovery gate, stale tab, Apply/inverse/re-inverse;
- Workspace uses the reviewed grammar;
- source regression proving no UI writer.

### Manual acceptance, dependency, boundary

Edit disposable `dai`, exercise valid/invalid/width/leftover examples, test a
multi-Block rollover/`!`, use it in Workspace, inverse it, and reproduce a
stale composition tab. Depends on CP1. Natural complete product boundary.

## 8. CP3 — consistency-rule authoring

### Goal and outcome

Add guided create/edit/delete for same-Block warn-and-confirm rules. The user
selects two Fields and exact triggering value sets, writes a warning, and
reviews where it starts/stops firing before confirmed Apply. This is separate
from Quick Type Studio because its clinical semantics and pending impact are
different.

### Scope and modules

- `pages/editor.py`, `content_studio.py`, `content_changes.py`
- `consistency.py`, `editor_preview.py`, `database.py`
- new `tests/test_stage7_consistency_ui.py`

Expose focused rules under a non-table Block or as a Content Studio area.
Present: warn with message when Field A is in a set **and** Field B is in a
set. Select/checkbox values use typed choices; number/decimal use bounded typed
entry; text, if used, is exact line-separated matching. Canonicalize sets so
input order alone creates no revision.

For rule operations, compare warnings in default Preset reports and every
reconstructable pending Case on the candidate connection. Classify
warning-only impact separately from rendering/fingerprint impact.

### Schema, invariants, interactions

No schema. Same-Block/two-distinct-Field/AND only. Rules remain warnings, never
hard blocks or repairs. Review includes matching/nonmatching examples and
before/after default/pending warning sets. Apply never edits a Case or its
fingerprint. A pending Case may show a new warning on reopen and uses the
ordinary consistency checkbox on its next Save. Validated artifacts stay
frozen. Block duplication and lifecycle cleanup retain existing behavior.
Manual, Preset, Quick Type, and later bulk values share the same resolved-value
evaluator. Stage 5 v1 is unchanged.

### Out of scope

No cross-Block rules, OR/not/ranges/expression language, dynamic option
restriction, automatic correction, hard prohibition, bulk, or `etc` work.

### Tests

- typed CRUD for every Field type;
- empty/duplicate/invalid/noncanonical/same-Field/reversed/unavailable/blank
  refusal;
- matching/nonmatching candidate examples;
- default and pending warning deltas;
- stable fingerprints and untouched Cases for warning-only changes;
- Workspace confirmation through manual and Quick Type input;
- Block duplicate/lifecycle regressions;
- stale endpoints, rollback, inverse/re-inverse;
- AppTest frozen no-write flow and no direct writer.

### Manual acceptance, dependency, boundary

Create a disposable Appendix rule, inspect examples/pending deltas, Apply,
reopen an affected pending Case and verify Save gating, then delete/inverse and
test stale Field/Block refusal. Depends on CP1; may be built independently of
CP2, though following it gives consistent UI. Separate natural boundary.

## 9. CP4 — decoded bulk preview with no Case writes

### Goal and outcome

Add an operational page accepting exactly Case ID and Quick Type, then
validate, decode, render, and summarize every row against one content state.
CP4 creates no Cases and has no multi-Case writer.

### Scope and modules

- new `bulk_intake.py`, new `pages/bulk_intake.py`, `app.py`
- `quicktype.py`, `database.py`, `composition.py`, `editor_preview.py`
- new `tests/test_stage7_bulk_preview.py`

Input contract:

- UTF-8 CSV/TSV, no Excel;
- explicit comma/tab delimiter and explicit **First row is header** control;
- exactly two cells per nonblank logical row;
- outer whitespace trimmed; internal text and case preserved;
- Case IDs and Quick Type remain case-sensitive;
- maximum 250 data rows and 1 MiB source bytes.

Use Python CSV parsing, accept UTF-8 BOM, and reject invalid UTF-8, NUL,
malformed quoting, wrong columns, blank normalized cells, and limits before DB
evaluation. A selected header is checked as two cells but not inferred from
words, so a clinical first row cannot be silently discarded.

In one read transaction, parse through the supplied connection, load active
Preset Blocks, derive explicit instances, apply overrides, normalize every
resolved value as Workspace does, and render with `editor_preview`. Construct
normal structured input with instances, complete `block_key#instance_no`
values, empty wildcard notes, locks off, and automatic final text fallbacks.
Store rendered automatic clinical information as Case `clinical_info`.

The frozen review shows a compact table and expandable decoded values,
warnings, conflicts, and restricted report. It binds normalized source,
content snapshot hash/revision, exact target-ID absence, and a deterministic
digest of every materialized input/report. The normalized-source digest is
SHA-256 over the canonical ordered array of trimmed data-row pairs after
explicit header removal; delimiter spelling and quoting do not make two
logically identical inputs different.

### Schema and invariants

No schema; audit/linkage wait for CP5. CP4 is provably read-only. All rows see
one content state. Duplicate input IDs and every existing pending/validated ID
are batch errors. Any malformed row, parse/reachability/endpoint/table/render/
reconstruction error makes the whole review non-applicable. Consistency
warnings are review results, not parse failures, and are signed prominently.
Every prepared Case must round-trip through `render_saved_case` identically.
Raw Quick Type remains session-local and absent from structured input, logs,
external errors, and `repr`.

### Existing behavior and out of scope

This is Workspace-adjacent and not snapshot-gated like content Apply. It uses
CP1 parsing and CP2-authored grammar, and honors existing rules without
depending on CP3 UI. It uses active Presets only and writes no revision, Case,
history, or audit. No Apply, inferred format, Excel, extra columns, per-row
clinical info/reason, table Preset, bulk validation/delete/inverse/overwrite,
or `etc` migration.

### Tests

- equivalent paste/upload CSV/TSV;
- BOM/CRLF/quotes/blank/malformed/NUL/columns/header/limits;
- exact normalization and case sensitivity;
- duplicate and existing pending/validated IDs;
- unknown/shadowed/lookup/digit/width/reserved/leftover/multi-Block cases;
- instance identity, thyroid automatic context/title, warning/conflict display;
- reconstruction/report equality and one-content-state staleness;
- immutable review privacy/no-`repr` leakage;
- SQL/state proof of zero writes.

### Manual acceptance, dependency, boundary

Paste/upload equivalent batches; inspect Appendix/thyroid reports; exercise
format controls and malformed, duplicate, existing-ID, warning, and parse
failures; confirm no Case exists. Depends on CP1/CP2, not CP3/CP6. Deliberate
no-write natural boundary.

## 10. CP5 — atomic creation of pending Cases

### Goal and outcome

After a valid CP4 review, separate confirmation and Apply create every row as
pending. Any stale state or failure leaves no Case and no batch audit.

### Scope and modules

- `database.py`, `init_db.py`, `bulk_intake.py`, `pages/bulk_intake.py`
- `pages/worklist.py` only for a bounded provenance caption if useful
- new `tests/test_stage7_bulk_apply.py`
- existing Stage 2/Workspace/Worklist/transaction/migration tests

Extract `database.save_case` internals into a typed connection-scoped
primitive. It performs no transaction control, enforces validated lock and
Preset identity, canonicalizes structured input, recomputes fingerprint,
records revision/frozen identity, and applies an explicit create/update mode.
The public one-Case wrapper preserves current behavior. Bulk uses create-only
pending mode inside one `BEGIN IMMEDIATE`.

Apply verifies review issuer, source digest, content hash/revision, warning
acknowledgement, interpretation digest, and every target absence under the
lock. It rebuilds and compares the decoded batch in that transaction, inserts
one audit plus all Cases, and commits once.

### Schema and migration

Add local provenance:

```text
Case_Batch_Imports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  row_count INTEGER NOT NULL,
  normalized_input_sha256 TEXT NOT NULL,
  content_snapshot_sha256 TEXT NOT NULL,
  content_revision_id INTEGER NOT NULL REFERENCES Content_Revisions(id)
)
```

Add nullable `Cases.batch_import_id INTEGER REFERENCES Case_Batch_Imports(id)`
and an index on it. This linkage is
justified: it identifies Cases created together for local troubleshooting,
proves the audit belongs to those inserts, and supports meaningful atomic
audit-failure testing without storing raw Case IDs, Quick Type, clinical
values, or reports in the audit row.

Migration is additive/idempotent with no backfill; historic and ordinary Cases
stay `NULL`. Batch metadata remains outside all content/AI/operational exports.

### Invariants and interactions

- Insert-only, `status='pending'`, `pending_reason IS NULL`; never overwrite.
- Warning-bearing batches require a separate signed batch-level
  acknowledgement, preserving warn-and-confirm semantics.
- Each Case has normal materialized input, exact HTML, matching fingerprint/
  revision/frozen Preset identity, and batch link.
- No validation-history or validated-status event is created.
- Any race, state/interpretation/warning/render/reconstruction difference,
  Case failure, or audit failure rolls back everything.
- Success uses a dedicated reset-generation flag and post-rerun message.
- Cases appear normally in Worklist and reopen/edit/validate individually.
- Content change after review stales the whole batch. Unrelated Case arrival
  need not stale it unless a target ID is occupied.
- Content candidate Apply retains its separate complete Case guard.

### Out of scope

No validation, overwrite/resave, partial retry, delete, unvalidation, inverse,
raw input/Quick Type audit storage, table Presets, provenance in content
exports, or `etc` migration.

### Tests

- additive/repeat migration with historic `NULL` linkage;
- unchanged public `save_case` behavior after refactor;
- successful multi-row Apply and exact audit linkage;
- parity of input/report/fingerprint/revision/frozen identity;
- pending/NULL reason/no validation history;
- confirmation and warning-ack gates;
- input/content/interpretation/target races and two-connection uniqueness;
- fault injection before/after audit and every Case/serialization/render/final
  comparison phase, always zero partial Cases/audits;
- Worklist/Workspace reopen/edit/individual validation;
- no privacy-boundary leakage and operational DB tripwires.

### Manual acceptance, dependency, boundary

Apply warning-free and warning-bearing batches, verify Worklist/reopen/edit and
individual validation, then occupy one reviewed ID in another tab and confirm
zero partial rows/audit. Confirm no bulk validation/delete/inverse controls.
Depends on CP4. Critical core Stage 7 boundary.

## 11. CP6 — conditional `etc0`–`etc5` consolidation

### Gate, goal, and outcome

This is explicitly conditional: proceed only after CP2 is browser-proven and
Thomas approves this clinical content migration. It never blocks CP3–CP5.

Create one active `etc` Preset. Bare `etc` defaults to Bethesda II; suffixes
`0/1/2/3/5` select the corresponding existing
`thyroid_cytology_pattern`. Archive those five old Presets in the same Apply.
Leave `etc_bi` unchanged.

### Strategy and modules

- `content_studio.py`, `content_changes.py`, `quicktype.py`
- `pages/editor.py` only if a bounded assistant is needed
- new `tests/test_stage7_etc_migration.py`

One ordinary content candidate must:

1. create immutable code `etc`;
2. copy approved single-Block metadata/composition from `etc2`, retaining
   Bethesda II as bare default;
3. link lookup `0/1/2/3/5` to exact values
   `etc0/etc1/etc2/etc3/etc5`;
4. archive Presets `etc0`, `etc1`, `etc2`, `etc3`, `etc5`; and
5. leave `etc_bi` and all Cases unchanged.

Do not create general rename/Case-conversion machinery. If needed, a bounded
assistant emits the same CP2/Preset Studio operations, never a second writer.
Archiving is atomic and required: active `etc2` would otherwise capture the
longest prefix and make `etc` + `2` unreachable.

### Schema, invariants, interactions

No schema; this is reviewed live content, not migration code. Review shows
exact routing and report equivalence for bare/all suffixes. Pending Cases keep
old IDs, values, and fingerprints and reopen through archived resolution.
Validated artifacts stay frozen. Old variants are archived, not deleted;
later deletion uses Stage 6 lifecycle. New selection sees `etc`; `etc_bi` is
unchanged. Inverse follows ordinary reviewed protections.

### Out of scope and tests

No Case rewrite/reattach, variant deletion, `etc_bi` change, grammar expansion,
seed/golden/artifact automation, or generic rename tooling. Test atomic
migration with pending/validated variants, exact routing/report equivalence,
`etc_bi`, archived pending reopen/fingerprint, frozen validation, rollback,
stale review, inverse/re-inverse, and inverse blockers.

### Manual acceptance, dependency, boundary

Compare every new decoded report to its old variant; after Apply test bare/all
suffixes, old pending `etc2`, and `etc_bi`; inspect inverse consequences.
Depends on CP1/CP2 plus explicit approval. Optional natural boundary.

## 12. CP7 — consolidation and final acceptance

### Goal and scope

Harden cross-feature behavior, remove temporary/duplicate paths, update actual
status documentation, and verify Stage 7 coherently. Likely touches all Stage
7 modules/tests plus `PROGRESS.md`, `TESTING.md`, `EDITOR_UI_PROPOSAL.md`, and
`CLAUDE.md` only to record results that actually exist. No new schema beyond
CP1/CP5; a newly discovered need requires a plan amendment.

### Final invariants

- One Quick Type and one consistency authoring path, both through reviewed
  Content Studio Apply/inverse.
- One shared inner Case primitive for one-Case and batch save, with no inner
  transaction ownership.
- One operational bulk workflow: frozen preview, separate confirmation,
  atomic pending-only Apply, no validation/delete/inverse.
- No UI SQL writer or candidate parser fallback to live state.
- Instance-number token identity survives Preset display reorder.
- Pending Cases are not rewritten by grammar; rule-only changes are not
  fingerprints; validated history stays frozen.
- Stage 5 package v1 is unchanged.
- Case/batch data never crosses content/AI/operational/privacy boundaries.
- Recovery restore, destructive content inverse, and archived pending
  resolution work with Stage 7 schema.
- CP6 is accurately recorded as performed or deliberately omitted and does
  not block CP1–CP5 acceptance.

### Automated acceptance

- full isolated suite and focused Stage 2–7 transaction/UI suites;
- realistic pre-Stage-7 and repeat migration;
- v1/v2 recovery with historic and batch Cases;
- configuration and bulk two-connection races;
- fault injection through config audit and all batch phases;
- privacy-boundary/source checks and unchanged package v1;
- `py_compile`, `git diff --check`, and all routes booting in isolation;
- operational DB, accepted artifacts, and goldens unchanged absent separate
  approved manual action.

### Minimum browser matrix and boundary

Cover Quick Type CRUD/examples/Workspace/stale/inverse; rule CRUD/pending
warning/confirmation/inverse; CSV/TSV/errors/review invalidation; warning-free
and acknowledged warning batch Apply; Worklist/reopen/edit/individual
validation; target/content races with zero partial rows; absence of bulk
validation/delete/inverse; and CP6 only if approved. Depends on CP1–CP5; CP6
optional. Final Stage 7 review/commit boundary.

## 13. Deliberate deferrals and non-goals

- multi-character lookup tokens;
- middle-token skip character such as `*`;
- cross-Block consistency expressions;
- OR/not/range/expression rules;
- table-Preset Quick Type/bulk creation and `Preset_Block_Rows` authoring;
- Field type/options migration;
- bulk validation, overwrite, deletion, inverse, or partial retry;
- more than two input columns, Excel, inferred delimiter/header, demographics;
- storing original Quick Type in Case structured input;
- Stage 7 AI package or changes to external v1;
- automatic seed rewrite, golden generation, operational acceptance, snapshot,
  Git commit, or deployment;
- multi-user collaboration beyond stale-tab/transaction protection.

These are resolved exclusions, not missing design. Future work needs a bounded
plan and a real use case.

## 14. Repository-specific risks and assumptions

- Tokens have surrogate IDs but snapshot/candidate identity is
  `(preset_code, sort_order)`. CP1 closes database uniqueness while baselines
  still bind IDs against delete/recreate ABA.
- Rules lack a simple immutable public key. Treat the complete Block rule set
  as the draft unit; do not add speculative `rule_key` storage.
- Current pending impact is fingerprint-oriented. CP3 adds explicit warning
  deltas rather than contaminating fingerprints.
- `quicktype.parse_quick_type()` currently opens global helpers. CP1 must add
  and prove a supplied-connection path before candidate examples or bulk.
- `database.save_case()` owns its transaction and returns boolean. CP5 must
  preserve that API while extracting a typed inner primitive; bulk cannot call
  the wrapper in a loop.
- Workspace saves complete materialized values and final-text fallbacks. CP4
  must build the same shape and prove `render_saved_case` equality.
- New Streamlit draft/reset needs get distinct generation/one-shot flags;
  reusing flags has repeatedly caused stale widget bugs.
- Content review intentionally binds complete relevant Case sets. Bulk instead
  binds all content and target absences; unrelated Case arrival need not stale.
- Table Blocks lack the ordinary path Stage 7 relies on and must fail clearly.
- Tests remain fail-closed on temporary DBs; no Stage 7 test opens operational
  `pathology.db`.

## 15. Amendment rule

Implementation may refine names and small factoring, but may not silently
change the two data planes, checkpoint outcomes, resolved grammar/input
decisions, privacy boundaries, pending-only atomicity, or exclusions. Record a
concrete repository contradiction or newly approved requirement here before
implementing the affected change.
