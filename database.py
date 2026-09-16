import sqlite3
import json
import re
import hashlib
import os
import pandas as pd
import template_analysis
from dataclasses import dataclass
from typing import Literal

# Allows an isolated app boot without ever migrating the operational file.
# Normal interactive use remains exactly ``pathology.db``.
DB_NAME = os.environ.get("PATHOPILOT_DB_NAME", "pathology.db")


def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _table_columns(conn, table_name):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def migrate_schema(db_name=None):
    """Apply additive operational-safety and Stage 6/7 schema migrations.

    This migration is deliberately additive and safe to run on every app
    start.  Existing validated cases receive exactly one history artifact;
    no existing report or structured input is regenerated.
    """
    conn = get_db_connection() if db_name is None else sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # CP1: identity snapshots and reviewed candidates address a token by
        # (preset_id, sort_order).  Do the duplicate check before *any*
        # migration write so an old malformed database is refused intact.
        # SQLite's CREATE UNIQUE INDEX error is not sufficiently explicit and
        # must not become a partially-applied migration.
        token_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Quick_Type_Tokens'"
        ).fetchone()
        if token_table:
            duplicate = conn.execute(
                """SELECT preset_id,sort_order FROM Quick_Type_Tokens
                   GROUP BY preset_id,sort_order HAVING COUNT(*) > 1 LIMIT 1"""
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    "Stage 7 Quick Type migration refused: duplicate token position "
                    f"for preset_id={duplicate['preset_id']}, sort_order={duplicate['sort_order']}."
                )
        case_columns = _table_columns(conn, "Cases")
        if "content_fingerprint" not in case_columns:
            conn.execute("ALTER TABLE Cases ADD COLUMN content_fingerprint TEXT")
        if "content_revision_id" not in case_columns:
            conn.execute("ALTER TABLE Cases ADD COLUMN content_revision_id INTEGER")

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS Schema_Migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS Content_Revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                origin TEXT NOT NULL,
                summary TEXT
            );
            CREATE TABLE IF NOT EXISTS Content_Changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                revision_id INTEGER NOT NULL REFERENCES Content_Revisions(id),
                table_name TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                operation TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                before_hash TEXT,
                after_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS Case_Validation_History (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL REFERENCES Cases(id),
                rendered_html TEXT NOT NULL,
                structured_input JSON,
                clinical_info TEXT,
                preset_id INTEGER,
                content_fingerprint TEXT,
                validated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS Case_Status_History (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL REFERENCES Cases(id),
                transition TEXT NOT NULL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS Editor_Safety_State (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                initial_snapshot_hash TEXT,
                initial_snapshot_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS Case_Content_Reference_Changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                revision_id INTEGER NOT NULL REFERENCES Content_Revisions(id),
                case_id INTEGER NOT NULL REFERENCES Cases(id),
                reference_kind TEXT NOT NULL,
                before_preset_id INTEGER,
                before_preset_key TEXT,
                after_preset_id INTEGER,
                after_preset_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Stage 5 provenance is additive; legacy revisions keep NULL values.
        revision_columns = _table_columns(conn, "Content_Revisions")
        for column in ("package_hash", "base_snapshot_hash", "result_snapshot_hash"):
            if column not in revision_columns:
                conn.execute(f"ALTER TABLE Content_Revisions ADD COLUMN {column} TEXT")
        revision = conn.execute("SELECT id FROM Content_Revisions ORDER BY id DESC LIMIT 1").fetchone()
        if not revision:
            conn.execute(
                "INSERT INTO Content_Revisions (origin, summary) VALUES (?, ?)",
                ("migration", "Stage 2 operational-safety baseline"),
            )
            revision_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("UPDATE Cases SET content_revision_id = ? WHERE content_revision_id IS NULL", (revision_id,))
        else:
            revision_id = revision["id"]
            conn.execute("UPDATE Cases SET content_revision_id = ? WHERE content_revision_id IS NULL", (revision_id,))

        fingerprint_v2_applied = conn.execute(
            "SELECT 1 FROM Schema_Migrations WHERE name = ?",
            ("stage2_relevant_content_fingerprint_v2",),
        ).fetchone()
        fingerprint_filter = "" if not fingerprint_v2_applied else "WHERE content_fingerprint IS NULL"
        for case in conn.execute(
            f"SELECT id, preset_id, structured_input FROM Cases {fingerprint_filter}"
        ).fetchall():
            try:
                fingerprint = compute_case_content_fingerprint(
                    case["preset_id"], json.loads(case["structured_input"] or "{}"), conn
                )
            except (ValueError, KeyError, TypeError):
                # Preserve an old, already-partially-corrupt case rather than
                # turning a migration into a destructive repair attempt.  A
                # later reopen will visibly fail closed if its content is gone.
                fingerprint = None
            if fingerprint:
                conn.execute("UPDATE Cases SET content_fingerprint = ? WHERE id = ?", (fingerprint, case["id"]))
        validated = conn.execute("SELECT * FROM Cases WHERE status = 'validated'").fetchall()
        for case in validated:
            exists = conn.execute(
                "SELECT 1 FROM Case_Validation_History WHERE case_id = ? LIMIT 1", (case["id"],)
            ).fetchone()
            if not exists:
                conn.execute(
                    """INSERT INTO Case_Validation_History
                       (case_id, rendered_html, structured_input, clinical_info, preset_id,
                        content_fingerprint, validated_at)
                       VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))""",
                    (case["id"], case["rendered_html"] or "", case["structured_input"],
                     case["clinical_info"], case["preset_id"], case["content_fingerprint"],
                     case["updated_at"] or case["created_at"]),
                )
        conn.execute(
            """UPDATE Case_Validation_History
               SET content_fingerprint = (
                   SELECT c.content_fingerprint FROM Cases c
                   WHERE c.id = Case_Validation_History.case_id
               )
               WHERE content_fingerprint IS NULL"""
        )
        conn.execute(
            "INSERT OR IGNORE INTO Schema_Migrations (name) VALUES (?)",
            ("stage2_relevant_content_fingerprint_v2",),
        )

        # Stage 6 checkpoint 1 is deliberately storage-only.  The marker
        # makes its data backfill one-shot: subsequent app starts must not
        # reinterpret Case identity or reset a future display order.
        for table in ("Fields", "Blocks", "Presets", "Snippets"):
            if "is_archived" not in _table_columns(conn, table):
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0"
                )
        preset_block_columns = _table_columns(conn, "Preset_Blocks")
        if "display_order" not in preset_block_columns:
            conn.execute(
                "ALTER TABLE Preset_Blocks ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0"
            )
        case_columns = _table_columns(conn, "Cases")
        if "preset_short_code_snapshot" not in case_columns:
            conn.execute("ALTER TABLE Cases ADD COLUMN preset_short_code_snapshot TEXT")
        if "preset_name_snapshot" not in case_columns:
            conn.execute("ALTER TABLE Cases ADD COLUMN preset_name_snapshot TEXT")
        validation_columns = _table_columns(conn, "Case_Validation_History")
        if "preset_short_code_snapshot" not in validation_columns:
            conn.execute("ALTER TABLE Case_Validation_History ADD COLUMN preset_short_code_snapshot TEXT")
        if "preset_name_snapshot" not in validation_columns:
            conn.execute("ALTER TABLE Case_Validation_History ADD COLUMN preset_name_snapshot TEXT")

        stage6_marker = "stage6_persistence_compatibility_v1"
        if not conn.execute(
            "SELECT 1 FROM Schema_Migrations WHERE name = ?", (stage6_marker,)
        ).fetchone():
            for table in ("Fields", "Blocks", "Presets", "Snippets"):
                conn.execute(f"UPDATE {table} SET is_archived = 0 WHERE is_archived IS NULL")
            conn.execute("UPDATE Preset_Blocks SET display_order = sort_order")
            conn.execute(
                """UPDATE Cases
                   SET preset_short_code_snapshot = (
                           SELECT short_code FROM Presets WHERE id = Cases.preset_id
                       ),
                       preset_name_snapshot = (
                           SELECT name FROM Presets WHERE id = Cases.preset_id
                       )
                   WHERE preset_id IS NOT NULL"""
            )
            conn.execute(
                """UPDATE Case_Validation_History
                   SET preset_short_code_snapshot = (
                           SELECT p.short_code FROM Presets p
                           WHERE p.id = Case_Validation_History.preset_id
                       ),
                       preset_name_snapshot = (
                           SELECT p.name FROM Presets p
                           WHERE p.id = Case_Validation_History.preset_id
                       )
                   WHERE preset_id IS NOT NULL
                     AND EXISTS (
                         SELECT 1 FROM Presets p
                         WHERE p.id = Case_Validation_History.preset_id
                     )
                     AND (preset_short_code_snapshot IS NULL
                          OR preset_name_snapshot IS NULL)"""
            )
            conn.execute(
                "INSERT INTO Schema_Migrations (name) VALUES (?)", (stage6_marker,)
            )

        # The original checkpoint-1 migration incorrectly obtained history
        # identities from each Case's then-current Preset.  Its effect is
        # provable only for a pre-marker history row whose non-null historical
        # Preset differs from its Case's current Preset while both frozen
        # fields exactly match the Case's.  Do not infer corruption from a
        # Preset's current metadata: a later rename is legitimate history.
        # Null or unavailable history references are similarly ambiguous and
        # must retain their existing frozen values.
        stage6_history_repair_marker = "stage6_validation_history_preset_identity_repair_v1"
        if not conn.execute(
            "SELECT 1 FROM Schema_Migrations WHERE name = ?", (stage6_history_repair_marker,)
        ).fetchone():
            conn.execute(
                """UPDATE Case_Validation_History
                   SET preset_short_code_snapshot = (
                           SELECT p.short_code FROM Presets p
                           WHERE p.id = Case_Validation_History.preset_id
                       ),
                       preset_name_snapshot = (
                           SELECT p.name FROM Presets p
                           WHERE p.id = Case_Validation_History.preset_id
                       )
                   WHERE EXISTS (
                           SELECT 1 FROM Presets p
                           WHERE p.id = Case_Validation_History.preset_id
                       )
                     AND preset_short_code_snapshot IS NOT NULL
                     AND preset_name_snapshot IS NOT NULL
                     AND validated_at < (
                         SELECT applied_at FROM Schema_Migrations
                         WHERE name = ?
                     )
                     AND EXISTS (
                         SELECT 1 FROM Cases c
                         WHERE c.id = Case_Validation_History.case_id
                           AND c.preset_id IS NOT NULL
                           AND c.preset_id <> Case_Validation_History.preset_id
                           AND Case_Validation_History.preset_short_code_snapshot
                               IS c.preset_short_code_snapshot
                           AND Case_Validation_History.preset_name_snapshot
                               IS c.preset_name_snapshot
                     )""",
                (stage6_marker,),
            )
            conn.execute(
                "INSERT INTO Schema_Migrations (name) VALUES (?)", (stage6_history_repair_marker,)
            )

        # Explicit saved composition now fingerprints the presence of its
        # exact Preset_Block link, so a later unlink/relink is acknowledged
        # even when its overrides are empty. An old-format match proves
        # freshness only for legacy composition (no saved instances): its
        # instance list was derived from the link graph itself. An explicit
        # composition did not persist whether an exact empty-override link
        # ever existed, so it is irreducibly ambiguous and must remain stale.
        # Validated Cases and their frozen history are intentionally never
        # touched.
        link_fingerprint_marker = "stage6_explicit_preset_link_fingerprint_v2"
        if not conn.execute(
            "SELECT 1 FROM Schema_Migrations WHERE name = ?", (link_fingerprint_marker,)
        ).fetchone():
            prior_unsafe_marker = conn.execute(
                "SELECT 1 FROM Schema_Migrations WHERE name = ?",
                ("stage6_explicit_preset_link_fingerprint_v1",),
            ).fetchone()
            for case in conn.execute(
                "SELECT id,preset_id,structured_input,content_fingerprint FROM Cases WHERE status='pending'"
            ).fetchall():
                try:
                    structured = json.loads(case["structured_input"] or "{}")
                    if structured.get("block_instances") is not None:
                        # A database that ran the superseded v1 migration may
                        # already carry an unsafe rebaseline. Clear only its
                        # metadata so the normal fingerprint comparison fails
                        # closed; otherwise retain the old hash, which also
                        # differs from the new link-aware calculation.
                        if prior_unsafe_marker:
                            conn.execute("UPDATE Cases SET content_fingerprint=NULL WHERE id=?", (case["id"],))
                        continue
                    legacy = compute_case_content_fingerprint(
                        case["preset_id"], structured, conn, include_preset_link=False
                    )
                    if legacy != case["content_fingerprint"]:
                        continue
                    current = compute_case_content_fingerprint(case["preset_id"], structured, conn)
                except (ValueError, KeyError, TypeError):
                    continue
                if current != legacy:
                    conn.execute("UPDATE Cases SET content_fingerprint=? WHERE id=?", (current, case["id"]))
            conn.execute("INSERT INTO Schema_Migrations (name) VALUES (?)", (link_fingerprint_marker,))

        # Additive, idempotent CP1 enforcement.  The preflight above makes
        # this all-or-nothing for an old database containing duplicate token
        # positions; no Case/content data is rewritten.
        token_position_marker = "stage7_quick_type_token_position_unique_v1"
        if not conn.execute(
            "SELECT 1 FROM Schema_Migrations WHERE name=?", (token_position_marker,)
        ).fetchone():
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS Quick_Type_Tokens_preset_sort_order_uq "
                "ON Quick_Type_Tokens(preset_id, sort_order)"
            )
            conn.execute("INSERT INTO Schema_Migrations (name) VALUES (?)", (token_position_marker,))

        # CP5 local operational provenance.  This is deliberately not part of
        # content revisions/snapshots: it records only that a set of Cases was
        # created together, never patient identifiers or raw Quick Type input.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS Case_Batch_Imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                row_count INTEGER NOT NULL,
                normalized_input_sha256 TEXT NOT NULL,
                content_snapshot_sha256 TEXT NOT NULL,
                content_revision_id INTEGER NOT NULL REFERENCES Content_Revisions(id)
            );
        """)
        case_columns = _table_columns(conn, "Cases")
        if "batch_import_id" not in case_columns:
            conn.execute(
                "ALTER TABLE Cases ADD COLUMN batch_import_id INTEGER REFERENCES Case_Batch_Imports(id)"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS Cases_batch_import_id_idx ON Cases(batch_import_id)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO Schema_Migrations (name) VALUES (?)",
            ("stage7_case_batch_import_provenance_v1",),
        )
        conn.commit()
    finally:
        conn.close()


def current_content_revision_id(conn=None):
    own_connection = conn is None
    conn = conn or get_db_connection()
    row = conn.execute("SELECT id FROM Content_Revisions ORDER BY id DESC LIMIT 1").fetchone()
    if own_connection:
        conn.close()
    return row["id"] if row else None


def load_table_as_df(table_name):
    """Loads a full database table into a Pandas DataFrame, for the Manager view."""
    conn = get_db_connection()
    df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
    conn.close()
    return df


def get_all_presets(include_archived=False):
    """Return Presets suitable for a new selection by default.

    Archived content is deliberately still addressable by stable ID for a
    saved draft, but must not leak back into the new-case or Quick Type
    pickers.  Callers reconstructing a persisted Case opt in explicitly.
    """
    conn = get_db_connection()
    query = "SELECT * FROM Presets"
    if not include_archived:
        query += " WHERE is_archived = 0"
    rows = conn.execute(query + " ORDER BY category, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_presets_on_connection(conn, include_archived=False):
    """Connection-scoped Preset read for candidate/batch interpretation.

    Supplying a candidate connection is an authority boundary: callers must
    never silently reopen the operational database while evaluating a frozen
    candidate graph.
    """
    query = "SELECT * FROM Presets"
    if not include_archived:
        query += " WHERE is_archived = 0"
    return [dict(row) for row in conn.execute(query + " ORDER BY category, name")]


def get_preset_by_id(preset_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM Presets WHERE id = ?", (preset_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_preset_by_id_on_connection(conn, preset_id):
    """Connection-scoped counterpart used by candidate-state validation."""
    row = conn.execute("SELECT * FROM Presets WHERE id = ?", (preset_id,)).fetchone()
    return dict(row) if row else None


def get_preset_blocks(preset_id, *, include_archived=False):
    """
    Returns the ordered list of blocks for a preset. Each block dict is enriched
    with 'fields': an ordered list of resolved field dicts, where the value for
    each field has already been resolved through the override chain:

        Field.default_value  <-  Block_Fields.default_override  <-  Preset_Blocks.field_overrides

    This is the generic replacement for the old hardcoded `if block_name == ...`
    branching: nothing here references a specific case type by name.
    """
    conn = get_db_connection()
    pb_rows = conn.execute(
        """SELECT pb.block_id, pb.sort_order, pb.field_overrides, b.*
           FROM Preset_Blocks pb
           JOIN Blocks b ON b.id = pb.block_id
           WHERE pb.preset_id = ?
             AND (? OR b.is_archived = 0)
           ORDER BY pb.display_order, pb.sort_order""",
        (preset_id, include_archived),
    ).fetchall()

    blocks = []
    for pb in pb_rows:
        block = dict(pb)
        block["_allow_archived_dependencies"] = bool(include_archived)
        preset_overrides = json.loads(block["field_overrides"]) if block["field_overrides"] else {}

        field_rows = conn.execute(
            """SELECT bf.*, f.key AS field_key, f.label AS field_label, f.type AS field_type,
                      f.options AS field_options, f.default_value AS field_default,
                      f.conclusion_addendum_template AS field_addendum_template
               FROM Block_Fields bf
               JOIN Fields f ON f.id = bf.field_id
               WHERE bf.block_id = ?
                 AND (? OR f.is_archived = 0)
               ORDER BY bf.sort_order""",
            (block["block_id"], include_archived),
        ).fetchall()

        resolved_fields = []
        for fr in field_rows:
            value = fr["field_default"]
            if fr["default_override"] is not None:
                value = fr["default_override"]
            if fr["field_key"] in preset_overrides:
                value = preset_overrides[fr["field_key"]]

            resolved_fields.append({
                "key": fr["field_key"],
                "label": fr["label_override"] or fr["field_label"],
                "type": fr["field_type"],
                "options": json.loads(fr["field_options"]) if fr["field_options"] else None,
                "value": value,
                "conclusion_addendum_template": fr["field_addendum_template"],
                "context_section": bool(fr["context_section"]),
            })

        block["fields"] = resolved_fields
        blocks.append(block)

    conn.close()
    return blocks


def get_preset_blocks_on_connection(conn, preset_id):
    """Load resolved Preset Blocks without opening another database.

    Candidate content validation must render rows visible in its own
    transaction/savepoint; this deliberately mirrors get_preset_blocks() but
    never silently falls back to the operational connection.
    """
    pb_rows = conn.execute(
        """SELECT pb.block_id, pb.sort_order, pb.field_overrides, b.*
           FROM Preset_Blocks pb JOIN Blocks b ON b.id = pb.block_id
           WHERE pb.preset_id = ? ORDER BY pb.display_order, pb.sort_order""", (preset_id,)
    ).fetchall()
    return [_resolved_block_on_connection(conn, dict(row), preset_id, row["sort_order"])
            for row in pb_rows]


def get_block_on_connection(conn, block_id, preset_id=None, instance_no=None, *, include_archived=True):
    """Load one Block with fields for a saved composed-case instance."""
    row = conn.execute("SELECT b.id AS block_id, b.* FROM Blocks b WHERE b.id = ? AND (? OR b.is_archived = 0)",
                       (block_id, include_archived)).fetchone()
    if not row:
        return None
    return _resolved_block_on_connection(conn, dict(row), preset_id, instance_no)


def _resolved_block_on_connection(conn, block, preset_id, instance_no):
    block.setdefault("_allow_archived_dependencies", True)
    overrides = {}
    if preset_id is not None and instance_no is not None:
        row = conn.execute(
            """SELECT field_overrides FROM Preset_Blocks
               WHERE preset_id = ? AND block_id = ? AND sort_order = ?""",
            (preset_id, block["block_id"], instance_no),
        ).fetchone()
        overrides = json.loads(row["field_overrides"]) if row and row["field_overrides"] else {}
    fields = conn.execute(
        """SELECT bf.*, f.key AS field_key, f.label AS field_label, f.type AS field_type,
                  f.options AS field_options, f.default_value AS field_default,
                  f.conclusion_addendum_template AS field_addendum_template
           FROM Block_Fields bf JOIN Fields f ON f.id = bf.field_id
           WHERE bf.block_id = ? ORDER BY bf.sort_order""", (block["block_id"],)
    ).fetchall()
    block["fields"] = [{
        "key": field["field_key"],
        "label": field["label_override"] or field["field_label"],
        "type": field["field_type"],
        "options": json.loads(field["field_options"]) if field["field_options"] else None,
        "value": overrides.get(field["field_key"], field["default_override"]
                               if field["default_override"] is not None else field["field_default"]),
        "conclusion_addendum_template": field["field_addendum_template"],
        "context_section": bool(field["context_section"]),
    } for field in fields]
    return block


def get_all_blocks(include_archived=False):
    """Non-table Blocks available for ad hoc case composition."""
    conn = get_db_connection()
    rows = conn.execute("SELECT id, key, name FROM Blocks WHERE is_table = 0 AND (? OR is_archived = 0) ORDER BY name",
                        (include_archived,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_editor_blocks():
    """Returns every Block with its rendering metadata for the read-only Editor."""
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM Blocks ORDER BY name, key").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_fields(include_archived=True):
    """Returns Fields for the Editor/Content Studio.

    The historic Editor showed everything, so retain that default while letting
    the Studio's lifecycle filter ask for active rows only.
    """
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM Fields WHERE (? OR is_archived = 0) ORDER BY label, key",
                        (include_archived,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_preset_usage(preset_id):
    """Ordered Blocks (with resolved Fields) used by one Preset."""
    return get_preset_blocks(preset_id)


def get_block_usage(block_id):
    """Relationships and conservative pending-case impact for one Block.

    A saved composed case records its actual Block instances. Older saved
    cases have no such list and therefore still use their Preset's current
    Block list. This mirrors the current reopen behaviour without changing
    it; Stage 1 only displays the result and never writes it.
    """
    conn = get_db_connection()
    preset_rows = conn.execute(
        """SELECT p.id, p.name, p.short_code, p.category, pb.sort_order
           FROM Preset_Blocks pb JOIN Presets p ON p.id = pb.preset_id
           WHERE pb.block_id = ? ORDER BY p.category, p.name, pb.sort_order""",
        (block_id,),
    ).fetchall()
    field_rows = conn.execute(
        """SELECT f.*, bf.sort_order, bf.label_override, bf.default_override,
                  bf.context_section
           FROM Block_Fields bf JOIN Fields f ON f.id = bf.field_id
           WHERE bf.block_id = ? ORDER BY bf.sort_order""",
        (block_id,),
    ).fetchall()
    pending_rows = conn.execute(
        "SELECT preset_id, structured_input FROM Cases WHERE status = 'pending'"
    ).fetchall()

    impacted = _pending_case_count_for_block_ids(conn, {block_id}, pending_rows)
    conn.close()
    return {
        "presets": [dict(row) for row in preset_rows],
        "fields": [dict(row) for row in field_rows],
        "pending_case_count": impacted,
    }


def get_field_usage(field_id):
    """Relationships and pending-case impact for one Field."""
    conn = get_db_connection()
    block_rows = conn.execute(
        """SELECT b.id, b.key, b.name, bf.sort_order, bf.label_override,
                  bf.default_override, bf.context_section
           FROM Block_Fields bf JOIN Blocks b ON b.id = bf.block_id
           WHERE bf.field_id = ? ORDER BY b.name, bf.sort_order""",
        (field_id,),
    ).fetchall()
    block_ids = {row["id"] for row in block_rows}
    preset_rows = conn.execute(
        """SELECT DISTINCT p.id, p.name, p.short_code, p.category
           FROM Preset_Blocks pb JOIN Presets p ON p.id = pb.preset_id
           JOIN Block_Fields bf ON bf.block_id = pb.block_id
           WHERE bf.field_id = ? ORDER BY p.category, p.name""",
        (field_id,),
    ).fetchall()
    pending_rows = conn.execute(
        "SELECT preset_id, structured_input FROM Cases WHERE status = 'pending'"
    ).fetchall()

    impacted = _pending_case_count_for_block_ids(conn, block_ids, pending_rows)
    conn.close()
    return {
        "blocks": [dict(row) for row in block_rows],
        "presets": [dict(row) for row in preset_rows],
        "pending_case_count": impacted,
    }


def get_snippet_usage(shortcut):
    """Return every Block/Field path whose rendering calls one Snippet."""
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM Blocks ORDER BY name, key").fetchall()
    snippet_call = re.compile(r"snippet\(\s*(['\"])" + re.escape(shortcut) + r"\1\s*\)")
    template_columns = (
        "macro_template", "micro_template", "conclusion_template", "context_template",
        "title_fragment_template", "conclusion_label_template",
    )
    direct_block_ids = {
        row["id"] for row in rows
        if any(snippet_call.search(row[column] or "") for column in template_columns)
    }
    fields = [
        dict(row) for row in conn.execute("SELECT * FROM Fields ORDER BY label, key")
        if snippet_call.search(row["conclusion_addendum_template"] or "")
    ]
    field_ids = {field["id"] for field in fields}
    addendum_block_ids = set()
    if field_ids:
        placeholders = ", ".join("?" for _ in field_ids)
        addendum_block_ids = {
            row["block_id"] for row in conn.execute(
                f"SELECT DISTINCT block_id FROM Block_Fields WHERE field_id IN ({placeholders})",
                tuple(sorted(field_ids)),
            )
        }
    block_ids = direct_block_ids | addendum_block_ids
    blocks = [dict(row) for row in rows if row["id"] in block_ids]
    pending_rows = conn.execute(
        "SELECT preset_id, structured_input FROM Cases WHERE status = 'pending'"
    ).fetchall()
    pending_case_count = _pending_case_count_for_block_ids(
        conn, block_ids, pending_rows
    )
    conn.close()
    return {"blocks": blocks, "fields": fields, "pending_case_count": pending_case_count}


def get_preset_pending_case_count(preset_id):
    """Number of pending cases directly saved against one Preset."""
    conn = get_db_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM Cases WHERE status = 'pending' AND preset_id = ?", (preset_id,)
    ).fetchone()[0]
    conn.close()
    return count


def _pending_case_count_for_block_ids(conn, block_ids, pending_rows):
    """Counts unique pending cases whose saved composition uses Block IDs.

    The helper deliberately accepts fetched rows so a caller can calculate its
    entire impact view with one connection and without relying on SQLite's
    JSON extension.  A case without saved composition remains a legacy case
    and inherits its Preset's Blocks, exactly as current reopen does.
    """
    if not block_ids:
        return 0
    impacted = 0
    for case in pending_rows:
        structured_input = json.loads(case["structured_input"]) if case["structured_input"] else {}
        instances = structured_input.get("block_instances")
        if instances is not None:
            impacted += any(instance.get("block_id") in block_ids for instance in instances)
            continue
        placeholders = ", ".join("?" for _ in block_ids)
        inherited = conn.execute(
            f"""SELECT 1 FROM Preset_Blocks WHERE preset_id = ?
                AND block_id IN ({placeholders}) LIMIT 1""",
            (case["preset_id"], *block_ids),
        ).fetchone()
        impacted += bool(inherited)
    return impacted


def get_block_by_id(block_id, *, include_archived=True):
    """One bare Block with Field/Block_Field defaults, never Preset overrides."""
    conn = get_db_connection()
    row = conn.execute("SELECT b.id AS block_id, b.* FROM Blocks b WHERE b.id = ? AND (? OR b.is_archived = 0)",
                       (block_id, include_archived)).fetchone()
    if not row:
        conn.close()
        return None
    block = dict(row)
    block["_allow_archived_dependencies"] = bool(include_archived)
    field_rows = conn.execute(
        """SELECT bf.*, f.key AS field_key, f.label AS field_label, f.type AS field_type,
                  f.options AS field_options, f.default_value AS field_default,
                  f.conclusion_addendum_template AS field_addendum_template
           FROM Block_Fields bf JOIN Fields f ON f.id = bf.field_id
           WHERE bf.block_id = ? ORDER BY bf.sort_order""",
        (block_id,),
    ).fetchall()
    conn.close()
    block["fields"] = [{
        "key": field["field_key"], "label": field["label_override"] or field["field_label"],
        "type": field["field_type"], "options": json.loads(field["field_options"]) if field["field_options"] else None,
        "value": field["default_override"] if field["default_override"] is not None else field["field_default"],
        "conclusion_addendum_template": field["field_addendum_template"],
        "context_section": bool(field["context_section"]),
    } for field in field_rows]
    return block


def get_preset_block_rows(preset_id, block_id):
    """Pre-filled row instances for is_table blocks (e.g. the 6 prostate sites)."""
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT * FROM Preset_Block_Rows
           WHERE preset_id = ? AND block_id = ?
           ORDER BY sort_order""",
        (preset_id, block_id),
    ).fetchall()
    conn.close()
    return [
        {**dict(r), "field_overrides": json.loads(r["field_overrides"]) if r["field_overrides"] else {}}
        for r in rows
    ]


def get_quick_type_tokens(preset_id):
    """Ordered Quick_Type_Tokens for a preset, flattened across whatever
    blocks it spans (see quicktype.py). Empty list for a preset with no
    Quick Type config -- meaning nothing beyond its bare short_code
    parses, which quicktype.parse_tokens treats as valid, not an error."""
    conn = get_db_connection()
    try:
        return get_quick_type_tokens_on_connection(conn, preset_id)
    finally:
        conn.close()


def get_quick_type_tokens_on_connection(conn, preset_id):
    """Connection-scoped token read; never opens a fallback connection."""
    rows = conn.execute(
        """SELECT sort_order, block_sort_order, field_key, token_kind, lookup_table, digit_width
           FROM Quick_Type_Tokens
           WHERE preset_id = ?
           ORDER BY sort_order""",
        (preset_id,),
    ).fetchall()
    return [
        {**dict(r), "lookup_table": json.loads(r["lookup_table"]) if r["lookup_table"] else None}
        for r in rows
    ]


def get_consistency_rules(block_id):
    """Field_Consistency_Rules for one Block (see consistency.py for the
    evaluation logic). Empty list for a Block with no rules configured --
    that's the common case, not an error; consistency.check_block()
    treats it as "nothing to check, no warnings.\""""
    conn = get_db_connection()
    try:
        return get_consistency_rules_on_connection(conn, block_id)
    finally:
        conn.close()


def get_consistency_rules_on_connection(conn, block_id):
    rows = conn.execute(
        """SELECT field_a_key, field_a_values, field_b_key, field_b_values, message
           FROM Field_Consistency_Rules WHERE block_id = ?""", (block_id,),
    ).fetchall()
    return [
        {
            "field_a_key": r["field_a_key"],
            "field_a_values": json.loads(r["field_a_values"]),
            "field_b_key": r["field_b_key"],
            "field_b_values": json.loads(r["field_b_values"]),
            "message": r["message"],
        }
        for r in rows
    ]


def get_conclusion_group_label(block_keys):
    """
    block_keys: list of block key strings sharing an identical conclusion
    signature. Returns the registered French combined label, or None if no
    combo is defined (caller should fall back to a comma-joined list).
    """
    key_set = ",".join(sorted(block_keys))
    conn = get_db_connection()
    row = conn.execute(
        "SELECT combined_label FROM Conclusion_Group_Labels WHERE block_key_set = ?",
        (key_set,),
    ).fetchone()
    conn.close()
    return row["combined_label"] if row else None


def get_snippet_by_shortcut(shortcut, *, include_archived=False):
    """Looks up a single Snippet by its shortcut key. Returns a dict or None."""
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM Snippets WHERE shortcut = ? AND (? OR is_archived = 0)",
                       (shortcut, include_archived)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_snippets(include_archived=True):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM Snippets WHERE (? OR is_archived = 0) ORDER BY category, shortcut",
                        (include_archived,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_conclusion_group_labels():
    """Return group-label configuration in its canonical stable-key order."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM Conclusion_Group_Labels ORDER BY block_key_set"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_snippet(shortcut, expansion, category):
    """Inserts a new Snippet. Returns (success, error_message)."""
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO Snippets (shortcut, expansion, category) VALUES (?, ?, ?)",
            (shortcut, expansion, category),
        )
        conn.commit()
        return True, None
    except sqlite3.IntegrityError:
        return False, f"Shortcut '{shortcut}' already exists — shortcuts must be unique."
    except Exception as e:
        print(f"Database error adding snippet: {e}")
        return False, "Unexpected database error — check the server log."
    finally:
        conn.close()


def get_all_cases(status=None, search_term=None):
    """
    Returns Cases for the Worklist page, optionally filtered by status
    ('pending'/'validated') and/or a search term matched against case
    number or clinical info. Joined with Presets for display. Ordered
    most-recently-touched first.
    """
    conn = get_db_connection()
    query = """
        SELECT c.*, COALESCE(p.name, c.preset_name_snapshot) AS preset_name,
               COALESCE(p.short_code, c.preset_short_code_snapshot) AS preset_code
        FROM Cases c
        LEFT JOIN Presets p ON p.id = c.preset_id
        WHERE 1=1
    """
    params = []
    if status:
        query += " AND c.status = ?"
        params.append(status)
    if search_term:
        query += " AND (c.case_number LIKE ? OR c.clinical_info LIKE ?)"
        like_term = f"%{search_term}%"
        params.extend([like_term, like_term])
    query += " ORDER BY COALESCE(c.updated_at, c.created_at) DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_cases():
    """
    Returns all 'pending' Cases as dicts, ordered for the compact sidebar
    list — grouped by pending_reason so similar work (all the IHC cases,
    all the niveaux cases...) can be processed together, then by case
    number within each reason.
    """
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT case_number, pending_reason, preset_id, updated_at
           FROM Cases
           WHERE status = 'pending'
           ORDER BY pending_reason, case_number"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_case_by_number(case_number):
    """Returns a saved Case as a dict, with structured_input already parsed
    from JSON, or None if no case with that number exists."""
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM Cases WHERE case_number = ?", (case_number,)).fetchone()
    conn.close()
    if not row:
        return None
    case = dict(row)
    case["structured_input"] = json.loads(case["structured_input"]) if case["structured_input"] else {}
    return case


_TEMPLATE_COLUMNS = (
    "macro_template", "micro_template", "conclusion_template", "context_template",
    "title_fragment_template", "conclusion_label_template",
)


def _snippet_shortcuts(templates):
    return template_analysis.snippet_shortcuts(templates)


def compute_case_content_fingerprint(preset_id, structured_input, conn=None, *, include_preset_link=True):
    """Hash only the currently-rendering content relevant to one Case.

    Saved composition is authoritative, including order and duplicate
    instances.  This intentionally excludes unrelated content, Cases, and
    audit rows so a correction elsewhere cannot block this draft.
    """
    own_connection = conn is None
    conn = conn or get_db_connection()
    try:
        preset = conn.execute(
            "SELECT name, default_title FROM Presets WHERE id = ?", (preset_id,)
        ).fetchone()
        if not preset:
            raise ValueError(f"Preset {preset_id} does not exist")
        data = structured_input or {}
        instances = data.get("block_instances")
        if instances is None:
            instances = [dict(row) for row in conn.execute(
                "SELECT block_id, sort_order AS instance_no FROM Preset_Blocks WHERE preset_id = ? ORDER BY display_order, sort_order",
                (preset_id,),
            )]
        blocks = []
        all_keys = []
        all_shortcuts = set()
        for instance in instances:
            block_id = instance["block_id"]
            block = conn.execute("SELECT * FROM Blocks WHERE id = ?", (block_id,)).fetchone()
            if not block:
                raise ValueError(f"Block {block_id} is unavailable")
            block_data = {column: block[column] for column in (
                "key", "name", "is_table", "site_label", "conclusion_group", *_TEMPLATE_COLUMNS
            )}
            all_keys.append(block["key"])
            all_shortcuts.update(_snippet_shortcuts([block[column] for column in _TEMPLATE_COLUMNS]))
            field_rows = conn.execute(
                """SELECT f.key, f.label, f.type, f.options, f.default_value,
                          f.conclusion_addendum_template, bf.sort_order, bf.label_override,
                          bf.default_override, bf.context_section
                   FROM Block_Fields bf JOIN Fields f ON f.id = bf.field_id
                   WHERE bf.block_id = ? ORDER BY bf.sort_order""", (block_id,)
            ).fetchall()
            all_shortcuts.update(_snippet_shortcuts(
                [row["conclusion_addendum_template"] for row in field_rows]
            ))
            pb = conn.execute(
                """SELECT field_overrides FROM Preset_Blocks
                   WHERE preset_id = ? AND block_id = ? AND sort_order = ?""",
                (preset_id, block_id, instance.get("instance_no")),
            ).fetchone()
            block_data["fields"] = [dict(row) for row in field_rows]
            # Explicit saved composition owns its specimen order, but it can
            # still inherit the exact matching Preset_Block relationship's
            # overrides.  Preserve whether that relationship exists as well:
            # unlinking/relinking it is a real pending-draft dependency even
            # where its effective override object happens to be empty.
            preset_overrides = json.loads(pb["field_overrides"]) if pb and pb["field_overrides"] else {}
            if include_preset_link:
                block_data["preset_link"] = {"field_overrides": preset_overrides} if pb else None
            block_data["preset_field_overrides"] = preset_overrides
            blocks.append(block_data)
        snippets = []
        if all_shortcuts:
            placeholders = ", ".join("?" for _ in all_shortcuts)
            snippets = [dict(row) for row in conn.execute(
                f"SELECT shortcut, expansion FROM Snippets WHERE shortcut IN ({placeholders}) ORDER BY shortcut",
                tuple(sorted(all_shortcuts)),
            )]
        possible_label_keys = {
            ",".join(sorted(all_keys[start:end]))
            for start in range(len(all_keys))
            for end in range(start + 2, len(all_keys) + 1)
        }
        labels = [dict(row) for row in conn.execute(
            "SELECT block_key_set, combined_label FROM Conclusion_Group_Labels ORDER BY block_key_set"
        ) if row["block_key_set"] in possible_label_keys]
        payload = {
            "preset": {"effective_title": preset["default_title"] or preset["name"]},
            "blocks": blocks,
            "snippets": snippets,
            "conclusion_group_labels": labels,
        }
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    finally:
        if own_connection:
            conn.close()


def get_case_validation_history(case_number):
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT h.* FROM Case_Validation_History h JOIN Cases c ON c.id = h.case_id
           WHERE c.case_number = ? ORDER BY h.id""", (case_number,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_case_status_history(case_number):
    """Audited lifecycle transitions, including return-to-pending reasons."""
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT h.transition, h.reason, h.created_at
           FROM Case_Status_History h JOIN Cases c ON c.id = h.case_id
           WHERE c.case_number = ? ORDER BY h.id""", (case_number,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_pending_case(case_number):
    """Permanently delete one currently pending Case and its owned records.

    A validated Case must first take the existing audited return-to-pending
    path.  Batch-import provenance belongs to an aggregate import, so its row
    is removed only after the Case is gone and only when no Cases still refer
    to it.
    """
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        case = conn.execute(
            "SELECT id, status, batch_import_id FROM Cases WHERE case_number = ?", (case_number,)
        ).fetchone()
        if not case or case["status"] != "pending":
            conn.rollback()
            return False

        # These tables own Case-level audit/history records and have no
        # cascading foreign keys.  Remove them before their parent Case.
        conn.execute("DELETE FROM Case_Content_Reference_Changes WHERE case_id = ?", (case["id"],))
        conn.execute("DELETE FROM Case_Validation_History WHERE case_id = ?", (case["id"],))
        conn.execute("DELETE FROM Case_Status_History WHERE case_id = ?", (case["id"],))
        conn.execute("DELETE FROM Cases WHERE id = ?", (case["id"],))

        if case["batch_import_id"] is not None:
            conn.execute(
                """DELETE FROM Case_Batch_Imports
                   WHERE id = ?
                     AND NOT EXISTS (
                         SELECT 1 FROM Cases WHERE batch_import_id = ?
                     )""",
                (case["batch_import_id"], case["batch_import_id"]),
            )
        conn.commit()
        return True
    except sqlite3.Error:
        if conn.in_transaction:
            conn.rollback()
        return False
    finally:
        conn.close()


def return_case_to_pending(case_number, reason):
    """The only allowed validated -> pending transition, with an audit row.

    A validated HTML artifact survives permanent content deletion, but a live
    draft must be reconstructable.  Preflight the exact saved structured
    inputs inside this transaction before changing status; archived rows are
    intentionally still resolvable by that path, deleted rows are not.
    """
    if not reason or not reason.strip():
        return False
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        case = conn.execute("SELECT * FROM Cases WHERE case_number = ?", (case_number,)).fetchone()
        if not case or case["status"] != "validated":
            return False
        try:
            import editor_preview
            # The helper intentionally rejects validated artifacts; use an
            # in-memory pending view solely for reconstruction preflight.
            editor_preview.render_saved_case(conn, {**dict(case), "status": "pending"}, strict=True)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            conn.rollback()
            return False
        conn.execute(
            "UPDATE Cases SET status = 'pending', pending_reason = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (case["id"],),
        )
        conn.execute(
            "INSERT INTO Case_Status_History (case_id, transition, reason) VALUES (?, ?, ?)",
            (case["id"], "validated_to_pending", reason.strip()),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False
    finally:
        conn.close()


@dataclass(frozen=True, slots=True)
class CasePersistenceResult:
    """The result of one persistence operation owned by the caller's transaction."""

    case_id: int
    created: bool
    content_fingerprint: str
    content_revision_id: int | None


class CasePersistenceError(ValueError):
    """A safe, non-patient-specific refusal from the shared Case writer."""


def persist_case_on_connection(
    conn, case_number, preset_id, clinical_info, structured_input, rendered_html,
    *, status="pending", pending_reason=None, content_fingerprint=None,
    content_revision_id=None, mode: Literal["create", "update"],
    batch_import_id=None,
):
    """Persist a Case without taking ownership of transaction control.

    Both the ordinary one-Case path and CP5 bulk Apply use this one serializer.
    ``create`` is intentionally strict: it cannot overwrite a pending Case;
    ``update`` cannot silently create one. Create is the only mode that
    accepts a batch link. This function never begins,
    commits, or rolls back a transaction.
    """
    if mode not in {"create", "update"}:
        raise CasePersistenceError("Unsupported Case persistence mode.")
    if mode != "create" and batch_import_id is not None:
        raise CasePersistenceError("Batch provenance is only valid for Case creation.")
    existing = conn.execute(
        "SELECT id, status FROM Cases WHERE case_number = ?", (case_number,)
    ).fetchone()
    if ((mode == "create" and existing)
            or (mode == "update" and not existing)
            or (existing and existing["status"] == "validated")):
        # A duplicate never bypasses the validated lock, and CP5 never updates.
        raise CasePersistenceError("Case namespace is unavailable.")
    preset_identity = conn.execute(
        "SELECT short_code, name FROM Presets WHERE id = ?", (preset_id,)
    ).fetchone()
    if not preset_identity:
        raise CasePersistenceError("Preset is unavailable.")
    current_fingerprint = compute_case_content_fingerprint(preset_id, structured_input, conn)
    if content_fingerprint is not None and content_fingerprint != current_fingerprint:
        raise CasePersistenceError("Case content changed before persistence.")
    if content_revision_id is None:
        content_revision_id = current_content_revision_id(conn)
    canonical_input = _canonical_json(structured_input)

    if existing:
        conn.execute(
            """UPDATE Cases
               SET preset_id = ?, status = ?, pending_reason = ?, clinical_info = ?,
                   structured_input = ?, rendered_html = ?, content_fingerprint = ?,
                   content_revision_id = ?, preset_short_code_snapshot = ?,
                   preset_name_snapshot = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (preset_id, status, pending_reason, clinical_info, canonical_input, rendered_html,
             current_fingerprint, content_revision_id, preset_identity["short_code"],
             preset_identity["name"], existing["id"]),
        )
        case_id = existing["id"]
        created = False
    else:
        cursor = conn.execute(
            """INSERT INTO Cases
               (case_number, preset_id, status, pending_reason, clinical_info, structured_input, rendered_html,
                content_fingerprint, content_revision_id, preset_short_code_snapshot, preset_name_snapshot,
                batch_import_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_number, preset_id, status, pending_reason, clinical_info, canonical_input, rendered_html,
             current_fingerprint, content_revision_id, preset_identity["short_code"],
             preset_identity["name"], batch_import_id),
        )
        case_id = cursor.lastrowid
        created = True
    if status == "validated":
        conn.execute(
            """INSERT INTO Case_Validation_History
               (case_id, rendered_html, structured_input, clinical_info, preset_id, content_fingerprint,
                preset_short_code_snapshot, preset_name_snapshot)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, rendered_html, canonical_input, clinical_info, preset_id, current_fingerprint,
             preset_identity["short_code"], preset_identity["name"]),
        )
        conn.execute(
            "INSERT INTO Case_Status_History (case_id, transition) VALUES (?, ?)",
            (case_id, "validated"),
        )
    return CasePersistenceResult(case_id, created, current_fingerprint, content_revision_id)


def save_case(case_number, preset_id, clinical_info, structured_input, rendered_html,
              status="pending", pending_reason=None, content_fingerprint=None,
              content_revision_id=None):
    """
    Saves both the structured input (for reopening/reusing the case later) and
    the frozen rendered HTML (the archived artifact). 'validated' cases are
    frozen forever, never regenerated even if Blocks/templates change later
    — 'pending' cases are live drafts, expected to be reopened and
    re-rendered from current templates.
    """
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT 1 FROM Cases WHERE case_number = ?", (case_number,)
        ).fetchone()
        persist_case_on_connection(
            conn, case_number, preset_id, clinical_info, structured_input, rendered_html,
            status=status, pending_reason=pending_reason, content_fingerprint=content_fingerprint,
            content_revision_id=content_revision_id, mode="update" if existing else "create",
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Database error during save: {e}")
        return False
    finally:
        conn.close()
