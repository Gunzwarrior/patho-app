# Stage 5 implementation plan — optional reviewed change packages

**Completed plan; all three checkpoints are implemented, tested, reviewed, and committed.**
Authority: the approved
`EDITOR_UI_PROPOSAL.md` §§3, 4, 9–10 and `PROGRESS.md` in commit `5dd1f44`.
Repository reviewed: `7178f71`. This document replaces the previous proposed
plan. Its implemented outcome is recorded below; it does not authorise Stage 6
or Stage 7 work.

## Completion record — 2026-09-10

All three Stage 5 checkpoints are complete: no-write contract/review
(`220cd2e`), atomic Apply/reviewed inverse (`5868ad9`), and Editor integration
with restricted presentation (`5522a7b`). The final checkpoint 1
authoring-contract refinement is `de8ccce`, checkpoint 2 safe actionable
diagnostics are `3b93dea`, and the independent Sol review's blocking remediation
is `717fda6` (`Clarify v1 package authoring rules`). No previously deferred
non-blocking suggestions were added.

Checkpoint 3 browser/manual review passed on 2026-09-08. Final isolated
regression verification on 2026-09-10 passed all 370 collected tests (run in
bounded terminal shards), including 243 focused Stage 5
package/transaction/Editor/Workspace tests; relevant Python files compiled,
`git diff --check` passed, and an isolated temporary-database boot returned
HTTP 200 for all five routes. The operational database was not opened through
SQLite and its raw SHA-256 was unchanged before and after. No goldens, accepted
operational-review artifacts, or seed content were changed.

Independent real-model acceptance used the same fresh
`pathopilot-ai-context-v1` export with no reference answer or repository access.
Sonnet 5, ChatGPT High, and Gemini each produced a valid first package for the
fully specified request; each passed PathoPilot dry run, despite slightly
different JSON sizes (about 1.0–1.4 kB), with the same intended
normalized/rendered result. All three declined the intentionally incomplete
Block request and requested missing clinical content without inventing wording,
placeholders, or an empty package. In the malformed-link feedback loop,
PathoPilot returned safe actionable `link_key` feedback and Sonnet 5 returned a
corrected replacement that passed dry run on its first attempt. These results
verify package authoring behavior, not clinical correctness.

## 1. Review findings and scope decisions

The previous plan matched the product outcome but left these gaps:

| Repository evidence / gap | Required correction |
|---|---|
| Ambiguous blank/null/coercion rules; invalid Jinja keys accepted. | Exact types, storage, identifiers and widget defaults (§3). |
| `composition.add_instance` reserves identities from `1000`. | New Preset positions stay below `1000`. |
| `_render_pending_case` returns microscopy only, ignoring notes and locks. | Complete Workspace-equivalent saved-case preview (§5). |
| Single-target validation misses graph/widget invariants and orphan dependencies. | Validate the finished candidate once (§4). |
| Snapshot SELECTs lack a read transaction; content hashes exclude IDs. | Consistent export/backup plus identity and pending guards (§6). |
| Public hash token, two clones and duplicate touched-row guards add complexity without proving review. | One memory candidate and immutable server-held review (§6). |
| Origin-based revert dispatch fails on revert-of-revert; ID-free audit loses identity. | Extend existing row audit by table; check IDs and removal dependencies (§7). |
| Exceptions can leak Case values; `text_to_html` leaves HTML unescaped. | Fixed AI feedback and restricted report presentation (§2, §5). |
| Unverified export-size claim and unresolved product questions. | Measure a fixture; settle bounded scope below without extra approval gates. |

**Outcome:** Download context → optional external AI discussion → upload small
hash-bound operations → no-write dry run → readable operations, complete reports
and affected-pending before/after review → separately confirmed atomic Apply,
audit and safe revert. Manual editing requires neither AI nor JSON.

**Stage 5 scope decisions:**

- Create Fields, non-table Blocks, Presets and Snippets. Add `Block_Fields` only
  under a new Block and `Preset_Blocks` only under a new Preset. Reuse existing
  non-table endpoints; creation-time type/options/order/overrides supply the graph.
- Update existing rows only through the Stage 3 allowlist (§3); table Blocks
  remain read-only. No existing relationship/configuration changes.
- New Blocks: `is_table=0`, `site_label=NULL`, `conclusion_group=NULL`.
  New Presets: `default_adicap=NULL`. Existing grouping still renders when
  reused; new grouping configuration waits for Stage 6.
- No deletion/archive, key rename, Field type/option migration, table rows,
  Quick Type/rule/group-label authoring or Case writes. Revert is a guarded
  audited inverse, not a general package deletion operation.
- Preserve both manual locks; use a privacy notice without another export
  checkbox. These decisions fit the approved bounded roadmap.

## 2. AI context, economy, and privacy

Download `pathopilot-ai-context.json` (`application/json`), with exactly:

```json
{"format":"pathopilot-ai-context-v1","snapshot_sha256":"<64 lowercase hex>","instructions":{"response":"Return one pathopilot-content-change-package-v1 JSON object, without markdown. Copy the base hash; return only changed/created values, never the source snapshot.","privacy":"Do not include patient information, case identifiers, report examples, or audit data.","contract":"<generated compact contract for section 3>"},"snapshot":{"format":"pathopilot-content-snapshot-v1","tables":{}}}
```

The actual `snapshot.tables` contains the existing four base and six relation/
configuration tables, using `content_snapshot`'s exact stable-key column sets
and ordering. Capture them in **one read transaction**; export currently runs
multiple SELECTs without this guarantee. No schema migration or safety-marker
write occurs as a side effect of export. Promote the existing connection-based
snapshot helper rather than opening another connection inside a transaction.

Canonical JSON: UTF-8, `ensure_ascii=False`, sorted object keys, compact
separators, finite numbers only, final newline. The snapshot hash is exactly
SHA-256 of `content_snapshot_json(snapshot)` bytes, preserving Stage 4
compatibility. Expose one shared hash helper. The package hash uses the same
serialization rules on the validated, normalized envelope and deterministic
operation order; the model only copies the base hash, never calculates hashes.

Keep the existing snapshot encoding, including JSON stored in text columns;
explain that **package values use native JSON arrays/objects**, unlike those
snapshot columns. No second encoding, compression, per-operation hashes,
dependency IDs or general schema engine. Generate the concise field/type/default/limit contract from
the same declarative allowlists used by the parser, with one small valid
example. Include the supported template context names and literal `snippet()`
syntax so the model need not infer them from the library. A future version may add subsets with a full-base hash;
omit unused `scope` machinery now.

Privacy is structural: the export accepts content and fixed contract metadata
only, verifies exact envelope/table/column allowlists, and never queries Cases,
Case histories, revision/change audit, or safety state. No IDs, example reports,
free-form context field, or local review result can enter this artifact. Tests
use canaries in forbidden data and a query tripwire, not only string searches.
Reusable templates themselves can contain text a person inserted: the UI says
“Contains reusable content/configuration, not Case records. Review content for
patient details before sharing; de-identify any examples you supply separately.”
Do not claim semantic de-identification or silently redact templates.

Show actual file bytes and hash; record bytes for a named seeded fixture plus
contract in tests/docs. Test the example against the importer. Reject snapshots, unknown payloads and normalized no-op updates.
Byte size is not token count or a free-plan guarantee. This download is
independent of the recovery-snapshot gate and makes no network request.

AI correction feedback is a separate allowlisted structure: fixed error code,
recognized operation path/table/column, validated content key where applicable,
and a fixed suggested correction. For pending failures, give a generic count
and “Review the local pending-case errors.” Never copy raw exception messages,
unrecognized input values/keys, the package summary, Case identifiers, rendered
Case text, SQL, paths, audit data, or traceback into feedback or logs. Local
patient-bearing previews/errors remain session-only, without downloads or
shared Streamlit caches. Uploaded prose can itself contain patient details;
free-text privacy cannot be guaranteed by schema validation.

## 3. Exact v1 package contract

```json
{"format":"pathopilot-content-change-package-v1","base_snapshot_sha256":"<64 lowercase hex>","summary":"Add an optional specimen size field","operations":[{"op":"create","table":"Fields","key":"specimen_size_mm","values":{"label":"Taille (mm)","type":"decimal","default_value":null}}]}
```

Exactly these four required keys. Summary is a trimmed nonblank string, at most
500 characters. Operations: 1–200. Raw file limit: 1 MiB before UTF-8 decoding;
JSON nesting limit: 20. Reject malformed UTF-8/JSON, duplicate object keys at
any depth, non-finite numbers (including overflow), wrong types, unsupported
nulls, unknown/missing keys, and unexpected trailing content. Integers exclude
booleans; strings are not automatically converted into numbers/booleans.
Report indexed paths using the original uploaded operation index even after
sorting. Cap displayed errors at 20 with an omitted-error count. File suffix
and MIME filtering are convenience only; the backend enforces the contract.

### Operations

| Operation | Exact members and permitted values |
|---|---|
| `create` | `op`, `table`, `key`, `values`. Table is one of the four base tables. Key absent from the base and unique in the package. Required/optional values below. |
| `update` | `op`, `table`, `key`, `set`. Target exists in the base. Nonempty `set` uses only the update allowlist below; every supplied value must change after normalization. |
| `link` | `op`, `table`, `key`, `values`. Table is `Block_Fields` or `Preset_Blocks`; exact composite identities below. Must not already exist. |

A base entity has at most one create/update operation; updates to same-package
creations are rejected (put final values in `create`). Duplicate logical links
are rejected. No delete, unlink, reorder, upsert, raw SQL, executable command,
patch expression, import file path, snapshot replacement, or other table.

Base key means `Fields.key`, `Blocks.key`, `Presets.short_code`, or
`Snippets.shortcut`. New keys are case-sensitive, 1–80 ASCII characters.
Fields use `[A-Za-z_][A-Za-z0-9_]*`; other keys use `[A-Za-z0-9_-]+`.
Existing keys are resolved exactly without retroactively imposing new grammar.
Reject new Field bindings that collide with reserved/derived template names
(`snippet`, `value`, `site_label`, `fragment_text`, Jinja literals, or another
Field's generated `<decimal_key>_display`). Never silently rename keys.

| Create table | Required `values` | Optional `values` and defaults |
|---|---|---|
| Fields | `label`, `type`, `default_value` | `options=null`, `conclusion_addendum_template=null` |
| Blocks | `name`, `macro_template`, `micro_template`, `conclusion_template` | `context_template=null`, `title_fragment_template=null`, `conclusion_label_template=null` |
| Presets | `name` | `category=null`, `default_title=null` |
| Snippets | `expansion` | `category=null` |

Required labels, names, expansions, and the three required Block templates
are nonblank strings. Preserve wording/template whitespace; trim summary and
new Snippet expansion as the current creation path does. Optional text accepts
string or null; whitespace-only becomes null. No other fields accept blank
values except text/decimal defaults described below. Server-forced columns
from §1 must be omitted. Stage 5 retains Stage 3's requirement for a nonblank
macro template on writable non-table Blocks; changing that policy is separate.

Update allowlist, matching `content_editing.EDITABLE`:

- Blocks: `macro_template`, `micro_template`, `conclusion_template`,
  `context_template`, `title_fragment_template`, `conclusion_label_template`.
- Fields: `label`, `default_value`, `conclusion_addendum_template`.
- Snippets: `expansion`, `category`.
- Presets: `name`, `category`, `default_title`.

Omitted update fields remain unchanged. Explicit null clears only a nullable
value. Field type is exactly `text`, `number`, `decimal`, `select`, or
`checkbox`. Select creation requires `options` as a nonempty unique array of
nonblank strings; other types require omitted/null options.

| Field value | Accepted package JSON | Persisted default/Block override |
|---|---|---|
| text | string (including empty) or null | same string or SQL NULL |
| number | nonnegative integer, at most `2^53-1` | base-10 integer text |
| decimal | finite nonnegative JSON number or null | canonical numeric text or SQL NULL |
| select | exact option string | same string |
| checkbox | boolean | `"true"` or `"false"` |

For **global** number/select/checkbox defaults, null may be supplied on creation
or retained on an already-null existing row only if every new effective use
has a usable override; do not clear a previously usable default. Existing
legacy empty defaults remain legal when untouched. New standalone Fields of
these types need usable global defaults. Text/decimal nulls are intentional.
Effective number/select/checkbox values must be valid in every new Block,
including standalone/ad hoc use, and new Preset. A template that renders `None`
is not proof that `int(None)` can mount a Workspace widget. Compare persisted
values after field-specific normalization; do not rewrite untouched columns.

Relationships:

- `Block_Fields.key` is exactly `{block_key,field_key}`. `values` requires
  `sort_order` (integer `0..999`), with optional `label_override=null`,
  `default_override=null`, `context_section=false` (JSON boolean). Owner Block
  is new; Field exists in the final graph. Positions are unique within owner.
  A null/omitted override **inherits**, so cannot clear a nonnull global value.
- `Preset_Blocks.key` is exactly `{preset_code,block_key,sort_order}`, with
  unique integer positions `0..999` within owner Preset. `values` has only
  optional `field_overrides`, default `{}`. Owner Preset is new; Block exists
  and is non-table. The same Block may occur at multiple distinct positions.
- `field_overrides` is a native object of linked Field keys to type-checked
  values. Missing member inherits; explicit null is a value only for
  text/decimal (number/select/checkbox cannot be cleared). Store canonical JSON
  with native typed values; Block overrides use the text storage rules above.
- All overrides are validated against final Field type/options and resolved in
  the existing order: Field → Block override → Preset override → saved Case
  value. Reject unknown keys and invalid values before calling the renderer;
  permissive `coerce_field_value` is not a validator.

Each new Preset has at least one Block. Fieldless static Blocks and standalone
reusable entities are allowed if valid. For new Block bindings, Fields used
by context/title templates must be in `context_section`, matching Workspace's
separate context input pass. New Preset codes can change Quick Type's existing
longest-prefix selection without editing token rows: display prefix overlaps
as an explicit warning, preserving existing matching behavior. Do not build a
Quick Type authoring or exhaustive grammar-analysis feature in Stage 5.

## 4. Shared candidate service and validation

`change_packages.py` owns export/parser/v1 policy; `content_changes.py` owns
source-independent operations, candidate review, Apply and inverse preparation.
Extract existing validation/report helpers, preserving Stage 3 public APIs.
Stage 6 forms construct internal operations without an AI envelope. Avoid a
policy registry, generic migration engine or second writer.

Materialize against an explicit connection with foreign keys enabled, using
parameterized values and fixed table/column allowlists. Derive dependencies;
input order has no semantic effect. Deterministic phases:

1. Create Fields/Snippets, then Blocks/Presets, sorted by table/key.
2. Apply existing-row updates in stable table/key order.
3. Insert Block–Field links, then Preset–Block links in owner/position order.
4. Validate the **complete candidate once**, then calculate complete reports.

Do not call `save_edit`, `create_snippet`, snapshot restore, migration, or any
helper that commits/opens a default connection inside this service. Snapshot
restore has different saved-case restrictions and replacement semantics.

Required validation combines existing Stage 3/4 helpers:

- Exact row/type/key/relationship and new graph/widget invariants from §3,
  all references resolved, foreign-key check, and no writes outside policy.
- Sandboxed Jinja/static variable validation for every template and Field
  addendum; literal `snippet('key')` calls only, no globals/arbitrary calls.
  Keep existing context aliases and addendum context `{value, snippet}`.
- Strict standalone Block and addendum renders, including dependencies of
  changed Fields/Snippets even where no Preset reaches them; complete defaults
  for all existing and new Presets; each legal checkbox/select value varied
  individually against resolved defaults; every saved pending composition.
- Return grouping conflicts and existing consistency-rule warnings for human
  review. They are warnings, as in Workspace, not new hard clinical rules.
  Consistency evaluation must use candidate-connection rules, not its current
  hidden default-DB lookup.

A candidate may repair invalid base content: retain local before-errors and
block on candidate failures. Defaults, individual discrete branches and saved
cases do not prove every numeric range or combination. No exhaustive Cartesian
enumeration. Exceptions, malformed saved inputs or incomplete results never
produce an applicable review. The Jinja sandbox/file limits are not CPU/memory
isolation; hostile-template quotas remain deferred (§9).

## 5. Complete candidate and pending-case review

Use `editor_preview.render_preset_defaults(..., conn=..., strict=True)` for
Preset reports. Compare by stable Preset code and report components, excluding
SQLite IDs. Show added/output-changed Presets in full, plus structurally or
input-affected Presets even if default text is unchanged. Determine the latter
from changed graph dependencies (Fields/Snippets/addenda included). Count
unaffected Presets; their successfully rendered previews may stay collapsed.
Standalone content receives its own wording/usable-default preview and a
“No Preset uses this yet” label. Complete reports include title, clinical info,
microscopy with macro layout, conclusion, conflicts/warnings, and final HTML.

Extract a saved-case report helper in `editor_preview.py` or a small
`case_preview.py`, sharing Workspace assembly rules. All content/snippet/label/
rule lookups use the supplied connection. Test Workspace reopen parity; reuse
pure assembly pieces there instead of copying a second renderer.

For saved pending Cases the helper must:

- Use ordered `block_instances` when present, including empty versus missing;
  only missing composition uses the Preset fallback. Preserve duplicate and
  ad hoc instance identities and `blocks["<block_key>#<instance_no>"]` values.
  Resolve instance-specific Preset overrides with both Block and position.
- Handle persisted decimal widget strings (including comma decimals and blank)
  using the same normalization as Workspace, not just database coercion.
- Add `wildcard_notes` by saved target position before microscopy formatting.
- Preserve `final_micro_edit` and `final_conc_edit` when `master_lock` is true;
  otherwise use automatic output. Preserve title and Case `clinical_info`
  under `context_title_lock`. Without that lock, recompute title, and clinical
  context only for a single specimen with context composition; otherwise keep
  saved free-text clinical information. Preserve empty manually edited text.
- Always validate automatic underlying content too: a manual lock cannot hide
  a template failure. Show lock labels; a fingerprint change may have no visible
  report change. A separate automatic-projection UI is unnecessary in Stage 5.

Affected pending Cases are those whose relevant fingerprints differ between
base and candidate. If a base fingerprint cannot be computed but the candidate
can, include that Case as affected with a local base-error label. Candidate
fingerprint/render failure blocks Apply. Validate every pending Case even when
its fingerprint appears unaffected; validated Cases are not re-rendered.

For each affected Case show its ID **locally**, “Current before this package”
and “Candidate after this package,” complete component comparison/HTML, and
whether it was already stale relative to its saved fingerprint. Make the stored
last-saved HTML available separately, labelled as saved (it may include manual
edits or older content). Acknowledge clinical change later through the existing
Workspace saved/current per-fingerprint workflow; Apply never acknowledges or
updates Cases. Unsaved browser drafts cannot be inventoried here and still
rely on Workspace's continuous fingerprint checks.

Summary, operation lists, template diffs and errors are escaped text. Display
complete generated reports through a restricted HTML presentation boundary
that permits the existing report formatting but removes active content, event
attributes and external resource URLs. Use that boundary for Workspace report
display too: imported content must not become an external request after Apply.
Do not change stored validated artifacts or canonical render output as part of
this display safeguard. Verify permitted formatting and synthetic HTML payloads.

## 6. No-write dry run, review binding, and atomic Apply

Dry run may run before the initial recovery-snapshot gate; Apply/revert require
that existing gate. A dry run creates no operational rows, journaled write
transaction, audit record, or persistent patient-bearing temporary file.

1. Parse the package; take one SQLite backup into a private **in-memory** DB,
   then close the source connection. All following reads use this consistent
   copy. Enable foreign keys/row factory; do not run migrations on it.
2. Compute and compare the copied content hash with the package base hash.
   Capture base reports/fingerprints and the local review guard below.
3. Materialize on that same copy after base results are captured; validate,
   derive logical changes and complete candidate reports, hash the candidate
   snapshot. Close the copy in `finally`, success or failure. No second full
   backup, operational snapshot restore, or disk-based copy is needed.
4. Return a server-side immutable review result only on success. It contains
   normalized operations, package/base/candidate hashes, local guard, exact
   before/after changes and reports/warnings. A failed run returns errors and
   cannot supply an applicable review.

The **local guard** is a canonical digest of the current revision ID, complete
base-table stable-key→SQLite-ID map, and pending rows ordered by ID:
`id`, `case_number`, `preset_id`, `clinical_info`, raw `structured_input`,
`rendered_html`, `content_fingerprint`, `status`. Query the entire pending set
so arrivals, departures, edits and status transitions invalidate review.
Revision ID detects intervening audited changes even if content returns to the
same hash; ID mapping detects same-content identity replacement. This guard is
local/session-only and is never part of AI output, provenance or error logs.
No per-operation model hash or duplicate local touched-row hash list is needed.

`apply_review(review)` accepts only the successful immutable server-held
result. The UI binds confirmation to its generation; the service independently
rechecks state. Uploaded previews, mutable widget operations or a supplied hash
are not proof of review. No additional signed-token protocol is needed.

One `BEGIN IMMEDIATE` transaction on the live DB:

1. Require the initial-snapshot marker. Check package normalization/hash,
   reviewed base hash and local guard against live locked state. Any mismatch
   refuses without rebasing, patching the hash, or partially applying.
2. Materialize the same operations, run complete validation on that connection,
   and compare candidate content hash and logical changes with the review.
   Preserve every pre-existing ID. Verify protected Case/history rows were not
   written; only allowlisted content and this action's audit may change.
3. Insert one `Content_Revisions` row, origin `package_import`, and one
   `Content_Changes` per changed base/relationship row. Commit once.

Any operation, FK, validation, render, hash, audit or commit failure rolls back
all work; lock contention returns a concise retry message. No `executescript`
or nested independent writer inside Apply. A repeated Apply is stale. After
an ambiguous UI disconnect, show the persisted revision on reload; never retry
an old review automatically. Content staleness requires a fresh export/package;
pending-only staleness permits the same package to dry-run again. Any failed or
repeated dry run clears prior confirmation before computing a new result.

## 7. Audit and conflict-safe inverse

Add only nullable `package_hash`, `base_snapshot_hash`, `result_snapshot_hash`
to `Content_Revisions` with the existing idempotent additive migration. Legacy
rows keep null provenance; no schema rebuild, archive fields, package/context
blob, pending guard or Case data is stored. These hashes describe content
provenance; report text remains a local review, not an audit payload.

**Reuse existing full persisted-row `before_json`/`after_json` and `row_hash`**
for base entities, retaining SQLite IDs in local audit images. For the two
relationship tables record their complete physical rows (including endpoint
IDs), using canonical JSON stable composite keys as `entity_key`. Hash exactly
those recorded rows with the existing ID-excluding row-hash rule. Null image
means absence. Do not introduce a second ID-free base-row encoding. Verify
recorded IDs separately on inverse application. Audit parsing/allowlists and
inverse dispatch depend on table and image shape, **not revision origin**, so
legacy revisions and revert-of-revert chains remain understandable.

Prepare package inverses through the same no-write candidate/impact review and
separate confirmed Apply. The inverse is backend-derived from recorded audit,
never an uploadable delete. Existing Stage 3 revert behavior/API remains
supported, and Recent revisions includes package imports and their inverses.

Before inversion under the write lock:

- Each target must equal its recorded after-state and recorded identity, or
  be absent if the image says absent. Reject corrupted/unknown audit shape,
  conflicting IDs, collisions, duplicate targets or later edits.
- Check dependencies of each proposed removal, including all configuration
  relationships, template/Snippet/addendum references and saved pending
  compositions/default-resolution links. Never silently remove a dependency
  merely because fallback content still renders. Later unrelated edits are
  allowed when touched-state checks, dependencies and whole-candidate checks
  pass; do not require the entire DB to equal the old revision's result hash.
- Existing `Cases.preset_id` foreign keys can also prevent deleting a created
  Preset after use by a validated Case. Refuse with a local explanation; do not
  disable foreign keys, rewrite Cases/history, or implement Stage 6 historical
  reference decoupling. Validated report HTML remains frozen.

Derive order from the inverse operations, not `reversed(changes)`: recreate
missing base endpoints with their **original IDs**, restore/update rows, remove
links before endpoints, insert links after endpoints, and validate only the
finished graph. Never cascade away later rows. For a revert-of-revert, original
ID reuse is required and any collision refuses. Relationship rows retain exact
original endpoint identities. Revalidate the whole candidate and pending
contexts before writing one new `revision_revert` with inverse audit rows and
base/result hashes in the same transaction. `package_hash` is null for an
inverse; summary names the reverted revision. Any failure is atomic.

Without intervening content, safe revert restores the exact pre-package
snapshot hash; with unrelated changes it restores just the audited targets.
Refusal is an acceptable safe outcome after later dependencies appear; Stage 5
does not promise that every applied package is forever removable.

## 8. Editor flow and acceptance tests

Add one persistent **AI package** section, English chrome/French clinical text:

1. Download context, privacy/economy notice, hash and byte size. Clearly label
   the existing recovery snapshot separately; this file is not a restore file.
2. Upload one JSON file; explicit **Run dry run**; structured validation errors
   and separate copyable AI feedback. Show progress, then “Nothing saved.”
3. Readable ordered operation list, actual before/after values/template diffs,
   warnings, affected Preset previews and local pending-case review. Show counts
   and a selector/expanders for large results; hashes can be collapsed. Do not
   truncate report content silently or require reading JSON.
4. A confirmation for this exact successful review, then a separate **Apply**
   button gated by the recovery snapshot marker. No upload, checkbox or rerun
   triggers an Apply. No per-report checkbox ceremony.
5. On success show revision ID, clear stale loaded manual edit forms, preserve
   section selection and point to Recent revisions. Inverses have the same
   review/confirm separation and show why a refusal occurred.

Changing/removing uploaded bytes, including same-name replacements, resets
review and confirmation; a new dry run also resets them. Ordinary reruns retain
the current result. Use dedicated generation-suffixed widget keys and a reset
flag processed before widgets mount, plus existing one-shot session messages.
Failed/stale Apply clears confirmation and requires a new review. No browser
review result or patient data goes in a cross-session cache.

Acceptance uses synthetic temporary DBs (`mutable_db` for writes) and the
existing fail-closed isolation. Required tests:

| Area | Evidence |
|---|---|
| Contract/privacy | Coherent create graph and existing-update fixtures; deterministic export/hash, measured bytes, generated contract/example validation; native versus stored JSON; forbidden operations/tables/columns; duplicate keys, UTF-8, overflow/non-finite/bool-as-int, depth/size/count, null/blank and no-op boundaries; forbidden-data canaries/query tripwires and feedback without raw exceptions. |
| Graph/widgets | Shuffled operations, duplicate/missing targets, owner restrictions, key/context collisions, options/defaults/overrides/inheritance, standalone widget defaults, optional decimal, static Block, empty Preset refusal, duplicate Blocks, position `999`/`1000`, context-field placement and Quick Type prefix warnings. |
| Rendering | Complete single/multi-specimen layouts, context/title/labels/grouping, Snippets/orphan addenda, discrete branches and consistency warnings; default-connector tripwire; invalid-base repair and candidate failure; active HTML/external resources blocked in review and Workspace. |
| Pending | Missing versus explicit composition; add/remove/reorder/duplicate/ad hoc instances; decimal widget strings, notes, both locks/empty manual text; saved/live/candidate distinction, already-stale/base-error cases and unchanged HTML with changed fingerprint; unrelated exclusion, all-pending validation, validated immutability, Workspace reopen/acknowledgement parity. |
| Stale/isolation | Quiescent source bytes/rows/revisions unchanged by dry run; memory cleanup; read-consistent export/backup under concurrent writes; content edits, audited ABA, ID replacement, pending arrival/edit/delete/status changes, altered review/upload; pending-only versus content-stale retry. |
| Transactions/inverse | Faults at each operation phase, validation/hash, revision/change insert and commit; real two-connection race/lock; double Apply; exact provenance/change rows and IDs; no Case/history writes; inverse dependency order, original-ID restoration/collision, later relation/template/pending dependencies, validated-Preset FK refusal, unrelated changes allowed, inverse rollback and legacy chains. |
| UI | AppTest for persistent section/result, reset/confirmation, failed dry run clearing old success, remove/replace upload with same filename, recovery gate, readable reports/safe feedback, two-session staleness, success and inverse navigation. Browser checks files, large review, two tabs and Workspace reopen. |

During implementation run focused tests, full `venv/bin/pytest -q`, compile
changed Python files, `git diff --check`, and isolated Streamlit boot/HTTP 200.
Keep the operational DB checksum unchanged. Do not regenerate goldens or
accepted operational artifacts to pass tests. Thomas performs final browser
review and decides commit timing.

## 9. Delivery boundary and implementation sequence

Required changes: the two new modules above; shared helpers in
`content_editing.py`, `content_snapshot.py`, `editor_preview.py`; provenance in
`database.py`; flow in `pages/editor.py`. Narrow assembly/display/connection
refactors may touch Workspace, `rendering.py` and `consistency.py`, preserving
report semantics. Stage 4 only adopts shared helpers without changing its CLI
or artifacts. Add `tests/test_stage5_packages.py` and focused regressions to
existing Stage 2/3, Editor, Workspace and operational-review tests.

Once implementation is requested, use three tested checkpoints:

1. **No-write contract/review:** shared snapshot hash/read boundary, export/parser,
   complete validation/rendering, memory candidate and impact. Preserve Stage
   3/4 tests.
2. **Atomic Apply/inverse:** additive provenance migration, state comparisons,
   writes/audit and dependency-safe inverse; stale/fault tests before UI Apply.
3. **Editor/verification:** review/confirm flow, restricted display, reset behavior,
   AppTest/full regression and browser review. Update progress/testing docs with
   actual results only; do not begin Stage 6.

**Deferred Stage 6:** guided create/duplicate/edit/archive/delete; existing
relationships/order/overrides; `site_label`, `conclusion_group`, group labels;
archive/deletion lifecycle and historical-reference handling; advanced Field
type/options migration and table rows. Forms reuse internal operations,
validation, review, Apply and inverse without AI/JSON. No dormant operations,
archive schema or Content Studio UI in Stage 5.

**Deferred Stage 7:** Quick Type/rule authoring and example parsing, variant-
Preset migration, and atomic `(case ID, Quick Type)` intake creating pending
Cases only. No batch importer or automatic Case validation in Stage 5.

**Other deferred work:** context subsets/token estimation, in-app model/API
integration, exhaustive template-state analysis and hostile-template execution
quotas. Technical validation supports human review; it does not establish
clinical correctness or guarantee free-plan capacity.
