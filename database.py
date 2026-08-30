import sqlite3
import json
import pandas as pd

DB_NAME = "pathology.db"


def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


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


def save_case(case_number, preset_id, clinical_info, structured_input, rendered_html,
              status="pending", pending_reason=None):
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
        existing = cursor.execute(
            "SELECT id FROM Cases WHERE case_number = ?", (case_number,)
        ).fetchone()
        if existing:
            cursor.execute(
                """UPDATE Cases
                   SET preset_id = ?, status = ?, pending_reason = ?, clinical_info = ?,
                       structured_input = ?, rendered_html = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE case_number = ?""",
                (preset_id, status, pending_reason, clinical_info,
                 json.dumps(structured_input), rendered_html, case_number),
            )
        else:
            cursor.execute(
                """INSERT INTO Cases
                   (case_number, preset_id, status, pending_reason, clinical_info, structured_input, rendered_html)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (case_number, preset_id, status, pending_reason, clinical_info,
                 json.dumps(structured_input), rendered_html),
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"Database error during save: {e}")
        return False
    finally:
        conn.close()