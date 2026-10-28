# Rapport : accès machine (jetons d'agent) et agent ASTRA 24 h/24

Date : 2026-10-28. Branche `feat/auth-deploy`, modifications **non commitées et non déployées**.
Plan validé par le titulaire du compte : [AGENT_ACCESS_PLAN.md](AGENT_ACCESS_PLAN.md).
Mode : SIMULATION uniquement. Aucune clé de courtier, aucune activation DEMO ou LIVE.

## Ce qui a été construit

| Élément | Fichier |
|---|---|
| Jetons d'agent : émission, révocation, liste, authentification, limitation des essais, audit | `src/astra/auth/agent_tokens.py` |
| CLI serveur `astra auth agent-create / agent-revoke / agent-list` | `src/astra/auth/admin_cli.py`, `src/astra/cli.py` |
| Chemin Bearer dans l'application, liste blanche `AGENT_ENDPOINTS`, route `/api/agent/whoami` | `src/astra/webapp.py` |
| Agent (client à liste blanche, boucle 24 h/24, journal, résumé quotidien), `astra agent run|once` | `src/astra/agent.py` |
| Unité systemd modèle (non installée) : utilisateur jetable, jeton par `LoadCredential`, sans accès à la base | `deploy/systemd/astra-agent.service` |
| Documentation | `SECURITY.md` (« Agent tokens »), `OPERATIONS.md` (« ASTRA agent ») |
| Tests | `tests/test_agent.py` (26 tests) ; une ligne dans `tests/test_webapp.py` (inventaire des routes) |

Le travail « learning » a été commité entre-temps par une autre session (`2c56b21`) ; ce lot ne modifie aucun
de ses fichiers, sauf une ligne de l'inventaire des routes dans `tests/test_webapp.py`.

## Résultats des tests (local, Windows, Python 3.14.6)

- `tests/test_agent.py` : 26 tests, OK. Ils couvrent :
  - la liste blanche serveur figée (un test échoue si une route est ajoutée sans revue) ;
  - le jeton stocké uniquement en empreinte (absent du fichier SQLite, de la liste et de l'audit) ;
  - la portée `read` refusée en écriture ; l'attribution `agent:bot` des demandes et leur audit ;
  - l'`Origin` exigé pour les modifications ;
  - les routes fermées à tout jeton : audit, ordres, réconciliations, admin, session, DEMO (lecture et
    demandes), dépôt CSV, pages, `/login`, scripts ;
  - le refus d'un cookie et d'un Bearer ensemble ;
  - les jetons expirés, révoqués ou inconnus ;
  - la révocation automatique après réinitialisation du mot de passe ou désactivation du propriétaire ;
  - la perte des routes `lab` si le propriétaire est rétrogradé ;
  - la liste d'adresses ; la limitation par adresse (429) ;
  - les refus d'émission : portée inconnue (`admin`, `live`, `demo`), propriétaire sans MFA, `lab` pour un
    propriétaire en lecture seule, durée hors bornes, plus de 2 jetons actifs ;
  - l'agent contre l'application réelle (série CSV importée par le vrai worker) : budget de backtests,
    journal et résumé, observation seule sous kill switch, aucune écriture avec un jeton `read`,
    arrêt (code 2) avec un jeton révoqué ;
  - l'agent sur HTTP réel (urllib vers l'application servie en boucle locale) ;
  - la logique de décision : arrêt d'une simulation perdante, démarrage sur le meilleur backtest,
    arrêt immédiat si le mode est DEMO ou LIVE, refus local des routes hors liste blanche ;
  - un jeton peut arrêter une simulation mais jamais la relancer ; `/api/agent/whoami` refuse les anonymes (401)
    et les sessions navigateur (404).
- Suite complète : 1135 tests, OK (5 ignorés, 15 échecs attendus des modules test_lot4_), sous Windows, le 2026-10-28 avant le commit.
- `ruff check src tests` : aucune erreur.

## Revue de sécurité

Une relecture par un agent relecteur distinct (pas un humain) n'a trouvé aucun défaut de confiance élevée.
Ses remarques mineures ont été traitées : un jeton ne peut plus relancer une simulation (seulement l'arrêter),
et la documentation précise que `read` inclut `/api/controls`, que la limitation dépend de
`ASTRA_TRUSTED_PROXY=1` et qu'un jeton valide n'est pas limité en débit.
Correctif issu de la suite complète : une base de comptes illisible ne bloque plus le démarrage de
l'application (chaque requête répond 503, comme avant ce lot) ; en production la table est créée
explicitement au démarrage.


- Pas d'usage du mot de passe ni du TOTP par l'agent ; ses actions sont distinguées des vôtres dans l'audit
  et dans les données (acteur `agent:<nom>`, propriétaire dans le détail).
- Moindre privilège à deux niveaux : liste blanche serveur par portée **et** rôle actuel du propriétaire ;
  le client de l'agent a sa propre liste blanche, plus étroite (il ne relance jamais une simulation).
- Aucune portée n'atteint ordres, DEMO, LIVE, kill switch, mode, comptes ou audit.
- Résiduels :
  - le jeton est un secret de longue durée (90 jours au plus) : vol = lecture des données de recherche et
    demandes de backtests ou simulations papier jusqu'à révocation ou expiration ;
  - la limitation est par adresse : un tiers qui partage l'adresse de l'agent et envoie de faux jetons peut
    retarder l'agent (429), sans accès ;
  - une réinitialisation de MFA ne révoque pas les jetons du propriétaire : `agent-revoke --owner` est
    documenté pour ce cas ;
  - le classement des backtests par l'agent est en échantillon et indicatif ; il ne remplace pas le
    protocole de recherche et l'agent ne promeut aucun modèle.

## Ce qui reste à valider avant tout déploiement sur https://astra.infranetgn.com

Chaque point demande l'approbation de l'architecte :

1. **Relecture humaine** du diff (nouveau chemin d'authentification dans `webapp.guard` et `require`).
2. **Commit** sur `feat/auth-deploy`, séparé du travail « learning » en cours, puis passage de la CI.
   Pas de fusion dans `main` sans approbation.
3. **Déploiement** de ce commit comme nouvelle release (procédure `DEPLOYMENT.md`, sauvegarde préalable de
   `auth.sqlite3` : la table `agent_tokens` est créée au démarrage).
4. **Vérifications sur le serveur** :
   - un Bearer passe bien par Caddy jusqu'à l'application ; le proxy ne doit ni retirer ni journaliser
     l'en-tête `Authorization` ;
   - `/api/agent/whoami` répond 401 sans jeton ;
   - les routes interdites répondent 403 avec un vrai jeton.
5. **Émission du premier jeton** (`astra auth agent-create --owner <votre compte> --scopes read,lab --days 30`),
   écrit dans `/etc/astra/agent.token` (root 0600).
6. **Installation de l'unité** `astra-agent` après avoir remplacé `ASTRA_AGENT_URL` par l'adresse publique,
   puis contrôles : `systemd-analyze security astra-agent`, un cycle `once`, lecture du résumé, révocation
   de test (l'agent doit s'arrêter avec le code 2 et ne pas redémarrer).
7. **Option non construite** : le serveur MCP s'appuyant sur le même jeton (phase 2, sur demande).
