"""
PathoPilot — database migration / initialization script (v2 schema).

Replaces the old flat 'Templates' / 'Master_Templates' model with the
Fields / Blocks / Presets architecture. Running this DROPS and recreates
all tables, then seeds them with a translation of the current "Gastric
Trio" data so the app has one working, fully migrated case type to
validate the new engine against.

⚠️ Destructive: wipes pathology.db and rebuilds from scratch. Any real
saved Cases from the old schema are not migrated (there weren't any
structured ones worth carrying over — the old Cases table only ever
stored rendered HTML, never structured_input).
"""

import sqlite3
import json

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
        -- old schema table names, dropped in case this runs against an old db
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

    print("Seeding Fields...")
    hp_addendum = (
        "{% if value %}Présence d'une infection à hélicobacter pylori."
        "{% else %}Absence d'hélicobacter pylori.{% endif %}"
    )
    fields = [
        # key, label, type, options(json or None), default_value, conclusion_addendum_template
        ("fragments", "Fragments", "number", None, "1", None),
        ("inflammation_intensity", "Inflammation", "select",
         json.dumps(["légère", "modérée", "sévère"]), "modérée", None),
        ("is_normal", "Normal ?", "checkbox", None, "1", None),
        ("hp_positive", "Helicobacter pylori positif", "checkbox", None, "1", hp_addendum),
    ]
    cursor.executemany(
        "INSERT INTO Fields (key, label, type, options, default_value, conclusion_addendum_template) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        fields,
    )

    def field_id(key):
        cursor.execute("SELECT id FROM Fields WHERE key = ?", (key,))
        return cursor.fetchone()[0]

    print("Seeding Blocks (Duodenum, Antrum, Fundus)...")

    duodenum_micro = (
        "{{fragment_text}}\n\n"
        "{% if is_normal %}"
        "Il s'agit d'une muqueuse duodénale dont les villosités sont de taille normale, "
        "tapissées par un épithélium régulier et bien différencié, sans hyperlymphocytose "
        "intraépithéliale. Le chorion est sans particularité.\n"
        "Absence d'agent pathogène ou de signe de malignité."
        "{% else %}"
        "Anomalie détectée."
        "{% endif %}"
    )
    duodenum_conc = (
        "{% if is_normal %}Muqueuse duodénale normale.{% else %}Duodénite.{% endif %}"
    )

    antrum_micro = (
        "{{fragment_text}}\n\n"
        "Il s'agit d'une muqueuse antrale dont les cryptes sont régulières et bien "
        "différenciées, sans métaplasie intestinale. Le chorion interstitiel abrite un "
        "infiltrat inflammatoire polymorphe d'intensité {{inflammation_intensity}}. "
        "Les glandes antrales sont en nombre normal.\n"
        "{% if hp_positive %}"
        "Présence d'éléments ayant la morphologie d'Helicobacter pylori en HES.\n\n"
        "Etude immunohistochimique :\n- HP : positif"
        "{% else %}"
        "Absence d'Helicobacter pylori en HES.\n\n"
        "Etude immunohistochimique :\n- HP : négatif"
        "{% endif %}"
    )
    # HP status is a case-level fact (see hp_positive.conclusion_addendum_template),
    # not a per-block one — deliberately not mentioned here, even though the
    # field is captured per-block for the HES/immunohisto findings above.
    antrum_conc = (
        "Gastrite chronique interstitielle {{site_label}} {{inflammation_intensity}}, active, "
        "sans métaplasie intestinale ni atrophie glandulaire."
    )

    fundus_micro = (
        "{{fragment_text}}\n\n"
        "Il s'agit d'une muqueuse fundique dont les cryptes sont régulières et bien "
        "différenciées, sans métaplasie intestinale. Le chorion interstitiel abrite un "
        "infiltrat inflammatoire polymorphe d'intensité {{inflammation_intensity}}. "
        "Les glandes fundiques sont en nombre normal.\n"
        "{% if hp_positive %}"
        "Présence d'éléments ayant la morphologie d'Helicobacter pylori en HES."
        "{% else %}"
        "Absence d'Helicobacter pylori en HES."
        "{% endif %}"
    )
    fundus_conc = (
        "Gastrite chronique interstitielle {{site_label}} {{inflammation_intensity}}, active, "
        "sans métaplasie intestinale ni atrophie glandulaire."
    )

    blocks = [
        # key, name, is_table, site_label, conclusion_group, micro_template, conclusion_template
        ("duodenum", "Duodenum", 0, None, "duodenum", duodenum_micro, duodenum_conc),
        ("antrum", "Antrum", 0, "antrale", "gastric", antrum_micro, antrum_conc),
        ("fundus", "Fundus", 0, "fundique", "gastric", fundus_micro, fundus_conc),
    ]
    cursor.executemany(
        "INSERT INTO Blocks (key, name, is_table, site_label, conclusion_group, micro_template, conclusion_template) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        blocks,
    )

    def block_id(key):
        cursor.execute("SELECT id FROM Blocks WHERE key = ?", (key,))
        return cursor.fetchone()[0]

    print("Wiring Block_Fields...")
    block_fields = [
        # block_key, field_key, sort_order, label_override, default_override
        ("duodenum", "fragments", 0, None, "2"),
        ("duodenum", "is_normal", 1, None, None),
        ("antrum", "fragments", 0, None, "3"),
        ("antrum", "inflammation_intensity", 1, None, None),
        ("antrum", "hp_positive", 2, None, None),
        ("fundus", "fragments", 0, None, "1"),
        ("fundus", "inflammation_intensity", 1, None, None),
        ("fundus", "hp_positive", 2, None, None),
    ]
    cursor.executemany(
        "INSERT INTO Block_Fields (block_id, field_id, sort_order, label_override, default_override) "
        "VALUES (?, ?, ?, ?, ?)",
        [(block_id(b), field_id(f), so, lo, do) for b, f, so, lo, do in block_fields],
    )

    print("Seeding Preset 'Gastric Trio'...")
    cursor.execute(
        "INSERT INTO Presets (short_code, name, category, default_adicap) VALUES (?, ?, ?, ?)",
        ("gt", "Gastric Trio", "digestif", "ADICAP_GAST1"),
    )
    preset_id = cursor.lastrowid

    cursor.executemany(
        "INSERT INTO Preset_Blocks (preset_id, block_id, sort_order, field_overrides) VALUES (?, ?, ?, ?)",
        [
            (preset_id, block_id("duodenum"), 0, None),
            (preset_id, block_id("antrum"), 1, None),
            (preset_id, block_id("fundus"), 2, None),
        ],
    )

    print("Seeding Conclusion_Group_Labels...")
    cursor.execute(
        "INSERT INTO Conclusion_Group_Labels (block_key_set, combined_label) VALUES (?, ?)",
        ("antrum,fundus", "antro-fundique"),
    )

    print("Seeding Snippets...")
    cursor.execute(
        "INSERT INTO Snippets (shortcut, expansion, category) VALUES (?, ?, ?)",
        ("hp+", "<b>Présence d'éléments ayant la morphologie d'Helicobacter pylori.</b>", "digestif"),
    )

    conn.commit()
    conn.close()
    print("✅ Database rebuilt on v2 schema. 'pathology.db' is ready.")


if __name__ == "__main__":
    setup_database()