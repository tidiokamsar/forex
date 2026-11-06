# 01 - Cahier d'architecture

## 1. Analyse de la maquette

La maquette fixe l'identité visuelle de toute l'application :

| Zone (pixels maquette) | Contenu | Réalisation Excel |
|---|---|---|
| Bandeau 0-145 px | Logo camion-benne orange, « MINING TRANSIT 360 », devise, société, site, photo de piste de bauxite, bloc date / heure / utilisateur | Formes + images (`logo.png`, `banner.png`), textes dynamiques `dv_*`, horloge rafraîchie toutes les 30 s |
| Menu latéral 0-262 px | 15 entrées avec icônes, entrée active orange, séparateur, état système, bouton « Se déconnecter » | Formes cliquables `nav_*` (une macro unique `UI_ShapeClick`) |
| 5 cartes KPI | Tonnage du jour, CA du mois, encaissements, créances, camions actifs (dégradés bleu / vert / orange / violet / turquoise) | Rectangles arrondis à dégradé, icône, valeur, tendance |
| 3 panneaux milieu | Production de la semaine (histogramme), répartition des charges (anneau), alertes (4 lignes + « Voir » + pastille de comptage) | Vrais graphiques Excel liés à la feuille `CALC`, alertes recalculées |
| 3 panneaux bas | Statut de la flotte (anneau + légende), factures du mois (tableau + statuts colorés + total), marge (montant, taux, tendance, courbe) | Graphiques + formes |
| 6 actions rapides | Nouvelle rotation, carburant, pneus, facture, rapports, paramètres | Boutons connectés |
| Pied de page | Version, devise, liseré orange | Formes |

Charte : marine `#0B2A55`, orange `#F7941D`, bleu `#1E6FD9`, vert `#169648`, violet `#763EC7`, turquoise `#16A0C4`,
rouge `#DC3535`, fond `#E8EEF6`, police Segoe UI. Les coordonnées du code sont celles de la maquette (px) converties en points (x 0,75)
et le zoom est ajusté à la fenêtre : la superposition des coordonnées codées sur l'image de la maquette a été vérifiée (voir 08_TESTS).

Les écrans absents de la maquette (fenêtres de module, fiches, dialogues, centre de rapports, connexion) reprennent la même charte :
bandeau marine avec liseré de couleur du module, boutons pleins colorés, fond gris-bleu, cartes blanches.

## 2. Principes

1. **Création à partir de zéro** : aucun classeur ni code antérieur ; tout est généré depuis ce dépôt.
2. **Source unique de vérité** du modèle : `tools/schema.py` produit les tables, les métadonnées `SYS_SCHEMA / SYS_MODULES`, la
   documentation et les contrôles de test.
3. **Interface pilotée par les métadonnées** : une seule fenêtre de liste (`frmList`) et une seule fiche (`frmRecord`) servent les
   30 modules à table, à partir de `SYS_SCHEMA`. Les fenêtres sont construites à l'exécution (contrôles dynamiques) : pas de fichier
   `.frx` binaire à maintenir, cohérence visuelle garantie.
4. **Aucune règle métier dans l'interface** : tout enregistrement passe par `Rec_Save` (contrôles, calculs, audit, transaction).
5. **Traçabilité** : journal d'audit de chaque création / modification (avec différences) / validation / rejet / suppression /
   connexion / export / sauvegarde.
6. **Évolutivité** : la couche `mData_DB` est le seul point de contact avec le stockage.

## 3. Couches logicielles

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ INTERFACE   mUI_Dashboard  mUI_Nav  mUI_Actions  mUI_Kit  mUI_Theme   │
 │             mUI_Dialogs    frmList  frmRecord  frmInput  frmLogin     │
 │             frmReports     cCtlEvents                                 │
 ├──────────────────────────────────────────────────────────────────────┤
 │ ÉTATS       mRpt_Engine (rendu A4, PDF, Excel)  mRpt_Catalog (43)     │
 │             mRpt_Invoice (facture)  cReport                           │
 ├──────────────────────────────────────────────────────────────────────┤
 │ MÉTIER      mBiz_Records (pipeline)  mBiz_Dispatch (aiguillage)       │
 │             mBiz_Fleet  mBiz_Operations  mBiz_Fuel  mBiz_Rental       │
 │             mBiz_Tires  mBiz_Maintenance  mBiz_Finance  mBiz_Billing  │
 │             mBiz_KPI  mBiz_Alerts  mBiz_Closing  mBiz_Admin           │
 ├──────────────────────────────────────────────────────────────────────┤
 │ SYSTÈME     mSys_Backup  mSys_Demo  mSys_Install  mTest_Suite         │
 ├──────────────────────────────────────────────────────────────────────┤
 │ NOYAU       mCore_Config  mCore_Util  mCore_Param (paramètres,        │
 │             numérotation)  mCore_Log (audit)  mCore_Security          │
 ├──────────────────────────────────────────────────────────────────────┤
 │ DONNÉES     mData_DB (tables, critères, transactions)  mData_Schema   │
 │             cFieldDef ; ListObjects T_* sur feuilles DB_* / SYS_*     │
 └──────────────────────────────────────────────────────────────────────┘
```

Dépendances descendantes uniquement (l'interface appelle le métier, le métier appelle les données). Les fenêtres reçoivent leurs
événements via `cCtlEvents` (relais `WithEvents`) et ne contiennent que de la présentation.

## 4. Pipeline d'enregistrement (`mBiz_Records.Rec_Save`)

```
 Formulaire ──► conversion des saisies (cFieldDef.Parse)
            ──► Rec_Save(table, valeurs, id)
                  1. droits (Sec_Require C/M)
                  2. verrous : enregistrement facturé / validé / annulé, période clôturée
                  3. valeurs par défaut + fusion avec l'existant
                  4. règles métier de la table (Biz_BeforeSave → mBiz_*) : calculs + contrôles
                  5. contrôles génériques du schéma (obligatoire, unicité, minimum, liste, référence)
                  6. numérotation métier (ROT-2026-00001...)
                  7. transaction (DB_BeginTx) → écriture → effets de bord (Biz_AfterSave)
                  8. audit (création ou différences) → DB_Commit
            ◄── erreur métier : message affiché dans la fiche, rollback automatique
```

Changements de statut : `Rec_SetStatus` (validation, rejet avec motif, annulation avec motif). Suppressions : `Rec_Delete`
(contrôle des dépendances, seules les saisies non validées sont supprimables, l'historique ne l'est jamais).

## 5. Couche de données

* Une table = un `ListObject` ; lecture en tableau mémoire mis en cache, invalidé à chaque écriture.
* Critères génériques : `DB_Sum("T_ROTATIONS", "PoidsNet", "Statut", "=", "Validée", "DateRotation", ">=", d1, ...)` ;
  opérateurs `= <> > >= < <= IN NOTIN LIKE ISBLANK NOTBLANK`.
* Transactions : instantané des tables concernées, restauration intégrale en cas d'erreur (`DB_Rollback`).
* Identifiants : `T_SEQ` (`ID_<table>` et compteurs métier `ROT-2026`, `FAC-2026`...), robustes aux imports (max + 1).

### Migration future (SQL / application web)

Seul `mData_DB` connaît Excel. Pour passer à SQL Server / PostgreSQL : réimplémenter `DB_Data`, `DB_Insert`, `DB_Update`,
`DB_Delete`, `DB_ReplaceAll` et les transactions avec ADODB ; le schéma (`tools/schema.py`) fournit directement le DDL ; les règles
métier documentées (05) et les tests (08) servent de spécification à une application web.

## 6. Fenêtres

| Fenêtre | Rôle |
|---|---|
| `frmLogin` | Connexion (verrouillage après 5 échecs) |
| `frmList` | Liste de module : recherche, filtres période / statut / actifs, tri par colonne, totaux, barre d'actions selon les droits |
| `frmRecord` | Création / modification / consultation pilotées par le schéma, champs calculés en direct, remplissage automatique (chauffeur affecté, client de la destination), justificatifs |
| `frmInput` | Dialogue générique : alertes, confirmations, motifs de rejet, sélection de période / mois, saisies métier courtes (pneus, paiements, stock...), fiches, sous-menus en tuiles, grilles |
| `frmReports` | Centre de rapports : catégories, états, période, filtre, consulter / aperçu / imprimer / PDF / Excel |

## 7. Feuilles du classeur

| Feuille | Visibilité | Contenu |
|---|---|---|
| ACCUEIL | visible | Tableau de bord (formes, graphiques) |
| RAPPORT, FACTURE | visibles | Rendu des états et de la facture (bouton de retour non imprimé) |
| TESTS | visible | Résultats de la recette automatique |
| CALC | très masquée | Données des graphiques |
| ASSETS | très masquée | Icônes incorporées (autonomie sans dossier assets) |
| DB_* | très masquées | 28 tables métier |
| SYS_* | très masquées | Utilisateurs, droits, paramètres, compteurs, audit, sauvegardes, schéma, modules |

## 8. Choix techniques justifiés

| Choix | Raison |
|---|---|
| Contrôles construits à l'exécution | Fichiers `.frm` lisibles et versionnables, aucun `.frx` binaire, identité visuelle centralisée (`mUI_Kit`) |
| Assemblage du `.xlsm` par script PowerShell | Seul moyen fiable et reproductible de produire un projet VBA compilé ; la compilation réelle et la recette sont exécutées par le script |
| Statuts accentués (« Validée ») comme valeurs stockées | Affichage professionnel sans table de traduction ; source VBA convertie en Windows-1252 par le VBE (vérifié par le linter) |
| Empreinte FNV maison | Pas de dépendance à .NET (désactivé sur certains postes) ; miroir Python testé ; usage : mots de passe et scellement des factures, pas de chiffrement |
| `DATE_TRAVAIL` | Permet de démontrer l'environnement à une date fixe ; vide en production |
