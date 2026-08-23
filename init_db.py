"""
PathoPilot — database schema (v2). Content (Fields/Blocks/Presets/Snippets)
lives in seed_data.py, not here — this file only defines table structure.

Running this DROPS and recreates all tables, then calls seed_data.seed_all()
to populate them.

⚠️ Destructive: wipes pathology.db and rebuilds from scratch. Cases are
never migrated across a schema rebuild — this script is for development,
not for a database holding real case history.
"""

import sqlite3
import seed_data

DB_NAME = "pathology.db"


def setup_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")

    print("Dropping old tables (if they exist)...")
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
        -- old (pre-v2) schema table names, dropped in case this runs
        -- against a database that predates the Fields/Blocks rebuild
        DROP TABLE IF EXISTS Templates;
        DROP TABLE IF EXISTS Master_Templates;
    """)

    print("Creating tables...")

    cursor.executescript("""
        -- FIELDS: atomic reusable inputs
        CREATE TABLE Fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            type TEXT NOT NULL,           -- 'text' | 'number' | 'select' | 'checkbox'
            options JSON,                 -- e.g. ["légère","modérée","sévère"]
            default_value TEXT,
            conclusion_addendum_template TEXT   -- optional Jinja2 template (context: {value}).
                                                 -- When set, this field's statement is removed
                                                 -- from every block's own conclusion_template and
                                                 -- instead rendered once, at the end of the whole
                                                 -- case's conclusion, from the value every block
                                                 -- using it agrees on. For facts that are really
                                                 -- case-level (e.g. overall H. pylori status) even
                                                 -- though captured per-specimen.
        );

        -- BLOCKS: one specimen/component type
        CREATE TABLE Blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            is_table BOOLEAN DEFAULT 0,
            site_label TEXT,               -- e.g. 'antrale', 'fundique' — the site-specific
                                            -- word a groupable conclusion_template substitutes
                                            -- via {{site_label}}. NULL for blocks that don't
                                            -- participate in site-word grouping.
            conclusion_group TEXT,         -- e.g. 'gastric', 'duodenum' — clinical category
                                            -- for conclusion sectioning: contiguous blocks
                                            -- sharing this value are visually clustered
                                            -- (blank line between different groups) and share
                                            -- one addendum placement. NULL = default bucket,
                                            -- behaves as if sectioning weren't in use at all.
            macro_template TEXT,           -- Jinja2 template for the macroscopic exam,
                                            -- rendered and combined with micro_template by
                                            -- rendering.render_block based on how many
                                            -- specimens are in the case: "Examen macroscopique"/
                                            -- "Examen microscopique" bold headers when this is
                                            -- the case's only specimen, no headers (just a
                                            -- blank line between them) when there are 2+.
                                            -- Intended to be set for every non-table block —
                                            -- NULL is only for a block with genuinely no macro
                                            -- content to state, not a per-block-type opt-out.
            micro_template TEXT NOT NULL,
            conclusion_template TEXT NOT NULL
        );

        -- BLOCK_FIELDS: which fields a block uses, with optional overrides
        CREATE TABLE Block_Fields (
            block_id INTEGER NOT NULL REFERENCES Blocks(id),
            field_id INTEGER NOT NULL REFERENCES Fields(id),
            sort_order INTEGER,
            label_override TEXT,
            default_override TEXT,
            PRIMARY KEY (block_id, field_id)
        );

        -- PRESETS: saved ordered Block lists, fast-access via short_code
        CREATE TABLE Presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            short_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT,
            default_adicap TEXT
        );

        -- PRESET_BLOCKS: which blocks a preset includes, in what order,
        -- with optional pre-filled field values (non-table blocks)
        CREATE TABLE Preset_Blocks (
            preset_id INTEGER NOT NULL REFERENCES Presets(id),
            block_id INTEGER NOT NULL REFERENCES Blocks(id),
            sort_order INTEGER,
            field_overrides JSON,
            PRIMARY KEY (preset_id, block_id, sort_order)
        );

        -- PRESET_BLOCK_ROWS: pre-filled row instances for is_table blocks
        -- (e.g. the 6 prostate sites, pre-ordered, per urologist)
        CREATE TABLE Preset_Block_Rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            preset_id INTEGER NOT NULL REFERENCES Presets(id),
            block_id INTEGER NOT NULL REFERENCES Blocks(id),
            sort_order INTEGER,
            field_overrides JSON
        );

        -- SNIPPETS: reusable text fragments, referenced by key from templates
        -- via {{ snippet('shortcut') }}, or used standalone as a text expander.
        CREATE TABLE Snippets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shortcut TEXT NOT NULL UNIQUE,
            expansion TEXT NOT NULL,
            category TEXT
        );

        -- CASES: structured input (reopen/reuse) + frozen rendered text (archive).
        -- 'pending' cases stay live drafts — reopening one re-renders from
        -- current templates, so a template fix since the case was started
        -- is reflected. 'validated' cases are frozen forever, exactly as
        -- originally rendered, never retroactively changed.
        CREATE TABLE Cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_number TEXT NOT NULL UNIQUE,
            preset_id INTEGER REFERENCES Presets(id),
            status TEXT DEFAULT 'pending',   -- 'pending' | 'validated'
            pending_reason TEXT,             -- e.g. 'IHC', 'Niveaux', 'Avis', 'Colo', 'Autre' —
                                              -- free text by convention, not schema-enforced,
                                              -- so a new reason never needs a migration.
                                              -- NULL once validated.
            clinical_info TEXT,
            structured_input JSON,
            rendered_html TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        );

        -- CONCLUSION_GROUP_LABELS: French combined labels for the grouping engine
        CREATE TABLE Conclusion_Group_Labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_key_set TEXT NOT NULL UNIQUE,   -- sorted, comma-joined: 'antrum,fundus'
            combined_label TEXT NOT NULL          -- 'antrale et fundique'
        );
    """)

    seed_data.seed_all(cursor)

    conn.commit()
    conn.close()
    print("✅ Database rebuilt on v2 schema. 'pathology.db' is ready.")


if __name__ == "__main__":
    setup_database()