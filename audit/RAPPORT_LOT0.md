# RAPPORT LOT 0 - Audit en lecture seule (15 novembre 2026)

Périmètre : production 147.79.114.208 et dépôt au commit 591c5cf. Aucun code modifié, aucune écriture en base (connexions SQLite `mode=ro`), aucun redémarrage, aucun ordre. Preuves brutes dans `audit/preuves/` : `serveur_brut.txt`, `base_brut.txt`, `h1_runs_par_jour.txt`, `code_extraits.txt`.

## Affirmations

| # | Affirmation | Classement | Preuve | Raison |
|---|---|---|---|---|
| 1 | Version déployée 591c5cf | PROUVÉE | `ls -la /opt/astra/app` : lien vers `/opt/astra/releases/591c5cf` (serveur_brut l.3) | Le lien actif pointe sur cette release. Le SHA complet est 591c5cfb102b1e50e479b3bfb7c7f8a66c8bab1b. |
| 2 | Quatre services actifs | PROUVÉE | `systemctl list-units "astra*"` : agent, mt5-bridge, web, worker `active running` (serveur_brut l.9-25) | Quatre services longs tournent ; astra-nft est `active exited`, astra-demo est arrêté. Un processus actif ne prouve pas une sortie valide récente (section XIII). |
| 3 | Risque par transaction passé de 0,5 % à 0,1 % | RÉFUTÉE telle que formulée | `mt5_risk.py:46` `risk_per_trade_pct = 0.5`, `:50` `unconfirmed_risk_scale = 0.2`, `mt5_bridge.py:320-322` | La base reste 0,5 %. Seul un champion adopté non confirmé trade à 0,5 x 0,2 = 0,1 %. |
| 4 | 120 variantes par jour et par instrument | PROUVÉE depuis le 12-11 | `learning.py:92` `H1_CANDIDATES_PER_RUN = 120` ; une exécution par instrument et par jour (h1_runs_par_jour) | 19 exécutions sur 19 instruments chaque jour du 12 au 15-11, 14 635 candidats tracés en H1. Le 11-11 en compte 50. |
| 5 | 1 354 tests passent | RÉFUTÉE | `/root/linuxtest-q/full.log` : `Ran 1360 tests`, `FAILED (failures=1, expected failures=15)` | Le chiffre est faux, et la suite n'est pas verte. Ce journal porte sur 24abedd, non déployé, pas sur 591c5cf. |
| 6 | Momentum et tendance négatifs | NON VÉRIFIABLE | Aucune sortie enregistrée de l'étude | Les scripts ont été exécutés sans conserver leur sortie. Il faudrait les rejouer et archiver la sortie avec l'empreinte des données. |
| 7 | Anomalie 20-23 h UTC, Sharpe 2 à 5 | NON VÉRIFIABLE | `scratchpad/study2.py` existe ; sa sortie n'est pas archivée | Même cause qu'au 6. La méthode a en plus des faiblesses, décrites en F. |

## Questions

**A. Serveur du compte.** `mt5_instruments.server = 'MetaQuotes-Demo'` (base_brut) et `ASTRA_MT5_SERVER=MetaQuotes-Demo` dans l'unité systemd. C'est le serveur de démonstration de MetaQuotes, pas un courtier. Ses spreads ne représentent aucun compte réel : condition bloquante pour les mesures de coûts (section III).

**B. Netting ou hedging.** NON VÉRIFIABLE. Le mode de marge n'est ni relevé ni stocké, et aucun contrôle du mode n'existe dans le pont (grep `margin_mode|hedg|netting` sans résultat). Il faut lire `account_info().margin_mode` dans le terminal.

**C. Instruments négociables.** 12 170 symboles au catalogue (126 Forex, 14 Metals, 26 Indexes, 12 004 Nasdaq), tous en `trade_mode` 4 (négociation complète). Spreads relevés à l'instant du catalogue, souvent à 0 car le marché est fermé (exemples : EURUSD 2, GBPJPY 14 points). Swaps : NON VÉRIFIABLE, la colonne n'existe pas en production (elle arrive avec 24abedd, non déployé). La table des cotations ne couvre que 7 paires.

**D. Données des backtests.** Bougies OHLC (source `MASSIVE_FX_QUOTES`, un prix par bougie), pas de ticks Bid/Ask. Le coût est une hypothèse fixe par instrument (`learning.py:364-365`, `spread_pips` et `slippage_pips` de la spécification), doublée dans un test de résistance. Ce coût n'est pas mesuré chez le courtier. Les swaps ne sont pas inclus.

**E. Variantes testées.** Tracées : 14 635 candidats H1 (`h1_learn_runs`, 140 exécutions du 10 au 15-11), 4 473 candidats D1 (`learn_runs`, 161 exécutions), 166 243 lignes `lab_backtest_items` (270 lots). La table `experiments` est vide. Les études de ce mois (saisonnalité, momentum, effet horaire) n'ont laissé aucune trace en base. Le total exact depuis le début est donc NON VÉRIFIABLE.

**F. Affirmation 7.** D'après `study2.py`, les heures sont choisies sur la première moitié de l'historique H1 et testées sur la seconde, sans recouvrement. Mais deux seuils (t = 2 et 3) ont été essayés et regardés sur la seconde moitié : le test n'est plus scellé. Le coût est le spread hypothétique, pas le spread mesuré à l'heure du rollover.

**G. Ordres réels.** Côté courtier, 27 deals pour 16 positions (`mt5_deals`). Côté journal du pont : 9 événements `open`, 6 `close`, 6 `refused`, 8 `request`. Les deux comptes ne se rapprochent pas un à un. L'écart n'est pas expliqué dans ce lot : les deals peuvent inclure des clôtures par stop et des ordres manuels.

**H. Redémarrage pendant une confirmation.** L'intention est écrite sur disque à l'état SOUMIS avant l'envoi (`mt5_bridge.py:264-272`). Une réponse perdue la passe en INCONNU (`:352`). Au cycle suivant, `resolve_intents` (`:282-300`) la cherche parmi les positions ouvertes ; sans position après 120 s, elle devient ABSENT. Ce rapprochement ne lit que les positions ouvertes, pas les deals. Un ordre exécuté puis fermé par son stop pendant la panne serait donc classé « non exécuté » (problème signalé, non corrigé).

**I. Limites de risque.** `RiskLimits` (`mt5_risk.py:40-62`) avec des valeurs par défaut dans le code. `load_limits` lit `ASTRA_MT5_RISK_FILE`, mais cette variable n'est pas définie dans l'unité du pont : la production applique les valeurs du code. Tout commit peut donc changer les limites. C'est contraire au principe 5 (fichier hors dépôt, en lecture seule pour les agents).

**J. Martingale, grille, moyenne à la baisse.** Aucune trouvée. `git grep -i "martingale|grid|averag"` ne renvoie que des grilles de paramètres de recherche et des prix moyens d'exécution (code_extraits, fin). C'est une recherche par mots-clés et non une preuve exhaustive. La taille est réduite après une perte, jamais augmentée (limites jour, semaine, mois, `mt5_risk.py:230`).

**K. Écart avec les sept composants.**

| Composant | État | Constat |
|---|---|---|
| 1 Market Data & Truth | Partiel | Bougies et dernière cotation ; pas de ticks, de swaps ni d'empreinte par jeu de données en production |
| 2 Evidence Registry | Partiel | Tables holdout, signatures et essais existent ; `experiments` est vide ; les études hors base ne sont pas tracées |
| 3 Strategy Library | Partiel | Familles paramétriques optimisées ; ni tendance multi-horizons fixe ni portage |
| 4 Regime | Partiel | `regimes.py` existe ; son usage n'a pas été audité ici |
| 5 Portfolio Allocator | Absent | Pas de registres A, B et C ; le pont exécute par instrument |
| 6 Risk & Execution | Partiel | Passerelle, intentions et refus présents ; limites dans le code ; pas de contrôle netting/hedging ; arrêt par fichier STOP, pas de processus séparé |
| 7 Learning | Partiel | Boucle de 120 variantes par jour, contraire à la section IV |

## Contradictions signalées

- La section XVII interdit toute écriture hors `audit/`. Ce rapport est donc écrit dans la copie de travail sans commit.
- Le commit local 24abedd (enregistreur de cotations, swaps) n'est ni poussé ni déployé. Il recouvre le LOT 1 et attend votre décision.
- La production continue de trader en DEMO à la réouverture : plusieurs champions adoptés n'ont pas passé le protocole de la section XI.
