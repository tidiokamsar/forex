# 09 - Jeu de données de démonstration et réinitialisation

Toutes les données sont **fictives** (clients suffixés « (DEMO) », identifiants fiscaux factices, tarifs et salaires inventés).
Génération déterministe : `python3 tools/build_workbook.py` produit toujours le même jeu.

## Période simulée

Du **01/09/2026 au 05/11/2026**, date de travail de démonstration (`DATE_TRAVAIL`) : **05/11/2026**. Exploitation 6 jours sur 7
(repos le dimanche), poste 06:30 - 18:30.

## Volumes

| Table | Lignes | Exigence |
|---|---:|---:|
| Camions | 10 | 10 |
| Chauffeurs | 12 | 12 |
| Rotations | 2 207 | 120 |
| Tickets de pesée | 2 207 | 120 |
| Opérations carburant | 576 | 90 |
| Relevés horaires | 570 | 70 |
| Pneus | 114 | 40 |
| Mouvements de pneus | 223 | - |
| Incidents | 16 | 15 |
| Dépenses | 30 | 30 |
| Factures mensuelles | 6 (septembre et octobre, 3 clients) | 3 |
| Paiements | 8 | 8 |
| + affectations, programme, objectifs, articles, mouvements de stock, maintenance, personnel, avances, caisse, relances, réclamations, documents, clôtures, utilisateurs | | |

## Scénarios couverts

* **Flotte** : 8 camions en service, ATK-007 en panne (02/11), ATK-010 immobilisé (28/10, boîte de vitesses, Atiko) ; assurance
  d'ATK-004 et visite technique d'ATK-008 à échéance proche.
* **Chauffeurs** : CH-005 au permis expiré (remplacé par CH-011 sur ATK-005 le 28/10), CH-011 dont le permis expire le 14/11 ;
  changement d'affectation sur ATK-008 à la mi-septembre.
* **Production** : ≈ 1 400 t / jour, 3 clients et 3 destinations, rotations rejetées (motifs) et annulées, journée du 05/11 en attente
  de validation.
* **Carburant** : approvisionnements de cuve, 3 surconsommations injectées, 1 doublon rejeté, distributions des 04 et 05/11 à valider.
* **Location** : immobilisations imputables à Atiko (location suspendue) et à l'exploitant (location due, crevaison ATK-003).
* **Pneus** : 100 pneus montés, achats d'octobre, crevaison et réparation, transfert ATK-009 → ATK-008, réformes ; pneus à remplacer
  sur ATK-003, ATK-007, ATK-010.
* **Incidents** : 16 incidents (responsabilités Atiko, exploitant, tiers, à déterminer), 2 immobilisations en cours.
* **Finances** : dépenses validées, en saisie, rejetée (doublon), exceptionnelle, avance à refacturer à Atiko ; caisse ; avances.
* **Facturation** : septembre entièrement soldée (dont un paiement en deux fois) ; octobre : une facture payée, une partielle, une en
  cours (avec litige ouvert) ; relances et réclamations.
* **Clôtures** : journées clôturées jusqu'au 03/11, mois de septembre et octobre clôturés.
* **Marge d'octobre** : CA 2,37 Md GNF, marge ≈ 32,8 %.

## Réinitialisation

Administration > Sauvegardes > **Réinitialiser démo** (administrateur, uniquement si `DEMO_MODE = Oui`) :

1. sauvegarde « Pré-restauration » de l'état actuel ;
2. lecture de `data\demo_dataset.xlsx` (une feuille par table, tableaux `DT_<table>`) ;
3. remplacement des données table par table (correspondance par nom de colonne) ;
4. recalcul des empreintes des mots de passe de démonstration et du stock cuve ;
5. enregistrement du classeur.

Pour régénérer le fichier : `python3 tools/build_workbook.py` (écrit `dist/MT360_base.xlsx` et `data/demo_dataset.xlsx`).
