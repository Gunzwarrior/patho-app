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


def save_case(case_number, preset_id, clinical_info, structured_input, rendered_html, status="in_progress"):
    """
    Saves both the structured input (for reopening/reusing the case later) and
    the frozen rendered HTML (the archived artifact). rendered_html is never
    regenerated after this point, even if Blocks/templates change later.
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
                   SET preset_id = ?, status = ?, clinical_info = ?,
                       structured_input = ?, rendered_html = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE case_number = ?""",
                (preset_id, status, clinical_info, json.dumps(structured_input), rendered_html, case_number),
            )
        else:
            cursor.execute(
                """INSERT INTO Cases
                   (case_number, preset_id, status, clinical_info, structured_input, rendered_html)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (case_number, preset_id, status, clinical_info, json.dumps(structured_input), rendered_html),
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"Database error during save: {e}")
        return False
    finally:
        conn.close()