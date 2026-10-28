# Plan : agent ASTRA 24 h/24 avec accès machine (proposition, non implémentée)

Statut : à valider par le titulaire du compte. Rien n'est déployé ; tout reste en SIMULATION.

## 1. Pourquoi l'agent ne se connectera pas avec vos identifiants

L'authentification actuelle (`src/astra/webapp.py`, `src/astra/auth/store.py`) est faite pour un humain :
formulaire, TOTP, cookie `__Host-astra_session` (15 min d'inactivité, 8 h maximum), jeton CSRF et `Origin`.
Donner votre mot de passe et votre graine TOTP à un programme qui tourne en permanence :

- annule le MFA : les deux facteurs seraient stockés au même endroit, sur la même machine ;
- donne à quiconque compromet l'agent votre compte complet (ADMIN : gestion des comptes, réinitialisations) ;
- rend l'audit faux : on ne distingue plus vos actions de celles de l'agent ;
- empêche de couper l'agent sans changer votre propre mot de passe et votre MFA.

« Être vous » se fait donc par **délégation** : l'agent agit pour votre compte, avec un jeton qui lui est propre,
et chaque action est inscrite au journal sous `agent:<nom>` avec votre nom comme propriétaire.

## 2. Accès machine : jetons d'agent

Nouveau dans `astra.auth` (table `agent_tokens` dans `auth.sqlite3`) :

| Propriété | Règle |
|---|---|
| Format | `astra_agt_` + 256 bits aléatoires ; seule l'empreinte SHA-256 est stockée |
| Propriétaire | un compte humain existant ; compte désactivé = jeton refusé |
| Durée | obligatoire, 30 jours par défaut, 90 jours maximum, non prolongeable (on en crée un nouveau) |
| Révocation | immédiate (`astra auth agent-revoke`) ; désactivation du propriétaire ou changement de mot de passe : révoqué |
| Droits | portées explicites, liste blanche de routes (voir 3) ; aucune portée ADMIN, DEMO ou LIVE n'existe |
| Adresses | liste facultative d'adresses ou réseaux autorisés |
| Audit | création, révocation, refus (route hors portée, jeton expiré ou révoqué) et chaque requête qui modifie l'état, dans `auth_events` (append-only) ; dernière utilisation et adresse enregistrées |
| Création | uniquement en local sur le serveur (`astra auth agent-create`), comme `init-admin` ; le jeton est affiché une seule fois |

Transport : en-tête `Authorization: Bearer <jeton>`, via `https://astra.infranetgn.com` (le proxy ajoute
toujours son secret `X-Astra-Proxy`). Une requête qui porte à la fois un jeton et un cookie de session est refusée.
Le contrôle `Origin` reste exigé pour les modifications ; le jeton CSRF ne s'applique pas au Bearer (il protège
les cookies envoyés automatiquement par un navigateur, ce que le Bearer n'est pas).

## 3. Routes utilisées par l'agent

Portée `read` (lecture) :
`/api/status`, `/api/summary`, `/api/runs`, `/api/models`, `/api/experiments`, `/api/controls` (kill switch et gels),
`/api/market/overview`, `/api/market/quality`, `/api/market/candles`, `/api/lab/catalog`, `/api/lab/backtests[/<id>]`,
`/api/sim/sessions[/<id>]`, `/api/learn/overview`, `/api/system/jobs`, et une nouvelle `/api/agent/whoami`.

Portée `lab` (demandes en SIMULATION, exécutées par le worker existant) :
`POST /api/market/ingest`, `POST /api/lab/backtests`, `POST /api/sim/sessions`, `POST /api/sim/sessions/<id>/start|stop`.

Jamais accessibles à un jeton, quelle que soit la portée : `/api/demo/*`, `/api/admin/*`, `/admin/*`, `/api/audit`,
`/api/orders`, `/api/reconciliations`, `/api/market/import` (dépôt de fichiers), les pages HTML et `/api/session`.
Aucune nouvelle route ne passe d'ordre, ne lève le kill switch, ne change le mode ou les limites de risque.

## 4. Ce que fera l'agent (`astra agent run`, bibliothèque standard uniquement)

Cycle toutes les 5 minutes, 24 h/24 :

1. **Garde-fous** : `/api/status` doit dire `mode=SIMULATION` et `live_locked=true`, sinon arrêt immédiat.
   Si le kill switch est actif ou des gels existent : observation seule, aucune demande.
2. **Données** : si la source ECB est périmée, demande une ingestion (au plus une toutes les 6 h).
3. **Recherche** : planifie des backtests sur les paires et unités de temps couvertes, à partir du catalogue,
   avec un budget (par défaut 20 par jour, 3 en attente au plus), puis lit les résultats.
4. **Simulation** : garde au plus 3 sessions papier sur les meilleures configurations (critères minimum :
   nombre de trades, facteur de profit, drawdown) et arrête celles qui atteignent leur arrêt de drawdown
   ou une perte seuil. Ce classement est indicatif : il ne remplace pas le protocole de recherche
   (holdout scellé, PBO/DSR) et l'agent ne promeut jamais un modèle.
5. **Compte rendu** : journal JSONL de chaque décision et résumé quotidien en Markdown (ce qu'il a vu, demandé,
   arrêté, et pourquoi), pour que vous puissiez relire et reprendre la main.

L'agent n'a pas d'autre chemin vers ASTRA que ces routes : son client refuse localement toute route hors
liste blanche, et le serveur refuse de toute façon.

Option, phase 2 : un serveur MCP local qui expose les mêmes outils (lecture, demande de backtest, simulation)
avec le même jeton, pour converser avec ASTRA depuis Claude. Non inclus tant que vous ne l'avez pas demandé.

## 5. Revue de sécurité (points à vérifier)

- Le jeton est un secret de longue durée : fichier `/etc/astra/agent.token` root:astra 0640, jamais dans Git,
  ni dans les journaux (masqué par `logs.py`), ni dans les sauvegardes du dépôt.
- Compromission de l'agent : l'attaquant peut lire les données de recherche et lancer des backtests ou
  simulations, rien d'autre. Réponse : `astra auth agent-revoke`, effet immédiat.
- Pas de nouvel accès anonyme : un Bearer invalide répond 401 sans écrire dans l'audit (comme les refus anonymes).
- Tentatives de jeton : limitées par adresse (même mécanisme de délai progressif que la connexion).
- Le choix « portées en liste blanche de routes » est testé : un test échoue si une nouvelle route POST devient
  accessible à un jeton sans être ajoutée explicitement.

## 6. Ce qui demande l'approbation de l'architecte

- Le commit et son déploiement sur `https://astra.infranetgn.com` (nouvelle table, nouveau chemin d'authentification).
- La création du premier jeton sur le serveur et l'installation de l'unité `astra-agent` (systemd).
- Toute fusion dans `main`.
- Toute portée au-delà de `read` et `lab`, et le serveur MCP.
- Toujours exclus : clés de courtier, activation DEMO ou LIVE.

## 7. Livrables après validation

1. `astra/auth/agent_tokens` (store, CLI `agent-create|agent-list|agent-revoke`), branchement dans `webapp.require`.
2. `astra/agent.py` (client et boucle) et commande `astra agent run|once`.
3. Tests : jeton valide, expiré, révoqué, propriétaire désactivé, route hors portée, cookie plus Bearer,
   audit, et boucle de l'agent contre l'application Flask locale en SIMULATION.
4. `deploy/systemd/astra-agent.service` (modèle, non installé) et mise à jour de `SECURITY.md` / `OPERATIONS.md`.
5. Rapport `reports/AGENT_REPORT.md` : résultats des tests et liste de ce qui reste à valider avant déploiement.
