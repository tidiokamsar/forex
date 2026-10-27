# ASTRA, rapport AUTH-2 et AUTH-2.1 : chiffrement MFA, vérification Linux, préparation du serveur dédié

**Destinataire : l'architecte principal (ChatGPT).** Copie : l'utilisateur. Date : 27/10/2026. Branche : `feat/auth-deploy`.

**Statut : corrections faites, revues indépendamment et testées sous Windows et sous Linux. Aucun déploiement, aucune publication Internet, aucune modification du trading.** La CI GitHub Actions n'a pas été exécutée (compte verrouillé pour facturation) : tous les résultats ci-dessous sont **locaux**, et aucun n'est présenté comme un résultat de CI.

| Repère | Valeur |
|---|---|
| SHA du code vérifié | `40d4264282479e6f5d732a5748b488552ea8decd` |
| Commit final de la branche | le commit de ce rapport, au-dessus de `40d4264` (documentation seulement : ce rapport et `docs/DEDICATED_SERVER_RUNBOOK.md`) ; les contrôles sont rejoués sur ce commit et leurs résultats donnés au point de contrôle |
| Base AUTH-2 | `8f5b301` (fin de la gate Auth & Deployment) |
| `main` | `60991ed`, inchangée, identique à `origin/main` ; aucune fusion |
| Serveur `102.211.199.132` | non utilisé et non modifié par ce lot |

## 1. Constats et corrections

| Constat | Correction | Commit |
|---|---|---|
| Secrets TOTP stockés en clair dans `auth.sqlite3` | Chiffrement authentifié AES-256-GCM (section 2) | `384c2df` |
| Secret de proxy non renouvelable | Le fichier accepte deux valeurs (courante et précédente) | `384c2df` |
| Version de Python de production non fixée | Python 3.14 imposé (`deploy/PYTHON_VERSION`, contrôle au démarrage) | `384c2df` |
| Revue M1 : réécrire le trousseau changeait propriétaire et mode | Propriétaire, groupe et mode conservés, option `--group`, verrou exclusif, erreurs système converties en erreurs de clé | `1157922` |
| Revue L1 à L6 : rechargement après rotation, migration trop permissive, chiffres Unicode, journal non limité, démarrage sans trousseau, hygiène | Corrigés, un test par constat | `1157922` |
| Confirmation R-L1 à R-L4 : empreinte du fichier, `init-admin` en root, limitation de l'enrôlement, propriétaire hérité | Corrigés ou documentés | `f9e2604` |
| Première exécution Linux : un test créait le fichier du secret de proxy en 0644, donc refusé à juste titre | Le test crée le fichier en 0640 comme en production ; nouveau test du refus d'un fichier lisible par tous | `d32bd0c` |
| Vérification Linux V-L1 : les sauvegardes suivaient l'umask (0644 sous 022) | Fichier de sauvegarde créé 0600 avant la copie | `31e79a0` |
| Vérification Linux V-L2 : l'empreinte de rechargement (inode, taille, date de modification) pouvait se répéter après une rotation suivie d'une restauration de la date | `st_ctime_ns` ajouté à l'empreinte | `31e79a0` |
| Vérification Linux V-L3 : le fichier de verrou suivait les liens symboliques | Ouverture avec `O_NOFOLLOW` | `31e79a0` |
| Test du lien symbolique mal préparé (verrou déjà présent) | Le test retire le verrou avant de poser le lien | `40d4264` |

## 2. Chiffrement des secrets TOTP

- **Pourquoi chiffrer plutôt que hacher :** la vérification d'un code TOTP exige le secret lui-même.
- **Algorithme :** AES-256-GCM, avec `cryptography` 50.0.2, épinglé avec ses empreintes dans `requirements/web.txt`.
- **Nonce :** 96 bits aléatoires à chaque chiffrement ; 20 000 chiffrements vérifiés sans répétition.
- **Données associées :** `astra-mfa:v1:<id de clé>|user=<id du compte>|<active|pending>`. Un secret copié vers un autre compte ou vers l'autre colonne ne se déchiffre pas.
- **Format stocké :** `astra-mfa:v1:<id de clé>:<nonce>:<chiffré+étiquette>`.
- **Clé :**
  - trousseau dans un fichier séparé (`ASTRA_MFA_KEY_FILE`, en production `/etc/astra/mfa.key`, root:astra 0640) ;
  - hors de Git, de SQLite, des sauvegardes de la base et des journaux (seul l'identifiant de clé est journalisé) ;
  - refusé s'il n'est pas un fichier régulier ou s'il est lisible par les autres utilisateurs (POSIX) ;
  - en mode public, démarrage refusé sans trousseau ou avec un trousseau dans le répertoire de la base.
- **Refus fermé :** clé absente, mauvaise clé, donnée corrompue, copie vers un autre compte ou secret en clair donnent une réponse 503, auditée (`mfa.secret_unavailable`) et limitée en fréquence. **La MFA n'est jamais désactivée automatiquement.** Les codes de récupération (empreintes SHA-256) restent utilisables.
- **Rotation :** `mfa-key-rotate` (root) ajoute une clé active et rechiffre ; écritures atomiques, verrouillées, propriétaire et mode conservés ; rechargement à chaud dès que le fichier change (inode, taille, dates de modification et de changement) ; `mfa-key-retire` refusé pour la clé active ou une clé encore utilisée.
- **Migration :** `mfa-reencrypt` chiffre les secrets base32 en clair hérités, laisse intacte toute autre valeur et ne supprime rien. **Aucune base réelle n'a été migrée** : aucune installation n'existe.
- **Récupération (OPERATIONS.md) :** restaurer la sauvegarde séparée du trousseau ; à défaut, `mfa-key-init` puis `reset-mfa` par compte.
- **Résidu :** qui détient à la fois la base et le trousseau peut retrouver les secrets ; ils doivent être sauvegardés séparément.

## 3. Sessions, proxy et accès

| Exigence | Vérification |
|---|---|
| Jetons de session non stockés en clair | Seule l'empreinte SHA-256 est stockée (tests ; vérifié sous Linux dans la base, le WAL et le SHM) |
| Révocation et expiration côté serveur | Tests : déconnexion, révocation, inactivité, durée absolue, désactivation, changement de rôle, jeton rejoué |
| Panne du stockage | 503, jamais d'accès anonyme |
| Secrets absents des journaux | Journaux capturés au niveau DEBUG sur tout le parcours : ni jeton, ni mot de passe, ni secret TOTP, ni code de récupération, ni secret de proxy |
| Secret de proxy | Comparaison à temps constant contre chaque valeur acceptée ; hors dépôt ; jamais journalisé ; renouvelable ; fichier refusé s'il est lisible par tous (vérifié sous Linux). **Il ne remplace pas l'authentification de l'utilisateur.** |
| Backend non exposé | Écoute sur 127.0.0.1 ; règle nftables réservée à l'utilisateur du proxy ; à vérifier sur l'hôte (runbook, étape 7) |

## 4. Tests et contrôles sur `40d4264` (codes de sortie réels)

### 4.1 Windows (poste de développement)

Python 3.14.6, Ruff 0.16.1, Node v24.18.1, paquets épinglés. Exécution du 27/10/2026, 14:31:34 à 14:53:20 UTC. `dirty_files=1` : le seul fichier non suivi était `docs/DEDICATED_SERVER_RUNBOOK.md` (documentation, sans effet sur les tests).

| Commande | Code | Résultat |
|---|---|---|
| `python -m ruff check src tests tools` | 0 | All checks passed! |
| `python -m compileall -q src tests tools deploy` | 0 | |
| `node --check src/astra/static/app.js` | 0 | |
| `tools/ci/secret_scan.py` | 0 | arbre suivi et 125 commits d'historique, aucun secret |
| Tests ciblés (7 modules multi-plateformes, `test_webapp`, `test_mfa_crypto`) | 0 | 356 tests OK, 4 ignorés (POSIX) |
| Suite complète `unittest discover -v` | 0 | 1030 tests OK, 5 ignorés (POSIX), 15 échecs attendus |
| `check_unittest_log.py` (15 échecs attendus, Lot 4) | 0 | `test_lot4_adversarial`=1, `test_lot4_security_review`=7, `test_lot4_verification`=7 |
| Bout en bout sur un vrai socket local (`astra serve`) | 0 | 22/22 contrôles |

Les 5 tests ignorés sous Windows sont les tests POSIX : trousseau lisible par tous refusé, secret de proxy lisible par tous refusé, mode conservé après rotation, lien symbolique de verrou refusé, sauvegarde en 0600 quelle que soit l'umask. **Ils ont tous été exécutés et réussis sous Linux (4.2).**

### 4.2 Linux (hôte propre et autorisé : conteneur local)

- **Environnement :** image officielle `python:3.14-slim` (empreinte `sha256:82abc9f53600280b0757126f83c5a7d47088592d2acb6b4c0b9e97f76865508c`), Python 3.14.10, noyau Linux 6.18 (WSL2), **réseau désactivé** (`--network none`), utilisateur non root uid 1000. Code extrait par `git archive 40d4264` ; mêmes paquets épinglés. Exécution du 27/10/2026, 14:31:34 à 14:49:56 UTC.
- **Ce conteneur n'est pas le serveur de production** : il qualifie le code sous POSIX. Le serveur dédié devra rejouer la suite (runbook, étape 4).

| Commande | Code | Résultat |
|---|---|---|
| `unittest test_mfa_crypto test_webapp test_ops_tools` (auth, MFA, proxy) | 0 | 98 tests OK, aucun ignoré |
| `unittest test_mfa_crypto test_backup` | 0 | 43 tests OK, aucun ignoré |
| Suite complète `unittest discover -v` | 0 | 1030 tests OK, **aucun ignoré**, 15 échecs attendus |
| `check_unittest_log.py` | 0 | les 15 échecs attendus, tous du Lot 4 |

**Contrôle par mutation sous Linux :** chaque correction de `31e79a0` a été annulée dans une copie, et le test correspondant échoue alors :
- mode des sauvegardes : `420 != 384` (0644 au lieu de 0600) ;
- verrou : `MfaKeyError not raised` (le lien symbolique est suivi) ;
- empreinte : le tuple sans `st_ctime_ns` diffère. **Ce test est structurel** (il vérifie la composition de l'empreinte) et ne reproduit pas le scénario réel ; le scénario réel a été reproduit par le vérificateur Linux avant la correction (section 5).

Mutations antérieures (`1157922`, `f9e2604`) : chiffres Unicode, empreinte du fichier de clé, limitation pendant l'enrôlement, cookie d'appareil malformé ; chacune fait échouer son test.

### 4.3 Ce qui n'a pas été exécuté

- **GitHub Actions :** non exécuté (verrou de facturation). Aucun contrôle CI n'est présenté comme réussi.
- **Exécution sur le futur serveur dédié :** impossible tant qu'il n'existe pas.
- **Restauration de `auth.sqlite3` avec le trousseau sur un hôte réel :** prévue à l'étape 10 du runbook.

## 5. Verdicts indépendants (agents distincts de l'auteur des corrections)

| Revue | Résultat |
|---|---|
| Revue AUTH-2 sur `384c2df` | Aucun critique ou élevé ; un moyen (M1) ; six faibles |
| Confirmation sur `1157922` | « no critical, high or medium finding is open », quatre résidus faibles R-L1 à R-L4 (corrigés dans `f9e2604`) |
| **Vérification Linux sur `d32bd0c`** | « no critical, high or medium finding is open » ; trois faibles V-L1 à V-L3 (corrigés dans `31e79a0`) |
| **Revue des corrections sur `40d4264`** | « no critical, high or medium finding is open » ; sept faibles L1 à L7, documentés ci-dessous |

La vérification Linux, menée comme root et comme uid 1000 dans le même conteneur officiel sans réseau, a couvert :
1. **Chiffrement au repos :** seuls des chiffrés `astra-mfa:v1:` dans la base, le WAL, le SHM et les sauvegardes ; aucun secret, mot de passe ou code en clair, y compris sous d'autres encodages.
2. **Permissions :** trousseau 0600 (root), root:groupe 0640 avec `--group`, mêmes modes sous umask 000 ; refus des modes 0644, 0604, 0601, 0602, 0666, d'un lien symbolique, d'un répertoire, d'un FIFO, d'un fichier absent ; secret de proxy refusé en 0644, 0604, 0606 et par lien symbolique.
3. **Rotation :** propriétaire, groupe et mode conservés ; un non-propriétaire est refusé proprement sans modifier le fichier ; 16 rotations concurrentes sans perte de clé.
4. **Rechargement à chaud :** une rotation est prise en compte par un processus en service.
5. **Refus fermé :** mauvaise clé, clé absente, chiffré corrompu ou copié, secret en clair : 503, `mfa_enabled` reste à 1, audit écrit, code de récupération fonctionnel ; migration en ligne de commande correcte et idempotente.
6. **Journaux, audit et réponses :** aucun jeton, mot de passe, secret TOTP, code de récupération ou secret de proxy sur 41 réponses et 10 jetons.
7. **`test_mfa_crypto` :** 33 tests OK, aucun ignoré (35 après `31e79a0`, revérifiés en 4.2).

**Constats faibles qui restent ouverts (aucun moyen, élevé ou critique) :**
- L1 : `restore_backup` crée sa copie intermédiaire selon l'umask, et la base restaurée prend le mode et le propriétaire de la sauvegarde ; après une restauration en root, le service peut ne plus l'ouvrir. Procédure : restaurer en tant qu'utilisateur `astra` ou rétablir propriétaire et mode (runbook, étape 10).
- L2 : le manifeste de sauvegarde (chemin, SHA-256, nombre de lignes) suit l'umask ; métadonnées seulement, et le répertoire de production est en 0700.
- L3 : si la copie SQLite échoue, un fichier de sauvegarde vide reste et compte dans la rétention.
- L4 : une collision de nom de sauvegarde lève `FileExistsError` et non `BackupError` (horodatage à la microseconde).
- L5 : le test de l'empreinte est structurel (voir 4.2).
- L6 : un FIFO ou un lien physique placé à l'emplacement du verrou n'est pas refusé ; exige l'écriture dans le répertoire des clés, ce qui permet déjà de remplacer le trousseau.
- L7 : granularité de `ctime` de quelques millisecondes sur certains noyaux ; fenêtre négligeable.
- Antérieurs : la limitation de la ré-authentification peut être déclenchée par le détenteur d'une session volée (récupération par la CLI locale) ; le MTA d'alerte doit être un client de relais.

## 6. Python

- **Cible de production : Python 3.14** (branche maintenue jusqu'en octobre 2030 selon le guide des versions de Python). Fixée dans `deploy/PYTHON_VERSION`, dans `create_app_from_env` (refus de toute autre version mineure, testé), dans la CI et dans DEPLOYMENT.md.
- Validé localement sur 3.14.6 (Windows) et 3.14.10 (Linux). **Python 3.12 n'est pas validé.**

## 7. `102.211.199.132`

- Ce lot n'a ni utilisé ni modifié ce serveur. Le processus du port 62354 n'a pas été arrêté par ce lot.
- La remédiation de l'intrusion du 19/10/2026 est traitée dans une session séparée, à la demande de l'utilisateur. Selon son rapport (non vérifié par ce lot), le serveur est compromis depuis le 09/02/2025, le port 62354 serait l'agent GLPI légitime, une reconstruction a été choisie, et le confinement attend une action de l'administrateur.
- **L'ancien hôte n'est pas considéré comme propre.** Il reste exclu de l'installation d'ASTRA (`docs/HOST_QUALIFICATION.md`, section A).

## 8. Serveur dédié : procédure préparée, rien d'installé

`docs/DEDICATED_SERVER_RUNBOOK.md` décrit les étapes, **à n'exécuter qu'après validation architecturale** :
0. données fournies par l'opérateur, hors conversation et hors dépôt ;
1. image officielle vérifiée (somme de contrôle et signature) ;
2. SSH par clé, pare-feu entrant en refus par défaut, NTP ;
3. Python 3.14 depuis une source de confiance ;
4. compte de service, code du SHA approuvé, dépendances avec empreintes, suite complète sur l'hôte (tests POSIX compris) ;
5. trousseau MFA et secrets créés sur l'hôte, en root, avec `--group astra` ;
6. premier administrateur dans le contexte du service ;
7. services systemd, cloisonnement, supervision et alerte ;
8. recette en mode privé par tunnel SSH ;
9. domaine et HTTPS, après recette et approbation ;
10. sauvegardes et exercice de restauration, trousseau sauvegardé séparément ;
11. arrêt et rapport.

**Conditions restantes pour le nouveau serveur :**
1. Un hôte dédié neuf, construit depuis une image officielle vérifiée, sans autre application.
2. Un accès SSH autorisé et des privilèges d'administration pour l'opérateur.
3. Un nom de domaine (ou une autre solution HTTPS valide).
4. Un canal d'alerte (destinataire et MTA de relais).
5. Une destination de sauvegarde hors de l'hôte, distincte pour la base et pour le trousseau.
6. La levée du verrou de facturation GitHub, pour que la CI s'exécute.
7. La validation architecturale de ce rapport et du SHA à installer.

## 9. État Git et publication

- **Commits AUTH-2 et AUTH-2.1 :** `384c2df`, `1157922`, `f9e2604`, `d32bd0c`, `31e79a0`, `40d4264`, puis le commit de ce rapport.
- **Fichiers ajoutés :** `src/astra/auth/mfa_crypto.py`, `tests/test_mfa_crypto.py`, `docs/HOST_QUALIFICATION.md`, `docs/DEDICATED_SERVER_RUNBOOK.md`, `deploy/PYTHON_VERSION`, ce rapport.
- **Fichiers modifiés :** code `src/astra/auth/{store,crypto,admin_cli,__init__}.py`, `src/astra/{webapp,config,cli,backup}.py` ; tests `tests/test_webapp.py`, `tests/test_backup.py` ; `requirements/web.txt`, `.github/workflows/ci.yml`, `deploy/astra-web.env.example` ; SECURITY, DEPLOYMENT, OPERATIONS, TASKS, ROADMAP, `docs/CI.md`, `docs/DEPENDENCIES.md`.
- **Publication :** `feat/auth-deploy` était absente de GitHub ; elle est publiée sans forçage après ce commit, et les SHA local et distant sont comparés au point de contrôle. La CI déclenchée sera refusée par le verrou de facturation. Aucune pull request, aucune fusion dans `main`.
- **Aucun secret** dans le dépôt, les journaux ou ce rapport. **Aucun ordre.** Mode SIMULATION, RÉEL verrouillé.

Arrêt au point de contrôle, en attente de la validation architecturale.
