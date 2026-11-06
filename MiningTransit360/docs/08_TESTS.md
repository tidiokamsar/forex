# 08 - Plan de tests, résultats, anomalies corrigées, limites

## 1. Stratégie

| Niveau | Outil | Où | Statut |
|---|---|---|---|
| Analyse statique du VBA | `tools/vba_lint.py` | tout poste (Python 3) | **exécuté : 0 erreur** sur 43 composants / 595 procédures / ~10 700 lignes |
| Données et règles métier (hors Excel) | `tests/test_mt360.py` (38 tests) | tout poste (Python 3 + openpyxl) | **exécuté : 38 / 38 OK** |
| Fidélité géométrique du tableau de bord | superposition des coordonnées du code sur la maquette | tout poste | **exécuté** (menu latéral recalé, voir § 4) |
| Compilation VBA | `build/Build-MT360.ps1` (Débogage > Compiler) | Windows + Excel | **à exécuter** : Excel n'était pas disponible dans l'environnement de développement |
| Recette fonctionnelle dans Excel | `mTest_Suite.Test_RunAll` (≈ 100 contrôles), lancée par le script de construction | Windows + Excel | **à exécuter** (résultat écrit dans `dist/test_results_vba.txt` et la feuille TESTS) |
| Rendu visuel, impression, PDF | contrôle visuel | Windows + Excel | **à exécuter** |

> Conformément au cahier des charges, aucune validation non exécutée n'est présentée comme acquise : la compilation, la recette VBA
> et le rendu visuel n'ont **pas** été exécutés dans l'environnement de développement (Linux, sans Excel). Le script de construction
> les exécute automatiquement et s'arrête avec un code d'erreur explicite en cas d'échec.

Commandes :

```
python3 tools/vba_lint.py
python3 -m unittest tests/test_mt360.py -v
build\Build-MT360.cmd          (Windows : compilation + installation + recette VBA)
```

## 2. Plan de tests fonctionnels (recette VBA `mTest_Suite`)

Toute la recette s'exécute dans une transaction annulée à la fin : les données de démonstration sont restituées à l'identique
(dernier contrôle de la recette).

| Domaine | Cas testés |
|---|---|
| Sécurité | vecteurs d'empreinte identiques à Python (dont caractères accentués), connexion valide, refus de mauvais mot de passe, droits de l'agent de saisie |
| Cohérence | jeu démo sans erreur d'intégrité ; empreintes des 6 factures démo valides |
| Outils | montant en lettres, conversions de dates et d'heures |
| Flotte | création de camion, doublon de code refusé, modification du tarif, chevauchement d'affectation refusé, permis expiré refusé, affectation valide, passage « En service », génération du programme |
| Rotations / pesées | création, poids net calculé, client déduit, numérotation, ticket créé, doublon de ticket refusé, surcharge refusée, chauffeur non affecté refusé, doublon horaire refusé, validation, synchronisation du ticket, rejet sans motif refusé, rejet avec motif, modification d'une rotation validée → Saisie |
| Carburant | montant, surconsommation détectée, compteur décroissant refusé, doublon signalé, approvisionnement sans fournisseur refusé, stock cuve |
| Location | heures brutes, déductions selon paramètres, montant, changement de règle paramétrable, doublon refusé, immobilisation > heures brutes refusée |
| Pneus / stock | entrée historisée, montage, position occupée refusée, transfert, démontage vers réparation, retour, réforme, historique complet, pneus à remplacer, PMP, sortie > stock refusée |
| Incidents | camion « En panne », coût Atiko non imputé, clôture (durée, remise en service), reprise des heures d'immobilisation pour la location |
| Dépenses / caisse | carburant hors module refusé, saisie, doublon fournisseur refusé, validation, sortie de caisse > solde refusée |
| Facturation / paiements | brouillon = rotations validées non facturées, HT = tonnage x tarif, lignes = HT, réservation des rotations, double facturation refusée, numéro légal séquentiel, empreinte, détection de modification silencieuse, rotation facturée verrouillée, paiement partiel, solde, trop-perçu refusé, annulation d'une facture encaissée refusée, paiement total, annulation d'encaissement, créances |
| Marge / KPI | CA reconnu = CA facturé (octobre), marge = CA - charges, décomposition des charges, taux plausible, charges Atiko exclues, flotte, alertes, actualisation du tableau de bord |
| États | génération et mise en page des 43 états, export PDF, export Excel, facture PDF, orientation automatique |
| Sauvegarde | création, vérification d'intégrité, restauration |
| Clôtures | clôture refusée si carburant non validé, clôture journalière, saisie refusée sur journée clôturée |

## 3. Résultats exécutés (environnement de développement)

### Tests Python : 38 / 38 OK

Schéma (unicité, clés étrangères, valeurs par défaut, modules ↔ tables, **chaque action du registre a un traitement et un libellé
dans `mUI_Actions`**, droits complets, modules exigés présents) ; volumes minimaux du jeu démo ; intégrité référentielle, unicités,
listes autorisées, compteurs ; règles recalculées indépendamment : poids net, ticket unique et synchronisé, capacité, client de la
destination, chauffeur affecté et permis valide à chaque rotation, absence de chevauchement d'affectations, seules les rotations
validées sont facturées, factures (tonnage, HT, lignes, paiements, solde, statut, empreinte, aucune rotation oubliée), location
(règle des heures retenues, montant, unicité), carburant (montant, anomalies, stock cuve), pneus (positions, mouvements, pneus à
remplacer = ATK-003/007/010), stock d'articles, solde de caisse, imputation des coûts d'incident, statut des camions, absence de
dépenses en catégories réservées, **marge d'octobre recalculée en miroir** (CA 2 368 955 100 GNF, charges 1 591 992 000 GNF,
marge 776 963 100 GNF, **taux 32,8 %**) ; empreintes des mots de passe ; vecteurs de test VBA = Python ; montant en lettres ;
analyse statique VBA ; classeur généré.

### Analyse statique VBA : 0 erreur

Contrôles : Option Explicit, équilibre des blocs, identifiants non déclarés, noms ambigus, membres inexistants sur les classes et
formulaires du projet, nombre d'arguments, `ByRef argument type mismatch`, `Set` manquant ou abusif, déclarations en double, variable
de `For Each` typée, `Exit` incohérent, appels parenthésés multi-arguments, macros référencées (OnAction, OnTime, Run), lignes
> 1023 caractères, > 24 continuations, caractères hors Windows-1252. L'outil a été validé en injectant volontairement des erreurs
(variable non déclarée, bloc If non fermé, mauvais nombre d'arguments, membre inexistant, Set manquant, ByRef incompatible) :
toutes ont été détectées.

## 4. Anomalies détectées et corrigées pendant le développement

| # | Anomalie | Détectée par | Correction |
|---|---|---|---|
| 1 | Constante VBA contenant un appel de fonction (`ChrW`) - erreur de compilation | relecture | transformée en fonction `APP_TAGLINE()` |
| 2 | `IsNumeric` faux sur les dates : comparaisons de dates en texte (critères `>=` erronés) | relecture | `IsNumOrDate` + comparaison numérique |
| 3 | Tri par insertion O(n²) trop lent sur 2 200 rotations | relecture | tri rapide sur index |
| 4 | Contrôle de cohérence O(n²) sur les références | relecture | ensembles d'identifiants en mémoire |
| 5 | Courbe de marge hebdomadaire : 8 calculs complets de KPI (lent) | relecture | agrégation en une passe par table |
| 6 | Variable locale `pick` homonyme de la fonction `Pick` du module | linter | renommée |
| 7 | `Shape.PrintObject` inexistant (erreur de compilation) | relecture API | `DrawingObject.PrintObject` en liaison tardive |
| 8 | Appel `p = Rpt_ToPDF rep, True` sans parenthèses (erreur de syntaxe) | relecture | corrigé |
| 9 | Restauration de la variable `Err` perdue après `On Error Resume Next` dans un gestionnaire | relecture | capture préalable |
| 10 | Rotation facturée : message de période clôturée au lieu de « facturée » | recette (cas prévu) | ordre des verrous |
| 11 | `Application.Run` dans la recette : propagation d'erreur incertaine | relecture | appels protégés directs |
| 12 | Données démo : profondeur de pneus sous le seuil sur d'autres camions que prévu | test Python | formule d'usure corrigée |
| 13 | Données démo : stock cuve théorique incohérent avec le paramètre | relecture | stock initial nul, paramètre recalculé |
| 14 | Données démo : 27 dépenses < 30 exigées | contrôle des volumes | 30 dépenses |
| 15 | Menu latéral décalé de ~10 px par rapport à la maquette | superposition | coordonnées recalées |
| 16 | Protection `UserInterfaceOnly` perdue à la réouverture (rafraîchissement impossible) | relecture | réappliquée à l'ouverture et à chaque actualisation |
| 17 | Données non enregistrées si l'utilisateur ferme sans sauvegarder | relecture | enregistrement automatique à la fermeture des fenêtres et du classeur |

## 5. Limites restantes (déclarées)

1. **Compilation, recette VBA et rendu visuel non exécutés ici** (pas d'Excel) : ils le sont par `build/Build-MT360.cmd`. Des
   ajustements mineurs peuvent être nécessaires au premier passage (propriétés d'objets Excel / MSForms non vérifiables hors Excel).
2. Les `.frx` sont produits par Excel lors de la construction (`dist/src_export`) ; les sources du dépôt sont les `.frm` texte dont les
   contrôles sont créés à l'exécution (choix d'architecture).
3. Application **monoposte** : pas d'accès concurrent ; un seul utilisateur à la fois.
4. Volumétrie : conçue pour quelques dizaines de milliers de lignes par table (une année d'exploitation ≈ 12 000 rotations) ; au-delà,
   archiver par exercice ou migrer vers une base serveur (`mData_DB`).
5. Sécurité limitée par la nature d'Excel (voir 05_REGLES_DE_GESTION) ; protéger le projet VBA par mot de passe avant diffusion.
6. Justificatifs : stockés en fichiers à côté du classeur (`Justificatifs\`), le classeur conserve le chemin ; les déplacer avec le dossier.
7. Montant en lettres : règles d'accord simplifiées (ex. « quatre-vingts mille ») ; à relire pour les montants ronds atypiques.
8. TVA à 0 % par défaut : le régime fiscal applicable au transport de bauxite doit être confirmé (paramètre `TVA_TAUX`).
9. Les tarifs, salaires, prix du gasoil et clients du jeu démo sont **fictifs**.
