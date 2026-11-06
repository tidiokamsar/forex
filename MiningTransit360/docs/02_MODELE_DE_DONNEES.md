# 02 - Modèle de données

Document généré automatiquement par `tools/make_docs.py` à partir de `tools/schema.py` (source unique de vérité).

Chaque table est un tableau Excel structuré (ListObject) nommé `T_...`, placé sur une feuille masquée `DB_...` (données métier) ou `SYS_...` (système). La clé technique `ID` est attribuée par la table de compteurs `T_SEQ`. Les champs `CreePar / CreeLe / ModifPar / ModifLe` sont renseignés automatiquement sur les tables auditées.

## Vue d'ensemble

| Table | Feuille | Rôle | Champs | Lignes démo |
|---|---|---|---:|---:|
| `T_CLIENTS` | DB_CLIENTS | Clients | 16 | 3 |
| `T_FOURNISSEURS` | DB_FOURNISSEURS | Fournisseurs | 12 | 8 |
| `T_LIEUX` | DB_LIEUX | Lieux (origines / destinations) | 11 | 8 |
| `T_CAMIONS` | DB_CAMIONS | Camions | 23 | 10 |
| `T_CHAUFFEURS` | DB_CHAUFFEURS | Chauffeurs | 20 | 12 |
| `T_AFFECTATIONS` | DB_AFFECTATIONS | Affectations chauffeur / camion | 12 | 12 |
| `T_OBJECTIFS` | DB_OBJECTIFS | Objectifs mensuels | 10 | 3 |
| `T_PLANNING` | DB_PLANNING | Programme journalier de transport | 14 | 48 |
| `T_ROTATIONS` | DB_ROTATIONS | Rotations | 25 | 2207 |
| `T_PESEES` | DB_PESEES | Pesées et tickets | 18 | 2207 |
| `T_CARBURANT` | DB_CARBURANT | Carburant | 21 | 576 |
| `T_LOCATION` | DB_LOCATION | Relevés horaires de location | 19 | 570 |
| `T_PNEUS` | DB_PNEUS | Pneumatiques | 20 | 114 |
| `T_PNEU_MVT` | DB_PNEU_MVT | Mouvements de pneus | 15 | 223 |
| `T_ARTICLES` | DB_ARTICLES | Articles et consommables | 13 | 10 |
| `T_STOCK_MVT` | DB_STOCK_MVT | Mouvements de stock | 15 | 30 |
| `T_INCIDENTS` | DB_INCIDENTS | Incidents | 24 | 16 |
| `T_MAINTENANCE` | DB_MAINTENANCE | Interventions de maintenance | 17 | 12 |
| `T_DEPENSES` | DB_DEPENSES | Dépenses | 20 | 30 |
| `T_PERSONNEL` | DB_PERSONNEL | Personnel (hors chauffeurs) | 12 | 6 |
| `T_AVANCES` | DB_AVANCES | Avances au personnel | 12 | 6 |
| `T_CAISSE` | DB_CAISSE | Caisse (petite caisse) | 11 | 21 |
| `T_FACTURES` | DB_FACTURES | Factures clients | 29 | 6 |
| `T_FACTURE_LIGNES` | DB_FACTURE_LIGNES | Lignes de facture | 7 | 6 |
| `T_PAIEMENTS` | DB_PAIEMENTS | Paiements clients | 13 | 8 |
| `T_RELANCES` | DB_RELANCES | Relances clients | 10 | 2 |
| `T_RECLAMATIONS` | DB_RECLAMATIONS | Réclamations et litiges | 14 | 3 |
| `T_DOCUMENTS` | DB_DOCUMENTS | Gestion documentaire | 13 | 36 |
| `T_CLOTURES` | DB_CLOTURES | Clôtures journalières et mensuelles | 14 | 57 |
| `T_USERS` | SYS_USERS | Utilisateurs | 14 | 5 |
| `T_DROITS` | SYS_DROITS | Droits par profil | 4 | 175 |
| `T_PARAM` | SYS_PARAM | Paramètres | 6 | 46 |
| `T_SEQ` | SYS_SEQ | Compteurs de numérotation | 2 | 43 |
| `T_AUDIT` | SYS_AUDIT | Journal d'audit | 7 | 1 |
| `T_SAUVEGARDES` | SYS_SAUVEGARDES | Historique des sauvegardes | 7 | 1 |

## Relations (clés étrangères)

```
T_LIEUX           .ClientID         -> T_CLIENTS.ID (affiché : Code)
T_AFFECTATIONS    .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_AFFECTATIONS    .ChauffeurID      -> T_CHAUFFEURS.ID (affiché : Matricule)
T_PLANNING        .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_PLANNING        .ChauffeurID      -> T_CHAUFFEURS.ID (affiché : Matricule)
T_PLANNING        .OrigineID        -> T_LIEUX.ID (affiché : Code)
T_PLANNING        .DestinationID    -> T_LIEUX.ID (affiché : Code)
T_ROTATIONS       .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_ROTATIONS       .ChauffeurID      -> T_CHAUFFEURS.ID (affiché : Matricule)
T_ROTATIONS       .OrigineID        -> T_LIEUX.ID (affiché : Code)
T_ROTATIONS       .DestinationID    -> T_LIEUX.ID (affiché : Code)
T_ROTATIONS       .ClientID         -> T_CLIENTS.ID (affiché : Code)
T_ROTATIONS       .FactureID        -> T_FACTURES.ID (affiché : NumFacture)
T_PESEES          .RotationID       -> T_ROTATIONS.ID (affiché : NumRotation)
T_PESEES          .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_CARBURANT       .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_CARBURANT       .ChauffeurID      -> T_CHAUFFEURS.ID (affiché : Matricule)
T_CARBURANT       .FournisseurID    -> T_FOURNISSEURS.ID (affiché : Code)
T_LOCATION        .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_PNEUS           .FournisseurID    -> T_FOURNISSEURS.ID (affiché : Code)
T_PNEUS           .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_PNEU_MVT        .PneuID           -> T_PNEUS.ID (affiché : NumSerie)
T_PNEU_MVT        .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_PNEU_MVT        .CamionDestID     -> T_CAMIONS.ID (affiché : Code)
T_STOCK_MVT       .ArticleID        -> T_ARTICLES.ID (affiché : Code)
T_STOCK_MVT       .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_STOCK_MVT       .FournisseurID    -> T_FOURNISSEURS.ID (affiché : Code)
T_INCIDENTS       .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_INCIDENTS       .ChauffeurID      -> T_CHAUFFEURS.ID (affiché : Matricule)
T_MAINTENANCE     .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_MAINTENANCE     .IncidentID       -> T_INCIDENTS.ID (affiché : NumIncident)
T_DEPENSES        .FournisseurID    -> T_FOURNISSEURS.ID (affiché : Code)
T_DEPENSES        .CamionID         -> T_CAMIONS.ID (affiché : Code)
T_FACTURES        .ClientID         -> T_CLIENTS.ID (affiché : Code)
T_FACTURE_LIGNES  .FactureID        -> T_FACTURES.ID (affiché : NumFacture)
T_PAIEMENTS       .FactureID        -> T_FACTURES.ID (affiché : NumFacture)
T_RELANCES        .FactureID        -> T_FACTURES.ID (affiché : NumFacture)
T_RECLAMATIONS    .FactureID        -> T_FACTURES.ID (affiché : NumFacture)
```

## Détail des tables

### `T_CLIENTS` - Clients

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Code` | Code client | Texte | obligatoire; unique |
| `RaisonSociale` | Raison sociale | Texte | obligatoire |
| `Adresse` | Adresse | Texte |  |
| `Telephone` | Téléphone | Texte |  |
| `Email` | E-mail | Texte |  |
| `NIF` | NIF / RCCM | Texte |  |
| `RefContrat` | Référence contrat | Texte |  |
| `TarifTonne` | Tarif transport (GNF/t) | Montant GNF | obligatoire; ≥ 0 |
| `DelaiPaiementJ` | Délai de paiement (jours) | Entier | obligatoire; ≥ 0; défaut : 30 |
| `Contact` | Interlocuteur | Texte |  |
| `Actif` | Actif | Oui/Non | défaut : Oui |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_FOURNISSEURS` - Fournisseurs

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Code` | Code | Texte | obligatoire; unique |
| `Nom` | Nom | Texte | obligatoire |
| `Categorie` | Catégorie | Liste | obligatoire; valeurs : Carburant, Pneumatiques, Pièces et consommables, Services, Propriétaire flotte, Divers |
| `Telephone` | Téléphone | Texte |  |
| `Adresse` | Adresse | Texte |  |
| `NIF` | NIF / RCCM | Texte |  |
| `Actif` | Actif | Oui/Non | défaut : Oui |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_LIEUX` - Lieux (origines / destinations)

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Code` | Code | Texte | obligatoire; unique |
| `Nom` | Nom | Texte | obligatoire |
| `TypeLieu` | Type | Liste | obligatoire; valeurs : Chargement, Déchargement, Atelier, Parc |
| `ClientID` | Client destinataire | Référence | → T_CLIENTS.Code |
| `DistanceKm` | Distance aller (km) | Décimal | ≥ 0 |
| `Actif` | Actif | Oui/Non | défaut : Oui |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_CAMIONS` - Camions

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Code` | Code camion | Texte | obligatoire; unique |
| `Immatriculation` | Immatriculation | Texte | obligatoire; unique |
| `Marque` | Marque | Texte | obligatoire |
| `Modele` | Modèle | Texte |  |
| `CapaciteT` | Capacité utile (t) | Décimal | obligatoire; ≥ 0 |
| `Proprietaire` | Propriétaire | Texte | obligatoire; défaut : Atiko Guinée |
| `NumChassis` | N° chassis | Texte |  |
| `DateMiseService` | Date de mise en service | Date |  |
| `Statut` | Statut | Liste | obligatoire; défaut : Disponible; valeurs : Disponible, En service, En panne, Immobilisé, Hors service |
| `TarifHoraire` | Tarif location (GNF/h) | Montant GNF | obligatoire; ≥ 0 |
| `HeuresMinJour` | Heures minimum facturables/jour | Décimal | ≥ 0; défaut : 0 |
| `ConsoRefLH` | Consommation de référence (L/h) | Décimal | ≥ 0; défaut : 18 |
| `NbPositionsPneus` | Nombre de positions pneus | Entier | ≥ 0; défaut : 10 |
| `AssuranceExp` | Expiration assurance | Date |  |
| `VisiteTechExp` | Expiration visite technique | Date |  |
| `CompteurHeures` | Compteur moteur (h) | Décimal | ≥ 0 |
| `Observations` | Observations | Texte long |  |
| `Actif` | Actif | Oui/Non | défaut : Oui |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_CHAUFFEURS` - Chauffeurs

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Matricule` | Matricule | Texte | obligatoire; unique |
| `Nom` | Nom | Texte | obligatoire |
| `Prenom` | Prénom | Texte | obligatoire |
| `Telephone` | Téléphone | Texte |  |
| `Adresse` | Adresse | Texte |  |
| `ContactUrgence` | Contact d'urgence | Texte |  |
| `TelUrgence` | Tel. urgence | Texte |  |
| `NumPermis` | N° permis | Texte | obligatoire; unique |
| `CategoriePermis` | Catégorie permis | Liste | obligatoire; défaut : C; valeurs : C, CE, D, EC, C1E |
| `ExpPermis` | Expiration permis | Date | obligatoire |
| `DateEmbauche` | Date d'embauche | Date |  |
| `SalaireMensuel` | Salaire mensuel (GNF) | Montant GNF | ≥ 0 |
| `PrimeRotation` | Prime par rotation (GNF) | Montant GNF | ≥ 0; défaut : 0 |
| `Statut` | Statut | Liste | obligatoire; défaut : Actif; valeurs : Actif, Congé, Suspendu, Sorti |
| `Observations` | Observations | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_AFFECTATIONS` - Affectations chauffeur / camion

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `CamionID` | Camion | Référence | obligatoire; → T_CAMIONS.Code |
| `ChauffeurID` | Chauffeur | Référence | obligatoire; → T_CHAUFFEURS.Matricule |
| `DateDebut` | Date début | Date | obligatoire |
| `DateFin` | Date fin (vide = en cours) | Date |  |
| `Poste` | Poste | Liste | obligatoire; défaut : Jour; valeurs : Jour, Nuit, Journée complète |
| `Statut` | Statut | Liste | obligatoire; défaut : Active; valeurs : Active, Terminée, Suspendue |
| `Observations` | Observations | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_OBJECTIFS` - Objectifs mensuels

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Periode` | Période (AAAA-MM) | Texte | obligatoire; unique |
| `TonnageObjectif` | Objectif tonnage (t) | Décimal | obligatoire; ≥ 0 |
| `RotationsObjectif` | Objectif rotations | Entier | ≥ 0 |
| `DispoObjectifPct` | Objectif disponibilite (%) | Pourcentage | ≥ 0; défaut : 0.85 |
| `CAObjectif` | Objectif CA (GNF) | Montant GNF | ≥ 0 |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_PLANNING` - Programme journalier de transport

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `DatePlan` | Date | Date | obligatoire |
| `CamionID` | Camion | Référence | obligatoire; → T_CAMIONS.Code |
| `ChauffeurID` | Chauffeur | Référence | → T_CHAUFFEURS.Matricule |
| `Poste` | Poste | Liste | défaut : Jour; valeurs : Jour, Nuit, Journée complète |
| `OrigineID` | Origine | Référence | → T_LIEUX.Code |
| `DestinationID` | Destination | Référence | → T_LIEUX.Code |
| `RotationsPrevues` | Rotations prévues | Entier | ≥ 0 |
| `TonnagePrevu` | Tonnage prévu (t) | Décimal | ≥ 0 |
| `Statut` | Statut | Liste | défaut : Planifié; valeurs : Planifié, Réalisé, Annulé |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_ROTATIONS` - Rotations

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `NumRotation` | N° rotation | Texte | unique; calculé / système; numérotation ROT-AAAA-nnnnn |
| `DateRotation` | Date | Date | obligatoire |
| `CamionID` | Camion | Référence | obligatoire; → T_CAMIONS.Code |
| `ChauffeurID` | Chauffeur | Référence | obligatoire; → T_CHAUFFEURS.Matricule |
| `HDepart` | Heure depart | Heure | obligatoire |
| `HArrivee` | Heure arrivee | Heure |  |
| `OrigineID` | Origine | Référence | obligatoire; → T_LIEUX.Code |
| `DestinationID` | Destination | Référence | obligatoire; → T_LIEUX.Code |
| `ClientID` | Client | Référence | obligatoire; → T_CLIENTS.Code |
| `NumTicket` | N° ticket de pesée | Texte | obligatoire; unique |
| `PoidsBrut` | Poids brut (t) | Décimal | obligatoire; ≥ 0 |
| `Tare` | Tare (t) | Décimal | obligatoire; ≥ 0 |
| `PoidsNet` | Poids net (t) | Décimal | calculé / système |
| `Statut` | Statut | Liste | calculé / système; défaut : Saisie; valeurs : Saisie, Validée, Rejetée, Annulée |
| `MotifRejet` | Motif rejet / annulation | Texte | calculé / système |
| `FactureID` | Facture | Référence | calculé / système; → T_FACTURES.NumFacture |
| `Justificatif` | Justificatif (ticket scanne) | Fichier |  |
| `ValidePar` | Valide par | Texte | calculé / système |
| `ValideLe` | Valide le | Date | calculé / système |
| `Observations` | Observations | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_PESEES` - Pesées et tickets

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `NumTicket` | N° ticket | Texte | obligatoire; unique |
| `RotationID` | Rotation | Référence | → T_ROTATIONS.NumRotation |
| `DatePesee` | Date | Date | obligatoire |
| `HeurePesee` | Heure | Heure |  |
| `Pont` | Pont bascule | Liste | défaut : PB-02 Gare; valeurs : PB-01 Mine, PB-02 Gare, PB-03 Port |
| `CamionID` | Camion | Référence | obligatoire; → T_CAMIONS.Code |
| `PoidsBrut` | Poids brut (t) | Décimal | obligatoire; ≥ 0 |
| `Tare` | Tare (t) | Décimal | obligatoire; ≥ 0 |
| `PoidsNet` | Poids net (t) | Décimal | calculé / système |
| `Operateur` | Opérateur pesage | Texte |  |
| `Statut` | Statut | Liste | calculé / système; défaut : Saisie; valeurs : Saisie, Validée, Rejetée, Annulée |
| `Justificatif` | Ticket scanne | Fichier |  |
| `Observations` | Observations | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_CARBURANT` - Carburant

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `NumBon` | N° bon | Texte | obligatoire; unique |
| `DateOp` | Date | Date | obligatoire |
| `TypeOp` | Type | Liste | obligatoire; défaut : Distribution; valeurs : Approvisionnement, Distribution |
| `CamionID` | Camion | Référence | → T_CAMIONS.Code |
| `ChauffeurID` | Chauffeur | Référence | → T_CHAUFFEURS.Matricule |
| `FournisseurID` | Fournisseur | Référence | → T_FOURNISSEURS.Code |
| `Litres` | Quantité (L) | Décimal | obligatoire; ≥ 0 |
| `PrixLitre` | Prix unitaire (GNF/L) | Montant GNF | obligatoire; ≥ 0 |
| `Montant` | Montant (GNF) | Montant GNF | calculé / système |
| `CompteurH` | Compteur moteur (h) | Décimal | ≥ 0 |
| `HeuresDepuisPlein` | Heures depuis plein | Décimal | calculé / système |
| `ConsoLH` | Consommation (L/h) | Décimal | calculé / système |
| `Anomalie` | Anomalie détectée | Texte | calculé / système |
| `Statut` | Statut | Liste | calculé / système; défaut : Saisie; valeurs : Saisie, Validée, Rejetée, Annulée |
| `Justificatif` | Bon scanne | Fichier |  |
| `Observations` | Observations | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_LOCATION` - Relevés horaires de location

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `DateReleve` | Date | Date | obligatoire |
| `CamionID` | Camion | Référence | obligatoire; → T_CAMIONS.Code |
| `HDebut` | Heure début | Heure | obligatoire |
| `HFin` | Heure fin | Heure | obligatoire |
| `HeuresBrutes` | Heures brutes | Décimal | calculé / système |
| `HeuresAttente` | Heures d'attente | Décimal | ≥ 0; défaut : 0 |
| `HeuresImmobAtiko` | Immobilisation imputable Atiko (h) | Décimal | ≥ 0; défaut : 0 |
| `HeuresImmobExploitant` | Immobilisation imputable exploitant (h) | Décimal | ≥ 0; défaut : 0 |
| `HeuresRetenues` | Heures retenues | Décimal | calculé / système |
| `TarifHoraire` | Tarif (GNF/h) | Montant GNF | calculé / système |
| `Montant` | Montant (GNF) | Montant GNF | calculé / système |
| `Statut` | Statut | Liste | calculé / système; défaut : Saisie; valeurs : Saisie, Validée, Rejetée, Annulée |
| `Justificatif` | Justificatif | Fichier |  |
| `Observations` | Observations | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_PNEUS` - Pneumatiques

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `NumSerie` | N° de série | Texte | obligatoire; unique |
| `Reference` | Référence | Texte |  |
| `Marque` | Marque | Texte | obligatoire |
| `Dimension` | Dimension | Texte | obligatoire |
| `FournisseurID` | Fournisseur | Référence | → T_FOURNISSEURS.Code |
| `PrixAchat` | Prix d'achat (GNF) | Montant GNF | obligatoire; ≥ 0 |
| `DateEntree` | Date d'entrée | Date | obligatoire |
| `Statut` | Statut | Liste | calculé / système; défaut : Stock; valeurs : Stock, Monté, Réparation, Réformé |
| `CamionID` | Camion actuel | Référence | calculé / système; → T_CAMIONS.Code |
| `Position` | Position | Texte | calculé / système |
| `HeuresCumulees` | Heures cumulées | Décimal | calculé / système |
| `DureeVieCibleH` | Durée de vie cible (h) | Décimal | ≥ 0; défaut : 2500 |
| `ProfondeurMM` | Profondeur sculpture (mm) | Décimal | ≥ 0 |
| `DateMontage` | Date dernier montage | Date | calculé / système |
| `Observations` | Observations | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_PNEU_MVT` - Mouvements de pneus

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `DateMvt` | Date | Date | obligatoire |
| `PneuID` | Pneu | Référence | obligatoire; → T_PNEUS.NumSerie |
| `TypeMvt` | Mouvement | Liste | obligatoire; valeurs : Entrée, Montage, Démontage, Transfert, Réparation, Retour réparation, Réforme |
| `CamionID` | Camion | Référence | → T_CAMIONS.Code |
| `Position` | Position | Texte |  |
| `CamionDestID` | Camion destination | Référence | → T_CAMIONS.Code |
| `PositionDest` | Position destination | Texte |  |
| `CompteurH` | Compteur moteur (h) | Décimal | ≥ 0 |
| `Cout` | Cout (GNF) | Montant GNF | ≥ 0; défaut : 0 |
| `Motif` | Motif | Texte |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_ARTICLES` - Articles et consommables

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Code` | Code | Texte | obligatoire; unique |
| `Designation` | Désignation | Texte | obligatoire |
| `Famille` | Famille | Liste | obligatoire; valeurs : Lubrifiants, Filtres, Pièces, Sécurité, Divers |
| `Unite` | Unité | Texte | obligatoire; défaut : U |
| `StockMin` | Stock minimum | Décimal | ≥ 0; défaut : 0 |
| `PrixMoyen` | Prix moyen (GNF) | Montant GNF | calculé / système |
| `Stock` | Stock actuel | Décimal | calculé / système |
| `Actif` | Actif | Oui/Non | défaut : Oui |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_STOCK_MVT` - Mouvements de stock

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `DateMvt` | Date | Date | obligatoire |
| `ArticleID` | Article | Référence | obligatoire; → T_ARTICLES.Code |
| `TypeMvt` | Type | Liste | obligatoire; valeurs : Entrée, Sortie, Ajustement |
| `Quantite` | Quantité | Décimal | obligatoire; ≥ 0 |
| `PU` | Prix unitaire (GNF) | Montant GNF | ≥ 0 |
| `Montant` | Montant (GNF) | Montant GNF | calculé / système |
| `CamionID` | Camion | Référence | → T_CAMIONS.Code |
| `FournisseurID` | Fournisseur | Référence | → T_FOURNISSEURS.Code |
| `Reference` | Référence pièce | Texte |  |
| `Observations` | Observations | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_INCIDENTS` - Incidents

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `NumIncident` | N° incident | Texte | unique; calculé / système; numérotation INC-AAAA-nnnnn |
| `DateIncident` | Date | Date | obligatoire |
| `TypeIncident` | Type | Liste | obligatoire; valeurs : Panne mécanique, Crevaison, Accident, Panne électrique, Attente chargement, Sécurité, Autre |
| `CamionID` | Camion | Référence | obligatoire; → T_CAMIONS.Code |
| `ChauffeurID` | Chauffeur | Référence | → T_CHAUFFEURS.Matricule |
| `Description` | Description | Texte long | obligatoire |
| `Gravite` | Gravité | Liste | obligatoire; défaut : Moyenne; valeurs : Mineure, Moyenne, Majeure, Critique |
| `Responsable` | Responsable contractuel | Liste | obligatoire; défaut : À déterminer; valeurs : Atiko (contrat), Exploitant, Tiers / assurance, À déterminer |
| `Immobilisation` | Immobilisation | Oui/Non | défaut : Non |
| `DebutImmob` | Début immobilisation | Date |  |
| `HeureDebutImmob` | Heure début immob. | Heure |  |
| `DateReprise` | Date de reprise | Date |  |
| `HeureReprise` | Heure de reprise | Heure |  |
| `DureeImmobH` | Durée immobilisation (h) | Décimal | calculé / système |
| `Intervention` | Intervention réalisée | Texte long |  |
| `Cout` | Cout total estime (GNF) | Montant GNF | ≥ 0; défaut : 0 |
| `CoutExploitant` | Cout a charge exploitant (GNF) | Montant GNF | calculé / système |
| `Statut` | Statut | Liste | calculé / système; défaut : Ouvert; valeurs : Ouvert, En cours, Clôturé |
| `Justificatif` | Photos / justificatif | Fichier |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_MAINTENANCE` - Interventions de maintenance

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `NumOT` | N° ordre de travail | Texte | unique; calculé / système; numérotation OT-AAAA-nnnnn |
| `DateOT` | Date | Date | obligatoire |
| `CamionID` | Camion | Référence | obligatoire; → T_CAMIONS.Code |
| `IncidentID` | Incident lie | Référence | → T_INCIDENTS.NumIncident |
| `TypeMaint` | Type | Liste | obligatoire; valeurs : Préventive, Corrective, Révision, Contrôle |
| `Intervenant` | Intervenant | Texte |  |
| `Responsable` | Responsable contractuel | Liste | obligatoire; défaut : Atiko (contrat); valeurs : Atiko (contrat), Exploitant, Tiers / assurance, À déterminer |
| `Description` | Description | Texte long | obligatoire |
| `DateDebut` | Début | Date |  |
| `DateFin` | Fin | Date |  |
| `Cout` | Cout (GNF) | Montant GNF | ≥ 0; défaut : 0 |
| `Statut` | Statut | Liste | défaut : Planifiée; valeurs : Planifiée, En cours, Terminée, Annulée |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_DEPENSES` - Dépenses

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `NumPiece` | N° pièce | Texte | unique; calculé / système; numérotation DEP-AAAA-nnnnn |
| `DateDep` | Date | Date | obligatoire |
| `Categorie` | Catégorie | Liste | obligatoire; valeurs : Personnel temporaire, Restauration, Sécurité et EPI, Lubrifiants et consommables, Entretien a charge exploitant, Frais administratifs, Télécom, Transport personnel, Taxes et redevances, Charges exceptionnelles, Divers, Carburant, Pneumatiques, Location camions |
| `Libelle` | Libelle | Texte | obligatoire |
| `FournisseurID` | Fournisseur | Référence | → T_FOURNISSEURS.Code |
| `RefFacture` | Ref. facture fournisseur | Texte |  |
| `Montant` | Montant (GNF) | Montant GNF | obligatoire; ≥ 0 |
| `ModePaiement` | Mode de paiement | Liste | obligatoire; défaut : Virement; valeurs : Espèces, Virement, Chèque, Mobile money, Caisse |
| `CamionID` | Camion concerne | Référence | → T_CAMIONS.Code |
| `CentreCout` | Centre de cout | Liste | défaut : Exploitation; valeurs : Exploitation, Flotte, Administration, Direction, Sécurité |
| `Responsable` | Responsable contractuel | Liste | obligatoire; défaut : Exploitant; valeurs : Exploitant, Atiko (contrat), Refacturable client |
| `Exceptionnelle` | Charge exceptionnelle | Oui/Non | défaut : Non |
| `Statut` | Statut | Liste | calculé / système; défaut : Saisie; valeurs : Saisie, Validée, Rejetée, Annulée |
| `Justificatif` | Justificatif | Fichier |  |
| `ValidePar` | Valide par | Texte | calculé / système |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_PERSONNEL` - Personnel (hors chauffeurs)

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Matricule` | Matricule | Texte | obligatoire; unique |
| `Nom` | Nom et prénom | Texte | obligatoire |
| `Fonction` | Fonction | Texte | obligatoire |
| `Telephone` | Téléphone | Texte |  |
| `DateEmbauche` | Date d'embauche | Date |  |
| `SalaireMensuel` | Salaire mensuel (GNF) | Montant GNF | ≥ 0 |
| `Statut` | Statut | Liste | défaut : Actif; valeurs : Actif, Congé, Sorti |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_AVANCES` - Avances au personnel

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `DateAvance` | Date | Date | obligatoire |
| `Beneficiaire` | Bénéficiaire (matricule) | Texte | obligatoire |
| `Montant` | Montant (GNF) | Montant GNF | obligatoire; ≥ 0 |
| `Motif` | Motif | Texte |  |
| `MontantRembourse` | Déjà rembourse (GNF) | Montant GNF | ≥ 0; défaut : 0 |
| `Solde` | Reste a rembourser | Montant GNF | calculé / système |
| `Statut` | Statut | Liste | calculé / système; défaut : En cours; valeurs : En cours, Soldée, Annulée |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_CAISSE` - Caisse (petite caisse)

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `DateOp` | Date | Date | obligatoire |
| `Sens` | Sens | Liste | obligatoire; valeurs : Entrée, Sortie |
| `Libelle` | Libelle | Texte | obligatoire |
| `Montant` | Montant (GNF) | Montant GNF | obligatoire; ≥ 0 |
| `Reference` | Référence | Texte |  |
| `SoldeApres` | Solde après opération | Montant GNF | calculé / système |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_FACTURES` - Factures clients

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `NumFacture` | N° facture | Texte | unique; calculé / système |
| `ClientID` | Client | Référence | obligatoire; → T_CLIENTS.Code |
| `DateFacture` | Date facture | Date | obligatoire |
| `PeriodeDebut` | Période du | Date | obligatoire |
| `PeriodeFin` | Période au | Date | obligatoire |
| `Echeance` | Échéance | Date | calculé / système |
| `RefContrat` | Référence contractuelle | Texte |  |
| `NbRotations` | Nombre de rotations | Entier | calculé / système |
| `Tonnage` | Tonnage valide (t) | Décimal | calculé / système |
| `TarifTonne` | Tarif (GNF/t) | Montant GNF | calculé / système |
| `MontantHT` | Montant HT (GNF) | Montant GNF | calculé / système |
| `TauxTVA` | Taux TVA | Pourcentage | calculé / système |
| `MontantTVA` | TVA (GNF) | Montant GNF | calculé / système |
| `MontantTTC` | Montant TTC (GNF) | Montant GNF | calculé / système |
| `MontantPaye` | Montant réglé (GNF) | Montant GNF | calculé / système |
| `Solde` | Solde du (GNF) | Montant GNF | calculé / système |
| `Statut` | Statut | Liste | calculé / système; défaut : Brouillon; valeurs : Brouillon, Validée, Partielle, Payée, Annulée |
| `Litige` | En litige | Oui/Non | défaut : Non |
| `ConditionsPaiement` | Conditions de paiement | Texte |  |
| `Mentions` | Mentions | Texte long |  |
| `Empreinte` | Empreinte d'intégrité | Texte | calculé / système |
| `ValidePar` | Validée par | Texte | calculé / système |
| `ValideLe` | Validée le | Date | calculé / système |
| `MotifAnnulation` | Motif d'annulation | Texte | calculé / système |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_FACTURE_LIGNES` - Lignes de facture

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `FactureID` | Facture | Référence | obligatoire; → T_FACTURES.NumFacture |
| `Designation` | Désignation | Texte | obligatoire |
| `Quantite` | Quantité | Décimal | obligatoire |
| `Unite` | Unité | Texte |  |
| `PU` | Prix unitaire (GNF) | Montant GNF |  |
| `Montant` | Montant (GNF) | Montant GNF |  |

### `T_PAIEMENTS` - Paiements clients

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `NumRecu` | N° encaissement | Texte | unique; calculé / système; numérotation ENC-AAAA-nnnnn |
| `FactureID` | Facture | Référence | obligatoire; → T_FACTURES.NumFacture |
| `DatePaiement` | Date | Date | obligatoire |
| `Montant` | Montant (GNF) | Montant GNF | obligatoire; ≥ 0 |
| `ModePaiement` | Mode | Liste | obligatoire; défaut : Virement; valeurs : Virement, Chèque, Espèces, Compensation |
| `Reference` | Référence bancaire | Texte |  |
| `Statut` | Statut | Liste | calculé / système; défaut : Enregistré; valeurs : Enregistré, Annulé |
| `Observations` | Observations | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_RELANCES` - Relances clients

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `FactureID` | Facture | Référence | obligatoire; → T_FACTURES.NumFacture |
| `DateRelance` | Date | Date | obligatoire |
| `Niveau` | Niveau | Liste | obligatoire; valeurs : 1 - Rappel, 2 - Relance, 3 - Mise en demeure |
| `Moyen` | Moyen | Liste | obligatoire; valeurs : Téléphone, E-mail, Courrier, Visite |
| `Commentaire` | Commentaire | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_RECLAMATIONS` - Réclamations et litiges

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `NumReclamation` | N° réclamation | Texte | unique; calculé / système; numérotation REC-AAAA-nnnnn |
| `DateRec` | Date | Date | obligatoire |
| `Origine` | Origine | Liste | obligatoire; valeurs : Client, Atiko, Interne, Fournisseur |
| `Objet` | Objet | Texte | obligatoire |
| `FactureID` | Facture concernee | Référence | → T_FACTURES.NumFacture |
| `Description` | Description | Texte long |  |
| `MontantConteste` | Montant contesté (GNF) | Montant GNF | ≥ 0; défaut : 0 |
| `Statut` | Statut | Liste | défaut : Ouverte; valeurs : Ouverte, En traitement, Résolue, Rejetée |
| `Resolution` | Résolution | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_DOCUMENTS` - Gestion documentaire

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Entite` | Rattache a | Liste | obligatoire; valeurs : Camion, Chauffeur, Contrat, Client, Fournisseur, Société |
| `EntiteRef` | Référence entite | Texte | obligatoire |
| `TypeDoc` | Type de document | Liste | obligatoire; valeurs : Assurance, Visite technique, Carte grise, Permis, Contrat location, Contrat transport, Pièce identite, Attestation, Autre |
| `Reference` | Référence | Texte |  |
| `DateEmission` | Émis le | Date |  |
| `DateExpiration` | Expire le | Date |  |
| `Chemin` | Fichier | Fichier |  |
| `Observations` | Observations | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_CLOTURES` - Clôtures journalières et mensuelles

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `TypeCloture` | Type | Liste | obligatoire; valeurs : Journalière, Mensuelle |
| `Periode` | Période | Texte | obligatoire |
| `DateCloture` | Date de clôture | Date | calculé / système |
| `Tonnage` | Tonnage valide (t) | Décimal | calculé / système |
| `NbRotations` | Rotations validées | Entier | calculé / système |
| `EnAttente` | Saisies en attente | Entier | calculé / système |
| `Anomalies` | Anomalies | Entier | calculé / système |
| `Statut` | Statut | Liste | calculé / système; valeurs : Clôturée, Réouverte |
| `Commentaire` | Commentaire | Texte long |  |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_USERS` - Utilisateurs

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Login` | Identifiant | Texte | obligatoire; unique |
| `NomComplet` | Nom complet | Texte | obligatoire |
| `Profil` | Profil | Liste | obligatoire; valeurs : Administrateur, Directeur, Responsable exploitation, Comptable, Agent de saisie |
| `Sel` | Sel | Texte | calculé / système |
| `HashMDP` | Empreinte mot de passe | Texte | calculé / système |
| `DoitChanger` | Doit changer le mot de passe | Oui/Non | défaut : Oui |
| `Actif` | Actif | Oui/Non | défaut : Oui |
| `Echecs` | Échecs de connexion | Entier | calculé / système; défaut : 0 |
| `DerniereConnexion` | Dernière connexion | Texte | calculé / système |
| `CreePar` | Créé par | Texte | calculé / système |
| `CreeLe` | Créé le | Date | calculé / système |
| `ModifPar` | Modifié par | Texte | calculé / système |
| `ModifLe` | Modifié le | Date | calculé / système |

### `T_DROITS` - Droits par profil

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Profil` | Profil | Texte | obligatoire |
| `Module` | Module | Texte | obligatoire |
| `Droits` | Droits (L C M V S I) | Texte |  |

### `T_PARAM` - Paramètres

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Cle` | Cle | Texte | obligatoire; unique; calculé / système |
| `Valeur` | Valeur | Texte |  |
| `TypeValeur` | Type | Liste | calculé / système; valeurs : Texte, Nombre, Date, Oui/Non |
| `Categorie` | Catégorie | Texte | calculé / système |
| `Description` | Description | Texte | calculé / système |

### `T_SEQ` - Compteurs de numérotation

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `Cle` | Cle | Texte | obligatoire; unique |
| `Dernier` | Dernier numéro | Entier |  |

### `T_AUDIT` - Journal d'audit

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Horodatage` | Date / heure | Texte | calculé / système |
| `Utilisateur` | Utilisateur | Texte | calculé / système |
| `Action` | Action | Texte | calculé / système |
| `TableCible` | Table | Texte | calculé / système |
| `EnregID` | Enreg. | Texte | calculé / système |
| `Details` | Détails | Texte | calculé / système |

### `T_SAUVEGARDES` - Historique des sauvegardes

| Champ | Libellé | Type | Règles |
|---|---|---|---|
| `ID` | ID | Identifiant (auto) | calculé / système |
| `Horodatage` | Date / heure | Texte | calculé / système |
| `TypeSauv` | Type | Liste | calculé / système; valeurs : Manuelle, Automatique, Pre-restauration, Clôture |
| `Fichier` | Fichier | Texte | calculé / système |
| `TailleKo` | Taille (Ko) | Entier | calculé / système |
| `Verifiee` | Intégrité vérifiée | Oui/Non | calculé / système |
| `Utilisateur` | Utilisateur | Texte | calculé / système |

## Registre des modules (menu)

| Clé | Titre | Catégorie | Table | Actions |
|---|---|---|---|---|
| DASHBOARD | Tableau de bord | PILOTAGE | - | - |
| EXPLOITATION | Exploitation (journée) | PILOTAGE | - | - |
| CAMIONS | Gestion des camions | FLOTTE | T_CAMIONS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, TRUCK_CARD, TRUCK_STATS, DEACTIVATE |
| CHAUFFEURS | Chauffeurs | FLOTTE | T_CHAUFFEURS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, DRV_CARD, DRV_STATS |
| AFFECTATIONS | Affectations | FLOTTE | T_AFFECTATIONS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, AFF_END |
| PLANNING | Programme journalier | EXPLOITATION | T_PLANNING | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, PLAN_GEN |
| ROTATIONS | Rotations | EXPLOITATION | T_ROTATIONS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, VALID, REJECT, CANCEL, ATTACH, OPENDOC, VALID_DAY |
| PESEES | Pesées et tickets | EXPLOITATION | T_PESEES | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, ATTACH, OPENDOC |
| OBJECTIFS | Objectifs de tonnage | EXPLOITATION | T_OBJECTIFS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH |
| CARBURANT | Carburant | EXPLOITATION | T_CARBURANT | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, VALID, REJECT, ATTACH, FUEL_ANALYZE |
| LOCATION | Location horaire | EXPLOITATION | T_LOCATION | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, VALID, REJECT, RENT_GEN, RENT_STATEMENT |
| PNEUS | Pneumatiques | MAINTENANCE | T_PNEUS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, TIRE_MOUNT, TIRE_UNMOUNT, TIRE_TRANSFER, TIRE_REPAIR, TIRE_RETURN, TIRE_SCRAP, TIRE_HIST |
| PNEU_MVT | Mouvements de pneus | MAINTENANCE | T_PNEU_MVT | VIEW, PRINT, EXPORT, REFRESH |
| ARTICLES | Stock et consommables | MAINTENANCE | T_ARTICLES | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, STOCK_IN, STOCK_OUT |
| STOCK_MVT | Mouvements de stock | MAINTENANCE | T_STOCK_MVT | VIEW, DEL, PRINT, EXPORT, REFRESH |
| INCIDENTS | Incidents | MAINTENANCE | T_INCIDENTS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, INC_PROGRESS, INC_CLOSE, ATTACH, OPENDOC |
| MAINTENANCE | Maintenance | MAINTENANCE | T_MAINTENANCE | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, MNT_DONE |
| DEPENSES | Dépenses | FINANCES | T_DEPENSES | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, VALID, REJECT, ATTACH, OPENDOC |
| PERSONNEL | Personnel | FINANCES | T_PERSONNEL | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH |
| AVANCES | Avances | FINANCES | T_AVANCES | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, ADV_REFUND |
| CAISSE | Caisse | FINANCES | T_CAISSE | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH |
| FACTURES | Facturation | FINANCES | T_FACTURES | VIEW, DEL, PRINT, EXPORT, REFRESH, INV_GEN, INV_VALID, INV_PREVIEW, INV_PDF, INV_CANCEL, INV_LITIGE, PAY_ADD |
| PAIEMENTS | Paiements | FINANCES | T_PAIEMENTS | NEW, VIEW, PRINT, EXPORT, REFRESH, CANCEL |
| RECOUVREMENT | Recouvrement | FINANCES | T_RELANCES | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, CLIENT_SIT, AGING |
| RECLAMATIONS | Réclamations / litiges | FINANCES | T_RECLAMATIONS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH |
| CLIENTS | Clients | REFERENTIELS | T_CLIENTS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, CLIENT_SIT |
| FOURNISSEURS | Fournisseurs | REFERENTIELS | T_FOURNISSEURS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH |
| LIEUX | Lieux | REFERENTIELS | T_LIEUX | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH |
| DOCUMENTS | Gestion documentaire | REFERENTIELS | T_DOCUMENTS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, ATTACH, OPENDOC |
| RAPPORTS | Rapports et états | PILOTAGE | - | - |
| CLOTURES | Clôtures | ADMINISTRATION | T_CLOTURES | VIEW, PRINT, EXPORT, REFRESH, CLOSE_DAY, CLOSE_MONTH, REOPEN, COHERENCE |
| PARAMETRES | Paramètres | ADMINISTRATION | T_PARAM | EDIT, VIEW, PRINT, EXPORT, REFRESH |
| UTILISATEURS | Utilisateurs et droits | ADMINISTRATION | T_USERS | NEW, EDIT, VIEW, DEL, PRINT, EXPORT, REFRESH, PWD_RESET, RIGHTS |
| AUDIT | Audit et historique | ADMINISTRATION | T_AUDIT | VIEW, PRINT, EXPORT, REFRESH |
| SAUVEGARDES | Sauvegardes | ADMINISTRATION | T_SAUVEGARDES | VIEW, REFRESH, BACKUP, RESTORE, INTEGRITY, DEMO_RESET |

## Matrice des droits par défaut

L lire, C créer, M modifier, V valider, S supprimer, I imprimer/exporter.

| Module | Administrateur | Directeur | Responsable exploitation | Comptable | Agent de saisie |
|---|---|---|---|---|---|
| Tableau de bord | LCMVSI | LVI | LCMVSI | LCMI | LI |
| Exploitation (journée) | LCMVSI | LVI | LCMVSI | LCMI | LI |
| Gestion des camions | LCMVSI | LVI | LCMVSI | LI | LI |
| Chauffeurs | LCMVSI | LVI | LCMVSI | LI | LI |
| Affectations | LCMVSI | LVI | LCMVSI | LI | LI |
| Programme journalier | LCMVSI | LVI | LCMVSI | LI | LCMI |
| Rotations | LCMVSI | LVI | LCMVSI | LI | LCMI |
| Pesées et tickets | LCMVSI | LVI | LCMVSI | LI | LCMI |
| Objectifs de tonnage | LCMVSI | LVI | LCMVSI | LI | LI |
| Carburant | LCMVSI | LVI | LCMVSI | LVI | LCMI |
| Location horaire | LCMVSI | LVI | LCMVSI | LVI | LCMI |
| Pneumatiques | LCMVSI | LVI | LCMVSI | LI | LCMI |
| Mouvements de pneus | LCMVSI | LVI | LCMVSI | LI | LI |
| Stock et consommables | LCMVSI | LVI | LCMVSI | LI | LCMI |
| Mouvements de stock | LCMVSI | LVI | LCMVSI | LI | LCMI |
| Incidents | LCMVSI | LVI | LCMVSI | LI | LCMI |
| Maintenance | LCMVSI | LVI | LCMVSI | LI | LI |
| Dépenses | LCMVSI | LVI | LCMI | LCMVSI | LCMI |
| Personnel | LCMVSI | LVI | LI | LCMVSI | - |
| Avances | LCMVSI | LVI | LI | LCMVSI | - |
| Caisse | LCMVSI | LVI | LI | LCMVSI | - |
| Facturation | LCMVSI | LVI | LI | LCMVSI | - |
| Paiements | LCMVSI | LVI | LI | LCMVSI | - |
| Recouvrement | LCMVSI | LVI | LI | LCMVSI | - |
| Réclamations / litiges | LCMVSI | LVI | LI | LCMVSI | - |
| Clients | LCMVSI | LVI | LCMVSI | LCMI | LI |
| Fournisseurs | LCMVSI | LVI | LCMVSI | LCMI | LI |
| Lieux | LCMVSI | LVI | LCMVSI | LCMI | LI |
| Gestion documentaire | LCMVSI | LVI | LCMVSI | LCMI | LI |
| Rapports et états | LCMVSI | LVI | LCMVSI | LCMI | LI |
| Clôtures | LCMVSI | LI | LVI | LVI | - |
| Paramètres | LCMVSI | LI | - | - | - |
| Utilisateurs et droits | LCMVSI | - | - | - | - |
| Audit et historique | LCMVSI | LI | - | - | - |
| Sauvegardes | LCMVSI | LI | - | - | - |

## Paramètres

| Clé | Valeur par défaut | Catégorie | Description |
|---|---|---|---|
| `SOCIETE_NOM` | MINING TRANSIT SARL | Société | Raison sociale affichée sur l'écran et les documents |
| `SOCIETE_SITE` | Sangaredi | Société | Site d'exploitation |
| `SOCIETE_ACTIVITE` | Exploitation Bauxite | Société | Activité affichée dans le bandeau |
| `SOCIETE_ADRESSE` | Quartier Centre, Sangaredi - Boke, Republique de Guinée | Société | Adresse (factures) |
| `SOCIETE_TEL` | +224 620 00 00 00 | Société | Téléphone (factures) |
| `SOCIETE_EMAIL` | contact@miningtransit.demo | Société | E-mail (factures) |
| `SOCIETE_NIF` | NIF 000000000 / RCCM GN.TCC.0000.B.00000 | Société | Identifiants fiscaux (DONNÉES FICTIVES) |
| `SOCIETE_BANQUE` | Banque Demo Guinée - IBAN GN00 0000 0000 0000 0000 | Société | Coordonnées bancaires (factures) |
| `DEVISE` | GNF | Finances | Devise de gestion |
| `TVA_TAUX` | 0 | Finances | Taux de TVA applique aux factures (0 = exonéré / hors champ, a confirmer avec le fiscaliste) |
| `FACT_PREFIXE` | FAC | Finances | Prefixe de numérotation des factures |
| `FACT_MENTIONS` | Paiement par virement a réception. Tout retard de paiement fera l'objet de relances. | Finances | Mentions en pied de facture |
| `FACT_SIGNATAIRE` | Le Directeur Général | Finances | Signataire des factures |
| `RELANCE_J1` | 7 | Finances | Jours de retard avant 1er rappel |
| `RELANCE_J2` | 21 | Finances | Jours de retard avant relance |
| `RELANCE_J3` | 45 | Finances | Jours de retard avant mise en demeure |
| `LOC_DEDUIRE_ATTENTE` | Non | Location | Les heures d'attente sont-elles déduites des heures facturables par Atiko ? |
| `LOC_DEDUIRE_IMMOB_ATIKO` | Oui | Location | Les immobilisations imputables a Atiko suspendent-elles la location ? (selon contrat) |
| `LOC_DEDUIRE_IMMOB_EXPLOITANT` | Non | Location | Les immobilisations imputables a l'exploitant suspendent-elles la location ? |
| `LOC_HEURES_MIN_JOUR` | 0 | Location | Minimum d'heures facturables par jour (0 = aucun). Le minimum propre au camion prime s'il est renseigné |
| `LOC_HEURES_MAX_JOUR` | 24 | Location | Plafond d'heures par relevé |
| `CARB_TOLERANCE_PCT` | 25 | Carburant | Écart toléré (%) par rapport a la consommation de référence avant alerte |
| `CARB_MAX_LITRES` | 600 | Carburant | Quantité maximale plausible par distribution (L) |
| `CARB_DOUBLON_HEURES` | 6 | Carburant | Deux distributions au meme camion dans ce délai (h) = doublon potentiel |
| `CARB_PRIX_DEFAUT` | 12000 | Carburant | Prix par défaut du litre de gasoil (GNF) - DEMO |
| `CARB_STOCK_ALERTE` | 3000 | Carburant | Seuil d'alerte stock cuve (L) |
| `ROT_TOLERANCE_SURCHARGE_PCT` | 10 | Exploitation | Surcharge tolérée au-dela de la capacité camion (%) |
| `ROT_NET_MIN` | 10 | Exploitation | Poids net minimum plausible (t) |
| `ROT_DUREE_MAX_H` | 6 | Exploitation | Durée maximale plausible d'une rotation (h) |
| `PNEU_PROFONDEUR_MIN` | 6 | Pneumatiques | Profondeur minimale avant remplacement (mm) |
| `PNEU_STOCK_MIN` | 4 | Pneumatiques | Stock minimum de pneus neufs |
| `ALERTE_DOC_JOURS` | 30 | Alertes | Délai d'anticipation des expirations de documents (jours) |
| `SAUV_DOSSIER` | (vide) | Sauvegarde | Dossier de sauvegarde (vide = sous-dossier Sauvegardes du classeur) |
| `SAUV_AUTO_HEURES` | 24 | Sauvegarde | Intervalle de sauvegarde automatique a la fermeture (h) |
| `SAUV_CONSERVER` | 30 | Sauvegarde | Nombre de sauvegardes conservées (la dernière valide n'est jamais supprimée) |
| `SAUV_DERNIERE` | (vide) | Sauvegarde | Horodatage de la dernière sauvegarde (système) |
| `DOSSIER_JUSTIFICATIFS` | (vide) | Documents | Dossier de stockage des justificatifs (vide = sous-dossier Justificatifs) |
| `DOSSIER_EXPORTS` | (vide) | Documents | Dossier des exports PDF/Excel (vide = sous-dossier Exports) |
| `CLOTURE_DERNIER_JOUR` | (vide) | Exploitation | Dernier jour clôture (système) - les saisies antérieures sont verrouillées |
| `CLOTURE_DERNIER_MOIS` | (vide) | Exploitation | Dernier mois clôture AAAA-MM (système) |
| `CHARGES_SOCIALES_PCT` | 18 | Finances | Taux de charges sociales employeur applique aux salaires (%) - a confirmer |
| `UI_PLEIN_ECRAN` | Oui | Système | Masquer le ruban et la barre de formule pendant l'utilisation (restaures a la fermeture) |
| `DEMO_MODE` | Oui | Système | Environnement de démonstration (bandeau DEMO, réinitialisation autorisée) |
| `APP_VERSION` | 1.0.0 | Système | Version de l'application |
