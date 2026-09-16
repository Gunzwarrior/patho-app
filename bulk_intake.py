"""Decoded bulk-intake review and CP5 atomic pending-Case Apply."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import hmac
import io
import json
import math
import os
import sqlite3
from typing import Any, Iterable

import composition
import content_snapshot
import database
import editor_preview
import rendering
import quicktype


MAX_SOURCE_BYTES = 1_048_576
MAX_DATA_ROWS = 250
DELIMITERS = {",", "\t"}
MAX_SAFE_WIDGET_INTEGER = 2**53 - 1
# An issued review is only usable in this running application session.  The
# secret never leaves this module or the server process; it signs no raw input.
_REVIEW_ISSUER_SECRET = os.urandom(32)


def _canonical_json(value: Any) -> str:
    """Stable bytes for review bindings; never uses a repr of input data."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class BulkInputError(ValueError):
    """A safe display error: callers must not echo patient/source values."""


@dataclass(frozen=True, slots=True, repr=False)
class SourceRow:
    """A normalized data row, retained only while the session prepares it."""

    row_number: int
    case_number: str
    quick_type: str

    def __repr__(self) -> str:  # pragma: no cover - the boundary is intentional
        return "SourceRow(<session-local>)"


@dataclass(frozen=True, slots=True, repr=False)
class ParsedSource:
    delimiter: str
    first_row_is_header: bool
    rows: tuple[SourceRow, ...]
    normalized_source_sha256: str

    def __repr__(self) -> str:  # pragma: no cover - avoid accidental source logging
        return f"ParsedSource(rows={len(self.rows)}, digest={self.normalized_source_sha256!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedCase:
    """A non-persisted, immutable normal pending-Case representation.

    The issued review owns only canonical JSON text, never a mutable object
    graph. ``structured_input`` materializes a detached value for legacy
    render/display consumers; mutating that value cannot affect this review or
    its interpretation digest.
    """

    row_number: int
    case_number: str
    preset_id: int
    preset_code: str
    preset_name: str
    clinical_info: str
    structured_input_json: str
    rendered_html: str
    warnings: tuple[str, ...]
    conflicts: tuple[str, ...]

    def __repr__(self) -> str:  # pragma: no cover - includes patient-bearing data
        return f"PreparedCase(row_number={self.row_number}, <session-local>)"

    @property
    def structured_input(self) -> dict[str, Any]:
        """Return a fresh materialization; never expose issued review state."""
        return json.loads(self.structured_input_json)


@dataclass(frozen=True, slots=True, repr=False)
class BatchReview:
    """Frozen session-local review binding for CP4 preview and CP5 Apply."""

    applicable: bool
    errors: tuple[str, ...]
    rows: tuple[PreparedCase, ...] = ()
    normalized_source_sha256: str | None = None
    content_snapshot_sha256: str | None = None
    content_revision_id: int | None = None
    target_case_numbers: tuple[str, ...] = ()
    interpretation_sha256: str | None = None
    issuer_signature: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - Case IDs stay out of logs/reprs
        return (
            "BatchReview("
            f"applicable={self.applicable}, rows={len(self.rows)}, errors={len(self.errors)}, "
            f"normalized_source_sha256={self.normalized_source_sha256!r}, "
            f"content_snapshot_sha256={self.content_snapshot_sha256!r}, "
            f"content_revision_id={self.content_revision_id!r}, "
            f"interpretation_sha256={self.interpretation_sha256!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class BatchApplyResult:
    """Safe Apply outcome; it deliberately contains neither IDs nor source text."""

    applied: bool
    error: str | None = None
    batch_import_id: int | None = None
    row_count: int = 0

    def __bool__(self) -> bool:
        return self.applied

    def __repr__(self) -> str:  # pragma: no cover - defensive privacy boundary
        return (
            f"BatchApplyResult(applied={self.applied}, error={self.error!r}, "
            f"row_count={self.row_count})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class BatchWarningAcknowledgement:
    """A session-local, review-bound acknowledgement of all batch warnings."""

    review_issuer_signature: str
    interpretation_sha256: str
    signature: str

    def __repr__(self) -> str:  # pragma: no cover - no patient data in reprs
        return "BatchWarningAcknowledgement(<session-local>)"


def parse_bulk_source(source: bytes | str, delimiter: str, first_row_is_header: bool) -> ParsedSource:
    """Parse the exact CP4 source contract before opening a database.

    Blank logical rows are ignored.  Every other CSV record must have exactly
    two cells.  The selected header is structurally checked but deliberately
    never interpreted or guessed from its words.
    """
    if delimiter not in DELIMITERS:
        raise BulkInputError("Choose an explicit comma or tab delimiter.")
    if type(first_row_is_header) is not bool:
        raise BulkInputError("Specify whether the first row is a header.")
    if isinstance(source, str):
        try:
            source_bytes = source.encode("utf-8")
        except UnicodeEncodeError as error:
            raise BulkInputError("Source must be valid UTF-8.") from error
    elif isinstance(source, bytes):
        source_bytes = source
    else:
        raise BulkInputError("Source must be UTF-8 CSV or TSV text.")
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise BulkInputError("Source exceeds the 1 MiB limit.")
    if b"\x00" in source_bytes:
        raise BulkInputError("Source contains a NUL character.")
    try:
        text = source_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise BulkInputError("Source must be valid UTF-8.") from error

    try:
        # The total-byte limit is authoritative. This ceiling is deliberately
        # no smaller, so every cell possible in a valid source is accepted.
        csv.field_size_limit(MAX_SOURCE_BYTES)
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        logical_rows = [(reader.line_num, [cell.strip() for cell in row]) for row in reader]
    except csv.Error as error:
        raise BulkInputError("Malformed CSV/TSV quoting.") from error

    # A blank logical row has no clinical cells after the required outer trim.
    logical_rows = [(line, cells) for line, cells in logical_rows if any(cells)]
    if first_row_is_header:
        if not logical_rows:
            raise BulkInputError("A selected header requires a header row.")
        _, header = logical_rows.pop(0)
        if len(header) != 2:
            raise BulkInputError("The selected header must contain exactly two cells.")
    if not logical_rows:
        raise BulkInputError("Provide at least one data row.")
    if len(logical_rows) > MAX_DATA_ROWS:
        raise BulkInputError("Source exceeds the 250 data-row limit.")

    rows: list[SourceRow] = []
    for line, cells in logical_rows:
        if len(cells) != 2:
            raise BulkInputError(f"Row {line} must contain exactly two cells.")
        if not cells[0] or not cells[1]:
            raise BulkInputError(f"Row {line} has a blank Case ID or Quick Type.")
        rows.append(SourceRow(line, cells[0], cells[1]))
    normalized_pairs = [[row.case_number, row.quick_type] for row in rows]
    return ParsedSource(
        delimiter=delimiter,
        first_row_is_header=first_row_is_header,
        rows=tuple(rows),
        normalized_source_sha256=_sha256(normalized_pairs),
    )


# A short alias makes the parser straightforward to use from non-UI callers.
parse_source = parse_bulk_source


def _quick_type_value(field: dict[str, Any], value: Any) -> Any:
    """Validate the semantic Quick Type value exactly at Workspace's edge."""
    kind = field["type"]
    if kind == "decimal":
        normalized = rendering.normalize_decimal_widget(value)
        if (normalized is None and value not in (None, "")) or (
            normalized is not None and not math.isfinite(normalized)
        ):
            raise ValueError(f"Quick Type value for '{field['key']}' is not a non-negative decimal.")
        return normalized
    if kind == "number":
        if isinstance(value, bool):
            raise ValueError(f"Quick Type value for '{field['key']}' is not a non-negative whole number.")
        try:
            normalized = int(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"Quick Type value for '{field['key']}' is not a non-negative whole number.") from error
        if normalized < 0 or normalized > MAX_SAFE_WIDGET_INTEGER:
            raise ValueError(f"Quick Type value for '{field['key']}' is outside the safe whole-number range.")
        return normalized
    if kind == "checkbox":
        if type(value) is not bool:
            raise ValueError(f"Quick Type value for '{field['key']}' is not boolean.")
        return value
    if kind == "select":
        if value not in (field.get("options") or []):
            raise ValueError(f"Quick Type value for '{field['key']}' is no longer an available selection.")
        return value
    if kind == "text" and not isinstance(value, str):
        raise ValueError(f"Quick Type value for '{field['key']}' is not text.")
    return value


def _workspace_persisted_values(block: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Convert semantic render values to Workspace's saved widget wire values.

    Decimal fields are deliberately ``st.text_input`` controls.  Workspace
    therefore persists their text wire value (for example ``"7"``), while
    report rendering consumes the normalized semantic float.  A bulk preview
    must retain that distinction: writing a float into saved input passes the
    report reconstruction path but crashes Streamlit when reopen seeds the
    text widget with that float.
    """
    saved: dict[str, Any] = {}
    for field in block["fields"]:
        value = values[field["key"]]
        if field["type"] == "decimal":
            saved[field["key"]] = (
                "" if value is None else rendering.format_decimal_display(value)
            )
        else:
            saved[field["key"]] = value
    return saved


def _active_endpoint_error(conn, preset: dict[str, Any], blocks: Iterable[dict[str, Any]]) -> str | None:
    if preset.get("is_archived"):
        return "Quick Type resolves to an archived Preset."
    for block in blocks:
        if block.get("is_table"):
            return "Table Presets are not supported by bulk intake."
        if block.get("is_archived"):
            return "Quick Type resolves to an archived Block."
        archived_field = conn.execute(
            """SELECT 1 FROM Block_Fields bf JOIN Fields f ON f.id=bf.field_id
               WHERE bf.block_id=? AND f.is_archived=1 LIMIT 1""",
            (block["block_id"],),
        ).fetchone()
        if archived_field:
            return "Quick Type resolves to an archived Field."
    return None


def _prepare_row(conn, source_row: SourceRow) -> PreparedCase:
    """Materialize one source row entirely against the supplied connection."""
    preset, parsed_overrides, parse_error = quicktype.parse_quick_type_on_connection(
        source_row.quick_type, conn
    )
    if parse_error:
        # parse_error includes source text in a few legacy parser messages;
        # it must not leave the session-local input pathway.
        raise BulkInputError(f"Row {source_row.row_number} has an invalid Quick Type.")
    assert preset is not None and parsed_overrides is not None
    preset_blocks = database.get_preset_blocks_on_connection(conn, preset["id"])
    endpoint_error = _active_endpoint_error(conn, preset, preset_blocks)
    if endpoint_error:
        raise BulkInputError(f"Row {source_row.row_number}: {endpoint_error}")
    instances = composition.derive_block_instances(preset_blocks)
    blocks = [{**block, "instance_no": instance["instance_no"]}
              for block, instance in zip(preset_blocks, instances)]
    if len(blocks) != len(instances):  # defensive: no partial composition
        raise BulkInputError(f"Row {source_row.row_number} has an unavailable Block instance.")
    by_instance = {block["instance_no"]: block for block in blocks}
    values: list[dict[str, Any]] = []
    persisted_values: list[dict[str, Any]] = []
    for block in blocks:
        parsed_values = parsed_overrides.get(block["instance_no"], {})
        field_map = {field["key"]: field for field in block["fields"]}
        if any(key not in field_map for key in parsed_values):
            raise BulkInputError(f"Row {source_row.row_number} has an unavailable Quick Type endpoint.")
        typed = {
            key: _quick_type_value(field_map[key], value)
            for key, value in parsed_values.items()
        }
        # This is the same resolved default + normalized widget material that
        # Workspace passes into the report engine and saves in structured_input.
        resolved = editor_preview.widget_values(block, typed)
        values.append(resolved)
        persisted_values.append(_workspace_persisted_values(block, resolved))
    if any(instance_no not in by_instance for instance_no in parsed_overrides):
        raise BulkInputError(f"Row {source_row.row_number} has an unavailable Quick Type instance.")

    structured = {
        "block_instances": instances,
        "blocks": {
            f"{block['key']}#{block['instance_no']}": value
            for block, value in zip(blocks, persisted_values)
        },
        "wildcard_notes": [],
        "master_lock": False,
        "context_title_lock": False,
        # Filled from the authoritative automatic render below.  They are
        # Workspace's ordinary historical fallback fields, not manual locks.
        "final_micro_edit": "",
        "final_conc_edit": "",
        "final_title_edit": "",
    }
    report = editor_preview.render_report(
        conn, preset, blocks, values, clinical_info="", structured=structured, strict=True,
    )
    structured = {
        **structured,
        "final_micro_edit": report["micro_plain"],
        "final_conc_edit": report["conclusion_plain"],
        "final_title_edit": report["title"],
    }
    prepared = PreparedCase(
        row_number=source_row.row_number,
        case_number=source_row.case_number,
        preset_id=preset["id"],
        preset_code=preset["short_code"],
        preset_name=preset["name"],
        clinical_info=report["clinical_info"],
        structured_input_json=_canonical_json(structured),
        rendered_html=report["html"],
        # CP3 warning multiplicity is clinical review evidence. Do not turn
        # distinct fired rules with identical wording into one warning.
        warnings=tuple(report["warnings"]),
        conflicts=tuple(report["conflicts"]),
    )
    _assert_saved_round_trip(conn, prepared)
    return prepared


def _assert_saved_round_trip(conn, prepared: PreparedCase) -> None:
    """CP4's critical parity check: the preview is a real pending Case shape."""
    reconstructed = editor_preview.render_saved_case(conn, {
        "status": "pending",
        "preset_id": prepared.preset_id,
        "clinical_info": prepared.clinical_info,
        "structured_input": prepared.structured_input,
    }, strict=True)
    if reconstructed["html"] != prepared.rendered_html:
        raise ValueError("Prepared report does not round-trip through saved-Case rendering.")


def _existing_case_numbers(conn, numbers: tuple[str, ...]) -> set[str]:
    if not numbers:
        return set()
    placeholders = ",".join("?" for _ in numbers)
    return {row["case_number"] for row in conn.execute(
        f"SELECT case_number FROM Cases WHERE case_number IN ({placeholders})", numbers
    )}


def _active_grammar_error(conn) -> str | None:
    """Use the Content Studio's complete connection-scoped graph validator."""
    import content_changes

    try:
        # This is the same complete active configuration check used after
        # content-candidate materialization. It validates every active token's
        # Block instance and Field endpoint even when a row uses a bare code.
        content_changes._validate_general_configuration(conn)
    except content_changes.ChangeError:
        return "Active Quick Type configuration or endpoint graph is invalid."
    return None


def _interpretation_digest(rows: Iterable[PreparedCase]) -> str:
    return _sha256([
        {
            "case_number": row.case_number,
            "preset_id": row.preset_id,
            "clinical_info": row.clinical_info,
            "structured_input": row.structured_input,
            "rendered_html": row.rendered_html,
            "warnings": list(row.warnings),
            "conflicts": list(row.conflicts),
        }
        for row in rows
    ])


def _issuer_payload(review: BatchReview) -> str:
    """Canonical non-clinical binding for an issued review capability."""
    return _canonical_json({
        "applicable": review.applicable,
        "errors": list(review.errors),
        "normalized_source_sha256": review.normalized_source_sha256,
        "content_snapshot_sha256": review.content_snapshot_sha256,
        "content_revision_id": review.content_revision_id,
        "target_case_numbers": list(review.target_case_numbers),
        "interpretation_sha256": review.interpretation_sha256,
    })


def _issue_review(review: BatchReview) -> BatchReview:
    """Attach the server-only issuer proof after all immutable fields exist."""
    signature = hmac.new(
        _REVIEW_ISSUER_SECRET, _issuer_payload(review).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return BatchReview(
        applicable=review.applicable, errors=review.errors, rows=review.rows,
        normalized_source_sha256=review.normalized_source_sha256,
        content_snapshot_sha256=review.content_snapshot_sha256,
        content_revision_id=review.content_revision_id,
        target_case_numbers=review.target_case_numbers,
        interpretation_sha256=review.interpretation_sha256,
        issuer_signature=signature,
    )


def _is_issued_review(review: BatchReview) -> bool:
    if not isinstance(review, BatchReview) or not review.issuer_signature:
        return False
    expected = hmac.new(
        _REVIEW_ISSUER_SECRET, _issuer_payload(review).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(review.issuer_signature, expected)


def acknowledge_batch_warnings(review: BatchReview) -> BatchWarningAcknowledgement | None:
    """Issue the distinct acknowledgement required for a warning-bearing batch."""
    if not _is_issued_review(review) or not any(row.warnings for row in review.rows):
        return None
    payload = _canonical_json({
        "review_issuer_signature": review.issuer_signature,
        "interpretation_sha256": review.interpretation_sha256,
    })
    return BatchWarningAcknowledgement(
        review.issuer_signature, review.interpretation_sha256,
        hmac.new(_REVIEW_ISSUER_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest(),
    )


def _has_valid_warning_acknowledgement(
    review: BatchReview, acknowledgement: BatchWarningAcknowledgement | None,
) -> bool:
    if not isinstance(acknowledgement, BatchWarningAcknowledgement):
        return False
    if (acknowledgement.review_issuer_signature != review.issuer_signature
            or acknowledgement.interpretation_sha256 != review.interpretation_sha256):
        return False
    payload = _canonical_json({
        "review_issuer_signature": acknowledgement.review_issuer_signature,
        "interpretation_sha256": acknowledgement.interpretation_sha256,
    })
    expected = hmac.new(_REVIEW_ISSUER_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(acknowledgement.signature, expected)


def prepare_bulk_review(source: bytes | str, delimiter: str, first_row_is_header: bool,
                        *, conn=None) -> BatchReview:
    """Create an immutable, no-write CP4 review for one complete batch.

    Source validation happens before database evaluation.  Database reads then
    share a single read transaction, including Quick Type routing, values,
    content snapshot/revision, target absence, rendering and reconstruction.
    """
    try:
        parsed = parse_bulk_source(source, delimiter, first_row_is_header)
    except BulkInputError as error:
        return BatchReview(applicable=False, errors=(str(error),))

    duplicate = len({row.case_number for row in parsed.rows}) != len(parsed.rows)
    if duplicate:
        return BatchReview(applicable=False, errors=("Duplicate Case IDs are not allowed in one batch.",),
                           normalized_source_sha256=parsed.normalized_source_sha256)

    owns_connection = conn is None
    conn = conn or database.get_db_connection()
    started = False
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
            started = True
        snapshot = content_snapshot.snapshot_from_connection(conn)
        snapshot_hash = content_snapshot.content_snapshot_hash(snapshot)
        revision_id = database.current_content_revision_id(conn)
        targets = tuple(row.case_number for row in parsed.rows)
        grammar_error = _active_grammar_error(conn)
        if grammar_error:
            return BatchReview(
                applicable=False, errors=(grammar_error,),
                normalized_source_sha256=parsed.normalized_source_sha256,
                content_snapshot_sha256=snapshot_hash,
                content_revision_id=revision_id,
            )
        if _existing_case_numbers(conn, targets):
            return BatchReview(
                applicable=False,
                errors=("One or more Case IDs already exist and cannot be imported.",),
                normalized_source_sha256=parsed.normalized_source_sha256,
                content_snapshot_sha256=snapshot_hash,
                content_revision_id=revision_id,
            )
        prepared = tuple(_prepare_row(conn, row) for row in parsed.rows)
        return _issue_review(BatchReview(
            applicable=True,
            errors=(),
            rows=prepared,
            normalized_source_sha256=parsed.normalized_source_sha256,
            content_snapshot_sha256=snapshot_hash,
            content_revision_id=revision_id,
            target_case_numbers=targets,
            interpretation_sha256=_interpretation_digest(prepared),
        ))
    except Exception as error:
        # Do not return parser/template details: they can contain raw Quick
        # Type or content fragments.  The source is still present only in the
        # current browser input, never in an external error/audit/log entry.
        return BatchReview(
            applicable=False,
            errors=(
                str(error) if isinstance(error, BulkInputError)
                else "A row could not be decoded, rendered, or reconstructed.",
            ),
            normalized_source_sha256=parsed.normalized_source_sha256,
        )
    finally:
        if started:
            conn.rollback()
        if owns_connection:
            conn.close()


# Names intentionally describe preview/review, never persistence.
prepare_bulk_preview = prepare_bulk_review
prepare_batch_review = prepare_bulk_review


def review_staleness(review: BatchReview, *, conn=None) -> str | None:
    """Return a safe stale reason without modifying the database.

    CP4 has no Apply, but the UI can still visibly invalidate a review if
    content/revision changes or any reviewed target namespace becomes occupied.
    """
    if not review.applicable:
        return "This batch review is not applicable."
    owns_connection = conn is None
    conn = conn or database.get_db_connection()
    started = False
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
            started = True
        current_hash = content_snapshot.content_snapshot_hash(content_snapshot.snapshot_from_connection(conn))
        if current_hash != review.content_snapshot_sha256:
            return "Content changed after this batch was reviewed."
        if database.current_content_revision_id(conn) != review.content_revision_id:
            return "Content revision changed after this batch was reviewed."
        if _existing_case_numbers(conn, review.target_case_numbers):
            return "A reviewed Case ID is now occupied."
        return None
    finally:
        if started:
            conn.rollback()
        if owns_connection:
            conn.close()


def _same_prepared_batch(issued: BatchReview, rebuilt: tuple[PreparedCase, ...]) -> bool:
    """Compare exact materialized rows as well as their signed digest."""
    return (
        issued.rows == rebuilt
        and issued.interpretation_sha256 == _interpretation_digest(rebuilt)
    )


def apply_bulk_review(
    review: BatchReview, source: bytes | str, delimiter: str, first_row_is_header: bool,
    *, confirmed: bool = False, warning_acknowledgement: BatchWarningAcknowledgement | None = None,
    conn=None,
) -> BatchApplyResult:
    """Atomically create an issued review's Cases as new pending Cases only.

    All state that can race (content, revision, target namespace, parsing,
    warning set, rendering, and saved-case reconstruction) is checked again
    after ``BEGIN IMMEDIATE``.  A caller-supplied connection must be idle so
    this function can own the one CP5 write transaction.
    """
    if not confirmed:
        return BatchApplyResult(False, "Confirm this batch before applying it.")
    if not _is_issued_review(review) or not review.applicable:
        return BatchApplyResult(False, "This batch review was not issued by this session.")
    # The review's detached rows are still checked before locking so a caller
    # cannot replace them while retaining an old issuer proof. The source is
    # reparsed and compared again inside the write transaction below.
    if review.interpretation_sha256 != _interpretation_digest(review.rows):
        return BatchApplyResult(False, "This batch review was altered after issuance.")
    if any(row.warnings for row in review.rows) and not _has_valid_warning_acknowledgement(
        review, warning_acknowledgement
    ):
        return BatchApplyResult(False, "Acknowledge the batch consistency warnings before applying it.")

    owns_connection = conn is None
    conn = conn or database.get_db_connection()
    try:
        if conn.in_transaction:
            return BatchApplyResult(False, "Batch Apply requires a fresh database transaction.")
        conn.execute("BEGIN IMMEDIATE")
        try:
            parsed = parse_bulk_source(source, delimiter, first_row_is_header)
            if parsed.normalized_source_sha256 != review.normalized_source_sha256:
                raise BulkInputError("The reviewed input changed.")
            targets = tuple(row.case_number for row in parsed.rows)
            if targets != review.target_case_numbers:
                raise BulkInputError("The reviewed target set changed.")
            if len(set(targets)) != len(targets):
                raise BulkInputError("The reviewed target set changed.")
            snapshot_hash = content_snapshot.content_snapshot_hash(
                content_snapshot.snapshot_from_connection(conn)
            )
            if snapshot_hash != review.content_snapshot_sha256:
                raise BulkInputError("Content changed after this batch was reviewed.")
            revision_id = database.current_content_revision_id(conn)
            if revision_id != review.content_revision_id:
                raise BulkInputError("Content revision changed after this batch was reviewed.")
            if _active_grammar_error(conn):
                raise BulkInputError("Quick Type configuration changed after this batch was reviewed.")
            if _existing_case_numbers(conn, targets):
                raise BulkInputError("A reviewed Case ID is now occupied.")
            rebuilt = tuple(_prepare_row(conn, row) for row in parsed.rows)
            if not _same_prepared_batch(review, rebuilt):
                raise BulkInputError("Decoded batch interpretation changed after review.")
            # This is intentionally separate from source/interpretation proof:
            # acknowledgement is a batch-level confirmation of all warnings.
            if any(row.warnings for row in rebuilt) and not _has_valid_warning_acknowledgement(
                review, warning_acknowledgement
            ):
                raise BulkInputError("Acknowledge the batch consistency warnings before applying it.")

            cursor = conn.execute(
                """INSERT INTO Case_Batch_Imports
                   (row_count, normalized_input_sha256, content_snapshot_sha256, content_revision_id)
                   VALUES (?, ?, ?, ?)""",
                (len(rebuilt), parsed.normalized_source_sha256, snapshot_hash, revision_id),
            )
            batch_import_id = cursor.lastrowid
            for prepared in rebuilt:
                database.persist_case_on_connection(
                    conn, prepared.case_number, prepared.preset_id, prepared.clinical_info,
                    prepared.structured_input, prepared.rendered_html,
                    status="pending", pending_reason=None,
                    content_revision_id=revision_id, mode="create", batch_import_id=batch_import_id,
                )
            conn.commit()
            return BatchApplyResult(True, batch_import_id=batch_import_id, row_count=len(rebuilt))
        except Exception as error:
            conn.rollback()
            # Keep error responses source-free even if a lower layer changes.
            if isinstance(error, BulkInputError):
                return BatchApplyResult(False, str(error))
            return BatchApplyResult(False, "The batch could not be applied; no Cases were created.")
    except sqlite3.Error:
        if conn.in_transaction:
            conn.rollback()
        return BatchApplyResult(False, "The batch could not be applied; no Cases were created.")
    finally:
        if owns_connection:
            conn.close()


# Apply aliases intentionally name only the reviewed pending-creation action.
apply_batch_review = apply_bulk_review
