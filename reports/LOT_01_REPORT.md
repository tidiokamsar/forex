# ASTRA, rapport du Lot 1 : corrections indispensables

Date : 25/10/2026. Branche : `fix/lot1-securite` (publiée). Destinataires : l'utilisateur et l'architecte principal (ChatGPT) pour arbitrage.

## A. Résumé exécutif

**Objectif du lot.** Corriger les défauts relevés par l'audit (C-01 à C-08), mettre le projet sous Git sans perdre l'historique distant, ne publier aucun secret et n'activer aucune exécution démo ou réelle.

**Résultats obtenus.**
- Tous les défauts du lot sont traités. C-04 s'est révélé ne pas être un défaut : mon audit s'était trompé, et un test le démontre.
- Une vérification indépendante a trouvé 2 failles supplémentaires de faible gravité (D-1, D-2), corrigées.
- La suite passe : 248 tests, 0 échec, 0 test ignoré. ruff, compileall et `node --check` sont propres.

**Niveau réel d'achèvement.** Le lot est terminé et vérifié, **en local et hors ligne uniquement**. Aucun test n'a été exécuté contre le vrai service cTrader. Le contrôle en lecture seule `ctrader-check` n'a pas encore été lancé.

**Difficultés principales.**
- Le lot a été réalisé **avant** l'arrivée du protocole multi-agents, par un seul agent, séquentiellement. Seules la vérification indépendante et la correction documentaire ont été faites par des agents distincts (section B).
- La règle « permis à usage unique » a d'abord bloqué des ordres légitimes : deux demandes identiques au même instant produisaient le même permis. Corrigé par un nonce aléatoire.

## B. Travail des agents

| Agent | Rôle et mission | Réalisé | Fichiers | Résultat vérifié |
|---|---|---|---|---|
| Coordinateur (moi) | Architecte et développeur du lot, intégration | Git, C-01, C-03, C-05, C-07, C-08, D-1, D-2, rapport | `src/astra/permits.py` (nouveau), `brokers/base.py`, `brokers/ctrader.py`, `brokers/simulated.py`, `execution/{gateway,controls,orders,resolution}.py`, `governance.py`, `cli.py`, `web.py`, `engine.py`, `pyproject.toml`, `.gitignore`, `.gitattributes`, tests | Suite complète relancée à chaque étape |
| QA et sécurité, indépendant | Vérifier le lot sans l'avoir écrit, dans un clone isolé, sans droit de modification | Relecture du code, recherche active de contournements du verrou d'ordres, relance des tests et des contrôles qualité, contrôle des secrets | Aucun (lecture seule) | 8 points sur 8 confirmés ; 245 tests passés dans son clone ; 2 failles faibles trouvées (D-1, D-2) |
| Audit documentaire, indépendant | Comparer la documentation au code, dans un clone isolé | Plus de 40 constats : faux, périmés ou manquants | Aucun (lecture seule) | Aucune surdéclaration (service réel, démo active, rentabilité) ; plusieurs incohérences |
| Ingénieur documentation | Appliquer les corrections documentaires, uniquement sur les `.md` | 13 fichiers Markdown corrigés | README, OPERATIONS, BROKERS, CHANGELOG, TASKS, TESTING, RISK_POLICY, ROADMAP, ARCHITECTURE, SECURITY, ACCEPTANCE_REPORT, ADR-0003, ADR-0004 | Diff relu par le coordinateur, recherche de surdéclarations vide |

Le travail en parallèle est resté sans conflit : l'agent documentation ne modifiait que des `.md`, pendant que je modifiais `src/` et `tests/`. Limite assumée : le lot lui-même n'a pas été réparti entre agents.

Non terminé, dans le périmètre du lot : **C-06**, borner l'ensemble d'identifiants que le RiskEngine garde en mémoire. Priorité basse : l'idempotence durable est assurée par la passerelle.

## C. Preuves techniques

**Commandes** (bash, depuis le dossier du projet) :
```bash
python -m ruff check src tests                          # All checks passed!
PYTHONPATH=src python -m unittest discover -s tests     # Ran 248 tests in 75.552s / OK
python -m compileall -q src tests                       # code de sortie 0
node --check src/astra/static/app.js                    # code de sortie 0
```

**Tests.** 248 découverts, 248 exécutés, 248 réussis, 0 échec, 0 ignoré. Répartition : cli 8, cli_operations 4, config 6, ctrader 32, data 19, engine 6, execution 29, features 17, governance 8, lot1_fixes 17, ml 11, regimes 9, registry 9, research 8, risk 15, signal_backtest 9, storage 6, strategies 15, validation 9, web 4, web_operations 2, websocket 5.

**Tests ajoutés par le lot.**
- `ExecutionBoundaryTests` (13) : permis, gardes d'environnement, mixin, usage unique.
- `ResolutionTests` (6), `LookupEventTests` (1), `AccountScopedControlTests` (5), `ErrorClassificationTests` (5).
- Un test qui échoue si `ctrader-check` atteint une méthode d'envoi d'ordre.
- Un test de cohérence entre la version du code et celle de `pyproject.toml`.

**Nature des tests, à ne pas confondre :**
- Simulés : `SimulatedBroker` avec pannes injectées (timeouts, rejets, exécutions partielles, déconnexions, événements inversés, positions externes, faux statut de compte).
- Locaux hors ligne : faux serveur cTrader scripté (`tests/ctrader_fixture.py`, étiqueté « pas du trafic réel »), serveur WebSocket et serveur HTTP en boucle locale.
- Service réel : **aucun**.

**Publication.**
- Recherche de secrets sur tous les fichiers suivis et sur tout l'historique (jetons, clés, clés privées, mots de passe, `.env`, données personnelles) : rien.
- Empreinte SHA-256 du jeu de données de test inchangée (`16402dc1…5ef8`), y compris sur un clone frais sous Windows, où `demo` fonctionne.

**Risques résiduels.**
- Le permis protège contre les contournements accidentels, ou venant d'une stratégie ou d'un LLM. Du code qui importerait délibérément l'autorité privée pourrait en forger un ; cela exigerait une modification de code revue. C'est documenté.
- Les rôles déclarés en ligne de commande ne sont pas authentifiés.
- La journalisation d'audit SQLite reste modifiable par son propriétaire.

## D. Avancement du cahier des charges

Mesure : les 49 exigences MUST ou BLOQUANT des sections 5.1 à 5.8.
- **Avant le lot : 34 vérifiées en simulation.**
- **Après le lot : 34**, avec des preuves renforcées (rien de nouveau n'est vérifié contre un vrai courtier) :
  - RSK-01 (tout ordre passe par la validation) et BRK-03 (idempotence) sont désormais imposés à la frontière d'exécution elle-même, pas seulement dans la passerelle.
  - RSK-07 (arrêt d'urgence et gel) : gels séparés par compte, résolution humaine des ordres d'état inconnu.
  - AI-08 (un LLM ne peut contourner ni le risque ni l'exécution) : même sans permis, un adaptateur refuse d'envoyer.
- **Partiellement satisfaites (12)**, inchangées :
  - ENV-01 et ENV-03 : DÉMO et RÉEL non exécutables ; statut du compte vérifié hors ligne seulement.
  - ENV-05 : secrets et permissions pas encore séparés par environnement.
  - DATA-02 et DATA-07 : pas de test de changement d'heure, pas d'import Parquet ni API.
  - BT-01 : pas de swap.
  - RSK-02, RSK-03, RSK-04, RSK-06 : EURUSD seul, une position, pas de limite de drawdown, pas de test négatif explicite contre la martingale.
  - BRK-04 : réconciliation prouvée seulement contre le simulateur et le faux serveur.
  - IMP-05 : pas de retour arrière automatique.
- **Non satisfaites (3)** : ENV-04 (autorisation réelle authentifiée et révocable), ENV-06 (audit des changements de mode, le mode n'étant pas modifiable), AI-06 (code généré par l'IA isolé en bac à sable).
- **Critères d'acceptation encore manquants** : un contrôle réel cTrader en lecture seule, puis un ordre démo supervisé.
- Sections 5.9 et 5.10, 12, 13 et 18 : voir le backlog de l'audit. La section 13 est en cours sur la branche de fondations (section G).

## E. Blocages et interventions humaines

| Blocage | Pourquoi | Ce qui continue sans vous | Action exacte |
|---|---|---|---|
| Compte démo cTrader et autorisation OAuth | Seule preuve possible du protocole réel, du statut démo et des unités | Tout le travail hors ligne | Créer l'application Open API chez Spotware, obtenir le jeton du compte **démo**, lancer `python -m astra ctrader-check --operator <nom>` (procédure dans BROKERS.md) et transmettre le JSON produit, qui ne contient aucun secret. Le diagnostic est revu et ne peut envoyer aucun ordre (C-01, D-1, test dédié). |
| Stockage sécurisé des jetons | Le renouvellement OAuth invalide l'ancien jeton : sans stockage durable et sûr, risque de perte d'accès | Le renouvellement est codé mais désactivé | Choisir un mécanisme : par exemple le Gestionnaire d'identification Windows ou un coffre dédié. |
| Hébergement continu | Le service doit tourner en permanence | Le service fonctionne localement sur le simulateur | Plus tard : machine allumée en permanence, ou serveur (éventuellement payant). |

Aucune décision financière n'est requise à ce stade.

## F. Recommandation technique

**Priorités.**
1. Lancer votre `ctrader-check`.
2. Publier puis relire la branche de fondations (section G).
3. Seulement ensuite, l'activation DÉMO, par une modification revue de `EXECUTABLE_MODES` et du RiskEngine, suivie d'un ordre démo supervisé de taille minimale.

**Dépendances critiques.** Pour la démo : contrôle réel, puis fondations publiées, puis activation DÉMO revue. Pour valider des stratégies : des données historiques réelles, que le compte démo pourra fournir.

**Risques avant de poursuivre.**
- Les hypothèses cTrader non documentées : type de trame, encodage des énumérations, formule d'équité et de marge. Seul le contrôle réel les tranchera.
- La couverture limitée au simulateur.

**Options pour la suite.**
- **Option A** : relire et publier les fondations, puis attendre le contrôle cTrader. Avantage : risque minimal. Inconvénient : la démo attend votre action.
- **Option B** : en parallèle, travailler la validation quantitative sur données synthétiques (walk-forward générique au registre, contrôle de surapprentissage). Avantage : avance le chemin des stratégies. Inconvénient : aucune conclusion réelle tant que les données sont synthétiques.
- **Option C** : préparer le déploiement web (authentification du dashboard avant toute exposition). Avantage : exploitation à distance. Inconvénient : surface d'attaque nouvelle ; à faire après les fondations.

Ma recommandation : **A, puis B en parallèle**.

## G. État Git et publication

- **Dépôt** `https://github.com/tidiokamsar/forex`. L'historique distant est préservé (`60991ed Initial commit`). `main` n'a pas été modifiée.
- **Branche du lot `fix/lot1-securite`, publiée** :
  - `d7eb52e` import du code 0.2.0
  - `11b4f08` C-01
  - `52bf702` C-03, C-05, C-07 et C-04 non défaut
  - `abd7325` ruff
  - `ab843d1` documentation
  - `7a6dc7f` D-1, D-2 et corrections documentaires
  - plus le commit de ce rapport.
  - L'état local et l'état distant ont été vérifiés identiques avec `git ls-remote`.
- **Branche `feat/fondations-exploitation`, locale, NON publiée.** Elle contient F-01 (journaux JSON), F-04 (sauvegarde et restauration vérifiées), F-03 (reconnexion cTrader, limite de débit, expiration du jeton) et F-02 (service continu sur simulateur), fusionnée avec le Lot 1.
  - 282 tests OK, ruff propre.
  - Elle a corrigé en route un défaut réel : le curseur d'événements courtier était global, alors qu'il doit être propre à chaque compte.
  - Elle n'a pas été relue indépendamment. Je ne la publie pas avant votre arbitrage, puisque le protocole demande l'arrêt au point de contrôle.
- **Modifications locales non commitées** : aucune.
- **Secrets** : aucun secret publié ; recherche faite avant chaque envoi.
- **Exécution** : aucun ordre envoyé, DÉMO non activée, RÉEL verrouillé.
