# LOT 2 - Vérification indépendante (registre des preuves)

Vérificateur indépendant, branche `lot2-registre`, HEAD `2e9ff7f`. Exécution locale : `test_lot2_registry test_papertrade` = 20 tests OK.

## Antériorité et modifications de tests
- Tests avant code : CONFORME. `ced5040` (tests) précède `7c9b757` (evidence.py).
- `91b525a` (E4, D-011) : ajout de `worker.initialize()` seul, aucune assertion touchée. Légitime.
- `1fcaf89` (D-012) : `test_labweb` x2 passent de DONE à FAILED (backtest du site sans fiche), `test_learning` x2 activent `learning_enabled` pour tester le planificateur. Conséquence directe de la règle IV ; légitime. Les assertions retirées (`equity` présent) sont cohérentes avec le refus.

## Porte (XIV)
| Élément | Verdict | Preuve |
|---|---|---|
| Aucun backtest possible sans fiche | **NON CONFORME** | Garde posée seulement sur `lab backtest` CLI (cli.py:384), tâche worker `backtest` (jobs.py:248) et tâche `learn` (drapeau `learning_enabled`). Contournements restants : commande `backtest` CSV (`run_sma_backtest`, cli.py:826), `walk-forward`, `evaluate-strategies` (strategy_validation, cli.py:1066), `ml`, `demo` ; `learning.evaluate` appelle `run_backtest` sans garde ; `run_backtest` lui-même ne vérifie rien (D-011 l'assume). Le jeton `_evidence` est une constante publique : tout appelant peut le poser. |
| Jeu final scellé | CONFORME (dans le registre) | `registered_backtest` refuse `end` > fin d'étude ; `final_test` un seul accès compté (E5, E6). Non scellé hors registre (voir ci-dessus). |
| Résultats rejouables à l'identique | CONFORME | `replay` compare SHA-256 ; E7 ; `ev_runs` immuable par triggers. |

## Exigences section IV
| Exigence | Verdict | Preuve |
|---|---|---|
| Fiche complète (14 champs) | CONFORME (présence) | `REQUIRED_FIELDS`, E1 ; périodes ordonnées sans recouvrement (E2). |
| Critère d'abandon numérique | **NON CONFORME** | Seule la non-vacuité est testée ; aucun parsing, aucune application au résultat final, aucune clôture automatique. |
| Empreinte des données | **NON CONFORME** | `data_sha256` jamais comparé aux bougies (le test utilise `"0"*64`). |
| Version du code | NON CONFORME (faible) | Champ libre, non comparé à la version exécutée. |
| Paramètres fixés | NON CONFORME (faible) | Les `params` des variantes ne sont pas rapprochés de `parameters` de la fiche. |
| 20 hypothèses/an, 5 variantes, `planned_variants` <= 5 | CONFORME | E3. Note : le budget réel est 5, pas `planned_variants` de la fiche. |
| 1 accès final par hypothèse | CONFORME | E6. |
| Compteur global cumulatif, essais antérieurs inclus | CONFORME partiel | `trials()` + `record_prior` (E8) ; production `prior 19390` (preuves/compteur_essais_production.txt). Ne compte pas les essais des commandes non gardées. |
| Boucle 120 variantes arrêtée, historique conservé | CONFORME | `learning_enabled = False` (jobs.py:180, 225, 304) ; tables d'apprentissage non supprimées. Arrêt par simple attribut, réactivable sans fiche. |
| Résultats négatifs conservés | CONFORME | E8, triggers no update/no delete. |
| Hypothèse close rouverte seulement par nouvelle fiche | CONFORME | `_open` refuse (E8). |
| Preuves serveur | NON VÉRIFIABLE en détail | suite_serveur.txt : 1401 tests OK sur `/opt/astra/releases/1fcaf89` (pas HEAD `2e9ff7f`) ; sorties brutes non rejouées par moi. |

## Défauts
1. Bloquant : plusieurs points d'entrée de backtest (CLI `backtest`, `walk-forward`, `evaluate-strategies`, `ml`, `demo`, `learning.evaluate`) s'exécutent sans fiche ; la garde est un jeton constant contrefaisable.
2. Bloquant : critère d'abandon numérique non appliqué.
3. Bloquant : empreinte des données non vérifiée contre les données.
4. Mineur : version du code et paramètres fixés non contrôlés ; preuve serveur sur un commit antérieur à HEAD.

## Verdict : PORTE NON FRANCHIE
Bloquants : défauts 1, 2, 3.
