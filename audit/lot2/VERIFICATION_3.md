# LOT 2 - Vérification 3 (indépendante, dernière)

## Ordre des commits
- `7793731` (01:00:31, test G6) précède `16ad0e3` (01:01:28, correction) : CONFORME.
- `16ad0e3` : 4 lignes ajoutées dans `cli.py` (630-636), aucun test modifié. `astra study spreads` écrit un refus et renvoie 2 avant tout import de `spreadstudy` ; le code de l'étude (cli.py:637-643) devient inatteignable.

## Bloquant de la vérification 2
| Défaut | Verdict | Preuve |
|---|---|---|
| `study spreads` : test hors échantillon sans fiche | CONFORME | Refus cli.py:633-636 ; G6 (test_lot2_fixes.py:60-61, `cli.main(["study","spreads"]) == 2`) ; serveur : JSON `refused` dans preuves/deploiement_3.txt. `spreadstudy.study` n'a pas d'autre appelant dans `src`. |

## Nouveau balayage des points d'entrée de production
`git grep` sur `run_backtest(`, `run_sma_backtest(`, `walk_forward`, `hour_effect`, `evaluate(`, `learning.learn(` :
- CLI : `backtest` (cli.py:389) précédé de `evidence.require_registration` (cli.py:384) ; `demo`, `backtest`, `walk-forward`, `evaluate-strategies`, `paper-replay`, `ml-baseline` refusés en bloc (cli.py:691-696) avant les appels cli.py:783, 837, 1025, 1116 ; `study spreads` refusé (cli.py:633).
- Registre : `evidence.py:212, 254` (runs et rejeu, sous fiche).
- Worker : `jobs.py:249` (tâche `backtest`, gardée) ; `jobs.py:229` (`learning.learn`, désactivé par `learning_enabled = False`).
- `labweb.py`, `webapp.py`, `agent.py` : aucun appel aux moteurs.
- Autres occurrences (`engine.py:122`, `gateway.py:436`, `research.py:256`, `signal_backtest.py:193`, `validation.py`, `strategy_validation.py`, `ml.py`, `learning.py`) : bibliothèques ou contrôle de risque, atteignables seulement par les chemins ci-dessus.
Aucun point d'entrée restant n'exécute de backtest ou d'évaluation sans run au registre.

## Exécution
- Local : `test_lot2_fixes test_lot2_registry` : 14 tests, OK.
- Serveur (deploiement_3.txt) : 1407 tests OK (20 skips, 15 échecs attendus), release `16ad0e3`, empreinte de l'archive vérifiée, services actifs.

## Porte (XIV) et section IV
Aucun backtest sans fiche : CONFORME. Jeu final scellé, rejouable à l'identique, fiche complète (critère numérique, empreinte), budget 20/an et 5 variantes, 1 accès final, compteur global (prior 19390), négatifs conservés, réouverture par nouvelle fiche : CONFORMES (inchangés depuis la vérification 2). Boucle 120 variantes : arrêtée (réserve ci-dessous).

## Réserves non bloquantes
1. `run_backtest` (papertrade.py:1118) sans garde interne : la protection repose sur les appelants.
2. Boucle d'apprentissage réactivable par l'attribut `learning_enabled` (jobs.py:180, 225, 304).
3. 19 tests en quarantaine (D-014) à reprendre au rebranchement ; code mort cli.py:637-643 à retirer ou brancher au registre (LOT 3).

## Verdict : PORTE FRANCHIE
