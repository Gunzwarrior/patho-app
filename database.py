import sqlite3
import pandas as pd

DB_NAME = 'pathology.db'

def get_db_connection():
    return sqlite3.connect(DB_NAME)

def load_table_as_df(table_name):
    """Loads a full database table into a Pandas DataFrame for display."""
    conn = get_db_connection()
    df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
    conn.close()
    return df

def get_template_details(template_name):
    """Fetches the raw default texts and type for a specific block template."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT type, default_micro, default_conclusion FROM Templates WHERE name = ?", (template_name,))
    result = cursor.fetchone()
    conn.close()
    return result

def get_master_template_sequence(master_name):
    """Fetches the sequence of blocks tied to a master protocol layout."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT template_sequence FROM Master_Templates WHERE name = ?", (master_name,))
    result = cursor.fetchone()
    conn.close()
    return result

def get_all_master_template_names():
    """Fetches all protocol names available for the UI dropdown selection."""
    conn = get_db_connection()
    names = [row[0] for row in conn.cursor().execute("SELECT name FROM Master_Templates").fetchall()]
    conn.close()
    return names

def save_case(case_id, protocol, html_content):
    """Inserts or updates a case report record in the archive table."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM Cases WHERE case_number = ?", (case_id,))
        if cursor.fetchone():
            cursor.execute("""
                UPDATE Cases 
                SET master_template_name = ?, final_microscopy = ?, status = 'Saved' 
                WHERE case_number = ?
            """, (protocol, html_content, case_id))
        else:
            cursor.execute("""
                INSERT INTO Cases (case_number, master_template_name, final_microscopy, status)
                VALUES (?, ?, ?, 'Saved')
            """, (case_id, protocol, html_content))
        conn.commit()
        return True
    except Exception as e:
        print(f"Database error during save: {e}")
        return False
    finally:
        conn.close()