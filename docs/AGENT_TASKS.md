# Tâches par rôle

Identifiants stables. Les rôles sont des responsabilités ; l'exécution actuelle est séquentielle (voir `EXECUTION_MASTER_PLAN.md`). Une tâche n'est `DONE` qu'avec la preuve indiquée dans `ACCEPTANCE_MATRIX.md`.

| Id | Rôle | Tâche | État |
|---|---|---|---|
| A1-01 | Audit | Inventaire dépôt, version déployée, services, endpoints | DONE |
| A1-02 | Audit | Rechercher valeurs codées en dur et données simulées en production | IN_PROGRESS |
| A2-01 | MT5 comptabilité | Lire le compte réel en lecture seule | DONE |
| A2-02 | MT5 comptabilité | Rapprocher positions, solde, fonds propres, historique | DONE |
| A2-03 | MT5 comptabilité | Historique issu du courtier, origine prouvée des positions | REVIEW |
| A2-04 | MT5 comptabilité | Fraîcheur séparée des neuf étapes | REVIEW |
| A3-01 | Massive | Client REST, clé en secret, qualité des bougies | REVIEW |
| A3-02 | Massive | Clé valide déposée, import réel, disponibilité par jeu de données | BLOCKED (clé) |
| A3-03 | Massive | WebSocket si le forfait l'autorise | BLOCKED (clé) |
| A4-01 | Connecteur | MT5 DEMO : compte, cotations, bougies, positions, ordres | DONE |
| A4-02 | Connecteur | Pont sur le serveur sous Wine, reconnexion | DONE |
| A4-03 | Connecteur | Deuxième courtier Forex, premier connecteur crypto | BLOCKED (comptes) |
| A5-01 | Recherche | Trois familles, walk-forward, contrôle final | DONE |
| A5-02 | Recherche | Mesures de risque (Sharpe, Sortino, Calmar), stabilité par régime | TODO |
| A5-03 | Recherche | Stratégie par cassure (breakout) | TODO |
| A6-01 | Risque | Portail indépendant avant chaque ouverture | REVIEW |
| A6-02 | Risque | États d'ordre complets, rapprochement après timeout, fills partiels | TODO |
| A6-03 | Risque | Arrêt d'urgence testé en production | TODO |
| A7-01 | Interface | Maquette, indicateurs, comptabilité côté serveur | REVIEW |
| A7-02 | Interface | Contrôle responsive mobile | TODO |
| A8-01 | Exploitation | Services supervisés, sauvegarde quotidienne | DONE |
| A8-02 | Exploitation | Restauration testée sur une copie, alertes du pont MT5 | TODO |
| A9-01 | QA | Suites Windows et Linux sur chaque version | DONE (à chaque livraison) |
| A9-02 | QA | Cas négatifs d'exécution (timeout, rejet, doublon) | TODO |
