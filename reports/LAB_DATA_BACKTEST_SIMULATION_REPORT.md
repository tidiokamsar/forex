# ASTRA, rapport du lot « application fonctionnelle » : données, backtests, simulation, exploitation

**Destinataire : l'architecte principal (ChatGPT).** Copie : l'utilisateur. Date : 27/10/2026, 23:50 UTC.

**Statut : déployé sur https://astra.infranetgn.com, mode SIMULATION, exécution réelle désactivée.** Aucune connexion à un courtier, aucun ordre réel. Les statuts suivent la convention RÉUSSI (preuve vérifiable), ÉCHEC, BLOQUÉ, NON TESTÉ.

| Repère | Valeur |
|---|---|
| Version déployée | `a1b9ade2b1c7b826fc18af12b945aa436bed4d16` (branche `feat/auth-deploy`, identique sur GitHub) |
| Version précédente (retour arrière) | `/opt/astra/releases/24c894a…` ; Caddyfile sauvegardé (`Caddyfile.avant-astra-import-*`) |
| `main` | inchangée, aucune fusion |

## 1. Analyse de départ (pourquoi le tableau de bord était vide)

| Élément | État constaté | Cause |
|---|---|---|
| Données de marché | absent | aucun connecteur Forex ; seul un CSV synthétique EUR/USD existait ; aucun stockage des bougies |
| Observations, résultats, backtests | vide | le tableau de bord lisait `backtest_runs`, rempli seulement par la commande `demo` en ligne de commande |
| Paires | incomplet | seul EUR/USD était défini ; le contrôle de risque refusait toute autre paire |
| Courbe du capital, transactions | non conservées | calculées en mémoire, jamais stockées |
| Simulation continue | non branchée | flux aléatoire, compte simulé en mémoire perdu à chaque redémarrage |
| Interface | lecture seule | aucune route pour importer, lancer un backtest ou une simulation |

Réutilisé : chargement et contrôle des CSV (principes), indicateurs, modèle de coûts (demi-spread et slippage par côté), règles d'exécution du backtest existant (décision à la clôture, exécution à l'ouverture suivante, stop sur écart), authentification, MFA, rôles, CSRF, journalisation, sauvegardes.

## 2. Fonctionnalités nouvellement implémentées

| Lot | Fonction | Statut | Preuve |
|---|---|---|---|
| 1 | Stockage persistant des bougies (paire, unité, heure UTC, OHLC, volume si fourni, source, nature, type) | RÉUSSI | tests ; production : 21 182 bougies |
| 1 | 7 paires : EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CHF, USD/CAD, NZD/USD ; unités M5, M15, H1, H4, D1 | RÉUSSI | tests ; données BCE des 7 paires en production |
| 1 | Connecteur BCE (taux de référence officiels, sans identifiant), reprise après le dernier jour stocké, 3 essais, pause progressive après échec, une requête toutes les 6 h au plus | RÉUSSI | acquisition réelle en production (tâche n° 1, 21 182 cours) ; tests de panne du fournisseur |
| 1 | Import CSV OHLC (ISO, MT4/MT5, Dukascopy, fuseaux horaires et heure d'été), refus motivé des lignes invalides, doublons idempotents, conflits signalés, volume absent conservé absent | RÉUSSI | tests ; essai dans le navigateur (local) |
| 1 | Cotations en temps réel | BLOQUÉ | aucun fournisseur avec identifiants configuré ; affiché « Temps réel : non connecté » |
| 2 | Chaîne source → acquisition → normalisation → contrôle qualité → stockage → indicateurs → stratégie → backtest → résultats → tableau de bord, avec statuts | RÉUSSI | essai complet dans le navigateur (local) et en production (ligne de commande) |
| 2 | Lacunes hors fermeture hebdomadaire (vendredi 17:00 à dimanche 17:00 heure de New York) ; jours fériés signalés | RÉUSSI | tests ; 39 lacunes BCE 2015-2026 (jours fériés TARGET) |
| 3 | Backtest paramétrable (série, période, capital, risque, stop, spread, slippage, commission, paramètres de stratégie), enregistré et reproductible (paramètres, versions, empreinte SHA-256 des données) | RÉUSSI | test de déterminisme ; même résultat local et production |
| 3 | Métriques : capital initial et final, brut, coûts, net, transactions, taux de réussite, profit factor, drawdown maximal, gain et perte moyens, espérance, courbe du capital | RÉUSSI | tests ; affichage vérifié dans le navigateur |
| 4 | Stratégie de référence `sma_atr_trend` 1.0.0, expérimentale : décisions ACHAT, VENTE, AUCUNE POSITION, avec indicateurs, conditions et justification | RÉUSSI | tests ; signaux affichés |
| 5 | Moteur de trading simulé unique pour backtest et simulation : risque par transaction, taille maximale, positions simultanées, stop obligatoire, perte journalière, drawdown maximal, marge ; tout refus est enregistré avec son motif | RÉUSSI | tests ; arrêt réel sur drawdown observé (essai local) |
| 5 | Simulation persistante (état, ordres, transactions, signaux, événements), reprise sans doublon après redémarrage, plusieurs paires avancées ensemble | RÉUSSI | tests de redémarrage ; la simulation reproduit les transactions du backtest |
| 6 | Tableau de bord : onglets Vue d'ensemble, Données, Backtests, Simulation, Journaux ; boutons, états d'attente, messages d'erreur, états vides avec l'action à faire | RÉUSSI (local) / NON TESTÉ en production avec une session | l'essai navigateur a été fait sur un serveur local avec les mêmes données ; je n'ai pas de session sur la production |
| 7 | Worker systemd (`astra-worker`, redémarrage automatique, alerte en cas d'échec), file de tâches persistante, tâche interrompue marquée en échec et jamais rejouée en silence | RÉUSSI | production : actif, redémarré sans perte |
| 7 | Sauvegardes quotidiennes vérifiées des deux bases (`astra-backup.timer`, 02:30 UTC) | RÉUSSI | sauvegarde lancée et vérifiée (`integrity ok`, 21 182 bougies) |
| 7 | Exercice de restauration sur la production | NON TESTÉ | procédure documentée (OPERATIONS.md) |
| 7 | Alerte par courriel | BLOQUÉ | aucun destinataire configuré (`ASTRA_ALERT_TO`) ; journal seulement |

## 3. Données disponibles en production

- **Provenance :** Banque centrale européenne, taux de référence officiels publiés vers 16:00 HEC pour 14:15 HEC. EUR/USD est un taux direct ; les 6 autres paires sont dérivées de deux taux officiels.
- **Nature :** données réelles, quotidiennes, une clôture par jour ouvré, sans plus haut ni plus bas. **Ce n'est pas du temps réel.** Les backtests le mentionnent dans leurs hypothèses.
- **Volume :** 3 026 bougies par paire, soit 21 182 au total, du 02/01/2015 au 27/10/2026 13:15 UTC.
- **Fraîcheur :** « à jour », taux du jour même. La prochaine acquisition a lieu automatiquement dans les 6 heures.
- **Anomalies :** 39 lacunes par paire sur la période, toutes des jours fériés TARGET ; aucune ligne refusée.
- **Intrajournalier (M5 à H4) :** disponible dès qu'un fichier CSV d'un fournisseur autorisé est importé. Aucun n'a été importé en production.

## 4. Backtests exécutés (stratégie expérimentale, coûts simulés inclus, sans validation hors échantillon)

Période : 01/01/2018 au 27/10/2026, D1 BCE, capital 10 000 USD, risque 0,5 % par transaction, spread 1 pip, slippage 0,2 pip par côté.

| Paire | Transactions | Brut | Coûts | Net | Drawdown max | Profit factor |
|---|---|---|---|---|---|---|
| EUR/USD (production, backtest n° 1) | 118 | −150,50 | 118,02 | −268,52 USD | 11,72 % | 0,94 |
| GBP/USD (local) | 89 | +1 528,84 | 69,86 | +1 458,97 USD | 9,43 % | 1,44 |
| USD/JPY (local) | 88 | +204,45 | 70,02 | +134,43 USD | 14,35 % | 1,03 |

**Ces chiffres ne démontrent aucune rentabilité.** Ils portent sur tout l'échantillon, sans période hors échantillon, avec une stratégie de référence non validée. Ils servent à prouver que la chaîne calcule réellement.

## 5. Tests automatisés

| Exécution | Version | Résultat |
|---|---|---|
| Suite complète Linux sur le serveur de production (utilisateur `astra`, Python 3.14.4) | `a1b9ade` | **1 076 tests OK, aucun ignoré, 15 échecs attendus (Lot 4), code 0** |
| Nouveaux tests | `a1b9ade` | 45 tests : `test_marketdata`, `test_papertrade`, `test_labweb`, `test_lab_review_fixes` |
| Contrôles Windows (Python 3.14.6, arbre propre) | `a1b9ade` | Ruff 0, compileall 0, Node 0, détection de secrets 0 (aucun secret), tests ciblés 0, suite complète 0 : **1 076 tests OK, 5 ignorés (POSIX), 15 échecs attendus (Lot 4)** |
| GitHub Actions | — | non exécuté (verrou de facturation) ; aucun contrôle CI n'est présenté comme réussi |

Couverture des tests ajoutés :
- import valide et invalide, doublons et conflits ;
- lacunes, fuseaux horaires et heure d'été ;
- BCE : dérivation des paires, heure du fixing, idempotence, panne du fournisseur et pause, réponse malformée ;
- décisions et indicateurs ;
- exécution à l'ouverture suivante, coûts nets, conversion USD/JPY ;
- stops, y compris sur écart d'ouverture ;
- limites de taille, de positions, de perte journalière et de drawdown ;
- déterminisme, persistance après redémarrage ;
- worker et reprise après redémarrage ;
- routes : accès anonyme, rôle READ_ONLY, CSRF, import, backtest, simulation.

## 6. Revues indépendantes

| Revue | Résultat |
|---|---|
| Revue du lot | Aucun critique ni élevé ; 5 moyens (backtest bloqué « en cours », unité de temps non vérifiée, limite journalière contournable par l'ordre des bougies, fausses lacunes D1, étiquetage synthétique non imposé) et 14 faibles. Les 5 moyens et 10 faibles sont corrigés, chacun avec un test (`10411e7`). Restent l'analyse du CSV dans la requête web (acceptée, 12 Mo maximum), le rattrapage à la relance (voulu, seul le texte de l'arrêt sur drawdown a été corrigé), le volume enregistré tel que fourni (pas d'étiquette « tel que fourni ») et les exemples nginx et Apache (une note ajoutée). |
| Revue des corrections | Aucun critique ni élevé ; 1 moyen (H4 suivant l'heure d'été) et 5 faibles, tous corrigés (`a1b9ade`) |

## 7. Sécurité

- **Ordres réels impossibles depuis l'interface :** aucune route web ne touche la passerelle d'exécution ni un courtier. Un test vérifie que `labweb`, `papertrade`, `jobs` et `marketdata` n'importent ni `execution`, ni `brokers`, ni `governance`, ni `venues`.
- **Modifications réservées au rôle OPERATOR**, avec jeton CSRF. Elles ne font qu'enregistrer une tâche ou changer le statut d'une simulation.
- **Import :** fichier enregistré sous un nom généré par le serveur ; limite de 13 Mo sur cette seule route (Caddy et l'application), 64 Ko ailleurs.
- **Worker :** service isolé (systemd), limité à 1 Go de mémoire, sans privilège ; seul accès sortant prévu, la BCE.
- **Aucun secret** dans le dépôt, les journaux ou ce rapport.

## 8. Ce qui reste bloqué ou à faire

| Élément | Statut | Action |
|---|---|---|
| Cotations en temps réel et données intrajournalières automatiques | BLOQUÉ | fournisseur avec identifiants (par exemple un compte de démonstration de courtier) : objet de la mission suivante |
| Vérification de l'interface en production avec une session | NON TESTÉ par moi | l'utilisateur peut le faire ; l'interface a été vérifiée localement avec les mêmes données |
| Alertes par courriel | BLOQUÉ | définir `ASTRA_ALERT_TO` |
| Exercice de restauration en production | NON TESTÉ | à planifier |
| Validation hors échantillon et amélioration des stratégies | NON TESTÉ | le module de validation walk-forward existant n'est pas encore relié aux nouvelles données |
