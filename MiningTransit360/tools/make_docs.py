# -*- coding: utf-8 -*-
"""Genere docs/02_MODELE_DE_DONNEES.md et docs/07_ETATS_CATALOGUE.md a partir des sources."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from schema import TABLES, MODULES, PARAMS, PROFILES, all_fields, parse_opts, default_rights  # noqa: E402
from demo_data import generate  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TYPES = {"ID": "Identifiant (auto)", "TXT": "Texte", "MEMO": "Texte long", "INT": "Entier", "NUM": "Décimal", "MNY": "Montant GNF",
         "PCT": "Pourcentage", "DATE": "Date", "TIME": "Heure", "BOOL": "Oui/Non", "LIST": "Liste", "FK": "Référence", "FILE": "Fichier"}


def data_model():
    data = generate()
    out = ["# 02 - Modèle de données", "",
           "Document généré automatiquement par `tools/make_docs.py` à partir de `tools/schema.py` (source unique de vérité).", "",
           "Chaque table est un tableau Excel structuré (ListObject) nommé `T_...`, placé sur une feuille masquée `DB_...` "
           "(données métier) ou `SYS_...` (système). La clé technique `ID` est attribuée par la table de compteurs `T_SEQ`. "
           "Les champs `CreePar / CreeLe / ModifPar / ModifLe` sont renseignés automatiquement sur les tables auditées.", "",
           "## Vue d'ensemble", "", "| Table | Feuille | Rôle | Champs | Lignes démo |", "|---|---|---|---:|---:|"]
    for t in TABLES:
        out.append("| `%s` | %s | %s | %d | %d |" % (t["name"], t["sheet"], t["label"], len(all_fields(t)), len(data[t["name"]])))
    out += ["", "## Relations (clés étrangères)", "", "```"]
    for t in TABLES:
        for f in t["fields"]:
            if f[2] == "FK":
                tgt = parse_opts(f[3])["fk"]
                out.append("%-18s.%-16s -> %s" % (t["name"], f[0], tgt.split(".")[0] + ".ID (affiché : " + tgt.split(".")[1] + ")"))
    out += ["```", "", "## Détail des tables", ""]
    for t in TABLES:
        out += ["### `%s` - %s" % (t["name"], t["label"]), "", "| Champ | Libellé | Type | Règles |", "|---|---|---|---|"]
        for f in all_fields(t):
            o = parse_opts(f[3])
            rules = []
            if o.get("req"):
                rules.append("obligatoire")
            if o.get("uniq"):
                rules.append("unique")
            if o.get("ro"):
                rules.append("calculé / système")
            if "min" in o:
                rules.append("≥ " + o["min"])
            if "def" in o:
                rules.append("défaut : " + o["def"])
            if "vals" in o:
                rules.append("valeurs : " + o["vals"].replace("|", ", "))
            if "fk" in o:
                rules.append("→ " + o["fk"])
            if "seq" in o:
                rules.append("numérotation %s-AAAA-nnnnn" % o["seq"])
            out.append("| `%s` | %s | %s | %s |" % (f[0], f[1], TYPES.get(f[2], f[2]), "; ".join(rules)))
        out.append("")
    out += ["## Registre des modules (menu)", "", "| Clé | Titre | Catégorie | Table | Actions |", "|---|---|---|---|---|"]
    for m in MODULES:
        out.append("| %s | %s | %s | %s | %s |" % (m[0], m[1], m[2], m[4] or "-", m[6].replace("|", ", ") or "-"))
    out += ["", "## Matrice des droits par défaut", "", "L lire, C créer, M modifier, V valider, S supprimer, I imprimer/exporter.", "",
            "| Module | " + " | ".join(PROFILES) + " |", "|---|" + "---|" * len(PROFILES)]
    for m in MODULES:
        out.append("| %s | %s |" % (m[1], " | ".join(default_rights(p, m[0]) or "-" for p in PROFILES)))
    out += ["", "## Paramètres", "", "| Clé | Valeur par défaut | Catégorie | Description |", "|---|---|---|---|"]
    for p in PARAMS:
        out.append("| `%s` | %s | %s | %s |" % (p[0], p[1] or "(vide)", p[3], p[4]))
    with open(os.path.join(ROOT, "docs", "02_MODELE_DE_DONNEES.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def reports():
    src = open(os.path.join(ROOT, "src", "vba", "modules", "mRpt_Catalog.bas"), encoding="utf-8").read()
    rx = re.compile(r'c\.Add Array\("(\w+)", "([^"]+)", "([^"]+)", (True|False), "(\w*)", "([^"]+)"\)')
    out = ["# 07 - Catalogue des états", "", "Généré par `tools/make_docs.py` à partir de `src/vba/modules/mRpt_Catalog.bas`.", "",
           "| Code | Catégorie | État | Période | Filtre | Contenu |", "|---|---|---|---|---|---|"]
    n = 0
    for m in rx.finditer(src):
        n += 1
        out.append("| `%s` | %s | %s | %s | %s | %s |" % (m.group(1), m.group(2), m.group(3), "oui" if m.group(4) == "True" else "à date",
                                                       {"TRUCK": "camion", "CLIENT": "client", "DRIVER": "chauffeur"}.get(m.group(5), "-"), m.group(6)))
    out += ["", "**%d états.** S'y ajoutent : la facture (aperçu, impression, PDF), l'impression / l'export Excel de toute liste de module, "
            "le relevé horaire, la situation client et l'historique d'un pneu accessibles depuis les fenêtres de module." % n]
    with open(os.path.join(ROOT, "docs", "07_ETATS_CATALOGUE.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return n


if __name__ == "__main__":
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    data_model()
    print("états :", reports())
