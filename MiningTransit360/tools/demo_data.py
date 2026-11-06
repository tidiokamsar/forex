# -*- coding: utf-8 -*-
"""
MINING TRANSIT 360 - Generateur du jeu de donnees de DEMONSTRATION.

Toutes les donnees sont FICTIVES (noms, tarifs, montants, identifiants).
Generation deterministe (graine fixe) : deux executions donnent le meme jeu.

Periode simulee : 01/09/2026 -> 05/11/2026 (date de travail demo : 05/11/2026)
  - 10 camions Atiko (8 actifs, ATK-007 en panne, ATK-010 immobilise)
  - 12 chauffeurs (1 permis expire, 1 expirant sous 15 jours)
  - rotations 6 jours / 7, 3 a 5 rotations par camion et par jour
  - factures mensuelles septembre (3) et octobre (3)
  - scenarios d'anomalies injectes (carburant, doublons, rejets, litiges)
"""
import datetime as dt
import random

from schema import TABLES, PARAMS, PROFILES, MODULES, default_rights, all_fields
from mt_hash import password_hash, invoice_fingerprint

SEED = 360
D0 = dt.date(2026, 9, 1)
DEMO_TODAY = dt.date(2026, 11, 5)
CREATOR = "admin"


def d(y, m, day):
    return dt.date(y, m, day)


def t(h, mi=0):
    return dt.time(int(h), int(mi))


def daterange(a, b):
    x = a
    while x <= b:
        yield x
        x += dt.timedelta(days=1)


def month_end(day):
    nxt = (day.replace(day=28) + dt.timedelta(days=4))
    return nxt - dt.timedelta(days=nxt.day)


class Demo:
    def __init__(self):
        self.r = random.Random(SEED)
        self.data = {tb["name"]: [] for tb in TABLES}
        self.seq = {}

    # ------------------------------------------------------------------ utils
    def add(self, table, rec, created=None):
        rows = self.data[table]
        tb = next(x for x in TABLES if x["name"] == table)
        if "ID" in [f[0] for f in tb["fields"]] and not rec.get("ID"):
            rec["ID"] = len(rows) + 1
        if tb["audit"]:
            c = created or DEMO_TODAY
            rec.setdefault("CreePar", CREATOR)
            rec.setdefault("CreeLe", c)
        rows.append(rec)
        return rec

    def num(self, prefix, year):
        k = "%s-%d" % (prefix, year)
        self.seq[k] = self.seq.get(k, 0) + 1
        return "%s-%d-%05d" % (prefix, year, self.seq[k])

    def find(self, table, col, val):
        for x in self.data[table]:
            if x.get(col) == val:
                return x
        raise KeyError((table, col, val))

    # ------------------------------------------------------------ referentiels
    def build_ref(self):
        A = self.add
        A("T_CLIENTS", dict(Code="SMN", RaisonSociale="Societe Miniere du Nord (DEMO)", Adresse="Kamsar, Guinee",
                           Telephone="+224 621 10 10 10", Email="factures@smn.demo", NIF="NIF-DEMO-001",
                           RefContrat="CT-SMN-2026-01", TarifTonne=60000, DelaiPaiementJ=30,
                           Contact="M. Diallo (DEMO)", Actif="Oui"))
        A("T_CLIENTS", dict(Code="CBK", RaisonSociale="Compagnie Bauxite de Kogon (DEMO)", Adresse="Boke, Guinee",
                           Telephone="+224 622 20 20 20", Email="compta@cbk.demo", NIF="NIF-DEMO-002",
                           RefContrat="CT-CBK-2026-03", TarifTonne=62000, DelaiPaiementJ=30,
                           Contact="Mme Camara (DEMO)", Actif="Oui"))
        A("T_CLIENTS", dict(Code="ALX", RaisonSociale="Alumina Export Logistique (DEMO)", Adresse="Conakry, Guinee",
                           Telephone="+224 623 30 30 30", Email="ap@alx.demo", NIF="NIF-DEMO-003",
                           RefContrat="CT-ALX-2026-02", TarifTonne=58000, DelaiPaiementJ=45,
                           Contact="M. Bah (DEMO)", Actif="Oui"))
        F = [("ATK", "Atiko Guinee", "Proprietaire flotte"), ("PGD", "PetroGuinee Distribution (DEMO)", "Carburant"),
             ("PAF", "Pneus Afrique Ouest (DEMO)", "Pneumatiques"), ("QBK", "Quincaillerie de Boke (DEMO)", "Pieces et consommables"),
             ("SEC", "Securite Plus (DEMO)", "Services"), ("TEL", "Telecom Guinee (DEMO)", "Services"),
             ("RST", "Restaurant du Site (DEMO)", "Divers"), ("LUB", "Lubrifiants du Fouta (DEMO)", "Pieces et consommables")]
        for c, n, cat in F:
            A("T_FOURNISSEURS", dict(Code=c, Nom=n, Categorie=cat, Telephone="+224 600 00 00 %02d" % len(self.data["T_FOURNISSEURS"]),
                                     Adresse="Boke / Sangaredi", NIF="NIF-DEMO-F%02d" % (len(self.data["T_FOURNISSEURS"]) + 1), Actif="Oui"))
        L = [("P1", "Fosse Nord P1", "Chargement", None, 0), ("P2", "Fosse Est P2", "Chargement", None, 0),
             ("P3", "Fosse Sud P3", "Chargement", None, 0),
             ("GSM", "Gare de chargement SMN", "Dechargement", 1, 18), ("PCB", "Plateforme CBK", "Dechargement", 2, 24),
             ("SAX", "Stockpile ALX", "Dechargement", 3, 15), ("ATL", "Atelier Atiko", "Atelier", None, 3),
             ("PRC", "Parc camions", "Parc", None, 1)]
        for c, n, ty, cli, km in L:
            A("T_LIEUX", dict(Code=c, Nom=n, TypeLieu=ty, ClientID=cli, DistanceKm=km, Actif="Oui"))

        trucks = [("ATK-001", "RC-2841-A", "Sinotruk", "HOWO 371", 40), ("ATK-002", "RC-2842-A", "Sinotruk", "HOWO 371", 40),
                  ("ATK-003", "RC-2843-A", "Shacman", "F3000", 38), ("ATK-004", "RC-2844-A", "Shacman", "F3000", 38),
                  ("ATK-005", "RC-2845-A", "Mercedes-Benz", "Actros 4141", 42), ("ATK-006", "RC-2846-A", "Mercedes-Benz", "Actros 4141", 42),
                  ("ATK-007", "RC-2847-A", "Sinotruk", "HOWO 371", 40), ("ATK-008", "RC-2848-A", "Shacman", "X3000", 40),
                  ("ATK-009", "RC-2849-A", "Shacman", "X3000", 40), ("ATK-010", "RC-2850-A", "Sinotruk", "HOWO 371", 40)]
        tarifs = [250000, 250000, 240000, 240000, 270000, 270000, 250000, 255000, 255000, 250000]
        for i, (c, im, ma, mo, cap) in enumerate(trucks):
            st = "En service"
            if c == "ATK-007":
                st = "En panne"
            if c == "ATK-010":
                st = "Immobilise"
            A("T_CAMIONS", dict(Code=c, Immatriculation=im, Marque=ma, Modele=mo, CapaciteT=cap, Proprietaire="Atiko Guinee",
                                NumChassis="CHS-DEMO-%05d" % (10231 + i), DateMiseService=d(2024, 1 + i % 12, 10),
                                Statut=st, TarifHoraire=tarifs[i], HeuresMinJour=0, ConsoRefLH=15,
                                NbPositionsPneus=10,
                                AssuranceExp=d(2026, 11, 20) if c == "ATK-004" else d(2027, 3, 31),
                                VisiteTechExp=d(2026, 11, 12) if c == "ATK-008" else d(2027, 1 + i % 6, 15),
                                CompteurHeures=4200 + i * 310, Observations="", Actif="Oui"),
                created=d(2026, 8, 25))

        first = ["Mamadou", "Ibrahima", "Alpha", "Ousmane", "Sekou", "Abdoulaye", "Moussa", "Thierno", "Lansana", "Boubacar", "Mohamed", "Fode"]
        last = ["Diallo", "Bah", "Camara", "Sylla", "Conde", "Barry", "Keita", "Sow", "Toure", "Soumah", "Kourouma", "Fofana"]
        for i in range(12):
            exp = d(2028, 1 + i % 12, 1)
            if i == 4:
                exp = d(2026, 10, 28)   # permis expire
            if i == 10:
                exp = d(2026, 11, 14)   # expire sous 15 jours
            A("T_CHAUFFEURS", dict(Matricule="CH-%03d" % (i + 1), Nom=last[i].upper(), Prenom=first[i],
                                   Telephone="+224 62%d %02d %02d %02d" % (i % 10, 10 + i, 20 + i, 30 + i),
                                   Adresse="Sangaredi", ContactUrgence="Famille %s" % last[i], TelUrgence="+224 66%d 00 00 0%d" % (i % 10, i % 10),
                                   NumPermis="GN-PC-%06d" % (481200 + i * 37), CategoriePermis="CE" if i % 3 else "C",
                                   ExpPermis=exp, DateEmbauche=d(2025, 1 + i % 12, 2), SalaireMensuel=4500000 + (i % 3) * 250000,
                                   PrimeRotation=15000, Statut="Actif", Observations=""),
                     created=d(2026, 8, 25))
        staff = [("ADM-01", "Aissatou Diallo", "Responsable administratif et financier", 9000000),
                 ("EXP-01", "Sekou Camara", "Responsable exploitation", 10000000),
                 ("EXP-02", "Mariama Bah", "Agent de saisie / pointage", 3500000),
                 ("EXP-03", "Kerfala Sylla", "Pointeur pont bascule", 3200000),
                 ("SEC-01", "Mory Keita", "Chef de securite (HSE)", 5500000),
                 ("LOG-01", "Fatoumata Barry", "Magasinier", 3800000)]
        for m, n, f, s in staff:
            A("T_PERSONNEL", dict(Matricule=m, Nom=n, Fonction=f, Telephone="+224 610 00 00 00", DateEmbauche=d(2025, 3, 1),
                                  SalaireMensuel=s, Statut="Actif"), created=d(2026, 8, 25))

    # ------------------------------------------------------------ affectations
    def build_assign(self):
        A = self.add
        # (camion, chauffeur, debut, fin)
        plan = [(1, 1, D0, None), (2, 2, D0, None), (3, 3, D0, None), (4, 4, D0, None),
                (5, 5, D0, d(2026, 10, 27)), (5, 11, d(2026, 10, 28), None),   # permis CH-005 expire -> remplace
                (6, 6, D0, None), (7, 7, D0, d(2026, 11, 2)), (8, 8, D0, None), (9, 9, D0, None),
                (10, 10, D0, d(2026, 10, 28)), (8, 12, D0, d(2026, 9, 14))]
        # Correction : CH-012 a conduit ATK-008 avant CH-008 -> on decale CH-008
        plan[8] = (8, 8, d(2026, 9, 15), None)
        for cam, ch, a, b in plan:
            A("T_AFFECTATIONS", dict(CamionID=cam, ChauffeurID=ch, DateDebut=a, DateFin=b, Poste="Jour",
                                     Statut="Active" if b is None else "Terminee",
                                     Observations="" if b is None else "Fin d'affectation"), created=a)
        self.assign = plan

    def driver_for(self, cam, day):
        for c, ch, a, b in self.assign:
            if c == cam and a <= day and (b is None or day <= b):
                return ch
        return None

    def truck_active(self, cam, day):
        if cam == 7 and day >= d(2026, 11, 2):
            return False
        if cam == 10 and day >= d(2026, 10, 28):
            return False
        if cam == 3 and d(2026, 9, 22) <= day <= d(2026, 9, 23):   # crevaison + reparation
            return False
        return self.driver_for(cam, day) is not None

    # ------------------------------------------------------------ exploitation
    def build_ops(self):
        r = self.r
        A = self.add
        dest_by_client = {1: 4, 2: 5, 3: 6}
        truck_client = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 3, 8: 3, 9: 1, 10: 3}
        compteur = {i: 4200 + (i - 1) * 310 for i in range(1, 11)}
        last_fill = {}
        fuel_price = 12000
        tank = 0.0
        self.worked = []
        rot_month_count = {}
        ticket_no = 880000
        for day in daterange(D0, DEMO_TODAY):
            if day.weekday() == 6:          # dimanche : repos
                continue
            if day.day == 1 or day.day == 15 or (day.weekday() == 0 and day.day in range(8, 13)):
                pass
            # Approvisionnement cuve tous les 3 jours ouvres
            if (day - D0).days % 3 == 0:
                fuel_price = 12000 if day < d(2026, 10, 1) else 12500
                q = 6000
                tank += q
                A("T_CARBURANT", dict(NumBon=self.num("APP", day.year), DateOp=day, TypeOp="Approvisionnement",
                                      CamionID=None, ChauffeurID=None, FournisseurID=2, Litres=q, PrixLitre=fuel_price,
                                      Montant=q * fuel_price, CompteurH=None, HeuresDepuisPlein=None, ConsoLH=None,
                                      Anomalie="", Statut="Validee", Justificatif="", Observations="Livraison citerne"), created=day)
            for cam in range(1, 11):
                if not self.truck_active(cam, day):
                    continue
                ch = self.driver_for(cam, day)
                cli = truck_client[cam]
                if cam == 9 and day >= d(2026, 10, 15):
                    cli = 2
                dest = dest_by_client[cli]
                orig = 1 + (cam + day.day) % 3
                nrot = r.choice([3, 4, 4, 4, 5])
                hstart = 6.5 + r.choice([0, 0.25, 0.5])
                cap = self.find("T_CAMIONS", "ID", cam)["CapaciteT"]
                cur = hstart + 0.3
                for k in range(nrot):
                    dur = r.uniform(1.8, 2.4)
                    dep = cur
                    arr = cur + dur
                    cur = arr + r.uniform(0.15, 0.35)
                    tare = round(r.uniform(15.2, 16.8), 2)
                    net = round(min(cap * 1.04, max(cap * 0.82, r.gauss(cap * 0.95, 1.4))), 2)
                    statut = "Validee"
                    motif = ""
                    if day >= DEMO_TODAY:
                        statut = "Saisie"
                    elif r.random() < 0.012:
                        statut = "Rejetee"
                        motif = r.choice(["Ticket illisible - pesee non confirmee", "Poids incoherent avec la capacite",
                                          "Destination non conforme au programme"])
                    elif r.random() < 0.004:
                        statut = "Annulee"
                        motif = "Saisie en double annulee par l'agent"
                    ticket_no += 1
                    num = self.num("ROT", day.year)
                    hh = lambda x: t(int(x) % 24, int(round((x - int(x)) * 60)) % 60)
                    rot = A("T_ROTATIONS", dict(NumRotation=num, DateRotation=day, CamionID=cam, ChauffeurID=ch,
                                                HDepart=hh(dep), HArrivee=hh(arr), OrigineID=orig, DestinationID=dest, ClientID=cli,
                                                NumTicket="TK-%d" % ticket_no, PoidsBrut=round(tare + net, 2), Tare=tare,
                                                PoidsNet=net, Statut=statut, MotifRejet=motif, FactureID=None, Justificatif="",
                                                ValidePar="exploitation" if statut in ("Validee", "Rejetee") else "",
                                                ValideLe=day if statut in ("Validee", "Rejetee") else None,
                                                Observations=""), created=day)
                    A("T_PESEES", dict(NumTicket=rot["NumTicket"], RotationID=rot["ID"], DatePesee=day, HeurePesee=hh(arr - 0.1),
                                       Pont="PB-02 Gare" if dest == 4 else ("PB-03 Port" if dest == 5 else "PB-01 Mine"),
                                       CamionID=cam, PoidsBrut=rot["PoidsBrut"], Tare=tare, PoidsNet=net,
                                       Operateur="K. Sylla", Statut=statut, Justificatif="", Observations=motif), created=day)
                hend = round(cur + r.uniform(0.2, 0.6), 2)
                hours_raw = round(18.5 - 6.5, 2)    # contrat : poste 06:30 - 18:30
                attente = r.choice([0, 0, 0.5, 0.5, 1, 1.5])
                self.worked.append((day, cam, ch, nrot))
                # carburant : un plein par jour travaille
                engine_h = round(r.uniform(10.2, 11.6), 1)
                compteur[cam] += engine_h
                ref = 15
                litres = round(engine_h * ref * r.uniform(0.93, 1.08), 0)
                anomalie = ""
                if (cam, day) in ((4, d(2026, 10, 9)), (2, d(2026, 9, 18)), (6, d(2026, 10, 21))):
                    litres = round(engine_h * ref * 1.48, 0)     # surconsommation injectee
                prev = last_fill.get(cam)
                hdep = round(compteur[cam] - prev, 1) if prev else engine_h
                conso = round(litres / hdep, 2) if hdep else None
                if conso and conso > ref * 1.25:
                    anomalie = "Surconsommation %.0f%% vs reference" % ((conso / ref - 1) * 100)
                last_fill[cam] = compteur[cam]
                tank -= litres
                A("T_CARBURANT", dict(NumBon=self.num("CARB", day.year), DateOp=day, TypeOp="Distribution", CamionID=cam,
                                      ChauffeurID=ch, FournisseurID=None, Litres=litres, PrixLitre=fuel_price,
                                      Montant=int(litres * fuel_price), CompteurH=round(compteur[cam], 1), HeuresDepuisPlein=hdep,
                                      ConsoLH=conso, Anomalie=anomalie,
                                      Statut="Saisie" if day >= d(2026, 11, 4) else "Validee",
                                      Justificatif="", Observations=""), created=day)
                if (cam, day) == (1, d(2026, 10, 14)):
                    # doublon injecte : 2e bon le meme jour, 1h plus tard -> rejete
                    A("T_CARBURANT", dict(NumBon=self.num("CARB", day.year), DateOp=day, TypeOp="Distribution", CamionID=cam,
                                          ChauffeurID=ch, FournisseurID=None, Litres=160, PrixLitre=fuel_price,
                                          Montant=160 * fuel_price, CompteurH=round(compteur[cam] + 1, 1), HeuresDepuisPlein=1,
                                          ConsoLH=160.0, Anomalie="Doublon potentiel (2 pleins en 1 h)", Statut="Rejetee",
                                          Justificatif="", Observations="Rejete apres controle : bon saisi deux fois"), created=day)
                # location horaire
                tarif = self.find("T_CAMIONS", "ID", cam)["TarifHoraire"]
                immob = 0
                ret = round(hours_raw - immob, 2)
                A("T_LOCATION", dict(DateReleve=day, CamionID=cam, HDebut=t(6, 30), HFin=t(18, 30), HeuresBrutes=hours_raw,
                                     HeuresAttente=attente, HeuresImmobAtiko=immob, HeuresImmobExploitant=0,
                                     HeuresRetenues=ret, TarifHoraire=tarif, Montant=int(round(ret * tarif)),
                                     Statut="Saisie" if day >= d(2026, 11, 1) else "Validee", Justificatif="",
                                     Observations=""), created=day)
            # releves pour les camions immobilises (heures non facturables si imputable Atiko)
            for cam, since in ((10, d(2026, 10, 28)), (7, d(2026, 11, 2))):
                if day >= since:
                    tarif = self.find("T_CAMIONS", "ID", cam)["TarifHoraire"]
                    A("T_LOCATION", dict(DateReleve=day, CamionID=cam, HDebut=t(6, 30), HFin=t(18, 30), HeuresBrutes=12,
                                         HeuresAttente=0, HeuresImmobAtiko=12, HeuresImmobExploitant=0, HeuresRetenues=0,
                                         TarifHoraire=tarif, Montant=0,
                                         Statut="Saisie" if day >= d(2026, 11, 1) else "Validee", Justificatif="",
                                         Observations="Immobilisation imputable Atiko - location suspendue (contrat art. 7 DEMO)"),
                      created=day)
            if day == d(2026, 9, 22):
                tarif = self.find("T_CAMIONS", "ID", 3)["TarifHoraire"]
                for dd in (d(2026, 9, 22), d(2026, 9, 23)):
                    A("T_LOCATION", dict(DateReleve=dd, CamionID=3, HDebut=t(6, 30), HFin=t(18, 30), HeuresBrutes=12,
                                         HeuresAttente=0, HeuresImmobAtiko=0, HeuresImmobExploitant=12, HeuresRetenues=12,
                                         TarifHoraire=tarif, Montant=12 * tarif, Statut="Validee", Justificatif="",
                                         Observations="Immobilisation imputable exploitant (crevaison) - location due"), created=dd)
        self.tank = tank
        self.compteur = compteur
        for cam in range(1, 11):
            self.find("T_CAMIONS", "ID", cam)["CompteurHeures"] = round(compteur[cam], 1)

    # ------------------------------------------------------------ pneus / stock
    def build_tires(self):
        r = self.r
        A = self.add
        brands = [("Michelin", "XZY3", "12.00R24"), ("Bridgestone", "M840", "12.00R24"), ("Triangle", "TR918", "12.00R20")]
        pos = ["AVG", "AVD", "AR1-GE", "AR1-GI", "AR1-DI", "AR1-DE", "AR2-GE", "AR2-GI", "AR2-DI", "AR2-DE"]
        n = 0

        def newtire(day, price, status="Stock"):
            nonlocal n
            n += 1
            b = brands[n % 3]
            rec = A("T_PNEUS", dict(NumSerie="PN-%s-%05d" % (b[0][:3].upper(), 24000 + n), Reference=b[1], Marque=b[0],
                                    Dimension=b[2], FournisseurID=3, PrixAchat=price, DateEntree=day, Statut=status,
                                    CamionID=None, Position="", HeuresCumulees=0, DureeVieCibleH=2500,
                                    ProfondeurMM=round(r.uniform(16, 19), 1), DateMontage=None, Observations=""), created=day)
            A("T_PNEU_MVT", dict(DateMvt=day, PneuID=rec["ID"], TypeMvt="Entree", CamionID=None, Position="", CamionDestID=None,
                                 PositionDest="", CompteurH=None, Cout=price, Motif="Achat - BL PAF-%d" % (300 + n)), created=day)
            return rec

        for cam in range(1, 11):
            for p in pos:
                tire = newtire(d(2026, 6, 2), 6500000)
                mount = d(2026, 6, 10)
                tire.update(Statut="Monte", CamionID=cam, Position=p, DateMontage=mount)
                used = round(r.uniform(700, 1500), 0)
                depth = round(max(7.0, 18 - used / 150), 1)
                if cam in (3, 7, 10) and p in ("AR2-GE", "AR1-DE"):
                    depth = round(r.uniform(4.2, 5.6), 1)    # a remplacer
                    used = 2350
                tire.update(HeuresCumulees=used, ProfondeurMM=depth)
                A("T_PNEU_MVT", dict(DateMvt=mount, PneuID=tire["ID"], TypeMvt="Montage", CamionID=cam, Position=p,
                                     CamionDestID=None, PositionDest="", CompteurH=3800 + cam * 300, Cout=0,
                                     Motif="Montage initial"), created=mount)
        # achats d'octobre (charge de la periode) + stock
        for i in range(10):
            newtire(d(2026, 10, 6), 6800000)
        # crevaison ATK-003 le 22/09 : demontage, reparation, retour, remontage d'un pneu neuf
        victim = next(x for x in self.data["T_PNEUS"] if x["CamionID"] == 3 and x["Position"] == "AVG")
        spare = newtire(d(2026, 9, 20), 6600000)
        A("T_PNEU_MVT", dict(DateMvt=d(2026, 9, 22), PneuID=victim["ID"], TypeMvt="Demontage", CamionID=3, Position="AVG",
                             CamionDestID=None, PositionDest="", CompteurH=None, Cout=0, Motif="Crevaison INC"), created=d(2026, 9, 22))
        A("T_PNEU_MVT", dict(DateMvt=d(2026, 9, 22), PneuID=victim["ID"], TypeMvt="Reparation", CamionID=None, Position="",
                             CamionDestID=None, PositionDest="", CompteurH=None, Cout=450000, Motif="Vulcanisation"), created=d(2026, 9, 22))
        victim.update(Statut="Reparation", CamionID=None, Position="")
        A("T_PNEU_MVT", dict(DateMvt=d(2026, 9, 23), PneuID=spare["ID"], TypeMvt="Montage", CamionID=3, Position="AVG",
                             CamionDestID=None, PositionDest="", CompteurH=None, Cout=0, Motif="Remplacement crevaison"), created=d(2026, 9, 23))
        spare.update(Statut="Monte", CamionID=3, Position="AVG", DateMontage=d(2026, 9, 23), HeuresCumulees=420)
        # transfert ATK-009 -> ATK-008
        tr = next(x for x in self.data["T_PNEUS"] if x["CamionID"] == 9 and x["Position"] == "AR2-DE")
        tr2 = next(x for x in self.data["T_PNEUS"] if x["CamionID"] == 8 and x["Position"] == "AR2-DE")
        A("T_PNEU_MVT", dict(DateMvt=d(2026, 10, 12), PneuID=tr2["ID"], TypeMvt="Réforme", CamionID=8, Position="AR2-DE",
                             CamionDestID=None, PositionDest="", CompteurH=None, Cout=0, Motif="Flanc endommage - reforme"), created=d(2026, 10, 12))
        tr2.update(Statut="Reforme", CamionID=None, Position="", Observations="Flanc endommage")
        A("T_PNEU_MVT", dict(DateMvt=d(2026, 10, 12), PneuID=tr["ID"], TypeMvt="Transfert", CamionID=9, Position="AR2-DE",
                             CamionDestID=8, PositionDest="AR2-DE", CompteurH=None, Cout=0, Motif="Transfert suite reforme"), created=d(2026, 10, 12))
        tr.update(CamionID=8)
        st = next(x for x in self.data["T_PNEUS"] if x["Statut"] == "Stock" and x["DateEntree"] == d(2026, 10, 6))
        A("T_PNEU_MVT", dict(DateMvt=d(2026, 10, 12), PneuID=st["ID"], TypeMvt="Montage", CamionID=9, Position="AR2-DE",
                             CamionDestID=None, PositionDest="", CompteurH=None, Cout=0, Motif="Remplacement apres transfert"), created=d(2026, 10, 12))
        st.update(Statut="Monte", CamionID=9, Position="AR2-DE", DateMontage=d(2026, 10, 12), HeuresCumulees=230)
        # 3 pneus reformes historiques
        for i in range(3):
            x = newtire(d(2026, 3, 1), 6200000)
            A("T_PNEU_MVT", dict(DateMvt=d(2026, 8, 20 + i), PneuID=x["ID"], TypeMvt="Réforme", CamionID=None, Position="",
                                 CamionDestID=None, PositionDest="", CompteurH=None, Cout=0, Motif="Usure limite atteinte"), created=d(2026, 8, 20))
            x.update(Statut="Reforme", HeuresCumulees=2600, ProfondeurMM=3.0)

        arts = [("HM-15W40", "Huile moteur 15W40 (fut 208 L)", "Lubrifiants", "Fut", 2, 4200000),
                ("HH-68", "Huile hydraulique ISO 68 (fut)", "Lubrifiants", "Fut", 1, 3900000),
                ("GR-EP2", "Graisse EP2 (seau 18 kg)", "Lubrifiants", "Seau", 3, 650000),
                ("FH-001", "Filtre a huile HOWO", "Filtres", "U", 6, 180000),
                ("FG-001", "Filtre a gasoil HOWO", "Filtres", "U", 6, 160000),
                ("FA-001", "Filtre a air Shacman", "Filtres", "U", 4, 420000),
                ("EPI-GIL", "Gilet haute visibilite", "Securite", "U", 10, 45000),
                ("EPI-CAS", "Casque de chantier", "Securite", "U", 10, 60000),
                ("AMP-24", "Ampoule 24V H4", "Pieces", "U", 10, 25000),
                ("COUR-01", "Courroie alternateur", "Pieces", "U", 4, 210000)]
        for c, des, fam, u, mn, pu in arts:
            a = A("T_ARTICLES", dict(Code=c, Designation=des, Famille=fam, Unite=u, StockMin=mn, PrixMoyen=pu, Stock=0, Actif="Oui"),
                  created=d(2026, 8, 25))
            qin = mn * 3
            A("T_STOCK_MVT", dict(DateMvt=d(2026, 9, 2), ArticleID=a["ID"], TypeMvt="Entree", Quantite=qin, PU=pu, Montant=qin * pu,
                                  CamionID=None, FournisseurID=8 if fam == "Lubrifiants" else 4, Reference="BL-%s-01" % c,
                                  Observations="Stock initial"), created=d(2026, 9, 2))
            stock = qin
            for k in range(2):
                q = min(stock, r.choice([1, 1, 2]) * (1 if u != "U" else 2))
                if a["Code"] == "FA-001":
                    q = stock - 1 if k == 1 else q          # passe sous le minimum -> alerte
                stock -= q
                dd = d(2026, 9 + k, 10 + 7 * k)
                A("T_STOCK_MVT", dict(DateMvt=dd, ArticleID=a["ID"], TypeMvt="Sortie", Quantite=q, PU=pu, Montant=q * pu,
                                      CamionID=1 + (a["ID"] + k) % 9, FournisseurID=None, Reference="BS-%04d" % (len(self.data["T_STOCK_MVT"]) + 1),
                                      Observations="Consommation atelier"), created=dd)
            a["Stock"] = stock

    # ------------------------------------------------------------ incidents
    def build_incidents(self):
        A = self.add
        inc = [
            (d(2026, 9, 6), "Crevaison", 2, "Crevaison roue arriere sur piste P2", "Mineure", "Exploitant", False, 0, 0, 350000),
            (d(2026, 9, 12), "Attente chargement", 4, "Attente pelle en panne a la fosse P1 (2 h)", "Mineure", "Tiers / assurance", False, 0, 0, 0),
            (d(2026, 9, 22), "Crevaison", 3, "Eclatement pneu avant gauche - camion immobilise 2 jours", "Moyenne", "Exploitant", True, 48, 0, 450000),
            (d(2026, 9, 29), "Panne electrique", 6, "Alternateur defaillant", "Moyenne", "Atiko (contrat)", True, 6, 0, 1800000),
            (d(2026, 10, 3), "Securite", 1, "Depassement vitesse detecte sur piste (sanction chauffeur)", "Mineure", "Exploitant", False, 0, 0, 0),
            (d(2026, 10, 7), "Panne mecanique", 9, "Fuite circuit hydraulique benne", "Moyenne", "Atiko (contrat)", True, 5, 0, 2500000),
            (d(2026, 10, 11), "Accident", 8, "Accrochage leger avec engin au point de chargement", "Majeure", "Tiers / assurance", True, 8, 0, 6500000),
            (d(2026, 10, 16), "Crevaison", 5, "Crevaison roue arriere", "Mineure", "Exploitant", False, 0, 0, 300000),
            (d(2026, 10, 19), "Panne mecanique", 2, "Embrayage - remplacement disque", "Moyenne", "Atiko (contrat)", True, 10, 0, 4800000),
            (d(2026, 10, 23), "Attente chargement", 6, "File d'attente gare SMN (3 h)", "Mineure", "Tiers / assurance", False, 0, 0, 0),
            (d(2026, 10, 25), "Securite", 4, "Chauffeur sans EPI - rappel a l'ordre", "Mineure", "Exploitant", False, 0, 0, 0),
            (d(2026, 10, 28), "Panne mecanique", 10, "Boite de vitesses - attente piece d'Europe", "Critique", "Atiko (contrat)", True, None, 0, 18000000),
            (d(2026, 10, 30), "Panne electrique", 1, "Demarreur", "Mineure", "Atiko (contrat)", True, 3, 0, 900000),
            (d(2026, 11, 2), "Panne mecanique", 7, "Surchauffe moteur - joint de culasse", "Majeure", "Atiko (contrat)", True, None, 0, 7500000),
            (d(2026, 11, 4), "Autre", 3, "Retroviseur casse", "Mineure", "A determiner", False, 0, 0, 150000),
            (d(2026, 9, 15), "Crevaison", 9, "Crevaison roue jumelee", "Mineure", "Exploitant", False, 0, 0, 250000),
        ]
        for dd, ty, cam, desc, grav, resp, imm, dur, _, cost in inc:
            ch = self.driver_for(cam, dd) or self.driver_for(cam, dd - dt.timedelta(days=1))
            closed = dur is not None and dd < d(2026, 11, 4)
            rep = None
            hrep = None
            if imm and dur:
                end = dt.datetime.combine(dd, t(8)) + dt.timedelta(hours=dur)
                rep, hrep = end.date(), end.time()
            ce = cost if resp == "Exploitant" else 0
            A("T_INCIDENTS", dict(NumIncident=self.num("INC", dd.year), DateIncident=dd, TypeIncident=ty, CamionID=cam, ChauffeurID=ch,
                                  Description=desc, Gravite=grav, Responsable=resp, Immobilisation="Oui" if imm else "Non",
                                  DebutImmob=dd if imm else None, HeureDebutImmob=t(8) if imm else None,
                                  DateReprise=rep, HeureReprise=hrep, DureeImmobH=dur if (imm and dur) else (None if imm else 0),
                                  Intervention="Intervention realisee" if closed else "",
                                  Cout=cost, CoutExploitant=ce, Statut="Clôturé" if closed else ("En cours" if imm else "Ouvert"),
                                  Justificatif=""), created=dd)
        k = 0
        for x in self.data["T_INCIDENTS"]:
            if x["Responsable"] == "Atiko (contrat)" or x["TypeIncident"] == "Accident":
                k += 1
                A("T_MAINTENANCE", dict(NumOT=self.num("OT", 2026), DateOT=x["DateIncident"], CamionID=x["CamionID"], IncidentID=x["ID"],
                                        TypeMaint="Corrective", Intervenant="Atelier Atiko" if x["Responsable"].startswith("Atiko") else "Garage agree assurance",
                                        Responsable=x["Responsable"], Description=x["Description"], DateDebut=x["DateIncident"],
                                        DateFin=x["DateReprise"], Cout=x["Cout"],
                                        Statut="Terminee" if x["Statut"] == "Clôturé" else "En cours"), created=x["DateIncident"])
        for cam in (1, 4, 5, 8):
            dd = d(2026, 10, 5 + cam)
            A("T_MAINTENANCE", dict(NumOT=self.num("OT", 2026), DateOT=dd, CamionID=cam, IncidentID=None, TypeMaint="Preventive",
                                    Intervenant="Atelier Atiko", Responsable="Atiko (contrat)", Description="Vidange 500 h + filtres",
                                    DateDebut=dd, DateFin=dd, Cout=2200000, Statut="Terminee"), created=dd)
        dd = d(2026, 11, 9)
        A("T_MAINTENANCE", dict(NumOT=self.num("OT", 2026), DateOT=dd, CamionID=6, IncidentID=None, TypeMaint="Revision",
                                Intervenant="Atelier Atiko", Responsable="Atiko (contrat)", Description="Revision 5000 h planifiee",
                                DateDebut=dd, DateFin=None, Cout=0, Statut="Planifiee"), created=d(2026, 11, 3))

    # ------------------------------------------------------------ finances
    def build_finance(self):
        r = self.r
        A = self.add
        deps = [
            ("Restauration", "Repas equipes de nuit et chauffeurs", 7, 4500000),
            ("Securite et EPI", "Dotation EPI chauffeurs", 5, 3200000),
            ("Telecom", "Forfaits telephoniques flotte", 6, 1200000),
            ("Transport personnel", "Navette personnel Sangaredi", None, 3500000),
            ("Frais administratifs", "Fournitures bureau et impression", None, 950000),
            ("Lubrifiants et consommables", "Achat graisse et liquide de refroidissement", 8, 2800000),
            ("Entretien a charge exploitant", "Lavage camions et nettoyage cabines", None, 1800000),
            ("Taxes et redevances", "Redevance passage piste communautaire", None, 2500000),
            ("Personnel temporaire", "Manoeuvres journaliers chargement", None, 3000000),
            ("Divers", "Petit materiel et outillage", 4, 700000),
        ]
        months = [(2026, 9), (2026, 10), (2026, 11)]
        for (y, m) in months:
            for i, (cat, lib, four, amt) in enumerate(deps):
                if (y, m) == (2026, 11) and i > 6:
                    continue
                dd = d(y, m, min(28, 3 + i * 2))
                if dd > DEMO_TODAY:
                    dd = DEMO_TODAY
                st = "Validee" if dd < d(2026, 11, 1) else "Saisie"
                A("T_DEPENSES", dict(NumPiece=self.num("DEP", y), DateDep=dd, Categorie=cat, Libelle=lib, FournisseurID=four,
                                     RefFacture="F-%d%02d-%02d" % (y, m, i + 1),
                                     Montant=int(amt * r.uniform(0.9, 1.15) // 1000 * 1000), ModePaiement="Virement" if amt > 2000000 else "Caisse",
                                     CamionID=None, CentreCout="Exploitation", Responsable="Exploitant", Exceptionnelle="Non",
                                     Statut=st, Justificatif="", ValidePar="comptable" if st == "Validee" else ""), created=dd)
        # cas particuliers
        A("T_DEPENSES", dict(NumPiece=self.num("DEP", 2026), DateDep=d(2026, 10, 12), Categorie="Charges exceptionnelles",
                             Libelle="Franchise assurance accrochage ATK-008", FournisseurID=None, RefFacture="ASS-778",
                             Montant=2000000, ModePaiement="Cheque", CamionID=8, CentreCout="Flotte", Responsable="Exploitant",
                             Exceptionnelle="Oui", Statut="Validee", Justificatif="", ValidePar="comptable"), created=d(2026, 10, 12))
        A("T_DEPENSES", dict(NumPiece=self.num("DEP", 2026), DateDep=d(2026, 10, 29), Categorie="Entretien a charge exploitant",
                             Libelle="Avance piece boite de vitesses ATK-010 (a refacturer Atiko)", FournisseurID=1, RefFacture="ATK-PR-55",
                             Montant=5000000, ModePaiement="Virement", CamionID=10, CentreCout="Flotte", Responsable="Atiko (contrat)",
                             Exceptionnelle="Non", Statut="Validee", Justificatif="", ValidePar="comptable"), created=d(2026, 10, 29))
        A("T_DEPENSES", dict(NumPiece=self.num("DEP", 2026), DateDep=d(2026, 10, 18), Categorie="Restauration",
                             Libelle="Repas equipes (saisie en double)", FournisseurID=7, RefFacture="F-202610-01",
                             Montant=4500000, ModePaiement="Caisse", CamionID=None, CentreCout="Exploitation", Responsable="Exploitant",
                             Exceptionnelle="Non", Statut="Rejetee", Justificatif="", ValidePar="comptable"), created=d(2026, 10, 18))

        # caisse
        solde = 0
        ops = [(d(2026, 9, 1), "Entree", "Alimentation caisse", 15000000)]
        for i in range(18):
            dd = d(2026, 9, 3) + dt.timedelta(days=i * 3 + 1)
            ops.append((dd, "Sortie", r.choice(["Achat eau et boissons", "Frais de mission chauffeur", "Petites fournitures",
                                                 "Credit telephonique", "Carburant groupe electrogene"]), int(r.uniform(150, 900)) * 1000))
            if i in (6, 12):
                ops.append((dd, "Entree", "Reapprovisionnement caisse", 10000000))
        for dd, s, lib, m in sorted(ops, key=lambda x: x[0]):
            solde += m if s == "Entree" else -m
            A("T_CAISSE", dict(DateOp=dd, Sens=s, Libelle=lib, Montant=m, Reference="PC-%03d" % (len(self.data["T_CAISSE"]) + 1),
                               SoldeApres=solde), created=dd)
        for i, (who, mt, mot, rb) in enumerate([("CH-001", 1000000, "Avance sur salaire", 1000000), ("CH-003", 1500000, "Frais medicaux", 500000),
                                                 ("CH-006", 800000, "Avance sur salaire", 0), ("EXP-02", 600000, "Avance scolarite", 300000),
                                                 ("CH-009", 1200000, "Evenement familial", 1200000), ("CH-011", 700000, "Avance sur salaire", 0)]):
            dd = d(2026, 9, 10) + dt.timedelta(days=i * 9)
            A("T_AVANCES", dict(DateAvance=dd, Beneficiaire=who, Montant=mt, Motif=mot, MontantRembourse=rb, Solde=mt - rb,
                                Statut="Soldee" if rb >= mt else "En cours"), created=dd)

        # factures mensuelles
        tva = 0.0
        fact_seq = 0
        self.invoices = []
        for (y, m) in [(2026, 9), (2026, 10)]:
            a, b = d(y, m, 1), month_end(d(y, m, 1))
            for cli in self.data["T_CLIENTS"]:
                rots = [x for x in self.data["T_ROTATIONS"] if x["ClientID"] == cli["ID"] and x["Statut"] == "Validee"
                        and a <= x["DateRotation"] <= b]
                ton = round(sum(x["PoidsNet"] for x in rots), 2)
                ht = int(round(ton * cli["TarifTonne"]))
                fact_seq += 1
                num = "FAC-%d-%04d" % (y, fact_seq)
                dfac = d(y, m + 1, 2) if m < 12 else d(y + 1, 1, 2)
                ech = dfac + dt.timedelta(days=cli["DelaiPaiementJ"])
                f = A("T_FACTURES", dict(NumFacture=num, ClientID=cli["ID"], DateFacture=dfac, PeriodeDebut=a, PeriodeFin=b,
                                         Echeance=ech, RefContrat=cli["RefContrat"], NbRotations=len(rots), Tonnage=ton,
                                         TarifTonne=cli["TarifTonne"], MontantHT=ht, TauxTVA=tva, MontantTVA=0, MontantTTC=ht,
                                         MontantPaye=0, Solde=ht, Statut="Validee", Litige="Non",
                                         ConditionsPaiement="Paiement a %d jours fin de mois par virement" % cli["DelaiPaiementJ"],
                                         Mentions="", Empreinte=invoice_fingerprint(num, cli["Code"], ton, ht), ValidePar="comptable",
                                         ValideLe=dfac, MotifAnnulation=""), created=dfac)
                for x in rots:
                    x["FactureID"] = f["ID"]
                A("T_FACTURE_LIGNES", dict(FactureID=f["ID"], Designation="Transport de bauxite %s -> %s - periode du %s au %s" % (
                    "Fosses P1/P2/P3", self.find("T_LIEUX", "ClientID", cli["ID"])["Nom"], a.strftime("%d/%m/%Y"), b.strftime("%d/%m/%Y")),
                    Quantite=ton, Unite="t", PU=cli["TarifTonne"], Montant=ht))
                self.invoices.append(f)
        self.seq["FAC-2026"] = fact_seq
        # paiements : septembre solde (dont 1 en 2 fois), octobre : 1 payee, 1 partielle, 1 en cours
        pays = [(0, d(2026, 10, 20), 1.0), (1, d(2026, 10, 25), 0.5), (1, d(2026, 11, 3), 0.5), (2, d(2026, 10, 30), 0.6),
                (2, d(2026, 11, 4), 0.4), (3, d(2026, 11, 3), 1.0), (4, d(2026, 11, 4), 0.35), (4, d(2026, 11, 5), 0.2)]
        for idx, dd, part in pays:
            f = self.invoices[idx]
            amt = int(round(f["MontantTTC"] * part)) if part < 1 else f["MontantTTC"] - f["MontantPaye"]
            if f["MontantPaye"] + amt > f["MontantTTC"]:
                amt = f["MontantTTC"] - f["MontantPaye"]
            if idx in (1, 2) and dd >= d(2026, 11, 1):
                amt = f["MontantTTC"] - f["MontantPaye"]
            A("T_PAIEMENTS", dict(NumRecu=self.num("ENC", dd.year), FactureID=f["ID"], DatePaiement=dd, Montant=amt,
                                  ModePaiement="Virement", Reference="VIR-%s-%d" % (dd.strftime("%m%d"), 4400 + idx),
                                  Statut="Enregistre", Observations=""), created=dd)
            f["MontantPaye"] += amt
        for f in self.invoices:
            f["Solde"] = f["MontantTTC"] - f["MontantPaye"]
            f["Statut"] = "Payee" if f["Solde"] <= 0 else ("Partielle" if f["MontantPaye"] > 0 else "Validee")
        # facture ALX septembre : en retard ? (payee). Octobre ALX : en cours, litige ouvert
        f_alx = self.invoices[5]
        A("T_RECLAMATIONS", dict(NumReclamation=self.num("REC", 2026), DateRec=d(2026, 11, 4), Origine="Client",
                                 Objet="Contestation de 3 tickets de pesee (ecart pont bascule)", FactureID=f_alx["ID"],
                                 Description="Le client conteste l'ecart entre pont PB-01 et son propre pont.",
                                 MontantConteste=7500000, Statut="En traitement", Resolution=""), created=d(2026, 11, 4))
        A("T_RECLAMATIONS", dict(NumReclamation=self.num("REC", 2026), DateRec=d(2026, 10, 10), Origine="Atiko",
                                 Objet="Ecart d'heures de location septembre ATK-003", FactureID=None,
                                 Description="Atiko facture 24 h d'immobilisation que nous considerons imputables a l'exploitant : accord trouve.",
                                 MontantConteste=0, Statut="Resolue", Resolution="Heures retenues confirmees (crevaison exploitant)."), created=d(2026, 10, 10))
        A("T_RECLAMATIONS", dict(NumReclamation=self.num("REC", 2026), DateRec=d(2026, 11, 1), Origine="Interne",
                                 Objet="Ecart stock cuve carburant fin octobre", FactureID=None,
                                 Description="Ecart de jauge de 180 L constate a l'inventaire.", MontantConteste=0, Statut="Ouverte",
                                 Resolution=""), created=d(2026, 11, 1))
        A("T_RELANCES", dict(FactureID=self.invoices[3]["ID"], DateRelance=d(2026, 10, 28), Niveau="1 - Rappel", Moyen="E-mail",
                             Commentaire="Rappel courtois avant echeance"))
        A("T_RELANCES", dict(FactureID=self.invoices[2]["ID"], DateRelance=d(2026, 10, 24), Niveau="1 - Rappel", Moyen="Telephone",
                             Commentaire="Client confirme un reglement en deux fois"))

    # ------------------------------------------------------------ divers
    def build_misc(self):
        A = self.add
        for cam in self.data["T_CAMIONS"]:
            A("T_DOCUMENTS", dict(Entite="Camion", EntiteRef=cam["Code"], TypeDoc="Assurance", Reference="POL-%s" % cam["Code"],
                                  DateEmission=d(2026, 4, 1), DateExpiration=cam["AssuranceExp"], Chemin="", Observations=""), created=d(2026, 8, 25))
            A("T_DOCUMENTS", dict(Entite="Camion", EntiteRef=cam["Code"], TypeDoc="Visite technique", Reference="VT-%s" % cam["Code"],
                                  DateEmission=d(2026, 1, 15), DateExpiration=cam["VisiteTechExp"], Chemin="", Observations=""), created=d(2026, 8, 25))
        for ch in self.data["T_CHAUFFEURS"]:
            A("T_DOCUMENTS", dict(Entite="Chauffeur", EntiteRef=ch["Matricule"], TypeDoc="Permis", Reference=ch["NumPermis"],
                                  DateEmission=d(2021, 1, 1), DateExpiration=ch["ExpPermis"], Chemin="", Observations=""), created=d(2026, 8, 25))
        A("T_DOCUMENTS", dict(Entite="Contrat", EntiteRef="ATIKO", TypeDoc="Contrat location", Reference="CL-ATK-2026-001",
                              DateEmission=d(2026, 8, 20), DateExpiration=d(2027, 8, 19), Chemin="",
                              Observations="Location horaire 10 camions - maintenance contractuelle a charge Atiko (DEMO)"), created=d(2026, 8, 25))
        for c in self.data["T_CLIENTS"]:
            A("T_DOCUMENTS", dict(Entite="Client", EntiteRef=c["Code"], TypeDoc="Contrat transport", Reference=c["RefContrat"],
                                  DateEmission=d(2026, 8, 15), DateExpiration=d(2027, 8, 14), Chemin="", Observations=""), created=d(2026, 8, 25))
        for per, ton, rot in (("2026-09", 30000, 780), ("2026-10", 32000, 820), ("2026-11", 32000, 820)):
            A("T_OBJECTIFS", dict(Periode=per, TonnageObjectif=ton, RotationsObjectif=rot, DispoObjectifPct=0.9, CAObjectif=ton * 60000))
        for day in daterange(d(2026, 11, 2), d(2026, 11, 7)):
            if day.weekday() == 6:
                continue
            for cam in range(1, 11):
                if cam in (7, 10):
                    continue
                ch = self.driver_for(cam, min(day, DEMO_TODAY))
                A("T_PLANNING", dict(DatePlan=day, CamionID=cam, ChauffeurID=ch, Poste="Jour", OrigineID=1 + cam % 3,
                                     DestinationID={1: 4, 2: 4, 3: 4, 9: 5, 4: 5, 5: 5, 6: 5, 8: 6}[cam], RotationsPrevues=4,
                                     TonnagePrevu=4 * 38, Statut="Realise" if day < DEMO_TODAY else "Planifie"), created=d(2026, 11, 1))
        # clotures
        for day in daterange(D0, d(2026, 11, 3)):
            if day.weekday() == 6:
                continue
            rots = [x for x in self.data["T_ROTATIONS"] if x["DateRotation"] == day and x["Statut"] == "Validee"]
            A("T_CLOTURES", dict(TypeCloture="Journaliere", Periode=day.strftime("%Y-%m-%d"), DateCloture=day + dt.timedelta(days=1),
                                 Tonnage=round(sum(x["PoidsNet"] for x in rots), 2), NbRotations=len(rots), EnAttente=0, Anomalies=0,
                                 Statut="Cloturee", Commentaire=""), created=day)
        for per, a, b in (("2026-09", d(2026, 9, 1), d(2026, 9, 30)), ("2026-10", d(2026, 10, 1), d(2026, 10, 31))):
            rots = [x for x in self.data["T_ROTATIONS"] if a <= x["DateRotation"] <= b and x["Statut"] == "Validee"]
            A("T_CLOTURES", dict(TypeCloture="Mensuelle", Periode=per, DateCloture=b + dt.timedelta(days=2),
                                 Tonnage=round(sum(x["PoidsNet"] for x in rots), 2), NbRotations=len(rots), EnAttente=0, Anomalies=0,
                                 Statut="Cloturee", Commentaire="Cloture mensuelle %s" % per), created=b)

    def build_system(self):
        A = self.add
        users = [("admin", "Administrateur Systeme", "Administrateur", "Admin@2026"),
                 ("directeur", "Directeur General (DEMO)", "Directeur", "Direction@2026"),
                 ("exploitation", "Sekou Camara (DEMO)", "Responsable exploitation", "Exploit@2026"),
                 ("comptable", "Aissatou Diallo (DEMO)", "Comptable", "Compta@2026"),
                 ("saisie", "Mariama Bah (DEMO)", "Agent de saisie", "Saisie@2026")]
        for i, (lg, nm, pf, pw) in enumerate(users):
            salt = "S%04dMT" % (7310 + i * 97)
            A("T_USERS", dict(Login=lg, NomComplet=nm, Profil=pf, Sel=salt, HashMDP=password_hash(pw, salt),
                              DoitChanger="Non", Actif="Oui", Echecs=0, DerniereConnexion=""), created=d(2026, 8, 25))
        for p in PROFILES:
            for m in MODULES:
                A("T_DROITS", dict(Profil=p, Module=m[0], Droits=default_rights(p, m[0])))
        for (k, v, ty, cat, desc) in PARAMS:
            val = v
            if k == "CLOTURE_DERNIER_JOUR":
                val = "2026-11-03"
            if k == "CLOTURE_DERNIER_MOIS":
                val = "2026-10"
            A("T_PARAM", dict(Cle=k, Valeur=val, TypeValeur=ty, Categorie=cat, Description=desc))
        A("T_PARAM", dict(Cle="DATE_TRAVAIL", Valeur="2026-11-05", TypeValeur="Date", Categorie="Systeme",
                          Description="Date de travail forcee (vide = date du jour). Utile en demonstration."))
        A("T_PARAM", dict(Cle="CARB_STOCK_CUVE", Valeur=str(int(self.tank)), TypeValeur="Nombre", Categorie="Carburant",
                          Description="Stock theorique de la cuve (L) - recalcule a chaque mouvement"))
        # compteurs
        for k, v in sorted(self.seq.items()):
            self.data["T_SEQ"].append(dict(Cle=k, Dernier=v))
        for tb in TABLES:
            if any(f[0] == "ID" for f in tb["fields"]):
                self.data["T_SEQ"].append(dict(Cle="ID_" + tb["name"], Dernier=len(self.data[tb["name"]]) + (1 if tb["name"] == "T_AUDIT" else 0) + (1 if tb["name"] == "T_SAUVEGARDES" else 0)))
        A("T_AUDIT", dict(Horodatage="2026-11-05 07:58:12", Utilisateur="admin", Action="INSTALLATION", TableCible="",
                          EnregID="", Details="Generation de l'environnement de demonstration MINING TRANSIT 360"))
        A("T_SAUVEGARDES", dict(Horodatage="2026-11-05 07:59:00", TypeSauv="Manuelle", Fichier="(jeu de demonstration initial)",
                                TailleKo=0, Verifiee="Oui", Utilisateur="admin"))
        # recale T_SEQ ID_ pour audit/sauvegardes (ajoutes apres)
        for s in self.data["T_SEQ"]:
            if s["Cle"].startswith("ID_"):
                s["Dernier"] = len(self.data[s["Cle"][3:]])

    def build(self):
        self.build_ref()
        self.build_assign()
        self.build_ops()
        self.build_tires()
        self.build_incidents()
        self.build_finance()
        self.build_misc()
        self.build_system()
        return self.data


SKIP_ACC = {"Sel", "HashMDP", "Empreinte", "NumSerie", "NumTicket", "NumBon", "Login", "Cle", "Valeur", "Periode"}


def generate():
    from fr import acc
    data = Demo().build()
    for tname, rows in data.items():
        for rec in rows:
            for k, v in list(rec.items()):
                if isinstance(v, str) and k not in SKIP_ACC:
                    rec[k] = acc(v)
    # les valeurs de parametres sont deja gerees par schema.PARAMS
    return data


if __name__ == "__main__":
    data = generate()
    for k, v in data.items():
        print("%-20s %6d" % (k, len(v)))
