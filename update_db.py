import sqlite3
import json

def update_french_templates():
    conn = sqlite3.connect('pathology.db')
    cursor = conn.cursor()

    # Update Duodenum
    duo_micro = "Il s'agit d'une muqueuse duodénale dont les villosités sont de taille normale, tapissées par un épithélium régulier et bien différencié, sans hyperlymphocytose intraépithéliale. Le chorion est sans particularité.\nAbsence d'agent pathogène ou de signe de malignité."
    duo_conc = "Muqueuse duodénale normale."
    cursor.execute("UPDATE Templates SET default_micro = ?, default_conclusion = ? WHERE name = 'Duodenum'", (duo_micro, duo_conc))

    # Update Antrum
    antrum_micro = "Il s'agit d'une muqueuse antrale dont les cryptes sont régulières et bien différenciées, sans métaplasie intestinale. Le chorion interstitiel abrite un infiltrat inflammatoire polymorphe d'intensité {inflam}. Les glandes antrales sont en nombre normal."
    cursor.execute("UPDATE Templates SET default_micro = ? WHERE name = 'Antrum'", (antrum_micro,))

    # Update Fundus
    fundus_micro = "Il s'agit d'une muqueuse fundique dont les cryptes sont régulières et bien différenciées, sans métaplasie intestinale. Le chorion interstitiel abrite un infiltrat inflammatoire polymorphe d'intensité {inflam}. Les glandes fundiques sont en nombre normal."
    cursor.execute("UPDATE Templates SET default_micro = ? WHERE name = 'Fundus'", (fundus_micro,))

    conn.commit()
    conn.close()
    print("✅ Database updated with French medical text!")

if __name__ == "__main__":
    update_french_templates()
