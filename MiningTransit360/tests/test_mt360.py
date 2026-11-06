# -*- coding: utf-8 -*-
"""
MINING TRANSIT 360 - tests automatises executables sans Excel.

  python3 -m unittest tests/test_mt360.py -v

Couvre : schema, registre des modules / actions, jeu de demonstration
(volumes, integrite referentielle, regles de gestion recalculees de maniere
independante), empreintes (mots de passe, factures), analyse statique VBA,
classeur genere.
Les tests fonctionnels DANS Excel sont dans src/vba/modules/mTest_Suite.bas
(executes par build/Build-MT360.ps1).
"""
import collections
import datetime as dt
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from schema import TABLES, MODULES, PARAMS, PROFILES, all_fields, parse_opts, table  # noqa: E402
from demo_data import generate, DEMO_TODAY  # noqa: E402
from mt_hash import password_hash, invoice_fingerprint, mt_hash  # noqa: E402

D = generate()
VBA = os.path.join(ROOT, "src", "vba")


def rows(t):
    return D[t]


def by_id(t):
    return {r["ID"]: r for r in D[t]}


def vba_source(name):
    for sub in ("modules", "classes", "forms", "document"):
        for ext in (".bas", ".cls", ".frm"):
            p = os.path.join(VBA, sub, name + ext)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    return fh.read()
    raise FileNotFoundError(name)


class TestSchema(unittest.TestCase):
    def test_unique_fields_and_tables(self):
        names = [t["name"] for t in TABLES]
        self.assertEqual(len(names), len(set(names)))
        for t in TABLES:
            f = [x[0] for x in all_fields(t)]
            self.assertEqual(len(f), len(set(f)), t["name"])

    def test_fk_targets_exist(self):
        for t in TABLES:
            for f in t["fields"]:
                o = parse_opts(f[3])
                if f[2] == "FK":
                    tt, col = o["fk"].split(".")
                    self.assertIn(col, [x[0] for x in all_fields(table(tt))], f)

    def test_list_defaults_in_values(self):
        for t in TABLES:
            for f in t["fields"]:
                o = parse_opts(f[3])
                if f[2] == "LIST" and "def" in o:
                    self.assertIn(o["def"], o["vals"].split("|"), (t["name"], f[0]))

    def test_modules_reference_existing_tables(self):
        tn = {t["name"] for t in TABLES}
        for m in MODULES:
            if m[4]:
                self.assertIn(m[4], tn, m[0])

    def test_every_action_has_handler_caption_and_right(self):
        acts = set()
        for m in MODULES:
            acts |= {a for a in m[6].split("|") if a}
        src = vba_source("mUI_Actions")
        run = src[src.index("Public Function Act_Run"):]
        cap = src[src.index("Public Function Act_Caption"):src.index("Public Function Act_Color")]
        for a in acts:
            self.assertRegex(run, r'Case [^\n]*"%s"' % a, "action sans traitement : " + a)
            self.assertIn('"%s"' % a, cap, "action sans libelle : " + a)

    def test_rights_cover_all_modules(self):
        n = collections.Counter((r["Profil"]) for r in rows("T_DROITS"))
        for p in PROFILES:
            self.assertEqual(n[p], len(MODULES))

    def test_required_menu_modules_present(self):
        keys = {m[0] for m in MODULES}
        for k in ("DASHBOARD", "EXPLOITATION", "CAMIONS", "CHAUFFEURS", "AFFECTATIONS", "ROTATIONS", "PESEES", "CARBURANT",
                  "LOCATION", "PNEUS", "ARTICLES", "INCIDENTS", "MAINTENANCE", "DEPENSES", "PERSONNEL", "FACTURES", "PAIEMENTS",
                  "RECOUVREMENT", "DOCUMENTS", "RAPPORTS", "PARAMETRES", "UTILISATEURS", "AUDIT"):
            self.assertIn(k, keys)


class TestDemoVolumes(unittest.TestCase):
    def test_minimum_volumes(self):
        mins = {"T_CAMIONS": 10, "T_CHAUFFEURS": 12, "T_ROTATIONS": 120, "T_PESEES": 120, "T_LOCATION": 70,
                "T_PNEUS": 40, "T_INCIDENTS": 15, "T_DEPENSES": 30, "T_FACTURES": 3, "T_PAIEMENTS": 8}
        for t, n in mins.items():
            self.assertGreaterEqual(len(rows(t)), n, t)
        self.assertGreaterEqual(len(rows("T_CARBURANT")), 90)

    def test_all_demo_flagged(self):
        for c in rows("T_CLIENTS"):
            self.assertIn("DEMO", c["RaisonSociale"])
        self.assertEqual([p for p in rows("T_PARAM") if p["Cle"] == "DEMO_MODE"][0]["Valeur"], "Oui")


class TestReferentialIntegrity(unittest.TestCase):
    def test_foreign_keys(self):
        ids = {t["name"]: {r.get("ID") for r in rows(t["name"])} for t in TABLES}
        for t in TABLES:
            for f in t["fields"]:
                if f[2] == "FK":
                    target = parse_opts(f[3])["fk"].split(".")[0]
                    for r in rows(t["name"]):
                        v = r.get(f[0])
                        if v not in (None, ""):
                            self.assertIn(v, ids[target], (t["name"], f[0], v))

    def test_unique_constraints(self):
        for t in TABLES:
            for f in t["fields"]:
                if "uniq" in parse_opts(f[3]):
                    vals = [r.get(f[0]) for r in rows(t["name"]) if r.get(f[0]) not in (None, "")]
                    self.assertEqual(len(vals), len(set(vals)), (t["name"], f[0]))

    def test_list_values_allowed(self):
        for t in TABLES:
            for f in t["fields"]:
                o = parse_opts(f[3])
                if f[2] == "LIST":
                    allowed = set(o["vals"].split("|"))
                    for r in rows(t["name"]):
                        v = r.get(f[0])
                        if v not in (None, ""):
                            self.assertIn(v, allowed, (t["name"], f[0], v))

    def test_seq_counters_ahead(self):
        seq = {s["Cle"]: s["Dernier"] for s in rows("T_SEQ")}
        for t in TABLES:
            if any(f[0] == "ID" for f in t["fields"]) and rows(t["name"]):
                self.assertGreaterEqual(seq["ID_" + t["name"]], max(r["ID"] for r in rows(t["name"])), t["name"])
        n = max(int(r["NumRotation"].split("-")[-1]) for r in rows("T_ROTATIONS"))
        self.assertEqual(seq["ROT-2026"], n)
        self.assertEqual(seq["FAC-2026"], 6)


class TestBusinessRules(unittest.TestCase):
    P = {p["Cle"]: p["Valeur"] for p in D["T_PARAM"]}

    def test_net_weight(self):
        for r in rows("T_ROTATIONS"):
            self.assertAlmostEqual(r["PoidsNet"], round(r["PoidsBrut"] - r["Tare"], 2), places=2)

    def test_one_ticket_per_rotation(self):
        tk = {p["RotationID"]: p for p in rows("T_PESEES")}
        for r in rows("T_ROTATIONS"):
            self.assertEqual(tk[r["ID"]]["NumTicket"], r["NumTicket"])
            self.assertEqual(tk[r["ID"]]["Statut"], r["Statut"])
            self.assertAlmostEqual(tk[r["ID"]]["PoidsNet"], r["PoidsNet"])

    def test_rotation_capacity_and_destination_client(self):
        cams = by_id("T_CAMIONS")
        lieux = by_id("T_LIEUX")
        tol = float(self.P["ROT_TOLERANCE_SURCHARGE_PCT"]) / 100
        for r in rows("T_ROTATIONS"):
            self.assertLessEqual(r["PoidsNet"], cams[r["CamionID"]]["CapaciteT"] * (1 + tol))
            self.assertEqual(lieux[r["DestinationID"]]["ClientID"], r["ClientID"])
            self.assertLessEqual(r["DateRotation"], DEMO_TODAY)

    def test_driver_assigned_on_rotation_date(self):
        aff = rows("T_AFFECTATIONS")
        for r in rows("T_ROTATIONS"):
            ok = [a for a in aff if a["CamionID"] == r["CamionID"] and a["ChauffeurID"] == r["ChauffeurID"]
                  and a["DateDebut"] <= r["DateRotation"] and (a["DateFin"] is None or r["DateRotation"] <= a["DateFin"])]
            self.assertTrue(ok, r["NumRotation"])

    def test_permits_valid_at_rotation(self):
        ch = by_id("T_CHAUFFEURS")
        for r in rows("T_ROTATIONS"):
            self.assertGreaterEqual(ch[r["ChauffeurID"]]["ExpPermis"], r["DateRotation"], r["NumRotation"])

    def test_no_overlapping_assignments(self):
        far = dt.date(2999, 1, 1)
        aff = rows("T_AFFECTATIONS")
        for i, a in enumerate(aff):
            for b in aff[i + 1:]:
                inter = a["DateDebut"] <= (b["DateFin"] or far) and b["DateDebut"] <= (a["DateFin"] or far)
                if inter:
                    self.assertNotEqual(a["ChauffeurID"], b["ChauffeurID"], (a, b))
                    self.assertFalse(a["CamionID"] == b["CamionID"] and a["Poste"] == b["Poste"], (a, b))

    def test_only_validated_rotations_invoiced(self):
        for r in rows("T_ROTATIONS"):
            if r["FactureID"]:
                self.assertEqual(r["Statut"], "Validée")

    def test_invoices(self):
        cli = by_id("T_CLIENTS")
        for f in rows("T_FACTURES"):
            linked = [r for r in rows("T_ROTATIONS") if r["FactureID"] == f["ID"]]
            ton = round(sum(r["PoidsNet"] for r in linked), 2)
            self.assertAlmostEqual(ton, f["Tonnage"], places=2)
            self.assertEqual(len(linked), f["NbRotations"])
            self.assertEqual(f["MontantHT"], int(round(ton * cli[f["ClientID"]]["TarifTonne"])))
            lines = [l for l in rows("T_FACTURE_LIGNES") if l["FactureID"] == f["ID"]]
            self.assertEqual(sum(l["Montant"] for l in lines), f["MontantHT"])
            paid = sum(p["Montant"] for p in rows("T_PAIEMENTS") if p["FactureID"] == f["ID"] and p["Statut"] != "Annulé")
            self.assertEqual(paid, f["MontantPaye"])
            self.assertEqual(f["Solde"], f["MontantTTC"] - paid)
            self.assertLessEqual(paid, f["MontantTTC"])
            expected = "Payée" if f["Solde"] <= 0 else ("Partielle" if paid > 0 else "Validée")
            self.assertEqual(f["Statut"], expected)
            self.assertEqual(f["Empreinte"], invoice_fingerprint(f["NumFacture"], cli[f["ClientID"]]["Code"], f["Tonnage"], f["MontantTTC"]))
            # toutes les rotations validees du client sur la periode sont facturees (pas d'oubli)
            missing = [r for r in rows("T_ROTATIONS") if r["ClientID"] == f["ClientID"] and r["Statut"] == "Validée"
                       and f["PeriodeDebut"] <= r["DateRotation"] <= f["PeriodeFin"] and r["FactureID"] != f["ID"]]
            self.assertEqual(missing, [])

    def test_mockup_statuses_present(self):
        oct_ = [f["Statut"] for f in rows("T_FACTURES") if f["PeriodeDebut"].month == 10]
        self.assertEqual(sorted(oct_), sorted(["Payée", "Partielle", "Validée"]))

    def test_rental_rules(self):
        ded_att = self.P["LOC_DEDUIRE_ATTENTE"] == "Oui"
        ded_a = self.P["LOC_DEDUIRE_IMMOB_ATIKO"] == "Oui"
        ded_e = self.P["LOC_DEDUIRE_IMMOB_EXPLOITANT"] == "Oui"
        seen = set()
        for r in rows("T_LOCATION"):
            ret = r["HeuresBrutes"] - (r["HeuresAttente"] if ded_att else 0) - (r["HeuresImmobAtiko"] if ded_a else 0) - (r["HeuresImmobExploitant"] if ded_e else 0)
            self.assertAlmostEqual(r["HeuresRetenues"], max(0, ret), places=2)
            self.assertEqual(r["Montant"], int(round(r["HeuresRetenues"] * r["TarifHoraire"])))
            k = (r["CamionID"], r["DateReleve"])
            self.assertNotIn(k, seen)
            seen.add(k)

    def test_fuel(self):
        for r in rows("T_CARBURANT"):
            self.assertEqual(r["Montant"], int(r["Litres"] * r["PrixLitre"]))
            if r["TypeOp"] == "Distribution":
                self.assertIsNotNone(r["CamionID"])
            else:
                self.assertIsNotNone(r["FournisseurID"])
        anomalies = [r for r in rows("T_CARBURANT") if r["Anomalie"]]
        self.assertGreaterEqual(len(anomalies), 3)
        stock = sum(r["Litres"] for r in rows("T_CARBURANT") if r["TypeOp"] == "Approvisionnement" and r["Statut"] in ("Saisie", "Validée")) - \
            sum(r["Litres"] for r in rows("T_CARBURANT") if r["TypeOp"] == "Distribution" and r["Statut"] in ("Saisie", "Validée"))
        self.assertGreater(stock, 0)
        self.assertEqual(int(self.P["CARB_STOCK_CUVE"]), int(stock))

    def test_tires(self):
        pos = collections.Counter((t["CamionID"], t["Position"]) for t in rows("T_PNEUS") if t["Statut"] == "Monté")
        self.assertTrue(all(v == 1 for v in pos.values()))
        for t in rows("T_PNEUS"):
            mv = [m for m in rows("T_PNEU_MVT") if m["PneuID"] == t["ID"]]
            self.assertTrue(any(m["TypeMvt"] == "Entrée" for m in mv), t["NumSerie"])
            if t["Statut"] == "Monté":
                self.assertIsNotNone(t["CamionID"])
            else:
                self.assertIn(t["CamionID"], (None, ""))
        low = [t for t in rows("T_PNEUS") if t["Statut"] == "Monté" and t["ProfondeurMM"] < float(self.P["PNEU_PROFONDEUR_MIN"])]
        self.assertEqual({by_id("T_CAMIONS")[t["CamionID"]]["Code"] for t in low}, {"ATK-003", "ATK-007", "ATK-010"})

    def test_stock_articles(self):
        for a in rows("T_ARTICLES"):
            s = 0
            for m in sorted([m for m in rows("T_STOCK_MVT") if m["ArticleID"] == a["ID"]], key=lambda x: (x["DateMvt"], x["ID"])):
                s += m["Quantite"] if m["TypeMvt"] == "Entrée" else -m["Quantite"]
                self.assertGreaterEqual(s, 0)
            self.assertEqual(s, a["Stock"], a["Code"])

    def test_cash_running_balance(self):
        s = 0
        for c in sorted(rows("T_CAISSE"), key=lambda x: (x["DateOp"], x["ID"])):
            s += c["Montant"] if c["Sens"] == "Entrée" else -c["Montant"]
            self.assertEqual(c["SoldeApres"], s)
            self.assertGreaterEqual(s, 0)

    def test_incident_cost_attribution(self):
        for i in rows("T_INCIDENTS"):
            self.assertEqual(i["CoutExploitant"], i["Cout"] if i["Responsable"] == "Exploitant" else 0)

    def test_truck_status_matches_open_incidents(self):
        cams = by_id("T_CAMIONS")
        open_inc = {i["CamionID"] for i in rows("T_INCIDENTS") if i["Immobilisation"] == "Oui" and not i["DateReprise"]}
        for cid, c in cams.items():
            if cid in open_inc:
                self.assertIn(c["Statut"], ("En panne", "Immobilisé"))
            else:
                self.assertIn(c["Statut"], ("En service", "Disponible"))

    def test_no_reserved_category_expenses(self):
        for d in rows("T_DEPENSES"):
            if d["Responsable"] == "Exploitant" and d["Statut"] == "Validée":
                self.assertNotIn(d["Categorie"], ("Carburant", "Pneumatiques", "Location camions"))

    def test_margin_october_mirror(self):
        """Recalcul independant de la marge d'octobre (miroir de mBiz_KPI)."""
        a, b = dt.date(2026, 10, 1), dt.date(2026, 10, 31)
        cli = by_id("T_CLIENTS")
        ca = sum(r["PoidsNet"] * cli[r["ClientID"]]["TarifTonne"] for r in rows("T_ROTATIONS") if r["Statut"] == "Validée" and a <= r["DateRotation"] <= b)
        inv = sum(f["MontantHT"] for f in rows("T_FACTURES") if f["PeriodeDebut"] == a)
        self.assertAlmostEqual(round(ca), inv, delta=1)
        ok = ("Saisie", "Validée")
        loc = sum(r["Montant"] for r in rows("T_LOCATION") if r["Statut"] in ok and a <= r["DateReleve"] <= b)
        fuel = sum(r["Montant"] for r in rows("T_CARBURANT") if r["TypeOp"] == "Distribution" and r["Statut"] in ok and a <= r["DateOp"] <= b)
        tires = sum(m["Cout"] for m in rows("T_PNEU_MVT") if m["TypeMvt"] in ("Entrée", "Réparation", "Retour réparation") and a <= m["DateMvt"] <= b)
        cons = sum(m["Montant"] for m in rows("T_STOCK_MVT") if m["TypeMvt"] == "Sortie" and a <= m["DateMvt"] <= b)
        rate = 1 + float(self.P["CHARGES_SOCIALES_PCT"]) / 100
        pay = (sum(c["SalaireMensuel"] for c in rows("T_CHAUFFEURS")) + sum(p["SalaireMensuel"] for p in rows("T_PERSONNEL"))) * rate
        prime = {c["ID"]: c["PrimeRotation"] for c in rows("T_CHAUFFEURS")}
        pay += sum(prime[r["ChauffeurID"]] for r in rows("T_ROTATIONS") if r["Statut"] == "Validée" and a <= r["DateRotation"] <= b)
        dep = sum(d["Montant"] for d in rows("T_DEPENSES") if d["Statut"] == "Validée" and d["Responsable"] == "Exploitant" and a <= d["DateDep"] <= b)
        charges = loc + fuel + tires + cons + pay + dep
        marge = ca - charges
        taux = marge / ca
        self.assertTrue(0.25 < taux < 0.40, taux)
        # resultat documente dans docs/05_TESTS.md
        self.__class__.margin = (ca, charges, marge, taux)


class TestSecurity(unittest.TestCase):
    def test_passwords(self):
        pw = {"admin": "Admin@2026", "directeur": "Direction@2026", "exploitation": "Exploit@2026", "comptable": "Compta@2026", "saisie": "Saisie@2026"}
        for u in rows("T_USERS"):
            self.assertEqual(u["HashMDP"], password_hash(pw[u["Login"]], u["Sel"]))
            self.assertNotIn(pw[u["Login"]], str(u.values()))

    def test_hash_vectors_used_by_vba_tests(self):
        src = vba_source("mTest_Suite")
        for text, rounds in (("MT360-TEST", 8), ("Sécurité é", 8)):
            self.assertIn(mt_hash(text, rounds), src)
        self.assertIn(password_hash("Admin@2026", "S7310MT"), src)

    def test_vba_hash_mirror(self):
        """Le code VBA de MT_Hash reprend exactement les constantes et l'arithmetique du miroir Python."""
        src = vba_source("mCore_Security")
        for const in ("2166136261#", "3735928559#", "305419896#", "2882400001#", "16777216#", "403#", "4294967296#"):
            self.assertIn(const, src)


class TestFrenchWords(unittest.TestCase):
    """Miroir Python de mRpt_Invoice.NumberToWordsFR (montant en lettres)."""
    U = ["", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze", "treize",
         "quatorze", "quinze", "seize", "dix-sept", "dix-huit", "dix-neuf"]
    T = ["", "dix", "vingt", "trente", "quarante", "cinquante", "soixante", "soixante", "quatre-vingt", "quatre-vingt"]

    def u100(self, n):
        if n < 17:
            return self.U[n]
        t, u = divmod(n, 10)
        if t == 1:
            return "dix-" + self.U[u]
        if t in (7, 9):
            return self.T[t] + (" et " if (t == 7 and u == 1) else "-") + self.U[10 + u]
        if t == 8:
            return "quatre-vingt" + ("s" if u == 0 else "-" + self.U[u])
        return self.T[t] if u == 0 else (self.T[t] + " et un" if u == 1 else self.T[t] + "-" + self.U[u])

    def u1000(self, n, last):
        c, r = divmod(n, 100)
        s = ""
        if c:
            s = "cent" if c == 1 else self.U[c] + " cent"
            if c > 1 and r == 0 and last:
                s += "s"
        if r:
            s = (s + " " if s else "") + self.u100(r)
        return s

    def words(self, v):
        n = int(round(v))
        names = ["", "mille", "million", "milliard"]
        out = []
        for i in range(3, -1, -1):
            g = (n // 1000 ** i) % 1000
            if g:
                if i == 1:
                    out.append("mille" if g == 1 else self.u1000(g, False) + " mille")
                elif i == 0:
                    out.append(self.u1000(g, True))
                else:
                    out.append(self.u1000(g, False) + " " + names[i] + ("s" if g > 1 else ""))
        return " ".join(out)

    def test_values(self):
        self.assertEqual(self.words(2368955100), "deux milliards trois cent soixante-huit millions neuf cent cinquante-cinq mille cent")
        self.assertEqual(self.words(71), "soixante et onze")
        self.assertEqual(self.words(91), "quatre-vingt-onze")
        self.assertEqual(self.words(200), "deux cents")
        self.assertEqual(self.words(1001), "mille un")
        self.assertIn('"deux milliards trois cent soixante-huit millions neuf cent cinquante-cinq mille cent"', vba_source("mTest_Suite"))


class TestStatic(unittest.TestCase):
    def test_vba_lint(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "vba_lint.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])

    def test_status_literals_exist_in_schema(self):
        """Les statuts ecrits en dur dans le VBA existent dans les listes du schema."""
        cfg = vba_source("mCore_Config")
        vals = set()
        for t in TABLES:
            for f in t["fields"]:
                o = parse_opts(f[3])
                if "vals" in o:
                    vals |= set(o["vals"].split("|"))
        for m in re.finditer(r'Public Const (?:ST|CAM|FAC|PN|RESP)_\w+ As String = "([^"]+)"', cfg):
            self.assertIn(m.group(1), vals, m.group(1))

    def test_workbook_generated(self):
        from openpyxl import load_workbook
        wb = load_workbook(os.path.join(ROOT, "dist", "MT360_base.xlsx"))
        tables = {name for ws in wb.worksheets for name in ws.tables.keys()}
        for t in TABLES:
            self.assertIn(t["name"], tables)
        for t in ("T_SCHEMA", "T_TABLES", "T_MODULES"):
            self.assertIn(t, tables)
        self.assertEqual(wb.worksheets[0].title, "ACCUEIL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
