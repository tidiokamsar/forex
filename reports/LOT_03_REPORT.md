# ASTRA, rapport du Lot 3 : validation quantitative

Date : 25/10/2026. Branche : `feat/validation-quant`, publiée. Destinataires : l'utilisateur et l'architecte principal (ChatGPT).

## A. Résumé exécutif

**Objectif.**
- Évaluer scientifiquement **toutes** les familles de stratégies : walk-forward, sélection sur la validation seule, holdout scellé et consommé une seule fois.
- Ajouter des contrôles de surapprentissage : PBO par CSCV et Sharpe dégonflé.
- Faire en sorte qu'une promotion ne repose que sur des preuves authentiques.

**Résultat.**
- Implémenté en parallèle par deux agents dans des worktrees isolés, puis intégré.
- Revu trois fois de façon indépendante.
- 361 tests passent, 0 échec, 0 ignoré.

La revue a établi trois points de fond :
- les formules sont conformes à la littérature ; l'exemple numérique de l'article (0,9004) est reproduit ;
- il n'y a pas de fuite d'information ;
- sur données sans compétence, les verrous de promotion ne passent jamais : 0 cas sur 400 simulations.

**Niveau réel.** Terminé et vérifié **sur données synthétiques uniquement**. Aucune stratégie n'est promue ni promouvable sur le jeu fourni : trop peu de transactions, statut `LOW_INFORMATION`.

**Difficultés.**
- **Mon erreur de coordination.** J'avais donné aux deux agents des règles incompatibles : le PBO ne pouvait jamais se calculer. Je l'ai corrigé en passant à une matrice par périodes de 5 barres.
- **La revue a montré des chiffres trompeurs** : un Sharpe dégonflé de 0,99997 obtenu sur 3 transactions quasi identiques, et un PBO de 0,0 sur une matrice presque vide.
- **La revue a montré des contournements de promotion** : preuves fabriquées, critères laxistes, paramètres différents de ceux évalués, réutilisation du holdout. Tous ont été corrigés (voir C).

## B. Travail des agents

| Agent | Mission | Résultat |
|---|---|---|
| Spécialiste statistique (worktree isolé) | `overfitting.py` : PBO par CSCV, Sharpe dégonflé, bibliothèque standard seulement | 18 tests ; commit `3c42c22` |
| Ingénieur quantitatif (worktree isolé) | `strategy_validation.py` : walk-forward multi-familles scellé, nouveaux critères au registre, commande `evaluate-strategies` | 15 tests au départ ; commit `78397f2` |
| Coordinateur (moi) | Interface entre les modules, intégration, correction du contrat PBO, toutes les corrections issues des revues | Suite complète relancée après chaque tour |
| QA quantitative indépendante, 3 tours | Formules recalculées indépendamment, recherche de fuites, simulations sans compétence, tentatives de contournement | Tour 1 : 6 défauts (2 élevés). Tour 2 : 3 contournements élevés. Tour 3 : 1 élevé, 1 faible. Verdict « publiable » à chaque tour. |
| Documentation (`.md` seulement) | RESEARCH_PROTOCOL, README, TASKS, ROADMAP, CHANGELOG, TESTING, ARCHITECTURE | Relu ; a signalé le Sharpe de 0,99997 sur 3 transactions |

## C. Preuves techniques

```bash
python -m ruff check src tests                        # All checks passed!
PYTHONPATH=src python -m unittest discover -s tests   # Ran 361 tests ... OK
python -m compileall -q src tests                     # code de sortie 0
```

**Tests du lot :** test_overfitting 18, test_strategy_validation 26, test_registry 19, test_cli_operations 6.

**Défauts trouvés par la revue et corrigés :**

| Gravité | Défaut | Correction |
|---|---|---|
| Élevée | Variance nulle testée exactement, d'où un Sharpe de 4,8×10¹² sur du bruit d'arrondi | Tolérance relative |
| Élevée | Preuves acceptées sans provenance : promotion obtenue sur preuves fabriquées | Les preuves sont recalculées à partir du rapport de holdout stocké. Sont vérifiés : le plan, le candidat, le moment (version enregistrée avant le holdout), les paramètres, le code et les données. |
| Élevée | Critères laxistes choisis par l'agent ; plusieurs versions sur un même holdout | Plancher de critères, vérifié à l'enregistrement et à la promotion. Une seule version par holdout. |
| Élevée | Promotion sur validation seule, sans holdout | Le holdout devient obligatoire (plancher) |
| Élevée | Même holdout rescoré sous un autre nom ou décalé dans le temps | Empreinte du contenu des prix du holdout |
| Moyenne | Sharpe dégonflé calculé dès 3 rendements | Au moins 30 rendements, sinon `LOW_INFORMATION` |
| Moyenne | PBO sur matrice creuse, ou avec des variantes inactives | Garde-fous par variante et globaux, sinon `LOW_INFORMATION` |
| Moyenne | Holdout réutilisable en ajoutant une barre aux données | Refus de tout chevauchement dans le temps, par instrument |
| Moyenne | Ce même refus bloquait un autre instrument sur la même période | Contrôle limité au même instrument |

**Sur le jeu synthétique fourni** : statut `LOW_INFORMATION`. Le PBO et le Sharpe dégonflé ne sont pas publiés, et la promotion est bloquée par `insufficient_trades`, `profit_factor_below_threshold` et `overfitting_check_missing`.

**Nature des tests** : uniquement locaux, sur données synthétiques et marches aléatoires. **Aucune donnée réelle.**

**Limites résiduelles, documentées :**
- Les dernières corrections (empreinte du holdout, plancher appliqué à la promotion) sont testées mais n'ont **pas** été re-revues de façon indépendante.
- `variants_tested` ne compte qu'un plan, pas tous les essais d'un agent dans la base.
- Un holdout légèrement altéré passerait l'empreinte.
- Quelqu'un qui réécrit le fichier SQLite peut tout falsifier.
- Le PBO est calculé sur du P&L réalisé à la sortie, et non réévalué à chaque période. Il classe les variantes par un ratio de type Sharpe, alors que la sélection réelle utilise le P&L net de validation moins le drawdown.
- La stratégie de retour à la moyenne est exclue (il faudrait des seuils de régime ajustés à chaque pli).

## D. Avancement du cahier des charges

| Exigence | État |
|---|---|
| Section 7.2, PBO/CSCV et nombre de variantes | Satisfaite sur données synthétiques |
| BT-02, BT-03, BT-04, BT-07 : séparation chronologique, walk-forward, jeu final, essais enregistrés | Étendues à toutes les familles |
| AI-05, IMP-03 : champion/candidat, critères prédéfinis | Renforcés : plancher et provenance |
| QNT-06 : familles de stratégies évaluées | Tendance, momentum, cassure ; retour à la moyenne exclu |
| Preuve de rentabilité | **Aucune.** Données réelles nécessaires. |

## E. Blocages et interventions humaines

| Blocage | Action |
|---|---|
| Données historiques réelles | Après votre `ctrader-check` : l'historique du compte démo, ou un fichier CSV que vous fournissez. Sans données réelles, aucune conclusion n'est possible. |
| Seuils de promotion | Valeurs de départ prudentes, pas des recommandations. À revoir avec l'architecte une fois des données réelles disponibles. |

## F. Recommandation technique

**Priorités.**
1. Revue indépendante des deux dernières corrections.
2. Comptage des essais sur l'ensemble des plans de la base.
3. Données réelles.
4. P&L réévalué à chaque période pour le PBO.

**Risques.** Le risque le plus important est de conclure sur des données synthétiques : le code refuse déjà de le faire.

## G. État Git et publication

- **Branche** `feat/validation-quant`, publiée sur `8e1c897` (ce rapport est le commit suivant). Elle contient le Lot 1 et le Lot 2.
- `main` est inchangée.
- **Secrets** : aucun ; analyse avant envoi.
- **Exécution** : aucun ordre envoyé, DÉMO non activée, RÉEL verrouillé.
