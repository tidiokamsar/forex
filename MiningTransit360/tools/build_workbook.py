# -*- coding: utf-8 -*-
"""
Construit :
  dist/MT360_base.xlsx      classeur de donnees (toutes les tables + SYS_*)
                            -> assemble en MINING_TRANSIT_360.xlsm par build/Build-MT360.ps1
  data/demo_dataset.xlsx    jeu de demonstration (1 feuille par table) utilise par
                            la fonction "Reinitialiser la demo" de l'application.
Usage : python3 tools/build_workbook.py
"""
import os
import sys

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.table import Table, TableStyleInfo

sys.path.insert(0, os.path.dirname(__file__))
from schema import TABLES, MODULES, all_fields, parse_opts  # noqa: E402
from demo_data import generate  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NAVY = "0B2A55"
FMT = {"DATE": "dd/mm/yyyy", "TIME": "hh:mm", "MNY": "#,##0", "NUM": "#,##0.00", "PCT": "0.0%", "INT": "0", "ID": "0"}


def col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write_table(ws, tname, headers, rows, types=None, style="TableStyleMedium2", start_row=1):
    hfont = Font(bold=True, color="FFFFFF", name="Segoe UI", size=10)
    hfill = PatternFill("solid", fgColor=NAVY)
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=start_row, column=j, value=h)
        c.font = hfont
        c.fill = hfill
        c.alignment = Alignment(horizontal="center", vertical="center")
    if not rows:
        rows = [[None] * len(headers)]
    for i, r in enumerate(rows, start_row + 1):
        for j, v in enumerate(r, 1):
            c = ws.cell(row=i, column=j, value=v)
            if types and types[j - 1] in FMT and v is not None:
                c.number_format = FMT[types[j - 1]]
    ref = "A%d:%s%d" % (start_row, col_letter(len(headers)), start_row + len(rows))
    tab = Table(displayName=tname, ref=ref)
    tab.tableStyleInfo = TableStyleInfo(name=style, showRowStripes=True)
    ws.add_table(tab)
    for j, h in enumerate(headers, 1):
        w = max(10, min(40, len(str(h)) + 4))
        if types and types[j - 1] in ("MEMO",):
            w = 40
        ws.column_dimensions[col_letter(j)].width = w
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)


def build():
    data = generate()
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "ACCUEIL"
    ws.sheet_view.showGridLines = False
    ws["B2"] = "MINING TRANSIT 360 - ouvrez MINING_TRANSIT_360.xlsm (le tableau de bord est dessine par le VBA a l'installation)"
    for name in ("RAPPORT", "FACTURE", "FICHE", "CALC", "ASSETS", "TESTS"):
        s = wb.create_sheet(name)
        s.sheet_view.showGridLines = False
        if name in ("CALC", "ASSETS"):
            s.sheet_state = "hidden"

    demo = Workbook()
    demo.remove(demo.active)

    for tb in TABLES:
        fields = all_fields(tb)
        headers = [f[0] for f in fields]
        types = [f[2] for f in fields]
        rows = [[rec.get(h) for h in headers] for rec in data[tb["name"]]]
        s = wb.create_sheet(tb["sheet"])
        write_table(s, tb["name"], headers, rows, types)
        s.sheet_properties.tabColor = "1E6FD9" if tb["sheet"].startswith("DB_") else "6C3FC5"
        ds = demo.create_sheet(tb["name"][:31])
        write_table(ds, "D" + tb["name"], headers, rows, types, style="TableStyleLight9")

    # --- schema systeme (lu par le VBA) ---
    s = wb.create_sheet("SYS_SCHEMA")
    rows = []
    for tb in TABLES:
        for i, f in enumerate(all_fields(tb), 1):
            o = parse_opts(f[3])
            rows.append([tb["name"], f[0], f[1], f[2], f[3], i,
                         "Oui" if o.get("list") else "Non", int(o.get("w", 0) or 0)])
    write_table(s, "T_SCHEMA", ["TableName", "FieldName", "Label", "FieldType", "Options", "Ordre", "InList", "ListWidth"], rows)
    s = wb.create_sheet("SYS_TABLES")
    write_table(s, "T_TABLES", ["TableName", "SheetName", "Label", "Audit"],
                [[t["name"], t["sheet"], t["label"], "Oui" if t["audit"] else "Non"] for t in TABLES])
    s = wb.create_sheet("SYS_MODULES")
    write_table(s, "T_MODULES", ["ModKey", "Titre", "Categorie", "Icone", "TableName", "Couleur", "Actions", "DateCol", "TriDefaut", "Ordre"],
                [list(m) + [i] for i, m in enumerate(MODULES, 1)])
    for sh in wb.worksheets:
        if sh.title.startswith("SYS_") or sh.title.startswith("DB_"):
            sh.sheet_state = "hidden"

    base = os.path.join(ROOT, "dist", "MT360_base.xlsx")
    wb.save(base)
    demo.save(os.path.join(ROOT, "data", "demo_dataset.xlsx"))
    print("OK", base)
    return data


if __name__ == "__main__":
    build()
