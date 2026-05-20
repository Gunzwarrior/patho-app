import sqlite3
import json

def setup_database():
    # This creates the file 'pathology.db' in your folder
    conn = sqlite3.connect('pathology.db')
    cursor = conn.cursor()

    print("Dropping old tables (if they exist)...")
    cursor.executescript("""
        DROP TABLE IF EXISTS Cases;
        DROP TABLE IF EXISTS Master_Templates;
        DROP TABLE IF EXISTS Templates;
        DROP TABLE IF EXISTS Snippets;
    """)

    print("Creating tables...")
    
    # 1. TEMPLATES (The individual blocks/specimens)
    cursor.execute("""
        CREATE TABLE Templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL, -- e.g., 'Smart', 'Text'
            default_micro TEXT,
            default_macro TEXT,
            default_conclusion TEXT
        )
    """)

    # 2. MASTER TEMPLATES (The Kits/Protocols)
    cursor.execute("""
        CREATE TABLE Master_Templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            organ_system TEXT,
            default_adicap TEXT,
            template_sequence JSON -- e.g., ["Antrum", "Fundus"]
        )
    """)

    # 3. SNIPPETS (The Text Expander)
    cursor.execute("""
        CREATE TABLE Snippets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shortcut TEXT NOT NULL UNIQUE,
            expansion TEXT NOT NULL,
            category TEXT
        )
    """)

    # 4. CASES (Your daily work archive)
    cursor.execute("""
        CREATE TABLE Cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_number TEXT NOT NULL UNIQUE,
            master_template_name TEXT,
            ui_state JSON,
            final_clinical TEXT,
            final_microscopy TEXT,
            final_conclusion TEXT,
            is_locked BOOLEAN DEFAULT 0,
            status TEXT DEFAULT 'Draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    print("Injecting sample data...")
    
    # Insert Dummy Templates
    cursor.execute("INSERT INTO Templates (name, type, default_micro) VALUES ('Antrum', 'Smart', 'Mucosa of antral type.')")
    cursor.execute("INSERT INTO Templates (name, type, default_micro) VALUES ('Fundus', 'Smart', 'Mucosa of fundic type.')")
    cursor.execute("INSERT INTO Templates (name, type, default_micro) VALUES ('Duodenum', 'Smart', 'Normal villous architecture.')")

    # Insert a Dummy Master Template (The Kit)
    gastric_sequence = json.dumps(["Duodenum", "Antrum", "Fundus"])
    cursor.execute("INSERT INTO Master_Templates (name, organ_system, default_adicap, template_sequence) VALUES ('Gastric Trio', 'Digestive', 'ADICAP_GAST1', ?)", (gastric_sequence,))

    # Insert a Dummy Snippet
    cursor.execute("INSERT INTO Snippets (shortcut, expansion, category) VALUES ('hp+', '<b>Presence of Helicobacter Pylori.</b>', 'Digestive')")

    conn.commit()
    conn.close()
    print("✅ Database setup complete! 'pathology.db' is ready.")

if __name__ == "__main__":
    setup_database()
