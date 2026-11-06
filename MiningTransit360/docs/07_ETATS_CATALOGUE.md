# 07 - Catalogue des états

Généré par `tools/make_docs.py` à partir de `src/vba/modules/mRpt_Catalog.bas`.

| Code | Catégorie | État | Période | Filtre | Contenu |
|---|---|---|---|---|---|
| `PRD_DAY` | Production | Rapport journalier de production | oui | - | Production du jour par camion et détail des rotations |
| `PRD_WEEK` | Production | Rapport hebdomadaire | oui | - | Tonnage par jour et par camion sur la semaine |
| `PRD_MONTH` | Production | Rapport mensuel de production | oui | - | Production par jour, camion, client ; objectif vs réalisé |
| `PRD_YEAR` | Production | Rapport annuel | oui | - | Tonnage, rotations et CA par mois |
| `PRD_TRUCK` | Production | Production par camion | oui | - | Rotations, tonnage, CA, coûts et marge par camion |
| `PRD_DRIVER` | Production | Production par chauffeur | oui | - | Performance des chauffeurs |
| `PRD_ROTATIONS` | Production | Historique des rotations | oui | camion | Détail des rotations (filtre camion optionnel) |
| `PRD_VALREJ` | Production | Tonnages validés et rejetés | oui | - | Validation, rejets et motifs |
| `PRD_OBJ` | Production | Objectifs / réalisations | à date | - | Comparaison mensuelle des objectifs |
| `PRD_WAIT` | Production | Temps d'attente et heures improductives | oui | - | Analyse par camion |
| `LOC_HOURS` | Location | Relevé des heures par camion | oui | camion | Heures brutes, attente, immobilisations, retenues |
| `LOC_COST` | Location | Coût de location | oui | - | Coût de location par camion |
| `LOC_IMMO` | Location | Immobilisations | oui | - | Incidents avec immobilisation et responsabilités |
| `LOC_DISPO` | Location | Disponibilité de la flotte | oui | - | Taux de disponibilité par camion |
| `FUEL_CONSO` | Carburant | Consommation par camion | oui | - | Litres, heures, L/h vs référence |
| `FUEL_COST` | Carburant | Coût du carburant | oui | - | Distributions, approvisionnements, stock cuve |
| `FUEL_COMPARE` | Carburant | Comparatif de consommation | oui | - | Période vs période précédente |
| `FUEL_TON` | Carburant | Coût carburant par tonne | oui | - | GNF/t et L/t par camion |
| `FUEL_ANOM` | Carburant | Anomalies carburant | oui | - | Surconsommations, doublons, quantités aberrantes |
| `TIRE_STOCK` | Pneumatiques | Stock de pneumatiques | à date | - | Pneus en stock et en réparation |
| `TIRE_MVT` | Pneumatiques | Mouvements de pneus | oui | - | Entrées, montages, transferts, réparations, réformes |
| `TIRE_TRUCK` | Pneumatiques | Pneus montés par camion | à date | camion | Positions et usure par camion |
| `TIRE_WEAR` | Pneumatiques | Usure et prévision des besoins | à date | - | Heures, profondeur, date prévisionnelle de remplacement |
| `TIRE_COST` | Pneumatiques | Coût pneumatique par camion | oui | - | Réparations et achats |
| `STOCK_ART` | Pneumatiques | Stock de consommables | à date | - | Valorisation au PMP et alertes |
| `FIN_EXP` | Finances | Dépenses | oui | - | Par catégorie et responsable contractuel |
| `FIN_INV` | Finances | Factures émises | oui | - | Factures de la période |
| `FIN_PAY` | Finances | Encaissements | oui | - | Règlements reçus |
| `FIN_REC` | Finances | Créances clients | à date | - | Soldes dus par client |
| `FIN_LATE` | Finances | Retards de paiement | à date | - | Factures échues et niveau de relance suggéré |
| `FIN_AGING` | Finances | Balance âgée | à date | - | Créances par ancienneté |
| `FIN_MARGIN` | Finances | Marge opérationnelle | oui | - | Compte d'exploitation simplifié |
| `FIN_CASH` | Finances | Journal de caisse | oui | - | Mouvements et solde |
| `FIN_ADV` | Finances | Avances au personnel | à date | - | Avances en cours |
| `CLIENT_SIT` | Finances | Situation client | à date | client | Relevé de compte client |
| `DIR_KPI` | Direction | Tableau de bord mensuel | oui | - | Indicateurs clés de la période |
| `DIR_GENERAL` | Direction | Rapport général de gestion | oui | - | Synthèse production, finances, flotte, alertes |
| `DIR_RENTA` | Direction | Analyse de rentabilité | oui | - | Structure des coûts et marge par camion |
| `DIR_PERF` | Direction | Rapport de performance | oui | - | Classement camions et chauffeurs |
| `DIR_ACTIVITY` | Direction | Rapport d'activité | oui | - | Faits marquants de la période |
| `CARD_TRUCK` | Fiches | Fiche camion | oui | camion | Fiche détaillée et statistiques d'un camion |
| `CARD_DRIVER` | Fiches | Fiche chauffeur | oui | chauffeur | Fiche détaillée d'un chauffeur |
| `CHECK_COH` | Fiches | Contrôle de cohérence | à date | - | Rapport d'intégrité des données |

**43 états.** S'y ajoutent : la facture (aperçu, impression, PDF), l'impression / l'export Excel de toute liste de module, le relevé horaire, la situation client et l'historique d'un pneu accessibles depuis les fenêtres de module.
