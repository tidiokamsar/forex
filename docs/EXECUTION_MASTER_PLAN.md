# Plan d'exécution central ASTRA

Index de la mission « Prompt maître d'orchestration ». Mis à jour à chaque tranche ; la preuve de chaque état est dans `ACCEPTANCE_MATRIX.md`.
États : `TODO`, `IN_PROGRESS`, `BLOCKED`, `REVIEW`, `DONE`, `NOT_APPLICABLE`.

## État initial constaté le 2026-10-29

| Élément | Constat | Preuve |
|---|---|---|
| Branche de travail | `feat/auth-deploy` (jamais fusionnée dans `main`) | `git branch` |
| Version en production | `504c370` | lien symbolique de release du serveur |
| Version locale | `67a97c5` (portail de risque, Massive) + correctifs de comptabilité non commités | `git log`, `git status` |
| Services actifs | web, worker, agent, pont MT5, nftables, Caddy ; `astra-demo` (Binance) inactif faute de clé | `systemctl` |
| Autres applications | app, auth, chantier360 répondent (HTTP 200 ou 302) | contrôle après chaque déploiement |
| Mode | SIMULATION, LIVE verrouillé (`live_locked: true`) | `/api/status` |
| Arrêt d'urgence | inactif ; fichier STOP posé sur le pont MT5 : aucune ouverture ni fermeture automatique | `/api/controls`, système de fichiers |
| Sauvegardes | minuteur quotidien actif, dernière exécution 2026-10-29 02:41 UTC, bases `astra` et `auth` | `systemctl list-timers` |
| Clé Massive | **aucune clé déposée** sur le serveur ; la clé évoquée comme exposée n'a pas pu être vérifiée (aucune clé n'a été utilisée par ASTRA) | absence de `/etc/astra/massive.key` |
| Données | BCE D1 21 189 bougies ; MT5 H1 140 077, M5/M15/H4/D1 chargées | base de production |
| Stratégies | 41 backtests terminés, 1 échec ; 9 simulations en cours ; 14 évaluations d'apprentissage | base de production |

## Gouvernance

Les sous-agents de l'outil n'ont pas été lancés : l'exécution s'est faite séquentiellement, sous une seule responsabilité d'intégration. La répartition en agents du prompt est conservée comme découpage des tâches (`AGENT_TASKS.md`). Aucune affirmation n'est reprise d'un agent sans test.

## Tranches

| Tranche | Contenu | État | Remarque |
|---|---|---|---|
| 0 | Inventaire, sécurité, gel des nouvelles ouvertures | DONE | STOP posé ; aucune position modifiée ; clé Massive : BLOCKED (voir limites) |
| 1 | Réconciliation du tableau de bord MT5 | REVIEW | audit réel fait ; correction codée et testée ; à déployer après la suite complète |
| 2 | Contrats et tests de référence | IN_PROGRESS | contrats de fait dans `mt5view`, `mt5accounting`, `mt5_risk` ; à formaliser |
| 3 | Sécurité, secrets, DEMO verrouillé | IN_PROGRESS | LIVE verrouillé prouvé ; comptes autorisés par numéro ; revue de sécurité complète à faire |
| 4 | Massive | BLOCKED | client écrit et testé sur faux fournisseur ; attend une nouvelle clé déposée par l'utilisateur |
| 5 | MT5 DEMO, premier cycle | DONE | ordre de test confirmé le 2026-10-28 ; pont sur le serveur sous Wine depuis le 2026-10-29 |
| 6 | Risk Gateway et exécution | REVIEW | portail codé et testé, non déployé ; états d'ordre et rapprochement après timeout à compléter |
| 7 | Recherche quantitative | IN_PROGRESS | trois familles, walk-forward annuel, contrôle final ; indicateurs sans fuite ; mesures de risque à compléter |
| 8 | Shadow et trading DEMO automatique | BLOCKED | reprise des entrées interdite tant que 1 et 6 ne sont pas déployées et vérifiées |
| 9 | Champion/challenger | IN_PROGRESS | en service sur 7 paires, versions et retour arrière ; écarts prévu/exécuté en cours |
| 10 | Tableau de bord fidèle | REVIEW | maquette reproduite ; comptabilité côté serveur ; à déployer |
| 11 | Deuxième courtier et crypto | BLOCKED | dépend de comptes démo à créer par l'utilisateur |
| 12 | Exploitation 24/7 | IN_PROGRESS | services supervisés ; restauration à tester |
| 13 | QA indépendante | IN_PROGRESS | 1 190 tests Windows, 1 test Linux connu en échec (exécuté en root) |
| 14 | Livraison | TODO | rapport final |

## Prochaines étapes dans l'ordre des dépendances

1. Suite complète sur la comptabilité, commit, déploiement, redémarrage du pont (STOP conservé).
2. Rapprocher la production avec le compte réel (tranche 1 → DONE).
3. Tests de non-régression du portail de risque en production, puis levée contrôlée du STOP (tranche 8).
4. Dépôt de la clé Massive par l'utilisateur, puis import réel (tranche 4).
5. Contrats formels, états d'ordre complets, restauration testée.
