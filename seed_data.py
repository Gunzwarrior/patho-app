"""
PathoPilot — seed content (Fields, Blocks, Presets, Snippets,
Conclusion_Group_Labels). Schema/table definitions live in init_db.py, not
here — this file is pure content.

Organized by case type: one seed_xxx(cursor) function per case type, plus
seed_shared_snippets(cursor) for phrases reused across otherwise-unrelated
Blocks. Add a new case type by adding a new function here and calling it
from seed_all() — no schema changes needed for ordinary content.
"""

import json


def field_id(cursor, key):
    cursor.execute("SELECT id FROM Fields WHERE key = ?", (key,))
    return cursor.fetchone()[0]


def block_id(cursor, key):
    cursor.execute("SELECT id FROM Blocks WHERE key = ?", (key,))
    return cursor.fetchone()[0]


def seed_gastric_trio(cursor):
    print("Seeding Fields (Gastric Trio)...")
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

    print("Wiring Block_Fields (Gastric Trio)...")
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
        [(block_id(cursor, b), field_id(cursor, f), so, lo, do) for b, f, so, lo, do in block_fields],
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
            (preset_id, block_id(cursor, "duodenum"), 0, None),
            (preset_id, block_id(cursor, "antrum"), 1, None),
            (preset_id, block_id(cursor, "fundus"), 2, None),
        ],
    )

    print("Seeding Conclusion_Group_Labels (Gastric Trio)...")
    cursor.execute(
        "INSERT INTO Conclusion_Group_Labels (block_key_set, combined_label) VALUES (?, ?)",
        ("antrum,fundus", "antro-fundique"),
    )

    cursor.execute(
        "INSERT INTO Snippets (shortcut, expansion, category) VALUES (?, ?, ?)",
        ("hp+", "<b>Présence d'éléments ayant la morphologie d'Helicobacter pylori.</b>", "digestif"),
    )


def seed_gallbladder(cursor):
    print("Seeding Fields (Gallbladder)...")
    fields = [
        # key, label, type, options(json or None), default_value, conclusion_addendum_template
        ("inflammation_type", "Inflammation", "select", json.dumps(["chronique", "aigue"]), "chronique", None),
        ("cholesterolosis", "Cholestérolose", "checkbox", None, "0", None),
        ("lithiasis", "Lithiase", "checkbox", None, "0", None),
        ("specimen_size_cm", "Taille (cm)", "decimal", None, "8", None),
        ("specimen_state", "État", "select", json.dumps(["fermée", "ouverte", "fragmentée"]), "fermée", None),
    ]
    cursor.executemany(
        "INSERT INTO Fields (key, label, type, options, default_value, conclusion_addendum_template) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        fields,
    )

    print("Seeding Block (Vésicule biliaire)...")
    # {{ snippet('absence_malignite') }} demonstrates the new snippet
    # mechanism: this exact phrase already showed up verbatim across six
    # unrelated block types in an earlier sample document, and now a
    # seventh (gallbladder) — a real, repeated case for the DRY phrase
    # living in one place rather than being retyped into every template.
    #
    # Macro content (size/state) is prepended directly to micro_template,
    # not a separate template slot — same technique fragment_text already
    # uses for gastric blocks, and matches how the real reports fold this
    # into the same numbered specimen entry rather than a distinct
    # "MACROSCOPIE:" section. lithiasis is reused here from the block's own
    # conclusion-driving field, not duplicated — one atomic Field, two
    # templates referencing it.
    gb_macro = (
        "{% if specimen_state == \"fragmentée\" %}"
        "Vésicule biliaire reçue fragmentée, le plus grand fragment mesurant {{specimen_size_cm}} cm."
        "{% else %}"
        "Vésicule biliaire mesurant {{specimen_size_cm}} cm de grand axe, reçue {{specimen_state}}."
        "{% endif %}"
        "{% if lithiasis %} Présence de calculs à la coupe.{% endif %}"
    )
    # Descriptive prose (from "La muqueuse..." onward) is one continuous
    # paragraph, sentences separated by single spaces — matches the real
    # sample and fixes the cholesterolosis blank-line bug (its trailing
    # separator now lives *inside* the conditional, so an omitted sentence
    # leaves no trace instead of an unconditional \n\n). The macro→micro
    # transition keeps its own \n\n on purpose: that boundary is the one
    # PROGRESS.md earmarks for a real "Examen macroscopique"/"Examen
    # microscopique" header split later, and it already matches the
    # fold pattern used for Gastric Trio's fragment_text line.
    gb_micro = (
        gb_macro + "\n\n"
        "La muqueuse est faite de franges tapissées par un épithélium cylindrique, "
        "régulier et bien différencié."
        "{% if cholesterolosis %} Leurs axes comportent des macrophages spumeux.{% endif %}"
        " {% if inflammation_type == \"chronique\" %}"
        "Le chorion abrite un infiltrat inflammatoire mononucléé. Cet infiltrat s'étend "
        "jusqu'à la musculeuse et la sous-séreuse."
        "{% else %}"
        "Il existe un infiltrat inflammatoire polymorphe riche en polynucléaires "
        "neutrophiles, atteignant toutes les couches de la paroi vésiculaire."
        "{% endif %}"
        "\n{{ snippet('absence_malignite') }}"
    )
    # The diagnosis here is exactly the "derived field" pattern an outside
    # conversation proposed as new architecture — but it's just Jinja2
    # conditionals combining several Fields, which conclusion_template
    # already supports directly. No new mechanism needed.
    gb_conc = (
        "{% if inflammation_type == \"chronique\" %}Cholécystite chronique"
        "{% else %}Cholécystite aiguë développée sur des lésions chroniques{% endif %}"
        "{% if lithiasis %} lithiasique{% endif %}"
        "{% if cholesterolosis %} avec cholestérolose{% endif %}."
    )

    cursor.execute(
        "INSERT INTO Blocks (key, name, is_table, site_label, conclusion_group, micro_template, conclusion_template) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("vesicule_biliaire", "Vésicule biliaire", 0, None, "vesicule_biliaire", gb_micro, gb_conc),
    )

    print("Wiring Block_Fields (Gallbladder)...")
    block_fields = [
        # field_key, sort_order, label_override, default_override
        ("specimen_size_cm", 0, None, None),
        ("specimen_state", 1, None, None),
        ("inflammation_type", 2, None, None),
        ("cholesterolosis", 3, None, None),
        ("lithiasis", 4, None, None),
    ]
    cursor.executemany(
        "INSERT INTO Block_Fields (block_id, field_id, sort_order, label_override, default_override) "
        "VALUES (?, ?, ?, ?, ?)",
        [(block_id(cursor, "vesicule_biliaire"), field_id(cursor, f), so, lo, do)
         for f, so, lo, do in block_fields],
    )

    print("Seeding Preset 'Vésicule biliaire'...")
    cursor.execute(
        "INSERT INTO Presets (short_code, name, category, default_adicap) VALUES (?, ?, ?, ?)",
        ("vb", "Vésicule biliaire", "digestif", None),
    )
    preset_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO Preset_Blocks (preset_id, block_id, sort_order, field_overrides) VALUES (?, ?, ?, ?)",
        (preset_id, block_id(cursor, "vesicule_biliaire"), 0, None),
    )


def seed_appendix(cursor):
    print("Seeding Fields (Appendix)...")
    fields = [
        # key, label, type, options(json or None), default_value, conclusion_addendum_template
        ("appendicite_type", "Type d'appendicite", "select",
         json.dumps(["endo", "suppuree", "periappendicite", "phlegmoneuse", "gangreneuse", "intervalle"]),
         "endo", None),
        ("appendix_size_cm", "Taille (cm)", "decimal", None, "8", None),
        ("false_membranes", "Fausses membranes", "checkbox", None, "0", None),
    ]
    cursor.executemany(
        "INSERT INTO Fields (key, label, type, options, default_value, conclusion_addendum_template) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        fields,
    )

    print("Seeding Block (Appendice)...")
    # One field drives both outputs directly — no combinatorics needed here,
    # unlike Gallbladder. Each severity level maps to one micro paragraph
    # and one conclusion sentence, via parallel if/elif chains keyed on the
    # same field. {{ snippet('absence_malignite') }} reused a third time now.
    # Macro (size/false membranes) prepended to micro_template, same
    # technique as Gallbladder and fragment_text before it.
    appendix_macro = (
        "Il s'agit d'un appendice mesurant {{appendix_size_cm}} cm de longueur."
        "{% if false_membranes %} Présence de fausses membranes.{% endif %}"
    )
    appendix_micro = (
        appendix_macro + "\n\n"
        "{% if appendicite_type == \"endo\" %}"
        "La muqueuse appendiculaire est focalement ulcérée et abrite de rares foyers inflammatoires "
        "polymorphes avec des polynucléaires neutrophiles. La musculeuse et la séreuse sont sans particularité."
        "{% elif appendicite_type == \"suppuree\" %}"
        "La muqueuse appendiculaire est focalement ulcérée et remplacée par un enduit fibrino-leucocytaire "
        "avec des polynucléaires altérés. Il existe un infiltrat inflammatoire polymorphe riche en "
        "polynucléaires neutrophiles, parfois altérés, intéressant la muqueuse et la musculeuse. "
        "La séreuse est sans particularité."
        "{% elif appendicite_type == \"periappendicite\" %}"
        "La muqueuse appendiculaire est focalement ulcérée et remplacée par un enduit fibrino-leucocytaire "
        "avec des polynucléaires altérés. Il existe un infiltrat inflammatoire polymorphe riche en "
        "polynucléaires neutrophiles, parfois altérés, intéressant toutes les couches de l'appendice. "
        "La séreuse est par endroits recouverte d'un enduit fibrino-leucocytaire."
        "{% elif appendicite_type == \"phlegmoneuse\" %}"
        "Il s'agit d'un appendice dont la muqueuse est ulcérée. Il existe un infiltrat inflammatoire "
        "polymorphe, riche en polynucléaires neutrophiles altérés et des foyers de nécrose intéressant "
        "toute la paroi appendiculaire. La séreuse est congestive et recouverte d'un enduit fibrino-leucocytaire."
        "{% elif appendicite_type == \"gangreneuse\" %}"
        "Il s'agit d'un appendice dont la muqueuse est largement ulcérée. Il existe un infiltrat "
        "inflammatoire polymorphe, riche en polynucléaires neutrophiles altérés, des foyers de nécrose "
        "ischémique intéressant toute la paroi appendiculaire et des thromboses vasculaires. "
        "La séreuse est recouverte d'un enduit fibrino-leucocytaire."
        "{% elif appendicite_type == \"intervalle\" %}"
        "La muqueuse appendiculaire comporte quelques cryptites et distorsions cryptiques. Il existe une "
        "fibrose avec inflammation chronique transmurale et des agrégats lymphoïdes."
        "{% endif %}"
        "\n{{ snippet('absence_malignite') }}"
    )
    appendix_conc = (
        "{% if appendicite_type == \"endo\" %}Endo-appendicite aiguë."
        "{% elif appendicite_type == \"suppuree\" %}Appendicite aiguë suppurée."
        "{% elif appendicite_type == \"periappendicite\" %}Appendicite aiguë suppurée avec péri-appendicite."
        "{% elif appendicite_type == \"phlegmoneuse\" %}Appendicite aiguë phlegmoneuse avec péri-appendicite."
        "{% elif appendicite_type == \"gangreneuse\" %}Appendicite aiguë gangreneuse avec péri-appendicite."
        "{% elif appendicite_type == \"intervalle\" %}Appendicite d'intervalle."
        "{% endif %}"
    )

    cursor.execute(
        "INSERT INTO Blocks (key, name, is_table, site_label, conclusion_group, micro_template, conclusion_template) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("appendice", "Appendice", 0, None, "appendice", appendix_micro, appendix_conc),
    )

    block_fields = [
        # field_key, sort_order, label_override, default_override
        ("appendix_size_cm", 0, None, None),
        ("false_membranes", 1, None, None),
        ("appendicite_type", 2, None, None),
    ]
    cursor.executemany(
        "INSERT INTO Block_Fields (block_id, field_id, sort_order, label_override, default_override) "
        "VALUES (?, ?, ?, ?, ?)",
        [(block_id(cursor, "appendice"), field_id(cursor, f), so, lo, do) for f, so, lo, do in block_fields],
    )

    print("Seeding Preset 'Appendice' (dai)...")
    cursor.execute(
        "INSERT INTO Presets (short_code, name, category, default_adicap) VALUES (?, ?, ?, ?)",
        ("dai", "Appendice", "digestif", None),
    )
    preset_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO Preset_Blocks (preset_id, block_id, sort_order, field_overrides) VALUES (?, ?, ?, ?)",
        (preset_id, block_id(cursor, "appendice"), 0, None),
    )


def seed_cytology_macro_fields(cursor):
    """
    Fields for the generic cytology macro pattern (volume, color, spread
    slides) — explicitly designed for reuse across any future cytology
    Block, not just Thyroid. Seeded once here; a future cytology Block
    references these same Field rows via its own Block_Fields entries,
    rather than each cytology type re-creating its own copies.
    """
    print("Seeding shared cytology macro Fields...")
    fields = [
        ("liquid_volume_ml", "Volume (mL)", "decimal", None, "5", None),
        ("liquid_color", "Couleur", "select", json.dumps(["clair", "hémorragique", "autre"]), "clair", None),
        ("spread_slides_sent", "Lames étalées reçues", "checkbox", None, "0", None),
    ]
    cursor.executemany(
        "INSERT INTO Fields (key, label, type, options, default_value, conclusion_addendum_template) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        fields,
    )


def seed_thyroid_cytology(cursor):
    print("Seeding Field (Thyroid Cytology)...")
    cursor.execute(
        "INSERT INTO Fields (key, label, type, options, default_value, conclusion_addendum_template) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "thyroid_cytology_pattern", "Aspect cytologique", "select",
            json.dumps(["etc0", "etc1", "etc2", "etc3", "etc5"]),
            "etc2", None,
        ),
    )

    print("Seeding Block (Cytologie thyroïdienne)...")
    # Macro (volume/color/spread slides) prepended to micro_template using
    # the shared cytology Fields, same technique as Gallbladder/Appendix.
    cytology_macro = (
        "Liquide {{liquid_color}} de {{liquid_volume_ml}} mL."
        "{% if spread_slides_sent %} Lames étalées reçues.{% endif %}"
    )
    thyroid_micro = (
        cytology_macro + "\n\n"
        "{% if thyroid_cytology_pattern == \"etc0\" %}"
        "Il existe sur un fond modérément hématique, de la colloïde, des macrophages et des éléments "
        "figurés du sang.\n"
        "Absence de cellule thyréocytaire visible.\n"
        "Absence de cellule suspecte de malignité."
        "{% elif thyroid_cytology_pattern == \"etc1\" %}"
        "Il existe sur un fond modérément hématique, de la colloïde, des macrophages et de très rares "
        "cellules thyroïdiennes régulières, groupées en petits amas.\n"
        "Absence d'atypie cytonucléaire ou de formation papillaire.\n"
        "Absence de cellule suspecte de malignité."
        "{% elif thyroid_cytology_pattern == \"etc2\" %}"
        "Il existe sur un fond modérément hématique, de la colloïde, des macrophages et des cellules "
        "thyroïdiennes régulières, groupées en petits amas et vésicules.\n"
        "Absence d'atypie cytonucléaire ou de formation papillaire.\n"
        "Absence de cellule suspecte de malignité."
        "{% elif thyroid_cytology_pattern == \"etc3\" %}"
        "Il existe sur un fond modérément hématique, de la colloïde, des macrophages et d'assez "
        "nombreuses cellules thyroïdiennes parfois augmentées de taille avec des noyaux irréguliers, "
        "groupées en amas.\n"
        "Absence de tassement, d'incisure, d'inclusion nucléaire ou de formation papillaire."
        "{% elif thyroid_cytology_pattern == \"etc5\" %}"
        "Il existe sur un fond modérément hématique, de la colloïde, des macrophages et de nombreuses "
        "cellules thyroïdiennes aux noyaux augmentés de taille, irréguliers, tassés, incisurés, "
        "groupées en amas.\n"
        "Absence d'inclusion nucléaire ou de formation papillaire."
        "{% endif %}"
    )
    thyroid_conc = (
        "{% if thyroid_cytology_pattern == \"etc0\" %}"
        "Matériel non satisfaisant pour le diagnostic (absence de matériel thyréocytaire).\n"
        "Classification de Bethesda : I – Non diagnostique."
        "{% elif thyroid_cytology_pattern == \"etc1\" %}"
        "Matériel non satisfaisant pour le diagnostic (matériel thyréocytaire insuffisant).\n"
        "Classification de Bethesda : I – Non diagnostique."
        "{% elif thyroid_cytology_pattern == \"etc2\" %}"
        "Matériel satisfaisant pour le diagnostic.\n"
        "Classification de Bethesda : II – Bénin, compatible avec un adénome vésiculaire."
        "{% elif thyroid_cytology_pattern == \"etc3\" %}"
        "Matériel satisfaisant pour le diagnostic.\n"
        "Classification de Bethesda : III – Atypies de signification indéterminée."
        "{% elif thyroid_cytology_pattern == \"etc5\" %}"
        "Matériel satisfaisant pour le diagnostic.\n"
        "Classification de Bethesda : V – Suspect de malignité."
        "{% endif %}"
    )

    cursor.execute(
        "INSERT INTO Blocks (key, name, is_table, site_label, conclusion_group, micro_template, conclusion_template) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("thyroid_cytology", "Cytologie thyroïdienne", 0, None, "thyroid_cytology", thyroid_micro, thyroid_conc),
    )
    block_fields = [
        # field_key, sort_order, label_override, default_override
        ("liquid_volume_ml", 0, None, None),
        ("liquid_color", 1, None, None),
        ("spread_slides_sent", 2, None, None),
        ("thyroid_cytology_pattern", 3, None, None),
    ]
    cursor.executemany(
        "INSERT INTO Block_Fields (block_id, field_id, sort_order, label_override, default_override) "
        "VALUES (?, ?, ?, ?, ?)",
        [(block_id(cursor, "thyroid_cytology"), field_id(cursor, f), so, lo, do) for f, so, lo, do in block_fields],
    )

    print("Seeding Presets 'etc0'-'etc5' (fast-access, pre-filled Bethesda category)...")
    thy_block_id = block_id(cursor, "thyroid_cytology")
    presets = [
        ("etc0", "Cytologie thyroïdienne — non diagnostique (absence)"),
        ("etc1", "Cytologie thyroïdienne — non diagnostique (insuffisant)"),
        ("etc2", "Cytologie thyroïdienne — bénin"),
        ("etc3", "Cytologie thyroïdienne — atypies indéterminées"),
        ("etc5", "Cytologie thyroïdienne — suspect malignité"),
    ]
    for short_code, name in presets:
        cursor.execute(
            "INSERT INTO Presets (short_code, name, category, default_adicap) VALUES (?, ?, ?, ?)",
            (short_code, name, "endocrinien", None),
        )
        preset_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO Preset_Blocks (preset_id, block_id, sort_order, field_overrides) VALUES (?, ?, ?, ?)",
            (preset_id, thy_block_id, 0, json.dumps({"thyroid_cytology_pattern": short_code})),
        )


def seed_shared_snippets(cursor):
    """Phrases reused verbatim across otherwise-unrelated Blocks — the
    common case for a genuine Snippet, as opposed to Field-driven variation
    within one Block's own template."""
    print("Seeding shared Snippets...")
    cursor.execute(
        "INSERT INTO Snippets (shortcut, expansion, category) VALUES (?, ?, ?)",
        ("absence_malignite", "Absence de signe de malignité.", "general"),
    )


def seed_all(cursor):
    seed_gastric_trio(cursor)
    seed_gallbladder(cursor)
    seed_appendix(cursor)
    seed_cytology_macro_fields(cursor)
    seed_thyroid_cytology(cursor)
    seed_shared_snippets(cursor)