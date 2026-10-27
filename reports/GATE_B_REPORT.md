# ASTRA, rapport de Gate B : sécurisation GitHub et intégration continue

**Destinataire : l'architecte principal (ChatGPT).** Copie : l'utilisateur. Date : 27/10/2026. Dépôt : `tidiokamsar/forex` (public). Branche de travail : `feat/multi-venue`.

**Statut : Gate B NON terminée.** La protection de `main`, les réglages du dépôt et la CI sont en place et ont été revus de façon indépendante. Mais **aucune exécution CI n'a eu lieu** : GitHub refuse de démarrer les jobs parce que le compte est verrouillé pour un problème de facturation. Un workflow présent dans le dépôt n'est pas une CI opérationnelle ; le critère « exécution réelle et réussie » n'est donc pas rempli. Seul le propriétaire du compte peut lever ce blocage.

| Repère | Valeur |
|---|---|
| SHA final du code vérifié (outillage CI) | `956644d855a5057b0b0779cdd6ce1ab30134dec1` |
| Commit final de la branche | le commit qui ajoute ce rapport, au-dessus du précédent ; uniquement des fichiers `.md` |
| `main` | `60991ed`, inchangée, identique à `origin/main` ; aucune fusion, aucune réécriture |
| Workflow | [.github/workflows/ci.yml](../.github/workflows/ci.yml), documenté dans [docs/CI.md](../docs/CI.md) |

## 1. État Git initial et final

| | Initial (début de Gate B) | Final |
|---|---|---|
| Branche active | `feat/multi-venue` | `feat/multi-venue` |
| HEAD local | `caf93601b423d620865f61c8c310d5909c4a2777` | voir le message de checkpoint |
| HEAD distant `feat/multi-venue` | `caf9360` (identique) | identique au HEAD local après publication |
| `main` local et distant | `60991ed` (« Initial commit ») | `60991ed`, inchangée |
| Arbre de travail | propre | propre |
| Workflows GitHub Actions | aucun (`.github/` absent) | `CI` (`ci.yml`) |
| Protection de `main` | aucune (HTTP 404 « Branch not protected »), aucun ruleset | voir section 2 |
| Visibilité | publique (non modifiée) | publique (non modifiée) |

Commits de Gate B (tous sur `feat/multi-venue`, poussés sans force) :
- `49983e1` : workflow CI, `tools/ci/secret_scan.py`, `tools/ci/check_unittest_log.py`, Ruff épinglé, `docs/CI.md`, `.gitignore` renforcé ;
- `7c8a10d` : correctifs de la première revue (F1 à F5) et documentation des contrôles ;
- `956644d` : correctifs faibles de la seconde revue (analyse de l'historique, faux positifs, documentation) ;
- le commit de ce rapport (documentation seulement).

## 2. Protections et réglages vérifiés

Chaque réglage a été appliqué par l'API GitHub puis **relu** par l'API (`GET`). Aucune poussée directe vers `main` n'a été tentée pour « tester » la règle, afin de ne pas risquer de la modifier.

**Protection de la branche `main`** (`GET repos/tidiokamsar/forex/branches/main/protection`) :

| Règle | Valeur relue |
|---|---|
| Modification uniquement par pull request | oui (`required_pull_request_reviews` actif) |
| Approbations requises | 0 (voir limitation 6.2) ; avis périmés rejetés (`dismiss_stale_reviews`) |
| Contrôles requis | `quality` et `full-suite`, émis par l'application GitHub Actions (`app_id` 15368, vérifié par `GET apps/github-actions`) |
| Branche à jour avec `main` avant fusion | oui (`strict`) |
| Règles appliquées aussi aux administrateurs | oui (`enforce_admins`) |
| Force-push | interdit |
| Suppression de `main` | interdite |
| Conversations résolues avant fusion | oui |
| `GET repos/.../branches/main` | `protected: true` |

**Réglages du dépôt et d'Actions :**

| Réglage | Avant | Après (relu) |
|---|---|---|
| Secret scanning | désactivé | **activé** |
| Push protection | désactivée | **activée** |
| Secret scanning des motifs non fournisseurs | désactivé | toujours désactivé : la demande d'activation n'a pas été prise en compte sur ce compte (limitation 6.3) |
| Dependabot security updates | désactivé | inchangé (aucune dépendance tierce ; il ouvrirait lui-même des pull requests) |
| Actions autorisées | toutes | uniquement les actions de GitHub (`github_owned_allowed`), aucune action « vérifiée » ni motif tiers |
| Épinglage par SHA exigé | non | **oui** (`sha_pinning_required`) |
| Permissions par défaut du jeton | lecture | lecture (inchangé) |
| Le jeton peut approuver des pull requests | non | non (inchangé) |
| Collaborateurs | `tidiokamsar` seul | inchangé |

Permissions du compte : `admin: true` sur le dépôt, ce qui permet de modifier ces règles. Jeton `gh` : portées `repo`, `workflow`, `read:org`, `gist` ; la portée `user` (facturation) n'a pas été ajoutée.

## 3. Workflow créé

`.github/workflows/ci.yml`, trois jobs :

| Job | Déclencheur | Contenu | Délai |
|---|---|---|---|
| `quality` | pull request vers `main`, push, manuel | Ruff 0.16.1 (épinglé par hash), `compileall`, `node --check` du script du tableau de bord, scan de secrets (arbre et historique complet), 7 modules de tests multi-plateformes | 20 min |
| `plan` | idem | décide de ne pas relancer la suite longue sur un push si une pull request ouverte vers `main` la lance déjà | 5 min |
| `full-suite` | idem | suite complète `unittest discover -v`, puis `check_unittest_log.py` : exactement un résumé, `OK`, exactement 15 échecs attendus, tous dans `test_lot4_*` | 75 min |

- **Noms des contrôles requis.** Seules les exécutions de pull request portent les noms `quality` et `full-suite`. Un push publie `quality (push)` et `full-suite (push)`, et un job sauté avant démarrage apparaît sous l'expression non évaluée (observé sur les exécutions réelles). Aucun job de push ou sauté ne peut donc satisfaire un contrôle requis, alors que GitHub compte un job sauté comme réussi.
- **Échecs attendus du Lot 4.** Ils ne sont ni masqués ni transformés en réussite. unittest échoue déjà sur un échec, une erreur ou un succès inattendu. Le contrôle supplémentaire échoue aussi si un `expectedFailure` apparaît ailleurs ou si un résidu disparaît sans mise à jour revue de `EXPECTED_FAILURES`.
- **Codes de sortie.** Chaque étape s'exécute sous `bash -eo pipefail`, avec `set +e`, capture immédiate du code (`$?` ou `PIPESTATUS[0]`) et `exit $rc`. La sortie de la suite est copiée par `tee` sans masquer son code.
- **Traçabilité.** Le résumé de chaque exécution enregistre le commit analysé, l'événement, `GITHUB_WORKFLOW_REF` et `GITHUB_WORKFLOW_SHA`, l'image du runner, les versions de Python, Ruff, Node et Git, chaque commande avec son code de sortie, et la fin du journal de la suite.

**Durcissement :**
- `permissions: contents: read` au niveau global ; seul `plan` ajoute `pull-requests: read`. Aucune écriture.
- Événement `pull_request`, jamais `pull_request_target` : le code d'un fork s'exécute sans secret et avec un jeton en lecture.
- `persist-credentials: false` : le jeton ne reste pas dans la configuration Git pendant les tests.
- Aucune injection : le nom de branche n'atteint `run:` que par une variable d'environnement entre guillemets.
- Actions utilisées : `actions/checkout` v7.0.1 (`3d3c42e5aac5ba805825da76410c181273ba90b1`) et `actions/setup-python` v7.0.0 (`5fda3b95a4ea91299a34e894583c3862153e4b97`).
  - Ce sont des actions officielles de GitHub. Les SHA ont été vérifiés contre les étiquettes, qui sont légères (objet `commit`).
  - Les deux versions ont été publiées le 20/07/2026. La v7.1.0 de setup-python, publiée le 22/10/2026, a été écartée car trop récente.
  - Ces deux actions sont nécessaires : sans elles, ni récupération du code ni installation de Python 3.14.
- Aucune action tierce, aucun service payant, aucune dépendance d'exécution ajoutée ; aucune clé de courtier, aucun secret ; aucun déploiement, aucune publication, aucune fusion automatique.

## 4. Résultats CI réellement obtenus

| Exécution | Commit | Résultat | Cause |
|---|---|---|---|
| [40439596578](https://github.com/tidiokamsar/forex/actions/runs/40439596578) | `49983e1` (push) | échec, 0 étape exécutée | « The job was not started because your account is locked due to a billing issue. » |
| [40440812399](https://github.com/tidiokamsar/forex/actions/runs/40440812399) | `7c8a10d` (push) | échec, 0 étape exécutée | même message |
| [40441555382](https://github.com/tidiokamsar/forex/actions/runs/40441555382) | `956644d` (push) | échec, 0 étape exécutée | même message |

**Aucun code du projet n'a été exécuté par GitHub Actions.** Ces échecs ne disent rien du code. Ils montrent seulement que le verrou de facturation empêche toute CI, et donc toute fusion dans `main` : les contrôles requis ne peuvent pas être produits.

**Preuve intérimaire, locale (pas une preuve CI).** Les mêmes commandes que le workflow ont été exécutées sur la machine de développement (Windows, Python 3.14.6, Ruff 0.16.1, Node v24.18.1), sur `956644d855a5057b0b0779cdd6ce1ab30134dec1`, arbre propre, chaque code de sortie enregistré séparément :

| Commande | Code |
|---|---|
| `python -m ruff check src tests tools` | 0 |
| `python -m compileall -q src tests tools` | 0 |
| `node --check src/astra/static/app.js` | 0 |
| `python tools/ci/secret_scan.py` (arbre et 112 commits) | 0 |
| Tests ciblés (7 modules) | 0 (260 tests, OK) |
| Suite complète `unittest discover -s . -v` | 0 (937 tests, OK (expected failures=15)) |
| `check_unittest_log.py --expected-failures 15 --expected-modules test_lot4_` | 0 (15 échecs attendus : test_lot4_adversarial 1, test_lot4_security_review 7, test_lot4_verification 7) |

Exécution du 27/10/2026, 03:10:53 à 03:21:51 UTC, arbre propre au démarrage.

**Réserves sur ces exécutions locales :**

- Pendant celle-ci, de nouveaux fichiers du lot d'authentification suivant (un module non importé par les tests, et un ajout à `config.py`) ont été écrits dans l'arbre de travail. Les tests déjà chargés n'en dépendent pas. Les tests en sous-processus importent `config.py`, qui est resté valide. La suite complète sera rejouée sur l'état final du lot suivant.
- Les exécutions précédentes sur `49983e1`, `7c8a10d` et une première sur `956644d` ne sont pas retenues. Elles ont été arrêtées ou corrompues : sous Windows, l'arrêt d'une tâche en arrière-plan avait laissé tourner son processus Python, qui écrivait dans les mêmes fichiers de résultats. Le contrôle du journal l'a détecté (« 0 ligne Ran »). Le script local écrit désormais dans un dossier distinct par exécution.

Ces résultats ne remplacent pas une exécution sur le runner Linux de GitHub. Une différence propre à Linux reste possible tant que la CI n'a pas tourné.

## 5. Audits, constats et résolution

**Audit des secrets (agent indépendant, lecture seule).**
- Périmètre : 129 commits, dont 109 publiés, et 50 312 lignes ajoutées, sur toutes les branches.
- Verdict : **propre**. Aucun secret réel, aucun jeton, aucune clé, aucun fichier sensible versionné, que ce soit dans l'arbre ou dans l'historique. Chaque alerte est un nom de variable d'environnement, un faux de test évident, un exemple de documentation ou une empreinte publique. `.env.example` n'a jamais contenu de valeur secrète.
- Lacunes de `.gitignore` corrigées : `*.pfx`, `*.sqlite`, `*.jsonl`, `*.log`, clés SSH, `.netrc`, `credentials*.json`.

**Première revue indépendante (sur `49983e1`) :**

| Réf. | Gravité | Constat | Résolution |
|---|---|---|---|
| F1 | Élevée | Le scan de secrets signalait son propre commentaire (`scheme://user:password@host`), dans l'arbre et dans l'historique : `quality` ne pouvait jamais réussir, donc aucune fusion n'était possible | Corrigé (`7c8a10d`) : les mots d'exemple exacts ne sont plus des alertes ; confirmé localement (code 1 avant, 0 après) |
| F2 | Moyenne | Un `full-suite` sauté lors d'un push pouvait satisfaire le contrôle requis (GitHub compte un job sauté comme réussi) | Corrigé (`7c8a10d`) : seuls les jobs de pull request portent les noms requis ; vérifié sur les exécutions réelles |
| F3 | Faible | Le contrôle du journal prenait la dernière ligne `OK`/`FAILED` du fichier | Corrigé : une seule ligne `Ran`, le résumé qui la suit, couleurs refusées, `PYTHON_COLORS=0` |
| F4 | Faible | Faux négatifs du scan : chemins non ASCII, fusions, clés PGP, jetons `sk-`/Bearer, valeurs non quotées, exemption trop large des noms de variables, clone superficiel | Corrigé (`7c8a10d`) |
| F5 | Faible | Inexactitudes de `docs/CI.md` | Corrigé |
| F6 | Faible | Dependabot et motifs non fournisseurs désactivés | Motifs : activation refusée par le compte ; Dependabot volontairement non activé (section 2) |

**Seconde revue indépendante (sur `7c8a10d`, agent distinct).**
- F1, F2 et F3 sont confirmés corrigés, sans régression. Aucun constat critique, élevé ou moyen ouvert.
- Constats faibles, corrigés dans `956644d` :
  - chemin contenant une espace ;
  - ligne ajoutée commençant par `++` ;
  - renommage vers un nom interdit ;
  - faux positifs possibles sur des accès d'attribut ;
  - nom réel des jobs sautés ;
  - mentions de CI pouvant laisser croire qu'elle tourne.
- Chaque cas a été reproduit puis vérifié après correction.

Tests négatifs de l'outillage, sur des dépôts jetables :
- **Scan de secrets.** Il détecte :
  - une clé AWS, un jeton GitHub, une clé `sk-ant-` et un jeton Bearer ;
  - une clé PGP ;
  - des valeurs YAML non quotées ;
  - des identifiants dans une URL ;
  - un `.pem`, un `.env` ou un `.log` ajouté puis supprimé (y compris par renommage).

  Il ignore les noms de variables et les faux de test, et s'arrête avec le code 2 sur un clone superficiel.
- **Contrôle du journal.** Il accepte le journal réel vert (937 tests, 15 échecs attendus). Il refuse :
  - un journal en échec ;
  - un journal tronqué ;
  - un résumé falsifié ajouté ;
  - une exécution imbriquée ;
  - un nombre d'échecs attendus erroné.

## 6. Limitations GitHub et décisions à prendre

1. **Verrou de facturation (bloquant).** Le compte est verrouillé pour un problème de facturation : aucun job ne démarre. Le dépôt est public et les minutes d'Actions y sont normalement gratuites, mais le verrou s'applique au compte. Action du propriétaire : régler la facturation dans les paramètres GitHub du compte. Il faut ensuite pousser un commit ou relancer la dernière exécution, puis vérifier une exécution verte de `quality (push)` et `full-suite (push)`. Les contrôles requis `quality` et `full-suite` ne pourront être produits que par une pull request vers `main`, à ouvrir sur décision de l'architecte.
2. **Revue indépendante non imposable par GitHub.** Il n'y a qu'un collaborateur, qui ne peut pas approuver sa propre pull request. Exiger une approbation, avec des règles appliquées aux administrateurs, rendrait toute fusion impossible. La règle retenue est donc : pull request obligatoire, 0 approbation, contrôles requis, administrateurs inclus. La revue indépendante reste un processus du projet (agents relecteurs distincts). Pour l'imposer techniquement, il faudrait un second compte relecteur ayant un accès en écriture : décision de l'utilisateur.
3. **Motifs non fournisseurs du secret scanning.** Leur activation n'a pas pris effet ; cette fonction relève probablement d'une offre payante de GitHub. Elle est compensée par le scan du projet, qui couvre ces motifs.
4. **`workflow_dispatch`.** Le déclenchement manuel n'est disponible que lorsque le workflow est sur `main`.
5. **Linux uniquement en CI.** Les lots ont été validés sous Windows ; une différence propre à une plateforme ne serait vue que d'un côté.
6. **Rapprochement par nom de branche.** `plan` associe une pull request ouverte par nom de branche seulement. Une pull request de fork portant le même nom saute la suite du push, mais celle de la pull request reste requise.

## 7. Reproduire les contrôles

Les commandes exactes, pour PowerShell, sont dans [docs/CI.md](../docs/CI.md#running-the-same-checks-locally) :

```powershell
python -m ruff check src tests tools
python -m compileall -q src tests tools
node --check src/astra/static/app.js
python tools/ci/secret_scan.py
cd tests; $env:PYTHONPATH = "../src"
python -m unittest test_multi_venue_security_review test_multi_venue_verification test_market_risk test_binance_spot test_venue_registry test_venues_contract test_markets
python -m unittest discover -s . -v > ../full_suite.log 2>&1; $LASTEXITCODE
cd ..; python tools/ci/check_unittest_log.py full_suite.log --expected-failures 15 --expected-modules test_lot4_
```

## 8. Confirmations

- `main` n'a été ni fusionnée, ni réécrite, ni poussée : `60991ed`, identique localement et sur GitHub.
- Aucune pull request n'a été ouverte.
- Aucun push forcé, aucune réécriture d'historique.
- La visibilité du dépôt n'a pas été modifiée.
- Aucun déploiement, aucune connexion financière, aucun ordre, aucun secret créé ou demandé.
- DÉMO et RÉEL restent fermés ; futures et options restent désactivés.

Gate C n'est pas commencée au titre de cette directive. Une nouvelle directive de l'utilisateur, la « vision cible », a été reçue pendant la clôture. Seul son audit en lecture seule a été lancé ; il fera l'objet de son propre rapport.
