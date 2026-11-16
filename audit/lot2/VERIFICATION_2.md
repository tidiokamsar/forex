# LOT 2 - Vérification indépendante 2 (après corrections)

HEAD `dd2f4f5`. Local : `test_lot2_fixes test_lot2_registry` = 13 tests OK. Serveur (preuves/deploiement_2.txt) : release `6779d9f`, 1406 tests OK (skipped=20, expected failures=15), non rejoué par moi.

## Antériorité et modifications de tests
- `170ffa9` (tests G1-G3, 00:28) précède `688ebd4` (corrections, 00:37) : CONFORME.
- `94ba703` (D-013) : arguments CLI complétés, fiche de test enrichie (`abandon_rule`, `__version__`, empreinte réelle) ; aucune assertion modifiée. Légitime (données imposées par les nouvelles règles).
- `6779d9f` (D-014) : 19 `@unittest.skip`, tous sur `demo`, `backtest`, `walk-forward`, `evaluate-strategies`, `paper-replay`, `ml-baseline` et le circuit holdout/register qui en dépend (s45-s52). Ces commandes renvoient désormais 2 sans rien exécuter (cli.py:687) ; les tests ne peuvent plus atteindre le code testé. Quarantaine légitime, ne masque pas de défaut ; perte de couverture à reprendre au rebranchement. Le serveur compte 20 skips (1 antérieur, non vérifié).

## Défauts de la vérification 1
| Défaut | Verdict | Preuve |
|---|---|---|
| 1. Commandes CLI sans fiche, jeton constant | CONFORME (pour les commandes citées) | Refus en bloc cli.py:687-692 ; G2. `_TOKEN = secrets.token_hex(16)` par processus (evidence.py:44) ; une requête stockée par le site ou une autre CLI ne peut le porter (G1). `learning.learn` toujours protégé seulement par `learning_enabled = False` (jobs.py:180, 225, 304). |
| 2. Critère d'abandon numérique | CONFORME | `abandon_rule {metric, op, threshold}` obligatoire et validé (fini, opérateur connu) ; `final_test` l'applique et clôt `REJETEE`, valeur non mesurable = abandon (evidence.py:239-247) ; G3. |
| 3. Empreinte des données | CONFORME | `_check_run` recalcule `data_fingerprint` sur [début étude, fin scellée] avant chaque run et refuse si différente. |
| 4. Version du code, paramètres fixés (mineur) | CONFORME | `code_version != __version__` refusé ; paramètres hors fiche refusés. |

## Porte (XIV) et section IV
| Élément | Verdict | Preuve |
|---|---|---|
| Aucun backtest possible sans fiche | **NON CONFORME** | Contournement restant : `astra study spreads` (cli.py:632-639 -> `spreadstudy.hour_effect`) sélectionne des heures sur la 1re moitié de l'historique H1, les "trade" sur la 2de avec coûts, calcule net/an et Sharpe et rend un verdict `EFFET_REEL` : c'est un backtest hors échantillon, sans fiche et non compté dans `trials()`. De plus `run_backtest` (papertrade.py:1118) ne vérifie toujours pas le jeton : la garde repose sur chaque appelant. Aucune route `labweb.py`/`webapp.py` ni tâche worker autre que `backtest` (gardée, jobs.py:248) et `learn` (désactivée) n'appelle les moteurs. |
| Jeu final scellé | CONFORME | Fin d'étude imposée ; un seul accès final (E5, E6). |
| Rejouable à l'identique | CONFORME | `replay` compare SHA-256 ; E7 ; empreinte données vérifiée. |
| Fiche complète, budget 20/an et 5 variantes, 1 accès final | CONFORME | `REQUIRED_FIELDS` (15), E1-E3, E6. |
| Moteur refuse sans fiche | CONFORME partiel | Voir première ligne. |
| Compteur global avec essais antérieurs | CONFORME partiel | prior 19390 (compteur_essais_production.txt) ; essais de `study spreads` non comptés. |
| Boucle 120 variantes arrêtée | CONFORME (réserve) | Arrêt par attribut, réactivable sans fiche. |
| Négatifs conservés, réouverture par nouvelle fiche | CONFORME | Triggers, `_open` (E8). |

## Défauts restants
1. Bloquant : `study spreads` exécute une évaluation de stratégie hors échantillon sans fiche ni comptage.
2. Mineur : `run_backtest` sans garde interne ; boucle d'apprentissage réactivable par un attribut ; 19 tests en quarantaine à reprendre.

## Verdict : PORTE NON FRANCHIE
Bloquant : défaut restant 1 (refuser `study spreads` sans fiche ou le faire passer par le registre, compté). Les trois bloquants de la vérification 1 sont corrigés.
