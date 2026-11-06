# -*- coding: utf-8 -*-
"""
MINING TRANSIT 360 - Source unique de verite du modele de donnees.

Ce fichier decrit :
  * toutes les tables (ListObjects Excel) et leurs champs ;
  * le registre des modules (menu, fenetres, actions) ;
  * les parametres par defaut ;
  * la matrice des droits par profil.

Il est lu par :
  * tools/build_workbook.py  -> cree les feuilles / tables / SYS_* du classeur ;
  * tools/demo_data.py       -> genere le jeu de demonstration ;
  * tools/make_docs.py       -> genere docs/02_MODELE_DE_DONNEES.md ;
  * tests/test_*.py          -> controles d'integrite.

Le VBA ne lit PAS ce fichier : il lit les tables SYS_SCHEMA / SYS_MODULES
ecrites dans le classeur (donc l'application reste autonome).

Types de champs
  ID    identifiant technique (Long, auto)
  TXT   texte court            MEMO  texte long
  INT   entier                 NUM   decimal (2 dec.)
  MNY   montant GNF (entier)   PCT   pourcentage
  DATE  date                   TIME  heure (hh:mm)
  BOOL  Oui / Non              LIST  liste fermee (vals=a|b|c)
  FK    cle etrangere (fk=TABLE.ColonneAffichee)
  FILE  chemin de justificatif
Options (separees par ';')
  req  obligatoire   uniq  unique   ro  lecture seule (calcule / systeme)
  list affiche dans la liste       w=NN largeur colonne liste (pt)
  def=valeur par defaut            min=0 valeur minimale
  hid  masque dans le formulaire   seq=PREFIXE numerotation automatique
"""

AUDIT_FIELDS = [
    ("CreePar", "Cree par", "TXT", "ro;hid"),
    ("CreeLe", "Cree le", "DATE", "ro;hid"),
    ("ModifPar", "Modifie par", "TXT", "ro;hid"),
    ("ModifLe", "Modifie le", "DATE", "ro;hid"),
]

# ---------------------------------------------------------------------------
# TABLES
# (nom_table, feuille, libelle, audit(bool), [champs])
# ---------------------------------------------------------------------------
TABLES = []


def T(name, sheet, label, fields, audit=True):
    TABLES.append({"name": name, "sheet": sheet, "label": label,
                   "audit": audit, "fields": fields})


ST_CAMION = "Disponible|En service|En panne|Immobilise|Hors service"
ST_VALID = "Saisie|Validee|Rejetee|Annulee"

# ------------------------------- REFERENTIELS -------------------------------
T("T_CLIENTS", "DB_CLIENTS", "Clients", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Code", "Code client", "TXT", "req;uniq;list;w=50"),
    ("RaisonSociale", "Raison sociale", "TXT", "req;list;w=170"),
    ("Adresse", "Adresse", "TXT", ""),
    ("Telephone", "Telephone", "TXT", "list;w=80"),
    ("Email", "E-mail", "TXT", ""),
    ("NIF", "NIF / RCCM", "TXT", ""),
    ("RefContrat", "Reference contrat", "TXT", "list;w=100"),
    ("TarifTonne", "Tarif transport (GNF/t)", "MNY", "req;min=0;list;w=80"),
    ("DelaiPaiementJ", "Delai de paiement (jours)", "INT", "req;min=0;def=30;list;w=50"),
    ("Contact", "Interlocuteur", "TXT", ""),
    ("Actif", "Actif", "BOOL", "def=Oui;list;w=35"),
])

T("T_FOURNISSEURS", "DB_FOURNISSEURS", "Fournisseurs", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Code", "Code", "TXT", "req;uniq;list;w=50"),
    ("Nom", "Nom", "TXT", "req;list;w=170"),
    ("Categorie", "Categorie", "LIST",
     "vals=Carburant|Pneumatiques|Pieces et consommables|Services|Proprietaire flotte|Divers;req;list;w=110"),
    ("Telephone", "Telephone", "TXT", "list;w=80"),
    ("Adresse", "Adresse", "TXT", ""),
    ("NIF", "NIF / RCCM", "TXT", ""),
    ("Actif", "Actif", "BOOL", "def=Oui;list;w=35"),
])

T("T_LIEUX", "DB_LIEUX", "Lieux (origines / destinations)", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Code", "Code", "TXT", "req;uniq;list;w=50"),
    ("Nom", "Nom", "TXT", "req;list;w=170"),
    ("TypeLieu", "Type", "LIST", "vals=Chargement|Dechargement|Atelier|Parc;req;list;w=80"),
    ("ClientID", "Client destinataire", "FK", "fk=T_CLIENTS.Code;list;w=60"),
    ("DistanceKm", "Distance aller (km)", "NUM", "min=0;list;w=50"),
    ("Actif", "Actif", "BOOL", "def=Oui;list;w=35"),
])

# ------------------------------- FLOTTE -------------------------------------
T("T_CAMIONS", "DB_CAMIONS", "Camions", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Code", "Code camion", "TXT", "req;uniq;list;w=55"),
    ("Immatriculation", "Immatriculation", "TXT", "req;uniq;list;w=80"),
    ("Marque", "Marque", "TXT", "req;list;w=60"),
    ("Modele", "Modele", "TXT", "list;w=70"),
    ("CapaciteT", "Capacite utile (t)", "NUM", "req;min=0;list;w=45"),
    ("Proprietaire", "Proprietaire", "TXT", "req;def=Atiko Guinee;list;w=80"),
    ("NumChassis", "N° chassis", "TXT", ""),
    ("DateMiseService", "Date de mise en service", "DATE", ""),
    ("Statut", "Statut", "LIST", "vals=" + ST_CAMION + ";req;def=Disponible;list;w=70"),
    ("TarifHoraire", "Tarif location (GNF/h)", "MNY", "req;min=0;list;w=70"),
    ("HeuresMinJour", "Heures minimum facturables/jour", "NUM", "min=0;def=0"),
    ("ConsoRefLH", "Consommation de reference (L/h)", "NUM", "min=0;def=18;list;w=45"),
    ("NbPositionsPneus", "Nombre de positions pneus", "INT", "min=0;def=10"),
    ("AssuranceExp", "Expiration assurance", "DATE", "list;w=60"),
    ("VisiteTechExp", "Expiration visite technique", "DATE", "list;w=60"),
    ("CompteurHeures", "Compteur moteur (h)", "NUM", "min=0"),
    ("Observations", "Observations", "MEMO", ""),
    ("Actif", "Actif", "BOOL", "def=Oui;list;w=35"),
])

T("T_CHAUFFEURS", "DB_CHAUFFEURS", "Chauffeurs", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Matricule", "Matricule", "TXT", "req;uniq;list;w=55"),
    ("Nom", "Nom", "TXT", "req;list;w=80"),
    ("Prenom", "Prenom", "TXT", "req;list;w=80"),
    ("Telephone", "Telephone", "TXT", "list;w=75"),
    ("Adresse", "Adresse", "TXT", ""),
    ("ContactUrgence", "Contact d'urgence", "TXT", ""),
    ("TelUrgence", "Tel. urgence", "TXT", ""),
    ("NumPermis", "N° permis", "TXT", "req;uniq"),
    ("CategoriePermis", "Categorie permis", "LIST", "vals=C|CE|D|EC|C1E;req;def=C;list;w=40"),
    ("ExpPermis", "Expiration permis", "DATE", "req;list;w=60"),
    ("DateEmbauche", "Date d'embauche", "DATE", ""),
    ("SalaireMensuel", "Salaire mensuel (GNF)", "MNY", "min=0"),
    ("PrimeRotation", "Prime par rotation (GNF)", "MNY", "min=0;def=0"),
    ("Statut", "Statut", "LIST", "vals=Actif|Conge|Suspendu|Sorti;req;def=Actif;list;w=55"),
    ("Observations", "Observations", "MEMO", ""),
])

T("T_AFFECTATIONS", "DB_AFFECTATIONS", "Affectations chauffeur / camion", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("CamionID", "Camion", "FK", "fk=T_CAMIONS.Code;req;list;w=55"),
    ("ChauffeurID", "Chauffeur", "FK", "fk=T_CHAUFFEURS.Matricule;req;list;w=60"),
    ("DateDebut", "Date debut", "DATE", "req;list;w=60"),
    ("DateFin", "Date fin (vide = en cours)", "DATE", "list;w=60"),
    ("Poste", "Poste", "LIST", "vals=Jour|Nuit|Journee complete;req;def=Jour;list;w=55"),
    ("Statut", "Statut", "LIST", "vals=Active|Terminee|Suspendue;req;def=Active;list;w=55"),
    ("Observations", "Observations", "MEMO", ""),
])

# --------------------------- EXPLOITATION -----------------------------------
T("T_OBJECTIFS", "DB_OBJECTIFS", "Objectifs mensuels", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Periode", "Periode (AAAA-MM)", "TXT", "req;uniq;list;w=60"),
    ("TonnageObjectif", "Objectif tonnage (t)", "NUM", "req;min=0;list;w=70"),
    ("RotationsObjectif", "Objectif rotations", "INT", "min=0;list;w=60"),
    ("DispoObjectifPct", "Objectif disponibilite (%)", "PCT", "min=0;def=0.85;list;w=60"),
    ("CAObjectif", "Objectif CA (GNF)", "MNY", "min=0;list;w=80"),
])

T("T_PLANNING", "DB_PLANNING", "Programme journalier de transport", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("DatePlan", "Date", "DATE", "req;list;w=60"),
    ("CamionID", "Camion", "FK", "fk=T_CAMIONS.Code;req;list;w=55"),
    ("ChauffeurID", "Chauffeur", "FK", "fk=T_CHAUFFEURS.Matricule;list;w=60"),
    ("Poste", "Poste", "LIST", "vals=Jour|Nuit|Journee complete;def=Jour;list;w=50"),
    ("OrigineID", "Origine", "FK", "fk=T_LIEUX.Code;list;w=50"),
    ("DestinationID", "Destination", "FK", "fk=T_LIEUX.Code;list;w=50"),
    ("RotationsPrevues", "Rotations prevues", "INT", "min=0;list;w=50"),
    ("TonnagePrevu", "Tonnage prevu (t)", "NUM", "min=0;list;w=55"),
    ("Statut", "Statut", "LIST", "vals=Planifie|Realise|Annule;def=Planifie;list;w=55"),
])

T("T_ROTATIONS", "DB_ROTATIONS", "Rotations", [
    ("ID", "ID", "ID", "ro;list;w=35"),
    ("NumRotation", "N° rotation", "TXT", "ro;uniq;seq=ROT;list;w=80"),
    ("DateRotation", "Date", "DATE", "req;list;w=55"),
    ("CamionID", "Camion", "FK", "fk=T_CAMIONS.Code;req;list;w=50"),
    ("ChauffeurID", "Chauffeur", "FK", "fk=T_CHAUFFEURS.Matricule;req;list;w=55"),
    ("HDepart", "Heure depart", "TIME", "req;list;w=40"),
    ("HArrivee", "Heure arrivee", "TIME", "list;w=40"),
    ("OrigineID", "Origine", "FK", "fk=T_LIEUX.Code;req;list;w=45"),
    ("DestinationID", "Destination", "FK", "fk=T_LIEUX.Code;req;list;w=50"),
    ("ClientID", "Client", "FK", "fk=T_CLIENTS.Code;req;list;w=40"),
    ("NumTicket", "N° ticket de pesee", "TXT", "req;uniq;list;w=65"),
    ("PoidsBrut", "Poids brut (t)", "NUM", "req;min=0;list;w=45"),
    ("Tare", "Tare (t)", "NUM", "req;min=0;list;w=40"),
    ("PoidsNet", "Poids net (t)", "NUM", "ro;list;w=45"),
    ("Statut", "Statut", "LIST", "vals=" + ST_VALID + ";ro;def=Saisie;list;w=50"),
    ("MotifRejet", "Motif rejet / annulation", "TXT", "ro"),
    ("FactureID", "Facture", "FK", "fk=T_FACTURES.NumFacture;ro;list;w=70"),
    ("Justificatif", "Justificatif (ticket scanne)", "FILE", ""),
    ("ValidePar", "Valide par", "TXT", "ro;hid"),
    ("ValideLe", "Valide le", "DATE", "ro;hid"),
    ("Observations", "Observations", "MEMO", ""),
])

T("T_PESEES", "DB_PESEES", "Pesees et tickets", [
    ("ID", "ID", "ID", "ro;list;w=35"),
    ("NumTicket", "N° ticket", "TXT", "req;uniq;list;w=65"),
    ("RotationID", "Rotation", "FK", "fk=T_ROTATIONS.NumRotation;list;w=80"),
    ("DatePesee", "Date", "DATE", "req;list;w=55"),
    ("HeurePesee", "Heure", "TIME", "list;w=40"),
    ("Pont", "Pont bascule", "LIST", "vals=PB-01 Mine|PB-02 Gare|PB-03 Port;def=PB-02 Gare;list;w=60"),
    ("CamionID", "Camion", "FK", "fk=T_CAMIONS.Code;req;list;w=50"),
    ("PoidsBrut", "Poids brut (t)", "NUM", "req;min=0;list;w=45"),
    ("Tare", "Tare (t)", "NUM", "req;min=0;list;w=40"),
    ("PoidsNet", "Poids net (t)", "NUM", "ro;list;w=45"),
    ("Operateur", "Operateur pesage", "TXT", "list;w=70"),
    ("Statut", "Statut", "LIST", "vals=" + ST_VALID + ";ro;def=Saisie;list;w=50"),
    ("Justificatif", "Ticket scanne", "FILE", ""),
    ("Observations", "Observations", "MEMO", ""),
])

# --------------------------- CARBURANT / LOCATION ---------------------------
T("T_CARBURANT", "DB_CARBURANT", "Carburant", [
    ("ID", "ID", "ID", "ro;list;w=35"),
    ("NumBon", "N° bon", "TXT", "req;uniq;list;w=65"),
    ("DateOp", "Date", "DATE", "req;list;w=55"),
    ("TypeOp", "Type", "LIST", "vals=Approvisionnement|Distribution;req;def=Distribution;list;w=80"),
    ("CamionID", "Camion", "FK", "fk=T_CAMIONS.Code;list;w=50"),
    ("ChauffeurID", "Chauffeur", "FK", "fk=T_CHAUFFEURS.Matricule;list;w=55"),
    ("FournisseurID", "Fournisseur", "FK", "fk=T_FOURNISSEURS.Code;list;w=55"),
    ("Litres", "Quantite (L)", "NUM", "req;min=0;list;w=50"),
    ("PrixLitre", "Prix unitaire (GNF/L)", "MNY", "req;min=0;list;w=50"),
    ("Montant", "Montant (GNF)", "MNY", "ro;list;w=75"),
    ("CompteurH", "Compteur moteur (h)", "NUM", "min=0;list;w=50"),
    ("HeuresDepuisPlein", "Heures depuis plein", "NUM", "ro"),
    ("ConsoLH", "Consommation (L/h)", "NUM", "ro;list;w=45"),
    ("Anomalie", "Anomalie detectee", "TXT", "ro;list;w=110"),
    ("Statut", "Statut", "LIST", "vals=" + ST_VALID + ";ro;def=Saisie;list;w=50"),
    ("Justificatif", "Bon scanne", "FILE", ""),
    ("Observations", "Observations", "MEMO", ""),
])

T("T_LOCATION", "DB_LOCATION", "Releves horaires de location", [
    ("ID", "ID", "ID", "ro;list;w=35"),
    ("DateReleve", "Date", "DATE", "req;list;w=55"),
    ("CamionID", "Camion", "FK", "fk=T_CAMIONS.Code;req;list;w=50"),
    ("HDebut", "Heure debut", "TIME", "req;list;w=40"),
    ("HFin", "Heure fin", "TIME", "req;list;w=40"),
    ("HeuresBrutes", "Heures brutes", "NUM", "ro;list;w=40"),
    ("HeuresAttente", "Heures d'attente", "NUM", "min=0;def=0;list;w=40"),
    ("HeuresImmobAtiko", "Immobilisation imputable Atiko (h)", "NUM", "min=0;def=0;list;w=45"),
    ("HeuresImmobExploitant", "Immobilisation imputable exploitant (h)", "NUM", "min=0;def=0"),
    ("HeuresRetenues", "Heures retenues", "NUM", "ro;list;w=45"),
    ("TarifHoraire", "Tarif (GNF/h)", "MNY", "ro;list;w=60"),
    ("Montant", "Montant (GNF)", "MNY", "ro;list;w=75"),
    ("Statut", "Statut", "LIST", "vals=" + ST_VALID + ";ro;def=Saisie;list;w=50"),
    ("Justificatif", "Justificatif", "FILE", ""),
    ("Observations", "Observations", "MEMO", ""),
])

# --------------------------- PNEUS / STOCK ----------------------------------
T("T_PNEUS", "DB_PNEUS", "Pneumatiques", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("NumSerie", "N° de serie", "TXT", "req;uniq;list;w=75"),
    ("Reference", "Reference", "TXT", "list;w=70"),
    ("Marque", "Marque", "TXT", "req;list;w=60"),
    ("Dimension", "Dimension", "TXT", "req;list;w=70"),
    ("FournisseurID", "Fournisseur", "FK", "fk=T_FOURNISSEURS.Code"),
    ("PrixAchat", "Prix d'achat (GNF)", "MNY", "req;min=0;list;w=70"),
    ("DateEntree", "Date d'entree", "DATE", "req"),
    ("Statut", "Statut", "LIST", "vals=Stock|Monte|Reparation|Reforme;ro;def=Stock;list;w=55"),
    ("CamionID", "Camion actuel", "FK", "fk=T_CAMIONS.Code;ro;list;w=50"),
    ("Position", "Position", "TXT", "ro;list;w=40"),
    ("HeuresCumulees", "Heures cumulees", "NUM", "ro;list;w=45"),
    ("DureeVieCibleH", "Duree de vie cible (h)", "NUM", "min=0;def=2500"),
    ("ProfondeurMM", "Profondeur sculpture (mm)", "NUM", "min=0;list;w=45"),
    ("DateMontage", "Date dernier montage", "DATE", "ro"),
    ("Observations", "Observations", "MEMO", ""),
])

T("T_PNEU_MVT", "DB_PNEU_MVT", "Mouvements de pneus", [
    ("ID", "ID", "ID", "ro;list;w=35"),
    ("DateMvt", "Date", "DATE", "req;list;w=55"),
    ("PneuID", "Pneu", "FK", "fk=T_PNEUS.NumSerie;req;list;w=75"),
    ("TypeMvt", "Mouvement", "LIST",
     "vals=Entree|Montage|Demontage|Transfert|Reparation|Retour reparation|Réforme;req;list;w=70"),
    ("CamionID", "Camion", "FK", "fk=T_CAMIONS.Code;list;w=50"),
    ("Position", "Position", "TXT", "list;w=40"),
    ("CamionDestID", "Camion destination", "FK", "fk=T_CAMIONS.Code;list;w=50"),
    ("PositionDest", "Position destination", "TXT", ""),
    ("CompteurH", "Compteur moteur (h)", "NUM", "min=0"),
    ("Cout", "Cout (GNF)", "MNY", "min=0;def=0;list;w=65"),
    ("Motif", "Motif", "TXT", "list;w=120"),
])

T("T_ARTICLES", "DB_ARTICLES", "Articles et consommables", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Code", "Code", "TXT", "req;uniq;list;w=55"),
    ("Designation", "Designation", "TXT", "req;list;w=170"),
    ("Famille", "Famille", "LIST", "vals=Lubrifiants|Filtres|Pieces|Securite|Divers;req;list;w=70"),
    ("Unite", "Unite", "TXT", "req;def=U;list;w=35"),
    ("StockMin", "Stock minimum", "NUM", "min=0;def=0;list;w=50"),
    ("PrixMoyen", "Prix moyen (GNF)", "MNY", "ro;list;w=65"),
    ("Stock", "Stock actuel", "NUM", "ro;list;w=50"),
    ("Actif", "Actif", "BOOL", "def=Oui"),
])

T("T_STOCK_MVT", "DB_STOCK_MVT", "Mouvements de stock", [
    ("ID", "ID", "ID", "ro;list;w=35"),
    ("DateMvt", "Date", "DATE", "req;list;w=55"),
    ("ArticleID", "Article", "FK", "fk=T_ARTICLES.Code;req;list;w=55"),
    ("TypeMvt", "Type", "LIST", "vals=Entree|Sortie|Ajustement;req;list;w=55"),
    ("Quantite", "Quantite", "NUM", "req;min=0;list;w=45"),
    ("PU", "Prix unitaire (GNF)", "MNY", "min=0;list;w=60"),
    ("Montant", "Montant (GNF)", "MNY", "ro;list;w=70"),
    ("CamionID", "Camion", "FK", "fk=T_CAMIONS.Code;list;w=50"),
    ("FournisseurID", "Fournisseur", "FK", "fk=T_FOURNISSEURS.Code"),
    ("Reference", "Reference piece", "TXT", "list;w=70"),
    ("Observations", "Observations", "MEMO", ""),
])

# --------------------------- INCIDENTS / MAINTENANCE ------------------------
RESP = "Atiko (contrat)|Exploitant|Tiers / assurance|A determiner"
T("T_INCIDENTS", "DB_INCIDENTS", "Incidents", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("NumIncident", "N° incident", "TXT", "ro;uniq;seq=INC;list;w=75"),
    ("DateIncident", "Date", "DATE", "req;list;w=55"),
    ("TypeIncident", "Type", "LIST",
     "vals=Panne mecanique|Crevaison|Accident|Panne electrique|Attente chargement|Securite|Autre;req;list;w=80"),
    ("CamionID", "Camion", "FK", "fk=T_CAMIONS.Code;req;list;w=50"),
    ("ChauffeurID", "Chauffeur", "FK", "fk=T_CHAUFFEURS.Matricule;list;w=55"),
    ("Description", "Description", "MEMO", "req"),
    ("Gravite", "Gravite", "LIST", "vals=Mineure|Moyenne|Majeure|Critique;req;def=Moyenne;list;w=50"),
    ("Responsable", "Responsable contractuel", "LIST", "vals=" + RESP + ";req;def=A determiner;list;w=80"),
    ("Immobilisation", "Immobilisation", "BOOL", "def=Non;list;w=40"),
    ("DebutImmob", "Debut immobilisation", "DATE", ""),
    ("HeureDebutImmob", "Heure debut immob.", "TIME", ""),
    ("DateReprise", "Date de reprise", "DATE", "list;w=55"),
    ("HeureReprise", "Heure de reprise", "TIME", ""),
    ("DureeImmobH", "Duree immobilisation (h)", "NUM", "ro;list;w=45"),
    ("Intervention", "Intervention realisee", "MEMO", ""),
    ("Cout", "Cout total estime (GNF)", "MNY", "min=0;def=0"),
    ("CoutExploitant", "Cout a charge exploitant (GNF)", "MNY", "ro;list;w=70"),
    ("Statut", "Statut", "LIST", "vals=Ouvert|En cours|Clôturé;ro;def=Ouvert;list;w=50"),
    ("Justificatif", "Photos / justificatif", "FILE", ""),
])

T("T_MAINTENANCE", "DB_MAINTENANCE", "Interventions de maintenance", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("NumOT", "N° ordre de travail", "TXT", "ro;uniq;seq=OT;list;w=70"),
    ("DateOT", "Date", "DATE", "req;list;w=55"),
    ("CamionID", "Camion", "FK", "fk=T_CAMIONS.Code;req;list;w=50"),
    ("IncidentID", "Incident lie", "FK", "fk=T_INCIDENTS.NumIncident;list;w=70"),
    ("TypeMaint", "Type", "LIST", "vals=Preventive|Corrective|Revision|Controle;req;list;w=60"),
    ("Intervenant", "Intervenant", "TXT", "list;w=80"),
    ("Responsable", "Responsable contractuel", "LIST", "vals=" + RESP + ";req;def=Atiko (contrat);list;w=80"),
    ("Description", "Description", "MEMO", "req"),
    ("DateDebut", "Debut", "DATE", ""),
    ("DateFin", "Fin", "DATE", "list;w=55"),
    ("Cout", "Cout (GNF)", "MNY", "min=0;def=0;list;w=65"),
    ("Statut", "Statut", "LIST", "vals=Planifiee|En cours|Terminee|Annulee;def=Planifiee;list;w=55"),
])

# --------------------------- FINANCES ---------------------------------------
DEP_CAT = ("Personnel temporaire|Restauration|Securite et EPI|Lubrifiants et consommables|"
           "Entretien a charge exploitant|Frais administratifs|Telecom|Transport personnel|"
           "Taxes et redevances|Charges exceptionnelles|Divers|Carburant|Pneumatiques|Location camions")
T("T_DEPENSES", "DB_DEPENSES", "Depenses", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("NumPiece", "N° piece", "TXT", "ro;uniq;seq=DEP;list;w=70"),
    ("DateDep", "Date", "DATE", "req;list;w=55"),
    ("Categorie", "Categorie", "LIST", "vals=" + DEP_CAT + ";req;list;w=110"),
    ("Libelle", "Libelle", "TXT", "req;list;w=150"),
    ("FournisseurID", "Fournisseur", "FK", "fk=T_FOURNISSEURS.Code;list;w=55"),
    ("RefFacture", "Ref. facture fournisseur", "TXT", "list;w=70"),
    ("Montant", "Montant (GNF)", "MNY", "req;min=0;list;w=75"),
    ("ModePaiement", "Mode de paiement", "LIST", "vals=Especes|Virement|Cheque|Mobile money|Caisse;req;def=Virement"),
    ("CamionID", "Camion concerne", "FK", "fk=T_CAMIONS.Code;list;w=50"),
    ("CentreCout", "Centre de cout", "LIST", "vals=Exploitation|Flotte|Administration|Direction|Securite;def=Exploitation;list;w=70"),
    ("Responsable", "Responsable contractuel", "LIST", "vals=Exploitant|Atiko (contrat)|Refacturable client;req;def=Exploitant;list;w=75"),
    ("Exceptionnelle", "Charge exceptionnelle", "BOOL", "def=Non"),
    ("Statut", "Statut", "LIST", "vals=" + ST_VALID + ";ro;def=Saisie;list;w=50"),
    ("Justificatif", "Justificatif", "FILE", ""),
    ("ValidePar", "Valide par", "TXT", "ro;hid"),
])

T("T_PERSONNEL", "DB_PERSONNEL", "Personnel (hors chauffeurs)", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Matricule", "Matricule", "TXT", "req;uniq;list;w=55"),
    ("Nom", "Nom et prenom", "TXT", "req;list;w=140"),
    ("Fonction", "Fonction", "TXT", "req;list;w=110"),
    ("Telephone", "Telephone", "TXT", "list;w=75"),
    ("DateEmbauche", "Date d'embauche", "DATE", ""),
    ("SalaireMensuel", "Salaire mensuel (GNF)", "MNY", "min=0;list;w=75"),
    ("Statut", "Statut", "LIST", "vals=Actif|Conge|Sorti;def=Actif;list;w=50"),
])

T("T_AVANCES", "DB_AVANCES", "Avances au personnel", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("DateAvance", "Date", "DATE", "req;list;w=55"),
    ("Beneficiaire", "Beneficiaire (matricule)", "TXT", "req;list;w=70"),
    ("Montant", "Montant (GNF)", "MNY", "req;min=0;list;w=70"),
    ("Motif", "Motif", "TXT", "list;w=140"),
    ("MontantRembourse", "Deja rembourse (GNF)", "MNY", "min=0;def=0;list;w=70"),
    ("Solde", "Reste a rembourser", "MNY", "ro;list;w=70"),
    ("Statut", "Statut", "LIST", "vals=En cours|Soldee|Annulee;ro;def=En cours;list;w=50"),
])

T("T_CAISSE", "DB_CAISSE", "Caisse (petite caisse)", [
    ("ID", "ID", "ID", "ro;list;w=35"),
    ("DateOp", "Date", "DATE", "req;list;w=55"),
    ("Sens", "Sens", "LIST", "vals=Entree|Sortie;req;list;w=45"),
    ("Libelle", "Libelle", "TXT", "req;list;w=170"),
    ("Montant", "Montant (GNF)", "MNY", "req;min=0;list;w=70"),
    ("Reference", "Reference", "TXT", "list;w=70"),
    ("SoldeApres", "Solde apres operation", "MNY", "ro;list;w=75"),
])

T("T_FACTURES", "DB_FACTURES", "Factures clients", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("NumFacture", "N° facture", "TXT", "ro;uniq;list;w=80"),
    ("ClientID", "Client", "FK", "fk=T_CLIENTS.Code;req;list;w=45"),
    ("DateFacture", "Date facture", "DATE", "req;list;w=55"),
    ("PeriodeDebut", "Periode du", "DATE", "req;list;w=55"),
    ("PeriodeFin", "Periode au", "DATE", "req;list;w=55"),
    ("Echeance", "Echeance", "DATE", "ro;list;w=55"),
    ("RefContrat", "Reference contractuelle", "TXT", ""),
    ("NbRotations", "Nombre de rotations", "INT", "ro"),
    ("Tonnage", "Tonnage valide (t)", "NUM", "ro;list;w=55"),
    ("TarifTonne", "Tarif (GNF/t)", "MNY", "ro"),
    ("MontantHT", "Montant HT (GNF)", "MNY", "ro"),
    ("TauxTVA", "Taux TVA", "PCT", "ro"),
    ("MontantTVA", "TVA (GNF)", "MNY", "ro"),
    ("MontantTTC", "Montant TTC (GNF)", "MNY", "ro;list;w=80"),
    ("MontantPaye", "Montant regle (GNF)", "MNY", "ro;list;w=80"),
    ("Solde", "Solde du (GNF)", "MNY", "ro;list;w=75"),
    ("Statut", "Statut", "LIST", "vals=Brouillon|Validee|Partielle|Payee|Annulee;ro;def=Brouillon;list;w=55"),
    ("Litige", "En litige", "BOOL", "def=Non"),
    ("ConditionsPaiement", "Conditions de paiement", "TXT", ""),
    ("Mentions", "Mentions", "MEMO", ""),
    ("Empreinte", "Empreinte d'integrite", "TXT", "ro;hid"),
    ("ValidePar", "Validee par", "TXT", "ro;hid"),
    ("ValideLe", "Validee le", "DATE", "ro;hid"),
    ("MotifAnnulation", "Motif d'annulation", "TXT", "ro"),
])

T("T_FACTURE_LIGNES", "DB_FACTURE_LIGNES", "Lignes de facture", [
    ("ID", "ID", "ID", "ro;list;w=35"),
    ("FactureID", "Facture", "FK", "fk=T_FACTURES.NumFacture;req;list;w=80"),
    ("Designation", "Designation", "TXT", "req;list;w=220"),
    ("Quantite", "Quantite", "NUM", "req;list;w=60"),
    ("Unite", "Unite", "TXT", "list;w=40"),
    ("PU", "Prix unitaire (GNF)", "MNY", "list;w=70"),
    ("Montant", "Montant (GNF)", "MNY", "list;w=80"),
], audit=False)

T("T_PAIEMENTS", "DB_PAIEMENTS", "Paiements clients", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("NumRecu", "N° encaissement", "TXT", "ro;uniq;seq=ENC;list;w=75"),
    ("FactureID", "Facture", "FK", "fk=T_FACTURES.NumFacture;req;list;w=80"),
    ("DatePaiement", "Date", "DATE", "req;list;w=55"),
    ("Montant", "Montant (GNF)", "MNY", "req;min=0;list;w=80"),
    ("ModePaiement", "Mode", "LIST", "vals=Virement|Cheque|Especes|Compensation;req;def=Virement;list;w=60"),
    ("Reference", "Reference bancaire", "TXT", "list;w=90"),
    ("Statut", "Statut", "LIST", "vals=Enregistre|Annule;ro;def=Enregistre;list;w=55"),
    ("Observations", "Observations", "MEMO", ""),
])

T("T_RELANCES", "DB_RELANCES", "Relances clients", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("FactureID", "Facture", "FK", "fk=T_FACTURES.NumFacture;req;list;w=80"),
    ("DateRelance", "Date", "DATE", "req;list;w=55"),
    ("Niveau", "Niveau", "LIST", "vals=1 - Rappel|2 - Relance|3 - Mise en demeure;req;list;w=80"),
    ("Moyen", "Moyen", "LIST", "vals=Telephone|E-mail|Courrier|Visite;req;list;w=60"),
    ("Commentaire", "Commentaire", "MEMO", "list;w=200"),
])

T("T_RECLAMATIONS", "DB_RECLAMATIONS", "Reclamations et litiges", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("NumReclamation", "N° reclamation", "TXT", "ro;uniq;seq=REC;list;w=75"),
    ("DateRec", "Date", "DATE", "req;list;w=55"),
    ("Origine", "Origine", "LIST", "vals=Client|Atiko|Interne|Fournisseur;req;list;w=55"),
    ("Objet", "Objet", "TXT", "req;list;w=160"),
    ("FactureID", "Facture concernee", "FK", "fk=T_FACTURES.NumFacture;list;w=75"),
    ("Description", "Description", "MEMO", ""),
    ("MontantConteste", "Montant conteste (GNF)", "MNY", "min=0;def=0;list;w=70"),
    ("Statut", "Statut", "LIST", "vals=Ouverte|En traitement|Resolue|Rejetee;def=Ouverte;list;w=55"),
    ("Resolution", "Resolution", "MEMO", ""),
])

T("T_DOCUMENTS", "DB_DOCUMENTS", "Gestion documentaire", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Entite", "Rattache a", "LIST", "vals=Camion|Chauffeur|Contrat|Client|Fournisseur|Societe;req;list;w=55"),
    ("EntiteRef", "Reference entite", "TXT", "req;list;w=65"),
    ("TypeDoc", "Type de document", "LIST",
     "vals=Assurance|Visite technique|Carte grise|Permis|Contrat location|Contrat transport|Piece identite|Attestation|Autre;req;list;w=85"),
    ("Reference", "Reference", "TXT", "list;w=75"),
    ("DateEmission", "Emis le", "DATE", ""),
    ("DateExpiration", "Expire le", "DATE", "list;w=55"),
    ("Chemin", "Fichier", "FILE", "list;w=140"),
    ("Observations", "Observations", "MEMO", ""),
])

T("T_CLOTURES", "DB_CLOTURES", "Clotures journalieres et mensuelles", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("TypeCloture", "Type", "LIST", "vals=Journaliere|Mensuelle;req;list;w=60"),
    ("Periode", "Periode", "TXT", "req;list;w=65"),
    ("DateCloture", "Date de cloture", "DATE", "ro;list;w=55"),
    ("Tonnage", "Tonnage valide (t)", "NUM", "ro;list;w=60"),
    ("NbRotations", "Rotations validees", "INT", "ro;list;w=55"),
    ("EnAttente", "Saisies en attente", "INT", "ro;list;w=55"),
    ("Anomalies", "Anomalies", "INT", "ro;list;w=50"),
    ("Statut", "Statut", "LIST", "vals=Cloturee|Reouverte;ro;list;w=55"),
    ("Commentaire", "Commentaire", "MEMO", "list;w=150"),
])

# --------------------------- SYSTEME ----------------------------------------
T("T_USERS", "SYS_USERS", "Utilisateurs", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Login", "Identifiant", "TXT", "req;uniq;list;w=70"),
    ("NomComplet", "Nom complet", "TXT", "req;list;w=140"),
    ("Profil", "Profil", "LIST",
     "vals=Administrateur|Directeur|Responsable exploitation|Comptable|Agent de saisie;req;list;w=110"),
    ("Sel", "Sel", "TXT", "ro;hid"),
    ("HashMDP", "Empreinte mot de passe", "TXT", "ro;hid"),
    ("DoitChanger", "Doit changer le mot de passe", "BOOL", "def=Oui;list;w=50"),
    ("Actif", "Actif", "BOOL", "def=Oui;list;w=35"),
    ("Echecs", "Echecs de connexion", "INT", "ro;def=0"),
    ("DerniereConnexion", "Derniere connexion", "TXT", "ro;list;w=95"),
])

T("T_DROITS", "SYS_DROITS", "Droits par profil", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Profil", "Profil", "TXT", "req;list;w=120"),
    ("Module", "Module", "TXT", "req;list;w=100"),
    ("Droits", "Droits (L C M V S I)", "TXT", "list;w=80"),
], audit=False)

T("T_PARAM", "SYS_PARAM", "Parametres", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Cle", "Cle", "TXT", "req;uniq;ro;list;w=150"),
    ("Valeur", "Valeur", "TXT", "list;w=170"),
    ("TypeValeur", "Type", "LIST", "vals=Texte|Nombre|Date|Oui/Non;ro;list;w=50"),
    ("Categorie", "Categorie", "TXT", "ro;list;w=80"),
    ("Description", "Description", "TXT", "ro;list;w=240"),
], audit=False)

T("T_SEQ", "SYS_SEQ", "Compteurs de numerotation", [
    ("Cle", "Cle", "TXT", "req;uniq;list;w=150"),
    ("Dernier", "Dernier numero", "INT", "list;w=60"),
], audit=False)

T("T_AUDIT", "SYS_AUDIT", "Journal d'audit", [
    ("ID", "ID", "ID", "ro;list;w=40"),
    ("Horodatage", "Date / heure", "TXT", "ro;list;w=95"),
    ("Utilisateur", "Utilisateur", "TXT", "ro;list;w=60"),
    ("Action", "Action", "TXT", "ro;list;w=70"),
    ("TableCible", "Table", "TXT", "ro;list;w=80"),
    ("EnregID", "Enreg.", "TXT", "ro;list;w=40"),
    ("Details", "Details", "TXT", "ro;list;w=280"),
], audit=False)

T("T_SAUVEGARDES", "SYS_SAUVEGARDES", "Historique des sauvegardes", [
    ("ID", "ID", "ID", "ro;list;w=30"),
    ("Horodatage", "Date / heure", "TXT", "ro;list;w=95"),
    ("TypeSauv", "Type", "LIST", "vals=Manuelle|Automatique|Pre-restauration|Cloture;ro;list;w=70"),
    ("Fichier", "Fichier", "TXT", "ro;list;w=260"),
    ("TailleKo", "Taille (Ko)", "INT", "ro;list;w=50"),
    ("Verifiee", "Integrite verifiee", "BOOL", "ro;list;w=50"),
    ("Utilisateur", "Utilisateur", "TXT", "ro;list;w=60"),
], audit=False)

# ---------------------------------------------------------------------------
# REGISTRE DES MODULES (menu + fenetres)
# key, titre, categorie, icone, table, couleur(hex), actions, dateCol, tri
# ---------------------------------------------------------------------------
STD = "NEW|EDIT|VIEW|DEL|PRINT|EXPORT|REFRESH"
MODULES = [
    # key, title, category, icon, table, color, actions, datecol, sortcol(desc?)
    ("DASHBOARD", "Tableau de bord", "PILOTAGE", "house", "", "F7941D", "", "", ""),
    ("EXPLOITATION", "Exploitation (journee)", "PILOTAGE", "truck-ramp-box", "", "1E6FD9", "", "", ""),
    ("CAMIONS", "Gestion des camions", "FLOTTE", "truck", "T_CAMIONS", "1E6FD9",
     STD + "|TRUCK_CARD|TRUCK_STATS|DEACTIVATE", "", "Code"),
    ("CHAUFFEURS", "Chauffeurs", "FLOTTE", "id-card", "T_CHAUFFEURS", "1E6FD9",
     STD + "|DRV_CARD|DRV_STATS", "", "Matricule"),
    ("AFFECTATIONS", "Affectations", "FLOTTE", "users", "T_AFFECTATIONS", "1E6FD9",
     STD + "|AFF_END", "DateDebut", "-DateDebut"),
    ("PLANNING", "Programme journalier", "EXPLOITATION", "calendar-days", "T_PLANNING", "0E9F6E",
     STD + "|PLAN_GEN", "DatePlan", "-DatePlan"),
    ("ROTATIONS", "Rotations", "EXPLOITATION", "route", "T_ROTATIONS", "0E9F6E",
     STD + "|VALID|REJECT|CANCEL|ATTACH|OPENDOC|VALID_DAY", "DateRotation", "-ID"),
    ("PESEES", "Pesees et tickets", "EXPLOITATION", "weight-scale", "T_PESEES", "0E9F6E",
     STD + "|ATTACH|OPENDOC", "DatePesee", "-ID"),
    ("OBJECTIFS", "Objectifs de tonnage", "EXPLOITATION", "bullseye", "T_OBJECTIFS", "0E9F6E",
     STD, "", "-Periode"),
    ("CARBURANT", "Carburant", "EXPLOITATION", "gas-pump", "T_CARBURANT", "F7941D",
     STD + "|VALID|REJECT|ATTACH|FUEL_ANALYZE", "DateOp", "-ID"),
    ("LOCATION", "Location horaire", "EXPLOITATION", "clock", "T_LOCATION", "F7941D",
     STD + "|VALID|REJECT|RENT_GEN|RENT_STATEMENT", "DateReleve", "-ID"),
    ("PNEUS", "Pneumatiques", "MAINTENANCE", "circle-dot", "T_PNEUS", "8E44AD",
     STD + "|TIRE_MOUNT|TIRE_UNMOUNT|TIRE_TRANSFER|TIRE_REPAIR|TIRE_RETURN|TIRE_SCRAP|TIRE_HIST", "DateEntree", "NumSerie"),
    ("PNEU_MVT", "Mouvements de pneus", "MAINTENANCE", "rotate", "T_PNEU_MVT", "8E44AD",
     "VIEW|PRINT|EXPORT|REFRESH", "DateMvt", "-ID"),
    ("ARTICLES", "Stock et consommables", "MAINTENANCE", "boxes-stacked", "T_ARTICLES", "8E44AD",
     STD + "|STOCK_IN|STOCK_OUT", "", "Code"),
    ("STOCK_MVT", "Mouvements de stock", "MAINTENANCE", "warehouse", "T_STOCK_MVT", "8E44AD",
     "VIEW|DEL|PRINT|EXPORT|REFRESH", "DateMvt", "-ID"),
    ("INCIDENTS", "Incidents", "MAINTENANCE", "triangle-exclamation", "T_INCIDENTS", "D64545",
     STD + "|INC_PROGRESS|INC_CLOSE|ATTACH|OPENDOC", "DateIncident", "-ID"),
    ("MAINTENANCE", "Maintenance", "MAINTENANCE", "screwdriver-wrench", "T_MAINTENANCE", "D64545",
     STD + "|MNT_DONE", "DateOT", "-ID"),
    ("DEPENSES", "Depenses", "FINANCES", "receipt", "T_DEPENSES", "0F7B6C",
     STD + "|VALID|REJECT|ATTACH|OPENDOC", "DateDep", "-ID"),
    ("PERSONNEL", "Personnel", "FINANCES", "users", "T_PERSONNEL", "0F7B6C", STD, "", "Matricule"),
    ("AVANCES", "Avances", "FINANCES", "hand-holding-dollar", "T_AVANCES", "0F7B6C",
     STD + "|ADV_REFUND", "DateAvance", "-ID"),
    ("CAISSE", "Caisse", "FINANCES", "cash-register", "T_CAISSE", "0F7B6C", STD, "DateOp", "-ID"),
    ("FACTURES", "Facturation", "FINANCES", "file-invoice-dollar", "T_FACTURES", "6C3FC5",
     "VIEW|DEL|PRINT|EXPORT|REFRESH|INV_GEN|INV_VALID|INV_PREVIEW|INV_PDF|INV_CANCEL|INV_LITIGE|PAY_ADD",
     "DateFacture", "-ID"),
    ("PAIEMENTS", "Paiements", "FINANCES", "money-bill-wave", "T_PAIEMENTS", "6C3FC5",
     "NEW|VIEW|PRINT|EXPORT|REFRESH|CANCEL", "DatePaiement", "-ID"),
    ("RECOUVREMENT", "Recouvrement", "FINANCES", "handshake", "T_RELANCES", "6C3FC5",
     STD + "|CLIENT_SIT|AGING", "DateRelance", "-ID"),
    ("RECLAMATIONS", "Reclamations / litiges", "FINANCES", "scale-balanced", "T_RECLAMATIONS", "6C3FC5",
     STD, "DateRec", "-ID"),
    ("CLIENTS", "Clients", "REFERENTIELS", "handshake", "T_CLIENTS", "1690B4", STD + "|CLIENT_SIT", "", "Code"),
    ("FOURNISSEURS", "Fournisseurs", "REFERENTIELS", "warehouse", "T_FOURNISSEURS", "1690B4", STD, "", "Code"),
    ("LIEUX", "Lieux", "REFERENTIELS", "route", "T_LIEUX", "1690B4", STD, "", "Code"),
    ("DOCUMENTS", "Gestion documentaire", "REFERENTIELS", "folder-open", "T_DOCUMENTS", "1690B4",
     STD + "|ATTACH|OPENDOC", "DateExpiration", "DateExpiration"),
    ("RAPPORTS", "Rapports et etats", "PILOTAGE", "chart-column", "", "1690B4", "", "", ""),
    ("CLOTURES", "Clotures", "ADMINISTRATION", "calendar-check", "T_CLOTURES", "0B2A55",
     "VIEW|PRINT|EXPORT|REFRESH|CLOSE_DAY|CLOSE_MONTH|REOPEN|COHERENCE", "", "-ID"),
    ("PARAMETRES", "Parametres", "ADMINISTRATION", "gear", "T_PARAM", "0B2A55",
     "EDIT|VIEW|PRINT|EXPORT|REFRESH", "", "Categorie"),
    ("UTILISATEURS", "Utilisateurs et droits", "ADMINISTRATION", "user-shield", "T_USERS", "0B2A55",
     STD + "|PWD_RESET|RIGHTS", "", "Login"),
    ("AUDIT", "Audit et historique", "ADMINISTRATION", "clock-rotate-left", "T_AUDIT", "0B2A55",
     "VIEW|PRINT|EXPORT|REFRESH", "", "-ID"),
    ("SAUVEGARDES", "Sauvegardes", "ADMINISTRATION", "database", "T_SAUVEGARDES", "0B2A55",
     "VIEW|REFRESH|BACKUP|RESTORE|INTEGRITY|DEMO_RESET", "", "-ID"),
]

# ---------------------------------------------------------------------------
# PARAMETRES PAR DEFAUT  (cle, valeur, type, categorie, description)
# ---------------------------------------------------------------------------
PARAMS = [
    ("SOCIETE_NOM", "MINING TRANSIT SARL", "Texte", "Societe", "Raison sociale affichee sur l'ecran et les documents"),
    ("SOCIETE_SITE", "Sangaredi", "Texte", "Societe", "Site d'exploitation"),
    ("SOCIETE_ACTIVITE", "Exploitation Bauxite", "Texte", "Societe", "Activite affichee dans le bandeau"),
    ("SOCIETE_ADRESSE", "Quartier Centre, Sangaredi - Boke, Republique de Guinee", "Texte", "Societe", "Adresse (factures)"),
    ("SOCIETE_TEL", "+224 620 00 00 00", "Texte", "Societe", "Telephone (factures)"),
    ("SOCIETE_EMAIL", "contact@miningtransit.demo", "Texte", "Societe", "E-mail (factures)"),
    ("SOCIETE_NIF", "NIF 000000000 / RCCM GN.TCC.0000.B.00000", "Texte", "Societe", "Identifiants fiscaux (DONNEES FICTIVES)"),
    ("SOCIETE_BANQUE", "Banque Demo Guinee - IBAN GN00 0000 0000 0000 0000", "Texte", "Societe", "Coordonnees bancaires (factures)"),
    ("DEVISE", "GNF", "Texte", "Finances", "Devise de gestion"),
    ("TVA_TAUX", "0", "Nombre", "Finances", "Taux de TVA applique aux factures (0 = exonere / hors champ, a confirmer avec le fiscaliste)"),
    ("FACT_PREFIXE", "FAC", "Texte", "Finances", "Prefixe de numerotation des factures"),
    ("FACT_MENTIONS", "Paiement par virement a reception. Tout retard de paiement fera l'objet de relances.", "Texte", "Finances", "Mentions en pied de facture"),
    ("FACT_SIGNATAIRE", "Le Directeur General", "Texte", "Finances", "Signataire des factures"),
    ("RELANCE_J1", "7", "Nombre", "Finances", "Jours de retard avant 1er rappel"),
    ("RELANCE_J2", "21", "Nombre", "Finances", "Jours de retard avant relance"),
    ("RELANCE_J3", "45", "Nombre", "Finances", "Jours de retard avant mise en demeure"),
    ("LOC_DEDUIRE_ATTENTE", "Non", "Oui/Non", "Location", "Les heures d'attente sont-elles deduites des heures facturables par Atiko ?"),
    ("LOC_DEDUIRE_IMMOB_ATIKO", "Oui", "Oui/Non", "Location", "Les immobilisations imputables a Atiko suspendent-elles la location ? (selon contrat)"),
    ("LOC_DEDUIRE_IMMOB_EXPLOITANT", "Non", "Oui/Non", "Location", "Les immobilisations imputables a l'exploitant suspendent-elles la location ?"),
    ("LOC_HEURES_MIN_JOUR", "0", "Nombre", "Location", "Minimum d'heures facturables par jour (0 = aucun). Le minimum propre au camion prime s'il est renseigne"),
    ("LOC_HEURES_MAX_JOUR", "24", "Nombre", "Location", "Plafond d'heures par releve"),
    ("CARB_TOLERANCE_PCT", "25", "Nombre", "Carburant", "Ecart toléré (%) par rapport a la consommation de reference avant alerte"),
    ("CARB_MAX_LITRES", "600", "Nombre", "Carburant", "Quantite maximale plausible par distribution (L)"),
    ("CARB_DOUBLON_HEURES", "6", "Nombre", "Carburant", "Deux distributions au meme camion dans ce delai (h) = doublon potentiel"),
    ("CARB_PRIX_DEFAUT", "12000", "Nombre", "Carburant", "Prix par defaut du litre de gasoil (GNF) - DEMO"),
    ("CARB_STOCK_ALERTE", "3000", "Nombre", "Carburant", "Seuil d'alerte stock cuve (L)"),
    ("ROT_TOLERANCE_SURCHARGE_PCT", "10", "Nombre", "Exploitation", "Surcharge toleree au-dela de la capacite camion (%)"),
    ("ROT_NET_MIN", "10", "Nombre", "Exploitation", "Poids net minimum plausible (t)"),
    ("ROT_DUREE_MAX_H", "6", "Nombre", "Exploitation", "Duree maximale plausible d'une rotation (h)"),
    ("PNEU_PROFONDEUR_MIN", "6", "Nombre", "Pneumatiques", "Profondeur minimale avant remplacement (mm)"),
    ("PNEU_STOCK_MIN", "4", "Nombre", "Pneumatiques", "Stock minimum de pneus neufs"),
    ("ALERTE_DOC_JOURS", "30", "Nombre", "Alertes", "Delai d'anticipation des expirations de documents (jours)"),
    ("SAUV_DOSSIER", "", "Texte", "Sauvegarde", "Dossier de sauvegarde (vide = sous-dossier Sauvegardes du classeur)"),
    ("SAUV_AUTO_HEURES", "24", "Nombre", "Sauvegarde", "Intervalle de sauvegarde automatique a la fermeture (h)"),
    ("SAUV_CONSERVER", "30", "Nombre", "Sauvegarde", "Nombre de sauvegardes conservees (la derniere valide n'est jamais supprimee)"),
    ("SAUV_DERNIERE", "", "Texte", "Sauvegarde", "Horodatage de la derniere sauvegarde (systeme)"),
    ("DOSSIER_JUSTIFICATIFS", "", "Texte", "Documents", "Dossier de stockage des justificatifs (vide = sous-dossier Justificatifs)"),
    ("DOSSIER_EXPORTS", "", "Texte", "Documents", "Dossier des exports PDF/Excel (vide = sous-dossier Exports)"),
    ("CLOTURE_DERNIER_JOUR", "", "Date", "Exploitation", "Dernier jour cloture (systeme) - les saisies anterieures sont verrouillees"),
    ("CLOTURE_DERNIER_MOIS", "", "Texte", "Exploitation", "Dernier mois cloture AAAA-MM (systeme)"),
    ("CHARGES_SOCIALES_PCT", "18", "Nombre", "Finances", "Taux de charges sociales employeur applique aux salaires (%) - a confirmer"),
    ("UI_PLEIN_ECRAN", "Oui", "Oui/Non", "Systeme", "Masquer le ruban et la barre de formule pendant l'utilisation (restaures a la fermeture)"),
    ("DEMO_MODE", "Oui", "Oui/Non", "Systeme", "Environnement de demonstration (bandeau DEMO, reinitialisation autorisee)"),
    ("APP_VERSION", "1.0.0", "Texte", "Systeme", "Version de l'application"),
]

# ---------------------------------------------------------------------------
# DROITS : L lire, C creer, M modifier, V valider, S supprimer, I imprimer/export
# ---------------------------------------------------------------------------
PROFILES = ["Administrateur", "Directeur", "Responsable exploitation", "Comptable", "Agent de saisie"]


def default_rights(profile, module_key):
    cat = next(m[2] for m in MODULES if m[0] == module_key)
    if profile == "Administrateur":
        return "LCMVSI"
    if profile == "Directeur":
        return "LVI" if cat != "ADMINISTRATION" else ("LI" if module_key != "UTILISATEURS" else "")
    if profile == "Responsable exploitation":
        if cat in ("PILOTAGE", "FLOTTE", "EXPLOITATION", "MAINTENANCE", "REFERENTIELS"):
            return "LCMVSI"
        if module_key in ("DEPENSES",):
            return "LCMI"
        if cat == "FINANCES":
            return "LI"
        if module_key == "CLOTURES":
            return "LVI"
        return ""
    if profile == "Comptable":
        if cat == "FINANCES":
            return "LCMVSI"
        if module_key in ("CARBURANT", "LOCATION"):
            return "LVI"
        if cat in ("PILOTAGE", "REFERENTIELS"):
            return "LCMI"
        if module_key == "CLOTURES":
            return "LVI"
        if cat in ("FLOTTE", "EXPLOITATION", "MAINTENANCE"):
            return "LI"
        return ""
    if profile == "Agent de saisie":
        if module_key in ("ROTATIONS", "PESEES", "CARBURANT", "LOCATION", "PNEUS", "INCIDENTS",
                          "STOCK_MVT", "ARTICLES", "DEPENSES", "PLANNING"):
            return "LCMI"
        if cat in ("PILOTAGE", "FLOTTE", "REFERENTIELS", "MAINTENANCE", "EXPLOITATION"):
            return "LI"
        return ""
    return ""


def table(name):
    for t in TABLES:
        if t["name"] == name:
            return t
    raise KeyError(name)


def all_fields(t):
    f = list(t["fields"])
    if t["audit"]:
        f += AUDIT_FIELDS
    return f


def parse_opts(opts):
    d = {}
    for part in [p for p in opts.split(";") if p]:
        if "=" in part:
            k, v = part.split("=", 1)
            d[k] = v
        else:
            d[part] = True
    return d


# ---------------------------------------------------------------------------
# Accentuation des libelles et valeurs (les noms techniques ne changent pas)
# ---------------------------------------------------------------------------
from fr import acc as _acc  # noqa: E402

for _t in TABLES:
    _t["label"] = _acc(_t["label"])
    _t["fields"] = [(f[0], _acc(f[1]), f[2], _acc(f[3])) for f in _t["fields"]]
AUDIT_FIELDS = [(f[0], _acc(f[1]), f[2], f[3]) for f in AUDIT_FIELDS]
MODULES = [(m[0], _acc(m[1])) + tuple(m[2:]) for m in MODULES]
PARAMS = [(p[0], _acc(p[1]) if p[2] == "Texte" and not p[0].startswith(("SOCIETE_NOM", "SOCIETE_EMAIL", "FACT_PREFIXE", "DEVISE")) else p[1],
           p[2], _acc(p[3]), _acc(p[4])) for p in PARAMS]
