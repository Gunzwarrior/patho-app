import sqlite3
import seed_data

DB_NAME = "pathology.db"


def setup_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")

    cursor.executescript("""
        DROP TABLE IF EXISTS Cases;
        DROP TABLE IF EXISTS Conclusion_Group_Labels;
        DROP TABLE IF EXISTS Preset_Block_Rows;
        DROP TABLE IF EXISTS Preset_Blocks;
        DROP TABLE IF EXISTS Presets;
        DROP TABLE IF EXISTS Block_Fields;
        DROP TABLE IF EXISTS Blocks;
        DROP TABLE IF EXISTS Fields;
        DROP TABLE IF EXISTS Snippets;
        DROP TABLE IF EXISTS Templates;
        DROP TABLE IF EXISTS Master_Templates;
    """)

    cursor.executescript("""
        CREATE TABLE Fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            type TEXT NOT NULL,
            options JSON,
            default_value TEXT,
            conclusion_addendum_template TEXT
        );

        CREATE TABLE Blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            is_table BOOLEAN DEFAULT 0,
            site_label TEXT,
            conclusion_group TEXT,
            macro_template TEXT,
            micro_template TEXT NOT NULL,
            conclusion_template TEXT NOT NULL,
            context_template TEXT,         -- optional: composed case/specimen-level
                                            -- sentence built from this block's own
                                            -- fields (e.g. "nodule lobaire gauche de
                                            -- 20 mm EUTIRADS 4"). NULL = block doesn't
                                            -- participate; behaves exactly as before
                                            -- this column existed. Destination depends
                                            -- on total_specimens, decided in workspace.py:
                                            -- 1 specimen -> auto-composed clinical-info
                                            -- box; 2+ specimens -> replaces this block's
                                            -- own numbered specimen header.
            title_fragment_template TEXT   -- optional: short fragment appended to
                                            -- Presets.default_title (e.g. "lobaire
                                            -- gauche"). Only used when total_specimens
                                            -- == 1 -- with 2+ specimens no single
                                            -- fragment can represent every specimen, so
                                            -- title stays at the static default. NULL =
                                            -- block doesn't participate.
        );

        CREATE TABLE Block_Fields (
            block_id INTEGER NOT NULL REFERENCES Blocks(id),
            field_id INTEGER NOT NULL REFERENCES Fields(id),
            sort_order INTEGER,
            label_override TEXT,
            default_override TEXT,
            PRIMARY KEY (block_id, field_id)
        );

        CREATE TABLE Presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            short_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT,
            default_adicap TEXT,
            default_title TEXT   -- optional: pre-fills the Workspace Title widget
                                  -- (e.g. "Cytologie thyroïdienne"). NULL falls back
                                  -- to Presets.name, same as today's behavior, so
                                  -- existing presets need no immediate update to
                                  -- keep working -- only to get a curated title.
        );

        CREATE TABLE Preset_Blocks (
            preset_id INTEGER NOT NULL REFERENCES Presets(id),
            block_id INTEGER NOT NULL REFERENCES Blocks(id),
            sort_order INTEGER,
            field_overrides JSON,
            PRIMARY KEY (preset_id, block_id, sort_order)
        );

        CREATE TABLE Preset_Block_Rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            preset_id INTEGER NOT NULL REFERENCES Presets(id),
            block_id INTEGER NOT NULL REFERENCES Blocks(id),
            sort_order INTEGER,
            field_overrides JSON
        );

        CREATE TABLE Snippets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shortcut TEXT NOT NULL UNIQUE,
            expansion TEXT NOT NULL,
            category TEXT
        );

        CREATE TABLE Cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_number TEXT NOT NULL UNIQUE,
            preset_id INTEGER REFERENCES Presets(id),
            status TEXT DEFAULT 'pending',
            pending_reason TEXT,
            clinical_info TEXT,
            structured_input JSON,
            rendered_html TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        );

        CREATE TABLE Conclusion_Group_Labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_key_set TEXT NOT NULL UNIQUE,
            combined_label TEXT NOT NULL
        );
    """)

    seed_data.seed_all(cursor)
    conn.commit()
    conn.close()
    print("Baseline DB built.")


if __name__ == "__main__":
    setup_database()