# PathoPilot v1 Production Readiness Plan

## Status

The Editor/authoring program is complete.

Stages 1–7 are implemented, reviewed, tested, and committed. No Stage 8 is planned.

The remaining work before first production use is a deliberately bounded **v1 production-readiness pass** focused on:

1. permanent Case deletion;
2. canonical Case identity across accession years;
3. automatic operational database backup and simple pending-Case rescue;
4. final production preflight;
5. a short release-candidate soak period.

This plan is the authoritative roadmap for the transition from completed Stage 7 to `v1.0.0`.

Do not expand it into another general architecture or Editor program without an observed production need.

---

# Guiding principles

* Prefer the smallest implementation that makes PathoPilot safe and usable in production.
* Reuse existing lifecycle, persistence, Quick Type, Bulk Intake, and transaction mechanisms.
* Avoid speculative v2 features before real production experience.
* One-off maintenance operations should remain one-off operations rather than becoming permanent runtime infrastructure.
* Focused automated tests + targeted manual acceptance are preferred over repeatedly re-running the complete historical browser matrix.
* The full isolated test suite is run manually at the final pre-RC boundary.
* Expensive independent review is used only where risk justifies it.

---

# PR0 — Pre-v1 baseline

**Status: complete.**

Before production-readiness implementation:

* Stage 7 work was committed.
* The repository working tree was confirmed clean.
* The exact baseline commit was recorded.
* The operational SQLite database was identified.
* A SQLite-safe manual backup was created.
* The backup passed SQLite integrity verification.

This baseline is the rollback point before PR1–PR3.

---

# PR1 — Permanent pending-Case deletion

## Product contract

Any **pending Case may be permanently deleted**.

This includes a Case that:

* was previously validated;
* was then explicitly returned to pending through the existing lifecycle;
* now needs to be removed because it was a mistake, test Case, or unwanted record.

A **currently validated Case cannot be deleted directly**.

The required path is:

`Validated → Return to Pending → Delete`

There is no requirement that a pending Case must never previously have been validated.

No new archive, tombstone, or soft-delete system is wanted.

## Backend behavior

Implement one narrow transactional Case-deletion operation.

It should:

* resolve the target inside the write transaction;
* refuse missing Cases;
* refuse currently validated Cases;
* explicitly remove Case-owned dependent/history rows in safe foreign-key order;
* delete the Case itself;
* roll back completely on failure.

Current repository inspection identified these relevant dependent areas:

* `Case_Validation_History`
* `Case_Status_History`
* `Case_Content_Reference_Changes`
* `Case_Batch_Imports`

A `Case_Batch_Imports` row should only be removed if repository semantics confirm it has become an unreferenced provenance/aggregate record. Shared batch records must remain.

No schema migration or cascade-table rebuild is currently expected.

## UI behavior

Deletion should be available from Worklist for pending Cases only.

This location is preferred because deletion must remain possible even if the pending Case can no longer be reconstructed/rendered in Workspace.

The UI should:

* clearly identify the Case being deleted;
* require deliberate confirmation;
* expose no deletion action for currently validated Cases.

Do not redesign Worklist beyond what is required.

## Acceptance

Automated coverage should include:

* ordinary pending deletion;
* direct validated deletion refusal;
* validated → Return to Pending → deletion;
* dependent/history cleanup;
* transactional rollback;
* batch-provenance cleanup behavior;
* pending-only Worklist deletion UI;
* Case disappearance after successful deletion.

Manual acceptance:

1. create/delete a disposable pending Case;
2. create/validate another disposable Case;
3. Return it to Pending with a reason;
4. delete it;
5. verify a validated Case has no direct deletion path.

## Existing development Cases

After PR1 passes manual acceptance, current development/test Cases should be cleaned using the normal application workflow where possible.

Repository inspection found six validated development Cases expected to be reconstructable:

* `qsdf`
* `qqq`
* `qsdfdqd`
* `sss`
* `classictest`
* `testwarning`

`ZRET` currently cannot Return to Pending because its saved Preset is unavailable.

`ZRET` should **not** cause runtime deletion semantics to be weakened. After a verified backup, it may be removed using a separately reviewed one-off maintenance transaction.

---

# PR2 — Canonical Case accession identity

PR2 begins only after PR1 and development/test Case cleanup.

## Problem

Current everyday Case entry uses abbreviated numbers such as:

`12345`

The corresponding Diamic accession is normally:

`26PR12345`

Without an accession year in the identity, longitudinal use would eventually collide:

* `26PR12345`
* `27PR12345`

must be distinct Cases.

## Product contract

Fast short entry remains supported.

Examples in 2026:

`12345` → `26PR12345`

`26PR12345` → `26PR12345`

`26pr12345` → `26PR12345`

In January 2027, a late 2026 accession can still be entered explicitly:

`26PR12345` → `26PR12345`

The Case creation timestamp must **not** determine accession identity.

The canonical accession string is the uniqueness boundary.

Current normal lab/site code:

`PR`

## Storage

Repository inspection shows:

`Cases.case_number` is already `TEXT NOT NULL UNIQUE`.

Therefore the preferred v1 implementation is to store the canonical accession directly in the existing field.

Do **not** add separate year/site columns unless implementation reveals a concrete requirement.

No SQL uniqueness migration is currently expected.

## Normalization

Implement one shared Case-ID resolver/normalizer.

Expected behavior:

* trim outer whitespace;
* normalize accession letters to uppercase;
* digits-only input resolves to `<current two-digit year>PR<number>`;
* explicit full accession accepts the supported `YYPR<number>` form;
* preserve leading zeroes in the numeric portion;
* reject unsupported new Case-ID forms rather than silently inventing identities.

The default year should come from the current operational/laboratory calendar at input time, not from `created_at`.

Production preflight must verify that the server/LXC clock and timezone correspond to the intended laboratory calendar.

## Shared namespace boundary

Normalization must occur before lookup, duplicate detection, persistence, or review binding.

The same rule must apply to:

* Workspace Case creation;
* Quick Type Case creation;
* Bulk Intake;
* duplicate/existing-ID checks;
* Worklist lookup/reopen;
* Return to Pending;
* Case deletion;
* other operational Case-number lookups.

Quick Type parsing itself remains case-sensitive. This normalization applies only to Case IDs.

## Bulk Intake

Bulk Intake remains:

`Case ID | Quick Type`

No year column is added.

Exceptional previous-year Cases can simply provide their explicit canonical accession.

Equivalent short/full representations in one batch must collide after normalization.

Example in 2026:

`12345`

and

`26PR12345`

refer to the same Case and must be rejected as duplicates.

## Legacy migration

All current operational Cases are development/test Cases.

The intended sequence is:

1. implement PR1;
2. clean development/test Cases;
3. then implement PR2.

Therefore no production legacy-ID migration should be necessary.

Do not infer accession years from Case timestamps.

Tests using symbolic Case IDs may need fixture updates; that is test maintenance, not an operational-data migration.

## Manual acceptance

Verify:

* `12345` becomes the current-year canonical accession;
* lowercase/space-padded explicit accession normalizes correctly;
* explicit previous-year accession remains unchanged;
* Worklist shows/reopens the canonical Case;
* Bulk Intake accepts short and explicit IDs;
* equivalent short/full IDs are detected as duplicates.

---

# Review boundary after PR1 + PR2

PR1 and PR2 affect:

* destructive Case lifecycle behavior;
* Case identity;
* duplicate detection;
* multiple operational creation/reopen paths.

After both checkpoints are implemented and individually accepted, perform **one targeted independent Sol High review spanning PR1 + PR2**.

The review should focus specifically on:

* deletion completeness/atomicity;
* validated Case protection;
* Return-to-Pending → Delete semantics;
* identity normalization consistency;
* duplicate-bypass possibilities;
* Workspace / Quick Type / Bulk Intake consistency;
* legacy/noncanonical Case-ID escape paths.

Do not request another broad adversarial review of the entire application.

---

# PR3 — Automatic operational database backup and pending rescue

PR3 should remain primarily external operational tooling, not a new PathoPilot subsystem.

## Goal

The primary risk being mitigated is loss of already-entered information on pending Cases, which could otherwise require reopening and rereading slides.

Two protections are required.

## A. Periodic SQLite backup

Implement a small external backup script, preferably using Python stdlib SQLite `Connection.backup()`.

Do not blindly copy a live SQLite file.

Expected behavior:

* run approximately every 10 minutes;
* create a consistent SQLite backup;
* write/publish safely;
* perform lightweight SQLite integrity verification;
* retain a bounded rolling history;
* store backups separately from the live application/database location;
* provide a simple documented manual restore procedure.

A `systemd` service + timer is preferred over scheduling inside Streamlit.

Retention should remain modest.

A reasonable initial shape is:

* frequent backups covering roughly the last 24–48 hours;
* a small number of daily backups.

Do not retain thousands of snapshots merely because storage permits it.

Final retention should be chosen during PR3 implementation based on the real deployment location.

## B. Human-readable pending-Case rescue

Generate a file such as:

`latest_pending_cases.html`

at approximately the same interval.

Its sole purpose is:

> If database restoration fails, show what was already saved for every pending Case so that microscopy findings do not need to be reconstructed from the slides.

Use stored/saved Case state from the verified backup snapshot.

Do not re-render Cases against current content.

Useful information may include:

* canonical Case ID;
* saved/update time;
* pending status/reason where relevant;
* frozen Preset identity;
* clinical information;
* saved rendered HTML;
* saved structured input where useful.

Publish the rescue file atomically so an interrupted update does not destroy the previous valid copy.

This is **not**:

* an audit journal;
* event sourcing;
* a second database;
* an automated Case-reconstruction mechanism;
* a new recovery UI.

## Deployment

Repository inspection found no existing deployment/backup infrastructure.

Likely tracked files:

* backup script;
* systemd service template;
* systemd timer template;
* short restore/deployment documentation.

The final backup destination must be selected from the actual deployment environment.

A directory merely elsewhere in the same LXC protects against logical database deletion/corruption but does not protect against loss of the LXC/storage itself.

Prefer a storage location outside the live application/LXC where practical.

## Verification

Automated tests should cover:

* independently readable SQLite backup;
* backup under WAL/concurrent usage;
* bounded retention;
* failure not replacing the previous good backup/rescue artifact;
* rescue containing pending saved Cases only;
* rescue using stored state rather than live rendering;
* disposable restore round trip.

Manual disaster drill:

1. create/save a recognizable pending Case;
2. trigger a backup;
3. inspect `latest_pending_cases.html`;
4. restore the SQLite backup to a disposable location;
5. point PathoPilot at the disposable DB;
6. boot it;
7. open representative pending and validated Cases.

A successful restore drill matters more than an expensive independent model review.

---

# PR4 — Production preflight

After PR1–PR3:

* development/test Cases are removed;
* `ZRET` is handled by one-off maintenance cleanup if still necessary;
* operational DB path is explicitly known;
* automatic backup timer is active;
* latest backup is recent;
* restore drill has passed;
* pending rescue HTML has been inspected;
* server clock/timezone is correct;
* application access boundary is verified;
* repository is clean;
* release documentation reflects completed status.

No complete historical browser review is required.

Only manually exercise functionality added during PR1–PR3.

Run the complete isolated automated suite once manually in tmux at this boundary.

---

# v1.0.0-rc1

After PR4:

* freeze architecture;
* tag or otherwise mark `v1.0.0-rc1`;
* begin real use for a small supported clinical scope;
* continue using the previous workflow for unsupported case types.

Initial clinical use can expand progressively:

* gallbladder;
* appendix;
* thyroid cytology;
* then additional case types as their Presets/Blocks/Fields are created and accepted.

The purpose of RC use is also to test whether Content Studio and optional AI-assisted content modification are sufficiently practical in real work.

Suggested soak period:

**3–5 normal working days.**

During RC, classify findings as:

* **blocker:** wrong output, data loss/state loss, inability to reopen/save/validate, or workflow failure;
* **pre-v1 friction:** repeatedly disruptive enough to make routine supported use impractical;
* **post-v1:** everything else.

Do not add speculative conveniences during RC.

---

# v1.0.0

If RC reveals no unresolved blocker:

* fix any accepted RC blockers;
* run focused regression for those fixes;
* run the complete suite again only if code changed after the pre-RC full run;
* confirm automatic backup health;
* take a final manual operational DB backup;
* confirm readable pending rescue;
* tag `v1.0.0`.

`main` then represents stable production v1.

Only after that should a separate v2 development branch/worktree be created.

Production v1 and v2 development must not casually share the same operational database.

---

# Explicitly deferred beyond v1

Unless actual RC use proves otherwise, defer:

* IHC / wildcard-IHC reporting-model redesign;
* broader technique/category schema redesign;
* table-Block authoring UI;
* Field type/options migration UI;
* elaborate backup dashboards;
* automatic rescue reconstruction;
* multi-user collaboration;
* sophisticated roles/auth for a private personal deployment;
* direct in-app AI;
* AI package v2;
* broad Diamic/LIS integration;
* voice reporting;
* speculative workflow conveniences.

These should be prioritized from observed production friction rather than pre-production assumptions.

---

# Implementation / model strategy

Preferred model use:

* **PR1:** Terra High
* **PR2:** Terra High
* **PR1 + PR2 review:** one targeted Sol High review
* **PR3:** Terra Medium unless implementation reveals unexpectedly complex persistence/deployment issues
* **documentation/mechanical status updates:** Luna or Terra Medium
* **Astra:** exceptional only

For implementation agents:

* inspect before changing;
* implement one checkpoint at a time;
* run focused tests rather than the full multi-minute suite;
* never touch the operational database during automated testing;
* stop for Thomas's targeted manual acceptance before committing when requested.

---

# Commit boundaries

Expected sequence:

1. `Add pending case deletion`
2. clean development/test operational Cases manually
3. one-off `ZRET` maintenance cleanup if needed
4. `Canonicalize Case accessions`
5. targeted PR1 + PR2 independent review/remediation if needed
6. `Add operational database backups`
7. production preflight
8. `v1.0.0-rc1`
9. RC soak
10. `v1.0.0`
