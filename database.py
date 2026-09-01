import sqlite3
import json
import re
import hashlib
import pandas as pd

DB_NAME = "pathology.db"


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
    """Apply Stage 2 additions without rebuilding content or Cases.

    This migration is deliberately additive and safe to run on every app
    start.  Existing validated cases receive exactly one history artifact;
    no existing report or structured input is regenerated.
    """
    conn = get_db_connection() if db_name is None else sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
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
        """)
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


def get_all_presets():
    """Returns all presets, ordered for a browsable dropdown (category, then name)."""
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM Presets ORDER BY category, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_preset_by_id(preset_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM Presets WHERE id = ?", (preset_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_preset_blocks(preset_id):
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
           ORDER BY pb.sort_order""",
        (preset_id,),
    ).fetchall()

    blocks = []
    for pb in pb_rows:
        block = dict(pb)
        preset_overrides = json.loads(block["field_overrides"]) if block["field_overrides"] else {}

        field_rows = conn.execute(
            """SELECT bf.*, f.key AS field_key, f.label AS field_label, f.type AS field_type,
                      f.options AS field_options, f.default_value AS field_default,
                      f.conclusion_addendum_template AS field_addendum_template
               FROM Block_Fields bf
               JOIN Fields f ON f.id = bf.field_id
               WHERE bf.block_id = ?
               ORDER BY bf.sort_order""",
            (block["block_id"],),
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


def get_all_blocks():
    """Non-table Blocks available for ad hoc case composition."""
    conn = get_db_connection()
    rows = conn.execute("SELECT id, key, name FROM Blocks WHERE is_table = 0 ORDER BY name").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_editor_blocks():
    """Returns every Block with its rendering metadata for the read-only Editor."""
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM Blocks ORDER BY name, key").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_fields():
    """Returns every Field for the read-only Editor."""
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM Fields ORDER BY label, key").fetchall()
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
    """Returns Blocks whose templates call one Snippet shortcut exactly."""
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM Blocks ORDER BY name, key").fetchall()
    snippet_call = re.compile(r"snippet\(\s*(['\"])" + re.escape(shortcut) + r"\1\s*\)")
    template_columns = (
        "macro_template", "micro_template", "conclusion_template", "context_template",
        "title_fragment_template", "conclusion_label_template",
    )
    blocks = [
        dict(row) for row in rows
        if any(snippet_call.search(row[column] or "") for column in template_columns)
    ]
    pending_rows = conn.execute(
        "SELECT preset_id, structured_input FROM Cases WHERE status = 'pending'"
    ).fetchall()
    pending_case_count = _pending_case_count_for_block_ids(
        conn, {block["id"] for block in blocks}, pending_rows
    )
    conn.close()
    return {"blocks": blocks, "pending_case_count": pending_case_count}


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


def get_block_by_id(block_id):
    """One bare Block with Field/Block_Field defaults, never Preset overrides."""
    conn = get_db_connection()
    row = conn.execute("SELECT b.id AS block_id, b.* FROM Blocks b WHERE b.id = ?", (block_id,)).fetchone()
    if not row:
        conn.close()
        return None
    block = dict(row)
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
    rows = conn.execute(
        """SELECT sort_order, block_sort_order, field_key, token_kind, lookup_table, digit_width
           FROM Quick_Type_Tokens
           WHERE preset_id = ?
           ORDER BY sort_order""",
        (preset_id,),
    ).fetchall()
    conn.close()
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
    rows = conn.execute(
        """SELECT field_a_key, field_a_values, field_b_key, field_b_values, message
           FROM Field_Consistency_Rules
           WHERE block_id = ?""",
        (block_id,),
    ).fetchall()
    conn.close()
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


def get_snippet_by_shortcut(shortcut):
    """Looks up a single Snippet by its shortcut key. Returns a dict or None."""
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM Snippets WHERE shortcut = ?", (shortcut,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_snippets():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM Snippets ORDER BY category, shortcut").fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
        SELECT c.*, p.name AS preset_name, p.short_code AS preset_code
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
    pattern = re.compile(r"snippet\s*\(\s*(['\"])([^'\"]+)\1\s*\)")
    return sorted({match.group(2) for template in templates for match in pattern.finditer(template or "")})


def compute_case_content_fingerprint(preset_id, structured_input, conn=None):
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
                "SELECT block_id, sort_order AS instance_no FROM Preset_Blocks WHERE preset_id = ? ORDER BY sort_order",
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
            pb = conn.execute(
                """SELECT field_overrides FROM Preset_Blocks
                   WHERE preset_id = ? AND block_id = ? AND sort_order = ?""",
                (preset_id, block_id, instance.get("instance_no")),
            ).fetchone()
            block_data["fields"] = [dict(row) for row in field_rows]
            block_data["preset_field_overrides"] = (
                json.loads(pb["field_overrides"]) if pb and pb["field_overrides"] else {}
            )
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


def return_case_to_pending(case_number, reason):
    """The only allowed validated -> pending transition, with an audit row."""
    if not reason or not reason.strip():
        return False
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        case = conn.execute("SELECT id, status FROM Cases WHERE case_number = ?", (case_number,)).fetchone()
        if not case or case["status"] != "validated":
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
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        existing = cursor.execute(
            "SELECT id, status FROM Cases WHERE case_number = ?", (case_number,)
        ).fetchone()
        if existing and existing["status"] == "validated":
            # A duplicate case number is never an escape hatch around the
            # validated-record lock.  return_case_to_pending is intentional.
            return False
        current_fingerprint = compute_case_content_fingerprint(preset_id, structured_input, conn)
        if content_fingerprint is not None and content_fingerprint != current_fingerprint:
            # The relevant content changed after the caller rendered or
            # acknowledged it. Refuse rather than archiving a report under a
            # fingerprint that no longer describes the current content.
            return False
        content_fingerprint = current_fingerprint
        if content_revision_id is None:
            content_revision_id = current_content_revision_id(conn)
        if existing:
            cursor.execute(
                """UPDATE Cases
                   SET preset_id = ?, status = ?, pending_reason = ?, clinical_info = ?,
                       structured_input = ?, rendered_html = ?, content_fingerprint = ?,
                       content_revision_id = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE case_number = ?""",
                (preset_id, status, pending_reason, clinical_info,
                 _canonical_json(structured_input), rendered_html, content_fingerprint,
                 content_revision_id, case_number),
            )
        else:
            cursor.execute(
                """INSERT INTO Cases
                   (case_number, preset_id, status, pending_reason, clinical_info, structured_input, rendered_html,
                    content_fingerprint, content_revision_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (case_number, preset_id, status, pending_reason, clinical_info,
                 _canonical_json(structured_input), rendered_html, content_fingerprint, content_revision_id),
            )
        if status == "validated":
            case_id = existing["id"] if existing else cursor.lastrowid
            cursor.execute(
                """INSERT INTO Case_Validation_History
                   (case_id, rendered_html, structured_input, clinical_info, preset_id, content_fingerprint)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (case_id, rendered_html, _canonical_json(structured_input), clinical_info,
                 preset_id, content_fingerprint),
            )
            cursor.execute(
                "INSERT INTO Case_Status_History (case_id, transition) VALUES (?, ?)",
                (case_id, "validated"),
            )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Database error during save: {e}")
        return False
    finally:
        conn.close()
